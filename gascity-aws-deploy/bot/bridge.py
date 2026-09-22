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

Replies arrive by callback, so there is no polling loop.
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
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Flask, jsonify, request

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

    def user_by_telegram_id(self, telegram_id: int) -> str | None:
        """Return the username owning a Telegram account id, or None."""
        for name, user in self.users.items():
            if int(user.get("telegram_id", 0)) == telegram_id:
                return name
        return None


class PendingApproval:
    """One outstanding approval request and the votes cast on it."""

    def __init__(self, responsibility: str, title: str, detail: str, required: int) -> None:
        self.responsibility = responsibility
        self.title = title
        self.detail = detail
        self.required = required
        self.approvals: set[str] = set()
        self.rejected_by: str | None = None
        self.resolved = False
        # Message coordinates so every approver's copy can be updated once the
        # request resolves, rather than leaving stale buttons on their phones.
        self.messages: list[tuple[str, int]] = []


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

    def send_inbound(self, text: str, actor_id: str, actor_name: str) -> dict[str, Any]:
        """Deliver a human turn to the agent and return the routing decision."""
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
                "conversation": {
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
             buttons: list[list[dict[str, str]]] | None = None) -> int:
        """Send a message as user's bot and return the Telegram message id."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": PARSE_MODE,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return self._call(self.tokens[user], "sendMessage", payload)["message_id"]

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
        self.lock = threading.Lock()

    # --- Gas City -> Telegram -------------------------------------------

    def on_publish(self, text: str) -> None:
        """Handle one outbound turn from the agent."""
        stripped = text.strip()
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
        pending = PendingApproval(responsibility, title, detail, required)

        with self.lock:
            self.pending[approval_id] = pending

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

    def poll(self, user: str) -> None:
        """Long-poll one bot forever, dispatching its updates."""
        offset = 0
        while True:
            try:
                updates = self.tg.get_updates(user, offset)
            except Exception:
                log.exception("polling %s", user)
                time.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self._dispatch(user, update)
                except Exception:
                    log.exception("handling update %s for %s", update.get("update_id"), user)

    def _dispatch(self, user: str, update: dict[str, Any]) -> None:
        """Route one Telegram update to the right handler."""
        if "callback_query" in update:
            query = update["callback_query"]
            sender = self.cfg.user_by_telegram_id(query["from"]["id"])
            if sender is None:
                self.tg.answer_callback(user, query["id"], "You are not a configured user.")
                return
            self.on_button(sender, query["id"], query.get("data", ""))
            return

        message = update.get("message")
        if not message or "text" not in message:
            return
        telegram_id = message["from"]["id"]
        sender = self.cfg.user_by_telegram_id(telegram_id)
        if sender is None:
            self.tg.send(
                user, telegram_id,
                "You are not a configured user of this city. Add your Telegram ID to "
                "responsibilities.json.",
            )
            return
        self.on_message(sender, telegram_id, message["text"])


def create_app(bridge: Bridge) -> Flask:
    """Build the Flask app that receives Gas City's outbound callbacks."""
    app = Flask(__name__)

    @app.post("/publish")
    def publish() -> Any:
        body = request.get_json(silent=True) or {}
        conversation = body.get("conversation", {})
        text = body.get("text", "")
        log.info("publish: %r", text[:200])
        try:
            bridge.on_publish(text)
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

    for user in cfg.users:
        threading.Thread(target=bridge.poll, args=(user,), daemon=True,
                         name=f"poll-{user}").start()
        log.info("polling Telegram as %s", user)

    app = create_app(bridge)
    log.info("callback listener on port %d (registered as %s)", cfg.listen_port, cfg.callback_url)
    app.run(host="0.0.0.0", port=cfg.listen_port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
