#!/usr/bin/env python3
"""Telegram bridge for a Gas City external-messaging adapter.

Carries turns between Telegram and a Gas City agent, and nothing else. It does
not decide what a change should be, which section it affects, or whether an
edit is safe — the agent's prompt owns all of that. The bridge's only judgment
is which human to ask, which it reads from responsibilities.json.

Protocol (verified against the generated OpenAPI spec, not the connected-clients
guide, which documents a register/SSE-subscribe surface this build does not
have):

  POST /v0/city/{city}/extmsg/adapters   register, with a callback_url
  POST /v0/city/{city}/extmsg/inbound    deliver a human turn to the agent
  POST <callback_url>/publish            Gas City delivers the agent's reply here
  GET  /v0/city/{city}/events/stream     workflow lifecycle, for phase cues

Replies arrive by callback, so there is no polling loop.

Traffic that belongs to a factory project — its conversation is the rig name —
is handed to the FactoryRouter, which owns desks, topics, and the rule that a
person may only speak when an agent is waiting on them. The landing-page flow
below remains for DMs no project is waiting on. The router is enabled by
PROJECT_STATE_DIR.
"""

from __future__ import annotations

import html
import json
import logging
import os
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

import requests
from flask import Flask, jsonify, request

import factory_router as fr

log = logging.getLogger("bridge")

TELEGRAM_API = "https://api.telegram.org"

# One shared conversation for the page keeps the agent's context intact: the
# request, its approval question, and the approval all land in the same thread,
# and the sticky binding created by the first turn keeps routing stable.
PROVIDER = "telegram"
# ConversationKind is a closed enum: dm, room, or thread. Anything else fails
# inbound with a 422 and a 500 on the transcript read.
CONVERSATION_KIND = "dm"

# Telegram caps callback_data at 64 bytes, so the button payload carries an
# opaque id and the bridge holds the rest in memory.
APPROVE_PREFIX = "a:"
REJECT_PREFIX = "r:"

# HTML rather than Markdown, because every message interpolates text the agent
# wrote and Telegram rejects a whole message when its markup does not parse.
# Markdown reads the underscore in "EDIT_DONE" as an unclosed italic and drops
# the message with "can't find end of the entity starting at byte offset 4", so
# the edit lands on disk while the person who approved it hears nothing. HTML
# needs only three characters escaped, which esc() below does exhaustively.
PARSE_MODE = "HTML"

# Bounds on the routing ledger served at /state. Resolved approvals stay
# queryable so a late button press is answered "already decided" rather than
# "no longer open", but a process that runs for weeks must not accumulate every
# request it ever handled.
APPROVAL_HISTORY = 100
TURN_HISTORY = 100


def esc(text: str) -> str:
    """Escape text for Telegram's HTML parse mode."""
    return html.escape(text, quote=False)


