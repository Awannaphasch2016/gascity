"""Bridge <-> factory router wiring.

The bridge keeps its landing-page flow for DMs nobody is waiting on, and hands
everything that belongs to a project — a publish on a project conversation, a
message in a project topic, a press on a project approval — to the router.

    Run: python3 -m pytest bot/test_bridge_factory.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONFIG = {
    "users": {
        "you": {"telegram_id": 1, "telegram_username": "@you", "bot_token_env": "TOK_ONE",
                "desk_chat_id": -1001, "responsibilities": ["architecture_review", "deployment_approval"]},
        "nordice": {"telegram_id": 2, "telegram_username": "@nordice", "bot_token_env": "TOK_ONE",
                    "responsibilities": ["security_review", "deployment_approval"]},
    },
    "responsibility_definitions": {
        "architecture_review": {"name": "Architecture Review", "icon": "A", "requires_multiple": False},
        "security_review": {"name": "Security Review", "icon": "S", "requires_multiple": False},
        "deployment_approval": {"name": "Deployment Approval", "icon": "D", "requires_multiple": True, "required_count": 2},
    },
}
ROSTER = {"members": {"you": ["requirements", "code_review"], "nordice": ["security_review"]}, "admins": ["you"]}


class Recorder:
    """Stands in for both Telegram and Gas City so nothing leaves the process."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.inbound: list[dict[str, Any]] = []
        self.callbacks: list[str] = []
        self.topics: list[tuple[int, str]] = []
        self.closed: list[tuple[int, int]] = []
        self.reopened: list[tuple[int, int]] = []
        self.next_thread = 100

    def wire(self, bridge: Any, monkeypatch) -> None:
        tg = bridge.tg
        monkeypatch.setattr(tg, "send", lambda user, chat, text, buttons=None, thread_id=None:
                            self.sent.append({"user": user, "chat": chat, "thread": thread_id, "text": text, "buttons": buttons}) or len(self.sent))
        monkeypatch.setattr(tg, "edit", lambda user, chat, mid, text: None)
        monkeypatch.setattr(tg, "answer_callback", lambda user, cid, text: self.callbacks.append(text))
        monkeypatch.setattr(tg, "create_forum_topic", self._create_topic)
        monkeypatch.setattr(tg, "close_forum_topic", lambda user, chat, thread: self.closed.append((chat, thread)))
        monkeypatch.setattr(tg, "reopen_forum_topic", lambda user, chat, thread: self.reopened.append((chat, thread)))
        monkeypatch.setattr(tg, "delete_forum_topic", lambda user, chat, thread: None)
        monkeypatch.setattr(bridge.gc, "send_inbound", lambda text, actor_id, actor_name, conversation=None:
                            self.inbound.append({"text": text, "conversation": conversation}) or {"TargetAgentName": "demo/factory.discoverer"})

    def _create_topic(self, user, chat_id, name):
        self.next_thread += 1
        self.topics.append((chat_id, name))
        return self.next_thread


@pytest.fixture
def factory_bridge(tmp_path, monkeypatch):
    config_path = tmp_path / "responsibilities.json"
    config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
    for key, value in {
        "GC_API": "http://127.0.0.1:8372", "GC_CITY_NAME": "test", "BRIDGE_CALLBACK_URL": "http://127.0.0.1:8099",
        "CONFIG_PATH": str(config_path), "TOK_ONE": "t1", "PAGE_URL": "",
        "PROJECT_STATE_DIR": str(tmp_path / "projects"),
    }.items():
        monkeypatch.setenv(key, value)
    import bridge as mod

    bridge = mod.Bridge(mod.Config())
    rec = Recorder()
    rec.wire(bridge, monkeypatch)
    return bridge, rec


def register(bridge):
    return bridge.factory.register(name="bakery", rig="bakery", workflow_id="ba-1", roster=ROSTER)


def conv(rig: str) -> dict[str, str]:
    return {"provider": "telegram", "account_id": "factory", "conversation_id": rig, "scope_id": "city", "kind": "room"}


def topic_message(chat_id: int, thread_id: int | None, text: str, sender: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"from": {"id": sender}, "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private"}, "text": text}
    if thread_id is not None:
        message.update({"is_topic_message": True, "message_thread_id": thread_id})
    return {"update_id": 1, "message": message}


# --- Configuration -----------------------------------------------------------

