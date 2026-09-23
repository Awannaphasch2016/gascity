"""Project routing for the software factory: desks, topics, and gated replies.

One rig is one project, and each project has its own Gas City conversation
whose id is the rig name. Every roster member gets a topic named after the
project in their desk — a Telegram supergroup with Topics where the bot is an
admin — and all project traffic lands in those topics. A topic is closed while
the agents work and reopened only when an agent asks that person something;
a message that arrives while nothing is being asked is refused here rather
than forwarded, so nobody is left believing a stray remark reached the agent.
People without a desk get the same conversation in their bot's DM, with the
same gate applied on this side since a DM cannot be closed.

The router decides only who to ask and whether a reply is expected. What is
asked, and what the answer means, belongs to the agents.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("factory")

PROVIDER = "telegram"
CONVERSATION_KIND = "room"
SCOPE_ID = "city"

APPROVE_PREFIX = "a:"
REJECT_PREFIX = "r:"

# Protocol lines the pack's agents open a message with. Everything else is a
# note for the whole roster.
QUESTIONS_TAG = "QUESTIONS:"
APPROVAL_TAG = "APPROVAL_NEEDED:"
DELIVER_TAG = "DELIVER:"

# What the bridge sends back to the agent when the human has spoken.
DELIVERY_UNAVAILABLE = (
    "DELIVERY_UNAVAILABLE: this bridge cannot push to GitHub yet; tell the "
    "roster the repository is ready locally and where it is."
)


def esc(text: str) -> str:
    """Escape text for Telegram's HTML parse mode."""
    return html.escape(text, quote=False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RosterError(ValueError):
    """A project roster names someone or something the bridge cannot route to."""


# --- Model ---------------------------------------------------------------------


@dataclass
class Roster:
    """Who is on a project and what each person is responsible for."""

    members: dict[str, list[str]]
    admins: list[str]

    @classmethod
    def from_doc(cls, doc: dict[str, Any], known_users: set[str]) -> "Roster":
        members = {name: list(resps) for name, resps in (doc.get("members") or {}).items()}
        if not members:
            raise RosterError("roster has no members")
        unknown = sorted(set(members) - known_users)
        if unknown:
            raise RosterError(f"roster names people missing from responsibilities.json: {', '.join(unknown)}")
        admins = [name for name in (doc.get("admins") or []) if name in members]
        return cls(members=members, admins=admins or list(members)[:1])

    def holders(self, responsibility: str) -> list[str]:
        return [name for name, resps in self.members.items() if responsibility in resps]

    def to_doc(self) -> dict[str, Any]:
        return {"members": self.members, "admins": self.admins}


@dataclass
class Project:
    """One rig, its workflow, its people, and where each of them is reached."""

    name: str
    rig: str
    workflow_id: str
    roster: Roster
    # user -> {"chat_id": desk supergroup, "thread_id": topic}
    topics: dict[str, dict[str, int]] = field(default_factory=dict)
    # user -> {"kind": "question" | "approval", "asked_at": iso}
    awaiting: dict[str, dict[str, str]] = field(default_factory=dict)
    phase: str | None = None
    reported_closed: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    # The extmsg account the bridge registered as; every project shares it.
    account_id: str = "factory"

    def conversation(self) -> dict[str, str]:
        return {
            "provider": PROVIDER,
            "account_id": self.account_id,
            "conversation_id": self.rig,
            "scope_id": SCOPE_ID,
            "kind": CONVERSATION_KIND,
        }

    def to_doc(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rig": self.rig,
            "workflow_id": self.workflow_id,
            "account_id": self.account_id,
            "roster": self.roster.to_doc(),
            "topics": self.topics,
            "awaiting": self.awaiting,
            "phase": self.phase,
            "reported_closed": self.reported_closed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any], known_users: set[str]) -> "Project":
        return cls(
            name=doc["name"],
            rig=doc["rig"],
            workflow_id=doc["workflow_id"],
            roster=Roster.from_doc(doc["roster"], known_users),
            topics={u: {"chat_id": int(t["chat_id"]), "thread_id": int(t["thread_id"])} for u, t in (doc.get("topics") or {}).items()},
            awaiting=dict(doc.get("awaiting") or {}),
            phase=doc.get("phase"),
            reported_closed=list(doc.get("reported_closed") or []),
            created_at=doc.get("created_at") or now_iso(),
            account_id=doc.get("account_id") or "factory",
        )