class Config:
    """Runtime configuration read from the environment and responsibilities.json."""

    def __init__(self) -> None:
        self.gc_api = os.environ["GC_API"].rstrip("/")
        self.city = os.environ["GC_CITY_NAME"]
        self.account_id = os.getenv("GC_ACCOUNT_ID", "factory")
        self.conversation_id = os.getenv("GC_CONVERSATION_ID", "landing-page")
        self.scope_id = os.getenv("GC_SCOPE_ID", "city")
        self.callback_url = os.environ["BRIDGE_CALLBACK_URL"].rstrip("/")
        self.listen_port = int(os.getenv("BRIDGE_PORT", "8081"))
        self.page_url = os.getenv("PAGE_URL", "")
        # Where the factory router keeps its projects. Unset means no factory:
        # the bridge is then only the landing-page approval channel.
        self.project_state_dir = os.getenv("PROJECT_STATE_DIR", "").strip()

        with open(os.environ["CONFIG_PATH"], encoding="utf-8") as fh:
            doc = json.load(fh)
        self.users: dict[str, dict[str, Any]] = doc["users"]
        self.responsibilities: dict[str, dict[str, Any]] = doc["responsibility_definitions"]

        # Every user needs a bot token to be reachable. A user without one can
        # still be named by the agent, so fail loudly at startup rather than
        # dropping their approval request at delivery time.
        self.tokens: dict[str, str] = {}
        for name, user in self.users.items():
            env_key = user.get("bot_token_env")
            if not env_key:
                raise ValueError(f"user {name!r} has no bot_token_env in responsibilities.json")
            token = (os.getenv(env_key) or "").strip()
            if not token:
                raise ValueError(f"user {name!r}: {env_key} is unset or empty")
            self.tokens[name] = token

    def approvers_for(self, responsibility: str) -> list[str]:
        """Return the usernames responsible for the named responsibility."""
        return [
            name
            for name, user in self.users.items()
            if responsibility in user.get("responsibilities", [])
        ]

    def required_count(self, responsibility: str) -> int:
        """Return how many distinct approvals the responsibility requires."""
        spec = self.responsibilities.get(responsibility, {})
        if spec.get("requires_multiple"):
            return int(spec.get("required_count", 2))
        return 1

    def owns_account(self, name: str, telegram_id: int) -> bool:
        """Report whether a configured user acts from a Telegram account."""
        user = self.users.get(name)
        return user is not None and int(user.get("telegram_id", 0)) == telegram_id


class PendingApproval:
    """One outstanding approval request and the votes cast on it."""

    def __init__(self, responsibility: str, title: str, detail: str, required: int,
                 asked: list[str]) -> None:
        self.responsibility = responsibility
        self.title = title
        self.detail = detail
        self.required = required
        self.asked = asked
        self.approvals: set[str] = set()
        self.rejected_by: str | None = None
        self.resolved = False
        # Message coordinates so every approver's copy can be updated once the
        # request resolves, rather than leaving stale buttons on their phones.
        self.messages: list[tuple[str, int]] = []
        # Sending to several reviewers takes a second each. Until this is set, a
        # reader seeing one name under `delivered` cannot tell "not yet" from
        # "Telegram refused", and the two call for opposite reactions.
        self.delivery_complete = False

    def snapshot(self) -> dict[str, Any]:
        """Return this approval's routing state as JSON-safe data.

        `asked` is who holds the responsibility; `delivered` is who Telegram
        actually accepted a message for. They differ when a reviewer is
        unreachable, which is the difference between a decision nobody has made
        yet and one nobody was ever asked to make.
        """
        return {
            "responsibility": self.responsibility,
            "title": self.title,
            "required": self.required,
            "asked": list(self.asked),
            "delivered": [name for name, _ in self.messages],
            "approved_by": sorted(self.approvals),
            "rejected_by": self.rejected_by,
            "resolved": self.resolved,
            "delivery_complete": self.delivery_complete,
        }