def test_the_router_is_on_only_when_a_state_dir_is_configured(factory_bridge, monkeypatch, tmp_path):
    bridge, _ = factory_bridge
    assert bridge.factory is not None
    monkeypatch.delenv("PROJECT_STATE_DIR")
    import bridge as mod
    assert mod.Bridge(mod.Config()).factory is None


# --- Gas City -> Telegram ----------------------------------------------------

def test_a_publish_on_a_project_conversation_goes_through_the_router(factory_bridge):
    bridge, rec = factory_bridge
    register(bridge)
    rec.sent.clear()

    bridge.on_publish("QUESTIONS: requirements\n1. Ship worldwide? — Recommended: no", conversation=conv("bakery"), session_id="gc-9")

    assert [m["user"] for m in rec.sent] == ["you"]
    assert rec.sent[0]["chat"] == -1001 and rec.sent[0]["thread"] == 101
    assert (-1001, 101) in rec.reopened


def test_a_publish_on_the_landing_page_conversation_keeps_the_legacy_broadcast(factory_bridge):
    bridge, rec = factory_bridge
    register(bridge)
    rec.sent.clear()

    bridge.on_publish("EDIT_DONE: moved the hero", conversation=conv("landing-page"), session_id="gc-3")

    assert {m["user"] for m in rec.sent} == {"you", "nordice"}
    assert all(m["thread"] is None for m in rec.sent)


def test_a_publish_without_a_conversation_is_legacy_too(factory_bridge):
    bridge, rec = factory_bridge
    bridge.on_publish("hello")
    assert {m["user"] for m in rec.sent} == {"you", "nordice"}


# --- Telegram -> Gas City ----------------------------------------------------

def test_an_answer_in_a_project_topic_reaches_that_project(factory_bridge):
    bridge, rec = factory_bridge
    register(bridge)
    bridge.on_publish("QUESTIONS: requirements\n1. Ship worldwide?", conversation=conv("bakery"), session_id="gc-9")

    bridge._dispatch("you", topic_message(-1001, 101, "1. Australia only"))

    assert rec.inbound and rec.inbound[-1]["conversation"]["conversation_id"] == "bakery"
    assert rec.inbound[-1]["text"] == "1. Australia only"


def test_chatter_in_the_desks_general_topic_is_ignored(factory_bridge):
    bridge, rec = factory_bridge
    register(bridge)
    rec.sent.clear()

    bridge._dispatch("you", topic_message(-1001, None, "anyone here?"))

    assert rec.inbound == [] and rec.sent == []


def test_a_stranger_in_the_desk_is_ignored_rather_than_dmed(factory_bridge):
    bridge, rec = factory_bridge
    register(bridge)
    rec.sent.clear()

    bridge._dispatch("you", topic_message(-1001, 101, "let me in", sender=999))

    assert rec.inbound == [] and rec.sent == []


def test_a_dm_nobody_is_waiting_on_still_reaches_the_landing_page_agent(factory_bridge):
    bridge, rec = factory_bridge
    register(bridge)

    bridge._dispatch("you", topic_message(1, None, "make the header blue"))

    assert rec.inbound[-1]["text"] == "make the header blue"
    assert rec.inbound[-1]["conversation"] is None, "the legacy flow uses the bridge's default conversation"


def test_a_dm_from_a_deskless_member_answers_the_project_asking_them(factory_bridge):
    bridge, rec = factory_bridge
    register(bridge)
    bridge.on_publish("QUESTIONS: security_review\n1. Store cards?", conversation=conv("bakery"), session_id="gc-9")

    bridge._dispatch("nordice", topic_message(2, None, "No cards.", sender=2))

    assert rec.inbound[-1]["conversation"]["conversation_id"] == "bakery"