class ProjectStore:
    """Projects on disk, one JSON file each, written atomically."""

    def __init__(self, directory: str) -> None:
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.directory, f"{name}.json")

    def save(self, project: Project) -> None:
        target = self._path(project.name)
        tmp = f"{target}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(project.to_doc(), fh, indent=2, sort_keys=True)
        os.replace(tmp, target)

    def load_all(self, known_users: set[str] | None = None) -> list[Project]:
        projects = []
        for entry in sorted(os.listdir(self.directory)):
            if not entry.endswith(".json"):
                continue
            with open(os.path.join(self.directory, entry), encoding="utf-8") as fh:
                doc = json.load(fh)
            projects.append(Project.from_doc(doc, known_users if known_users is not None else set((doc.get("roster") or {}).get("members") or {})))
        return projects


@dataclass
class PendingApproval:
    """One open approval on a project and the votes cast on it."""

    project: str
    responsibility: str
    title: str
    detail: str
    required: int
    asked: list[str]
    approvals: set[str] = field(default_factory=set)
    resolved: bool = False
    # (user, chat_id, message_id) so every copy can lose its buttons on resolve.
    messages: list[tuple[str, int, int]] = field(default_factory=list)


# --- Parsing ---------------------------------------------------------------------


def parse_publish(text: str) -> tuple[str, str, str]:
    """Classify one agent message: (kind, subject, body).

    kind is questions | approval | deliver | note. For questions the subject
    is the responsibility and the body the numbered rounds; for approval the
    subject is the responsibility and the body "title | detail"; for deliver
    the subject is the visibility.
    """
    stripped = text.strip()
    if stripped.startswith(QUESTIONS_TAG):
        head, _, body = stripped[len(QUESTIONS_TAG):].partition("\n")
        return "questions", head.strip(), body.strip()
    if stripped.startswith(APPROVAL_TAG):
        parts = [p.strip() for p in stripped[len(APPROVAL_TAG):].split("|")]
        return "approval", parts[0], " | ".join(parts[1:])
    if stripped.startswith(DELIVER_TAG):
        return "deliver", stripped[len(DELIVER_TAG):].strip(), ""
    return "note", "", stripped


PROJECT_PREFIX = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:\s*(.*)$", re.DOTALL)


# --- Router -----------------------------------------------------------------------