class GasCityClient:
    """Client for the Gas City external-messaging API."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            # Mutating city routes are CSRF-gated; the header is the gate.
            "X-GC-Request": "1",
        })

    def _url(self, path: str) -> str:
        return f"{self.cfg.gc_api}/v0/city/{self.cfg.city}/extmsg/{path}"

    def register_adapter(self) -> None:
        """Register this bridge so Gas City can deliver the agent's replies."""
        body = {
            "provider": PROVIDER,
            "account_id": self.cfg.account_id,
            "name": "telegram-bridge",
            "callback_url": self.cfg.callback_url,
            "capabilities": {
                "MaxMessageLength": 4096,
                "SupportsAttachments": False,
                "SupportsChildConversations": False,
            },
        }
        resp = self.session.post(self._url("adapters"), json=body, timeout=30)
        resp.raise_for_status()
        log.info("registered adapter: %s", resp.json())

    def adapter_registered(self) -> bool:
        """Report whether the controller still holds this bridge's adapter."""
        resp = self.session.get(self._url("adapters"), timeout=15)
        resp.raise_for_status()
        items = resp.json().get("items") or []
        return any(
            item.get("provider") == PROVIDER
            and item.get("account_id") == self.cfg.account_id
            for item in items
        )

    def send_inbound(self, text: str, actor_id: str, actor_name: str,
                     conversation: dict[str, str] | None = None) -> dict[str, Any]:
        """Deliver a human turn to the agent and return the routing decision.

        Without a conversation the turn lands on the bridge's default one, the
        landing page. Factory projects pass their own: the rig, as a room.
        """
        body = {
            "message": {
                "provider_message_id": str(uuid.uuid4()),
                "received_at": datetime.now(timezone.utc).isoformat(),
                "text": text,
                "actor": {
                    "id": actor_id,
                    "display_name": actor_name,
                    "is_bot": False,
                },
                "conversation": conversation or {
                    "provider": PROVIDER,
                    "account_id": self.cfg.account_id,
                    "conversation_id": self.cfg.conversation_id,
                    "scope_id": self.cfg.scope_id,
                    "kind": CONVERSATION_KIND,
                },
            }
        }
        resp = self.session.post(self._url("inbound"), json=body, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def stream_events(self, last_id: str | None) -> Iterator[tuple[str, dict[str, Any]]]:
        """Follow the city's event stream, yielding (event id, event).

        With no cursor the server starts from now; with Last-Event-ID it
        replays what this bridge missed while it was away, so a phase cue is
        not lost to a restart. Returns when the server closes the connection;
        the caller reconnects with the last id it saw.
        """
        headers = {"Accept": "text/event-stream"}
        if last_id:
            headers["Last-Event-ID"] = last_id
        resp = self.session.get(
            f"{self.cfg.gc_api}/v0/city/{self.cfg.city}/events/stream",
            headers=headers, stream=True, timeout=(15, 90),
        )
        resp.raise_for_status()
        yield from iter_sse(resp.iter_lines(decode_unicode=True))


def iter_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Parse server-sent-event frames into (id, decoded JSON data).

    A frame is a run of fields ended by a blank line. Comment lines (":") are
    keepalives. Frames whose data is not JSON are reported and skipped rather
    than ending the stream, since one bad frame must not silence phase cues.
    """
    event_id = ""
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data:
                try:
                    yield event_id, json.loads("\n".join(data))
                except json.JSONDecodeError:
                    log.error("unparseable SSE frame id=%r: %r", event_id, "\n".join(data)[:200])
            event_id, data = "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            event_id = value
        elif field == "data":
            data.append(value)


class Telegram:
    """Minimal synchronous Telegram client.

    Deliberately not python-telegram-bot: the bridge sends from a Flask request
    handler and a polling thread, and an asyncio client fights both.
    """

    def __init__(self, tokens: dict[str, str]) -> None:
        self.tokens = tokens

    def _call(self, token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(f"{TELEGRAM_API}/bot{token}/{method}", json=payload, timeout=30)
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {body.get('description')}")
        return body["result"]

    def send(self, user: str, chat_id: int, text: str,
             buttons: list[list[dict[str, str]]] | None = None,
             thread_id: int | None = None) -> int:
        """Send a message as user's bot and return the Telegram message id.

        thread_id addresses a topic inside a desk supergroup; without it the
        message goes to the chat itself (a DM, or a desk's General topic).
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": PARSE_MODE,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return self._call(self.tokens[user], "sendMessage", payload)["message_id"]

    # Forum topics: the bot must be an admin of the desk with can_manage_topics.

    def create_forum_topic(self, user: str, chat_id: int, name: str) -> int:
        """Create a topic in a desk and return its thread id."""
        result = self._call(self.tokens[user], "createForumTopic", {"chat_id": chat_id, "name": name[:128]})
        return int(result["message_thread_id"])

    def close_forum_topic(self, user: str, chat_id: int, thread_id: int) -> None:
        """Close a topic so members cannot post in it."""
        self._call(self.tokens[user], "closeForumTopic", {"chat_id": chat_id, "message_thread_id": thread_id})

    def reopen_forum_topic(self, user: str, chat_id: int, thread_id: int) -> None:
        """Reopen a topic so its owner can answer."""
        self._call(self.tokens[user], "reopenForumTopic", {"chat_id": chat_id, "message_thread_id": thread_id})

    def delete_forum_topic(self, user: str, chat_id: int, thread_id: int) -> None:
        """Delete a topic and everything in it."""
        self._call(self.tokens[user], "deleteForumTopic", {"chat_id": chat_id, "message_thread_id": thread_id})

    def edit(self, user: str, chat_id: int, message_id: int, text: str) -> None:
        """Replace a message's text and drop its buttons."""
        self._call(self.tokens[user], "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": PARSE_MODE,
        })

    def answer_callback(self, user: str, callback_id: str, text: str) -> None:
        """Acknowledge a button press so the client stops showing a spinner."""
        self._call(self.tokens[user], "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text,
        })

    def get_updates(self, user: str, offset: int, timeout: int = 25) -> list[dict[str, Any]]:
        """Long-poll one bot for updates."""
        resp = requests.get(
            f"{TELEGRAM_API}/bot{self.tokens[user]}/getUpdates",
            params={"offset": offset, "timeout": timeout},
            timeout=timeout + 10,
        )
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"telegram getUpdates failed: {body.get('description')}")
        return body["result"]