def test_a_press_on_a_project_approval_falls_through_to_the_router(factory_bridge):
    bridge, rec = factory_bridge
    project = register(bridge)
    bridge.on_publish("APPROVAL_NEEDED: code_review | first cut | tests pass", conversation=conv("bakery"), session_id="gc-12")
    approval_id = next(iter(bridge.factory.pending))

    bridge._dispatch("you", {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": 1}, "data": f"a:{approval_id}"}})

    assert rec.inbound[-1]["text"].startswith("APPROVED by you")
    assert "you" not in project.awaiting


def test_a_press_on_a_legacy_approval_is_still_the_bridges(factory_bridge):
    bridge, rec = factory_bridge
    bridge.on_publish("APPROVAL_NEEDED: security_review | add a form | an email field")
    approval_id = next(iter(bridge.pending))

    bridge._dispatch("nordice", {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": 2}, "data": f"a:{approval_id}"}})

    assert rec.inbound[-1]["text"].startswith("APPROVED by nordice")
    assert rec.inbound[-1]["conversation"] is None


# --- One bot, several people -------------------------------------------------

def test_a_shared_bot_resolves_the_sender_to_the_user_whose_account_it_is(factory_bridge):
    bridge, _ = factory_bridge
    assert bridge.actor_for(["you", "nordice"], topic_message(2, None, "x", sender=2)) == "nordice"
    assert bridge.actor_for(["you", "nordice"], {"callback_query": {"from": {"id": 1}}}) == "you"
    # Unknown senders land on the first user, whose ownership check then refuses them.
    assert bridge.actor_for(["you", "nordice"], topic_message(5, None, "x", sender=5)) == "you"


def test_polling_groups_users_by_token(factory_bridge):
    bridge, _ = factory_bridge
    assert bridge.polling_groups() == [["you", "nordice"]]


# --- HTTP surface --------------------------------------------------------------

def test_projects_endpoints_register_list_and_restart(factory_bridge):
    bridge, rec = factory_bridge
    import bridge as mod
    client = mod.create_app(bridge).test_client()

    created = client.post("/projects", json={"name": "bakery", "rig": "bakery", "workflow_id": "ba-1", "roster": ROSTER})
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["topics"]["you"]["thread_id"] == 101

    assert client.post("/projects", json={"name": "bakery", "rig": "bakery", "workflow_id": "ba-1", "roster": ROSTER}).status_code == 409
    assert client.post("/projects", json={"name": "x", "rig": "x", "workflow_id": "x-1", "roster": {"members": {"ghost": []}}}).status_code == 400

    listed = client.get("/projects").get_json()
    assert [p["name"] for p in listed["projects"]] == ["bakery"]

    restarted = client.post("/projects/bakery/restart", json={"workflow_id": "ba-7"})
    assert restarted.status_code == 200 and restarted.get_json()["workflow_id"] == "ba-7"
    assert client.post("/projects/nope/restart", json={"workflow_id": "x"}).status_code == 404


def test_publish_endpoint_passes_conversation_and_session_to_the_bridge(factory_bridge):
    bridge, rec = factory_bridge
    register(bridge)
    rec.sent.clear()
    import bridge as mod
    client = mod.create_app(bridge).test_client()

    resp = client.post("/publish", json={"session_id": "gc-9", "conversation": conv("bakery"), "text": "QUESTIONS: requirements\n1. Q?"})

    assert resp.status_code == 200 and resp.get_json()["delivered"] is True
    assert rec.sent and rec.sent[0]["thread"] == 101


def test_projects_endpoints_are_absent_without_the_router(monkeypatch, factory_bridge):
    monkeypatch.delenv("PROJECT_STATE_DIR")
    import bridge as mod
    client = mod.create_app(mod.Bridge(mod.Config())).test_client()
    assert client.get("/projects").status_code == 503


# --- Event stream ----------------------------------------------------------------

def test_sse_frames_are_parsed_into_events_with_their_ids():
    import bridge as mod
    lines = [
        ": keepalive", "",
        "id: 561", "event: event", 'data: {"seq": 561, "type": "bead.updated", "payload": {"bead": {"id": "ba-2"}}}', "",
        "id: 562", "event: event", 'data: {"seq": 562,', 'data: "type": "execution.step_started"}', "",
    ]
    frames = list(mod.iter_sse(lines))
    assert frames[0] == ("561", {"seq": 561, "type": "bead.updated", "payload": {"bead": {"id": "ba-2"}}})
    assert frames[1][0] == "562" and frames[1][1]["type"] == "execution.step_started"


def test_followed_events_reach_the_router(factory_bridge, monkeypatch):
    bridge, rec = factory_bridge
    project = register(bridge)
    rec.sent.clear()
    monkeypatch.setattr(bridge.gc, "stream_events", lambda last_id: iter([
        ("7", {"type": "execution.step_started", "run_id": "ba-1", "step_id": "website-factory.discover"}),
    ]))

    last = bridge.follow_events_once(None)

    assert last == "7"
    assert project.phase == "website-factory.discover"
    assert rec.sent and "discover" in rec.sent[0]["text"]