class FactoryRouter:
    """Routes project traffic between agents and the people on the roster."""

    def __init__(self, users: dict[str, dict[str, Any]], responsibilities: dict[str, dict[str, Any]],
                 account_id: str, tg: Any, gc: Any, store: ProjectStore) -> None:
        self.users = users
        self.responsibilities = responsibilities
        self.account_id = account_id
        self.tg = tg
        self.gc = gc
        self.store = store
        self.lock = threading.RLock()
        self.pending: dict[str, PendingApproval] = {}
        self.projects: dict[str, Project] = {p.name: p for p in store.load_all(set(users))}

    # --- lookups ---

    def project_for_conversation(self, conversation_id: str) -> Project | None:
        with self.lock:
            for project in self.projects.values():
                if project.rig == conversation_id:
                    return project
        return None

    def project_for_topic(self, chat_id: int, thread_id: int | None) -> tuple[Project, str] | None:
        if thread_id is None:
            return None
        with self.lock:
            for project in self.projects.values():
                for user, topic in project.topics.items():
                    if topic["chat_id"] == chat_id and topic["thread_id"] == thread_id:
                        return project, user
        return None

    def _desk(self, user: str) -> int | None:
        desk = self.users.get(user, {}).get("desk_chat_id")
        return int(desk) if desk else None

    def _dm(self, user: str) -> int:
        return int(self.users[user]["telegram_id"])

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return [p.to_doc() for p in self.projects.values()]

    # --- lifecycle ---

    def register(self, name: str, rig: str, workflow_id: str, roster: dict[str, Any]) -> Project:
        """Create the project's topics (or DM intros) and remember it."""
        project = Project(name=name, rig=rig, workflow_id=workflow_id,
                          roster=Roster.from_doc(roster, set(self.users)), account_id=self.account_id)
        with self.lock:
            if name in self.projects:
                raise KeyError(f"project {name!r} already exists; use restart")
            self._open_rooms(project)
            self.projects[name] = project
            self.store.save(project)
        return project

    def restart(self, name: str, workflow_id: str) -> Project:
        """Throw the project's topics away, make fresh ones, forget open asks."""
        with self.lock:
            old = self.projects[name]
            for user, topic in old.topics.items():
                try:
                    self.tg.delete_forum_topic(user, topic["chat_id"], topic["thread_id"])
                except Exception:
                    log.exception("deleting topic for %s in project %s", user, name)
            for approval_id, pending in list(self.pending.items()):
                if pending.project == name:
                    del self.pending[approval_id]
            project = Project(name=name, rig=old.rig, workflow_id=workflow_id, roster=old.roster,
                              account_id=self.account_id)
            self._open_rooms(project)
            self.projects[name] = project
            self.store.save(project)
        return project

    def _open_rooms(self, project: Project) -> None:
        intro = (
            f"<b>{esc(project.name)}</b> is starting. Agents will ask here when they need "
            "you; until then the room stays closed."
        )
        for user in project.roster.members:
            desk = self._desk(user)
            if desk is None:
                self.tg.send(user, self._dm(user), intro)
                continue
            thread_id = self.tg.create_forum_topic(user, desk, project.name)
            project.topics[user] = {"chat_id": desk, "thread_id": thread_id}
            self.tg.send(user, desk, intro, thread_id=thread_id)
            self.tg.close_forum_topic(user, desk, thread_id)

    # --- delivery primitives ---

    def _send(self, project: Project, user: str, text: str, buttons: list | None = None) -> tuple[int, int]:
        topic = project.topics.get(user)
        if topic:
            message_id = self.tg.send(user, topic["chat_id"], text, buttons, thread_id=topic["thread_id"])
            return topic["chat_id"], message_id
        chat = self._dm(user)
        return chat, self.tg.send(user, chat, text, buttons)

    def _open(self, project: Project, user: str, kind: str) -> None:
        topic = project.topics.get(user)
        if topic:
            self.tg.reopen_forum_topic(user, topic["chat_id"], topic["thread_id"])
        project.awaiting[user] = {"kind": kind, "asked_at": now_iso()}

    def _close(self, project: Project, user: str) -> None:
        project.awaiting.pop(user, None)
        topic = project.topics.get(user)
        if topic:
            self.tg.close_forum_topic(user, topic["chat_id"], topic["thread_id"])

    def _broadcast(self, project: Project, text: str) -> None:
        for user in project.roster.members:
            try:
                self._send(project, user, text)
            except Exception:
                log.exception("broadcasting to %s in %s", user, project.name)

    def _label(self, responsibility: str) -> str:
        spec = self.responsibilities.get(responsibility, {})
        icon = spec.get("icon", "")
        name = spec.get("name", responsibility.replace("_", " "))
        return f"{esc(icon)} <b>{esc(name)}</b>".strip()

    # --- agent -> humans ---

    def on_publish(self, project: Project, text: str, session_id: str) -> None:
        """Deliver one agent message to the right people on the project."""
        kind, subject, body = parse_publish(text)
        with self.lock:
            if kind == "questions":
                self._ask_questions(project, subject, body)
            elif kind == "approval":
                self._ask_approval(project, subject, body)
            elif kind == "deliver":
                self._deliver(project, subject)
            else:
                self._broadcast(project, esc(body))
            self.store.save(project)

    def _recipients(self, project: Project, responsibility: str) -> tuple[list[str], str]:
        holders = project.roster.holders(responsibility)
        if holders:
            return holders, ""
        note = (
            f"The agent asked for <b>{esc(responsibility)}</b>, but nobody on this project "
            "holds that responsibility. As an admin, please answer in their place or fix roster.json."
        )
        return list(project.roster.admins), note

    def _ask_questions(self, project: Project, responsibility: str, body: str) -> None:
        recipients, note = self._recipients(project, responsibility)
        header = f"{self._label(responsibility)} — questions\n\n" if not note else note + "\n\n"
        for user in recipients:
            self._open(project, user, "question")
            self._send(project, user, header + esc(body) + "\n\n<i>Reply here. The room closes again once you have answered.</i>")

    def _ask_approval(self, project: Project, responsibility: str, body: str) -> None:
        title, _, detail = body.partition(" | ")
        recipients, note = self._recipients(project, responsibility)
        spec = self.responsibilities.get(responsibility, {})
        required = int(spec.get("required_count", 2)) if spec.get("requires_multiple") else 1
        required = min(required, len(recipients)) or 1
        approval_id = uuid.uuid4().hex[:12]
        pending = PendingApproval(project.name, responsibility, title.strip(), detail.strip(), required, recipients)
        self.pending[approval_id] = pending
        message = (
            (note + "\n\n" if note else "") +
            f"{self._label(responsibility)}\n\n<b>{esc(pending.title)}</b>\n{esc(pending.detail)}\n\n"
            f"<i>Approvals needed: {required}</i>"
        )
        buttons = [[
            {"text": "Approve", "callback_data": f"{APPROVE_PREFIX}{approval_id}"},
            {"text": "Reject", "callback_data": f"{REJECT_PREFIX}{approval_id}"},
        ]]
        for user in recipients:
            self._open(project, user, "approval")
            try:
                chat, message_id = self._send(project, user, message, buttons)
            except Exception:
                log.exception("delivering approval %s to %s", approval_id, user)
                continue
            pending.messages.append((user, chat, message_id))

    def _deliver(self, project: Project, visibility: str) -> None:
        for admin in project.roster.admins:
            self._send(project, admin, (
                f"The agent asked to deliver <b>{esc(project.name)}</b> ({esc(visibility)}), but GitHub "
                "delivery is not configured on this bridge yet. The repository is complete in its rig."
            ))
        self._inbound(project, DELIVERY_UNAVAILABLE, "bridge", "bridge")

    def _inbound(self, project: Project, text: str, actor_id: str, actor_name: str) -> bool:
        try:
            self.gc.send_inbound(text, actor_id, actor_name, conversation=project.conversation())
            return True
        except Exception:
            log.exception("sending inbound for project %s", project.name)
            return False

    # --- humans -> agent ---

    def on_message(self, user: str, chat_id: int, thread_id: int | None, text: str) -> bool:
        """Forward a human message if an agent is waiting for it. Returns whether handled."""
        with self.lock:
            located = self.project_for_topic(chat_id, thread_id)
            if located:
                project, owner = located
                if owner != user:
                    self._send(project, user, "This room belongs to someone else on the project.")
                    return True
                return self._answer(project, user, text)
            if thread_id is not None or chat_id != self._dm(user):
                return False
            return self._answer_by_dm(user, text)

    def _answer_by_dm(self, user: str, text: str) -> bool:
        waiting = [p for p in self.projects.values() if user in p.awaiting and user not in p.topics]
        if not waiting:
            return False
        match = PROJECT_PREFIX.match(text)
        if match and match.group(1) in self.projects:
            named = self.projects[match.group(1)]
            if user in named.awaiting:
                return self._answer(named, user, match.group(2).strip())
        if len(waiting) == 1:
            return self._answer(waiting[0], user, text)
        names = ", ".join(sorted(p.name for p in waiting))
        self.tg.send(user, self._dm(user), (
            f"Several projects are waiting on you: <b>{esc(names)}</b>. Start your reply with the "
            "project name and a colon, e.g. <code>bakery: ...</code>"
        ))
        return True

    def _answer(self, project: Project, user: str, text: str) -> bool:
        if user not in project.awaiting:
            self._send(project, user, (
                "The agent is not waiting on you right now, so this was not forwarded. "
                "This room opens when it has a question for you."
            ))
            return True
        display = self.users[user].get("telegram_username", user)
        if not self._inbound(project, text, user, display):
            self._send(project, user, "Could not reach the agent; your answer was not delivered. Try again shortly.")
            return True
        self._close(project, user)
        self._send(project, user, "Passed to the agent. The room is closed until it needs you again.")
        self.store.save(project)
        return True

    def on_button(self, user: str, callback_id: str, approval_id: str, approved: bool) -> bool:
        """Record a vote on a project approval. Returns whether the id was ours."""
        with self.lock:
            pending = self.pending.get(approval_id)
            if pending is None:
                return False
            project = self.projects.get(pending.project)
            if project is None:
                return False
            if pending.resolved:
                self.tg.answer_callback(user, callback_id, "Already decided.")
                return True
            if user not in pending.asked:
                self.tg.answer_callback(user, callback_id, "Not your responsibility.")
                return True
            if not approved:
                pending.resolved = True
                outcome = f"REJECTED by {user}"
                note = f"Rejected by {user}. Say why in this room."
            else:
                pending.approvals.add(user)
                remaining = pending.required - len(pending.approvals)
                if remaining > 0:
                    self.tg.answer_callback(user, callback_id, f"Recorded. {remaining} more needed.")
                    return True
                pending.resolved = True
                voters = ", ".join(sorted(pending.approvals))
                outcome = f"APPROVED by {voters}"
                note = f"Approved by {voters}."

            self.tg.answer_callback(user, callback_id, note)
            for voter, chat, message_id in pending.messages:
                try:
                    self.tg.edit(voter, chat, message_id, f"<b>{esc(pending.title)}</b>\n{esc(pending.detail)}\n\n{esc(note)}")
                except Exception:
                    log.exception("updating approval message for %s", voter)
            self._inbound(project, f"{outcome} — for: {pending.title}", user,
                          self.users[user].get("telegram_username", user))
            for asked in pending.asked:
                if not approved and asked == user:
                    # The rejecting reviewer owes a reason; keep their room open for it.
                    project.awaiting[asked] = {"kind": "question", "asked_at": now_iso()}
                    continue
                self._close(project, asked)
            self.store.save(project)
            return True

    # --- event stream ---

    def on_event(self, event: dict[str, Any]) -> None:
        """Turn workflow lifecycle events into phase notes for the roster."""
        kind = event.get("type", "")
        with self.lock:
            if kind == "execution.step_started":
                project = self._project_for_run(event.get("run_id", ""))
                if project is None:
                    return
                step = event.get("step_id", "")
                project.phase = step
                self._broadcast(project, f"\N{BLACK RIGHT-POINTING TRIANGLE} Working on <b>{esc(self._step_name(step))}</b>")
                self.store.save(project)
            elif kind in ("bead.updated", "bead.closed"):
                payload = event.get("payload") or {}
                # The SSE stream wraps the snapshot as {"bead": {...}}; the
                # on-disk log carries it bare. Accept both.
                if isinstance(payload.get("bead"), dict):
                    payload = payload["bead"]
                if payload.get("status") != "closed":
                    return
                metadata = payload.get("metadata") or {}
                project = self._project_for_run(metadata.get("gc.root_bead_id", ""))
                if project is None:
                    return
                bead_id = payload.get("id", "")
                if bead_id in project.reported_closed:
                    return
                project.reported_closed.append(bead_id)
                title = payload.get("title") or self._step_name(metadata.get("gc.step_ref", ""))
                self._broadcast(project, f"\N{CHECK MARK} Done: <b>{esc(title)}</b>")
                self.store.save(project)

    def _project_for_run(self, run_id: str) -> Project | None:
        if not run_id:
            return None
        for project in self.projects.values():
            if project.workflow_id == run_id:
                return project
        return None

    @staticmethod
    def _step_name(step_id: str) -> str:
        return step_id.rsplit(".", 1)[-1].replace("-", " ") if step_id else "the next step"