class Bridge:
    """Routes turns between Telegram and the Gas City agent."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.gc = GasCityClient(cfg)
        self.tg = Telegram(cfg.tokens)
        self.pending: dict[str, PendingApproval] = {}
        self.turns: deque[dict[str, str]] = deque(maxlen=TURN_HISTORY)
        self.lock = threading.Lock()
        self.factory: fr.FactoryRouter | None = None
        if cfg.project_state_dir:
            self.factory = fr.FactoryRouter(
                users=cfg.users, responsibilities=cfg.responsibilities,
                account_id=cfg.account_id, tg=self.tg, gc=self.gc,
                store=fr.ProjectStore(cfg.project_state_dir),
            )

    def _project_for(self, conversation: dict[str, Any] | None) -> fr.Project | None:
        """Return the factory project a conversation belongs to, if any."""
        if self.factory is None or not conversation:
            return None
        return self.factory.project_for_conversation(str(conversation.get("conversation_id", "")))

    # --- Routing ledger ---------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Return every approval this bridge has routed and every agent turn.

        Routing is otherwise visible only as a message on somebody's phone, so
        whether a request reached the right reviewer cannot be checked from
        outside the conversation. This makes it a question with an answer.
        """
        with self.lock:
            return {
                "approvals": [
                    dict(id=approval_id, **pending.snapshot())
                    for approval_id, pending in self.pending.items()
                ],
                "turns": list(self.turns),
            }

    def _remember(self, approval_id: str, pending: PendingApproval) -> None:
        """Record an approval, retiring resolved ones once history fills.

        Unresolved approvals are never retired. Someone is still looking at that
        message, and forgetting it turns their press into "no longer open" —
        discarding a decision at the moment it is made.
        """
        with self.lock:
            self.pending[approval_id] = pending
            if len(self.pending) <= APPROVAL_HISTORY:
                return
            for old_id, old in list(self.pending.items()):
                if len(self.pending) <= APPROVAL_HISTORY:
                    break
                if old.resolved:
                    del self.pending[old_id]

    # --- Registration upkeep ---------------------------------------------

    def ensure_registered(self) -> bool:
        """Re-register if the controller has forgotten this adapter.

        Returns whether a registration was performed. Never raises: a controller
        that is restarting refuses the check, and a reconcile loop that dies on
        that leaves the bridge permanently deaf.
        """
        try:
            if self.gc.adapter_registered():
                return False
            log.warning("adapter registration is gone; registering again")
            self.gc.register_adapter()
            return True
        except Exception:
            log.exception("checking adapter registration")
            return False

    def reconcile_registration(self, interval: float = 30.0) -> None:
        """Keep this bridge registered for as long as it runs.

        Registrations live in the controller's memory, so `gc stop`/`gc start`, a
        supervisor restart, or a crash drops them. Nothing tells the bridge: Gas
        City keeps accepting inbound turns and the agent keeps working, but its
        replies come back 422 and the human waiting on an approval sees silence.
        There is no event to subscribe to for "the controller restarted", so this
        converges on the desired state by checking it, the same way the rest of
        the system stays correct.
        """
        while True:
            time.sleep(interval)
            self.ensure_registered()

    # --- Gas City -> Telegram -------------------------------------------

    def on_publish(self, text: str, conversation: dict[str, Any] | None = None,
                   session_id: str = "") -> None:
        """Handle one outbound turn from the agent.

        A turn on a project's conversation is the router's; anything else is
        the landing-page flow.
        """
        stripped = text.strip()
        with self.lock:
            self.turns.append({
                "at": datetime.now(timezone.utc).isoformat(),
                "text": stripped,
                "conversation": str((conversation or {}).get("conversation_id", "")),
            })
        project = self._project_for(conversation)
        if project is not None and self.factory is not None:
            self.factory.on_publish(project, stripped, session_id)
            return
        if stripped.startswith("APPROVAL_NEEDED:"):
            self._ask_approval(stripped[len("APPROVAL_NEEDED:"):])
        else:
            self._broadcast(self._agent_turn_html(stripped))

    def _agent_turn_html(self, text: str) -> str:
        """Render one plain turn from the agent as Telegram-safe HTML."""
        rendered = esc(text)
        if self.cfg.page_url and text.startswith("EDIT_DONE:"):
            rendered += f'\n\n<a href="{esc(self.cfg.page_url)}">View the page</a>'
        return rendered

    def _ask_approval(self, body: str) -> None:
        """Parse an approval request and deliver it to whoever is responsible."""
        parts = [p.strip() for p in body.split("|")]
        if len(parts) < 3:
            log.error("malformed approval request: %r", body)
            self._broadcast(
                    "The agent sent a malformed approval request. Nothing was changed.\n\n"
                    f"<code>{esc(body)}</code>"
                )
            return

        responsibility, title, detail = parts[0], parts[1], " | ".join(parts[2:])
        approvers = self.cfg.approvers_for(responsibility)
        if not approvers:
            log.error("no approver for responsibility %r", responsibility)
            self._broadcast(
                f"The agent asked for <b>{esc(responsibility)}</b> approval, but nobody "
                "holds that responsibility. Nothing was changed."
            )
            return

        required = min(self.cfg.required_count(responsibility), len(approvers))
        approval_id = uuid.uuid4().hex[:12]
        pending = PendingApproval(responsibility, title, detail, required, approvers)
        self._remember(approval_id, pending)

        spec = self.cfg.responsibilities.get(responsibility, {})
        label = spec.get("name", responsibility)
        icon = spec.get("icon", "")

        message = (
            f"{esc(icon)} <b>{esc(label)}</b>\n\n"
            f"<b>{esc(title)}</b>\n{esc(detail)}\n\n"
            f"<i>Approvals needed: {required}</i>"
        )
        buttons = [[
            {"text": "Approve", "callback_data": f"{APPROVE_PREFIX}{approval_id}"},
            {"text": "Reject", "callback_data": f"{REJECT_PREFIX}{approval_id}"},
        ]]

        for name in approvers:
            chat_id = int(self.cfg.users[name]["telegram_id"])
            try:
                message_id = self.tg.send(name, chat_id, message, buttons)
            except Exception:
                # One unreachable approver must not silence the others.
                log.exception("delivering approval %s to %s", approval_id, name)
                continue
            with self.lock:
                pending.messages.append((name, message_id))
            log.info("approval %s sent to %s", approval_id, name)
        with self.lock:
            pending.delivery_complete = True

    def _broadcast(self, html_text: str) -> None:
        """Send one already-escaped HTML message to every configured user."""
        for name, user in self.cfg.users.items():
            try:
                self.tg.send(name, int(user["telegram_id"]), html_text)
            except Exception:
                log.exception("broadcasting to %s", name)

    # --- Telegram -> Gas City -------------------------------------------

    def on_message(self, actor: str, telegram_id: int, text: str) -> None:
        """Forward a human message to the agent."""
        if text.strip().startswith("/start"):
            self.tg.send(actor, telegram_id, (
                f"Connected as <b>{esc(actor)}</b>.\n\n"
                "Ask for a change to the landing page and I'll pass it to the agent. "
                "The agent works out which section it touches and who has to approve "
                "before it edits anything."
            ))
            return

        try:
            decision = self.gc.send_inbound(text, str(telegram_id), actor)
        except Exception as exc:
            log.exception("sending inbound to Gas City")
            self.tg.send(actor, telegram_id,
                         f"Could not reach the agent: <code>{esc(str(exc))}</code>")
            return

        target = decision.get("TargetAgentName") or "the agent"
        self.tg.send(actor, telegram_id,
                     f"Sent to <b>{esc(target)}</b>. Waiting for its reply.")

    def on_button(self, actor: str, callback_id: str, data: str) -> None:
        """Record a vote and, once the threshold is met, tell the agent."""
        if data.startswith(APPROVE_PREFIX):
            approved, approval_id = True, data[len(APPROVE_PREFIX):]
        elif data.startswith(REJECT_PREFIX):
            approved, approval_id = False, data[len(REJECT_PREFIX):]
        else:
            log.error("unrecognized callback data %r", data)
            return

        with self.lock:
            pending = self.pending.get(approval_id)
        if pending is None and self.factory is not None:
            if self.factory.on_button(actor, callback_id, approval_id, approved):
                return

        with self.lock:
            if pending is None:
                self.tg.answer_callback(actor, callback_id, "That request is no longer open.")
                return
            if pending.resolved:
                self.tg.answer_callback(actor, callback_id, "Already decided.")
                return
            if actor not in self.cfg.approvers_for(pending.responsibility):
                self.tg.answer_callback(actor, callback_id, "Not your responsibility.")
                return

            if not approved:
                pending.rejected_by = actor
                pending.resolved = True
                outcome = f"REJECTED by {actor}"
                note = f"Rejected by {actor}."
            else:
                pending.approvals.add(actor)
                remaining = pending.required - len(pending.approvals)
                if remaining > 0:
                    self.tg.answer_callback(
                        actor, callback_id, f"Recorded. {remaining} more needed."
                    )
                    return
                pending.resolved = True
                outcome = "APPROVED by " + ", ".join(sorted(pending.approvals))
                note = "Approved by " + ", ".join(sorted(pending.approvals)) + "."
            messages = list(pending.messages)

        self.tg.answer_callback(actor, callback_id, note)
        for name, message_id in messages:
            try:
                self.tg.edit(
                    name, int(self.cfg.users[name]["telegram_id"]), message_id,
                    f"<b>{esc(pending.title)}</b>\n{esc(pending.detail)}\n\n{esc(note)}",
                )
            except Exception:
                log.exception("updating approval message for %s", name)

        try:
            self.gc.send_inbound(
                f"{outcome} — for: {pending.title}", actor,
                self.cfg.users[actor].get("telegram_username", actor),
            )
        except Exception:
            log.exception("reporting decision %s to Gas City", approval_id)
            self._broadcast(
                f"Recorded <b>{esc(note)}</b> but could not reach the agent. "
                "It will not act on this."
            )

    # --- Telegram polling ------------------------------------------------

    def polling_groups(self) -> list[list[str]]:
        """Group users by bot token: Telegram allows one getUpdates consumer per bot.

        The drill config gives every reviewer their own bot; the factory gives
        everyone one bot. Both shapes poll each token exactly once.
        """
        groups: dict[str, list[str]] = {}
        for user, token in self.cfg.tokens.items():
            groups.setdefault(token, []).append(user)
        return list(groups.values())

    def actor_for(self, users: list[str], update: dict[str, Any]) -> str:
        """Pick which of a bot's users an update is from.

        With one user per bot, the bot identifies them and the sender's account
        only authorizes them (two reviewers may share one phone). With several
        users on one bot, the sender's account is the only identity there is;
        an unknown sender resolves to the first user, whose ownership check
        then refuses them.
        """
        if len(users) == 1:
            return users[0]
        source = update.get("callback_query") or update.get("message") or {}
        sender = int((source.get("from") or {}).get("id", 0))
        for user in users:
            if self.cfg.owns_account(user, sender):
                return user
        return users[0]

    def poll(self, users: list[str]) -> None:
        """Long-poll one bot forever, dispatching its updates to its users."""
        offset = 0
        while True:
            try:
                updates = self.tg.get_updates(users[0], offset)
            except Exception:
                log.exception("polling %s", users)
                time.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                user = self.actor_for(users, update)
                try:
                    self._dispatch(user, update)
                except Exception:
                    log.exception("handling update %s for %s", update.get("update_id"), user)

    def _dispatch(self, user: str, update: dict[str, Any]) -> None:
        """Route one Telegram update to the reviewer whose bot received it.

        The bot identifies the reviewer; the sender's Telegram account only
        authorizes them. Two reviewers may share one account — the deployed
        config has both on a single phone, each with their own bot — so deriving
        the actor from the sender's id resolves every update to whichever user
        happens to come first in responsibilities.json. That failure is quiet and
        total: the other reviewer's exclusive responsibilities become
        unapprovable by anyone, and a responsibility needing two approvals can
        never reach two.

        Messages from inside a desk (a group chat) belong to the factory: the
        project topic decides who may speak, and anything else said in the desk
        is between the people in it, not for the agent.
        """
        if "callback_query" in update:
            query = update["callback_query"]
            if not self.cfg.owns_account(user, query["from"]["id"]):
                self.tg.answer_callback(
                    user, query["id"], "You are not this bot's reviewer."
                )
                return
            self.on_button(user, query["id"], query.get("data", ""))
            return

        message = update.get("message")
        if not message or "text" not in message:
            return
        telegram_id = message["from"]["id"]
        chat_id = int((message.get("chat") or {}).get("id", telegram_id))
        in_group = chat_id != telegram_id
        thread_id = message.get("message_thread_id") if message.get("is_topic_message") else None
        if not self.cfg.owns_account(user, telegram_id):
            if in_group:
                log.info("ignoring message from %s in desk %s", telegram_id, chat_id)
                return
            self.tg.send(
                user, telegram_id,
                "This bot answers to a different Telegram account. Set this user's "
                "telegram_id in responsibilities.json to act as them.",
            )
            return
        if self.factory is not None and self.factory.on_message(user, chat_id, thread_id, message["text"]):
            return
        if in_group:
            return
        self.on_message(user, telegram_id, message["text"])

    # --- Event stream ------------------------------------------------------

    def follow_events_once(self, last_id: str | None) -> str | None:
        """Consume one connection's worth of events; return the last id seen."""
        assert self.factory is not None
        for event_id, event in self.gc.stream_events(last_id):
            last_id = event_id or last_id
            try:
                self.factory.on_event(event)
            except Exception:
                log.exception("handling event %s", event.get("type"))
        return last_id

    def follow_events(self) -> None:
        """Keep the factory's phase cues flowing for as long as the bridge runs."""
        last_id: str | None = None
        while True:
            try:
                last_id = self.follow_events_once(last_id)
            except Exception:
                log.exception("event stream dropped; reconnecting")
            time.sleep(5)


def create_app(bridge: Bridge) -> Flask:
    """Build the Flask app that receives Gas City's outbound callbacks."""
    app = Flask(__name__)

    @app.post("/publish")
    def publish() -> Any:
        body = request.get_json(silent=True) or {}
        conversation = body.get("conversation", {})
        text = body.get("text", "")
        log.info("publish on %r: %r", conversation.get("conversation_id"), text[:200])
        try:
            bridge.on_publish(text, conversation=conversation, session_id=str(body.get("session_id", "")))
        except Exception:
            log.exception("handling publish")
            return jsonify({
                "conversation": conversation,
                "delivered": False,
                "failure_kind": "transient",
            }), 200
        return jsonify({
            "message_id": uuid.uuid4().hex,
            "conversation": conversation,
            "delivered": True,
        }), 200

    @app.get("/health")
    def health() -> Any:
        with bridge.lock:
            open_approvals = sum(1 for p in bridge.pending.values() if not p.resolved)
        return jsonify({"status": "ok", "open_approvals": open_approvals})

    @app.get("/state")
    def state() -> Any:
        return jsonify(bridge.state())

    # --- Factory projects. The intake command (factory.py) drives these. ---

    def factory_or_503() -> tuple[fr.FactoryRouter | None, Any]:
        if bridge.factory is None:
            return None, (jsonify({"error": "factory routing is off: PROJECT_STATE_DIR is unset"}), 503)
        return bridge.factory, None

    @app.get("/projects")
    def list_projects() -> Any:
        factory, refusal = factory_or_503()
        if factory is None:
            return refusal
        return jsonify({"projects": factory.snapshot()})

    @app.post("/projects")
    def create_project() -> Any:
        factory, refusal = factory_or_503()
        if factory is None:
            return refusal
        body = request.get_json(silent=True) or {}
        missing = [k for k in ("name", "rig", "workflow_id", "roster") if not body.get(k)]
        if missing:
            return jsonify({"error": f"missing: {', '.join(missing)}"}), 400
        try:
            project = factory.register(name=body["name"], rig=body["rig"],
                                       workflow_id=body["workflow_id"], roster=body["roster"])
        except fr.RosterError as exc:
            return jsonify({"error": str(exc)}), 400
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(project.to_doc()), 201

    @app.post("/projects/<name>/restart")
    def restart_project(name: str) -> Any:
        factory, refusal = factory_or_503()
        if factory is None:
            return refusal
        body = request.get_json(silent=True) or {}
        if not body.get("workflow_id"):
            return jsonify({"error": "missing: workflow_id"}), 400
        try:
            project = factory.restart(name, workflow_id=body["workflow_id"])
        except KeyError:
            return jsonify({"error": f"no project named {name!r}"}), 404
        return jsonify(project.to_doc()), 200

    return app


def main() -> int:
    """Register the adapter, start the pollers, and serve the callback listener."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        cfg = Config()
    except (KeyError, ValueError) as exc:
        log.error("configuration error: %s", exc)
        return 1

    bridge = Bridge(cfg)
    try:
        bridge.gc.register_adapter()
    except Exception as exc:
        log.error("could not register adapter with Gas City at %s: %s", cfg.gc_api, exc)
        return 1

    threading.Thread(target=bridge.reconcile_registration, daemon=True,
                     name="reconcile-registration").start()

    for users in bridge.polling_groups():
        threading.Thread(target=bridge.poll, args=(users,), daemon=True,
                         name=f"poll-{'+'.join(users)}").start()
        log.info("polling Telegram for %s", ", ".join(users))

    if bridge.factory is not None:
        threading.Thread(target=bridge.follow_events, daemon=True, name="events").start()
        log.info("factory routing on: %d project(s) in %s",
                 len(bridge.factory.projects), cfg.project_state_dir)

    app = create_app(bridge)
    log.info("callback listener on port %d (registered as %s)", cfg.listen_port, cfg.callback_url)
    app.run(host="0.0.0.0", port=cfg.listen_port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
