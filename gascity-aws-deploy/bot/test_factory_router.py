"""Tests for the factory router: projects, desks, topics, and gated replies.

A project is one rig with its own Gas City conversation. Every roster member
gets a topic named after the project in their desk (a Telegram supergroup with
Topics). Topics stay closed while agents work and open only when an agent asks
that person something; a message sent while nothing is being asked is refused
rather than forwarded. People without a desk fall back to their bot's DM with
the same gate applied on the bridge side.

Run: python3 -m pytest bot/test_factory_router.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import factory_router as fr  # noqa: E402

USERS = {
    "you": {"telegram_id": 1, "telegram_username": "@you", "desk_chat_id": -1001},
    "nordice": {"telegram_id": 2, "telegram_username": "@nordice"},  # no desk: DM fallback
}
RESPONSIBILITIES = {
    "code_review": {"name": "Code Review", "icon": "R", "requires_multiple": False},
    "deployment_approval": {"name": "Deployment Approval", "icon": "D", "requires_multiple": True, "required_count": 2},
}
ROSTER = {
    "members": {"you": ["requirements", "code_review", "deployment_approval"], "nordice": ["security_review", "deployment_approval"]},
    "admins": ["you"],
}


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.topics: list[tuple[str, int, str]] = []
        self.closed: list[tuple[int, int]] = []
        self.reopened: list[tuple[int, int]] = []
        self.deleted: list[tuple[int, int]] = []
        self.edits: list[str] = []
        self.callbacks: list[str] = []
        self.next_thread = 100
        self.next_message = 1

    def send(self, user, chat_id, text, buttons=None, thread_id=None):
        self.sent.append({"user": user, "chat": chat_id, "thread": thread_id, "text": text, "buttons": buttons})
        self.next_message += 1
        return self.next_message

    def create_forum_topic(self, user, chat_id, name):
        self.next_thread += 1
        self.topics.append((user, chat_id, name))
        return self.next_thread

    def close_forum_topic(self, user, chat_id, thread_id):
        self.closed.append((chat_id, thread_id))

    def reopen_forum_topic(self, user, chat_id, thread_id):
        self.reopened.append((chat_id, thread_id))

    def delete_forum_topic(self, user, chat_id, thread_id):
        self.deleted.append((chat_id, thread_id))

    def edit(self, user, chat_id, message_id, text):
        self.edits.append(text)

    def answer_callback(self, user, callback_id, text):
        self.callbacks.append(text)


class FakeGasCity:
    def __init__(self) -> None:
        self.inbound: list[dict[str, Any]] = []

    def send_inbound(self, text, actor_id, actor_name, conversation=None):
        self.inbound.append({"text": text, "actor": actor_name, "conversation": conversation})
        return {"TargetAgentName": "demo/factory.discoverer"}


@pytest.fixture
def router(tmp_path):
    tg, gc = FakeTelegram(), FakeGasCity()
    store = fr.ProjectStore(str(tmp_path / "projects"))
    r = fr.FactoryRouter(
        users=USERS, responsibilities=RESPONSIBILITIES, account_id="factory",
        tg=tg, gc=gc, store=store,
    )
    return r, tg, gc, store


def register(r):
    return r.register(name="bakery", rig="bakery", workflow_id="ba-1", roster=ROSTER)


# --- Registration ---------------------------------------------------------

def test_register_creates_a_closed_topic_in_each_desk_and_a_dm_for_the_rest(router):
    r, tg, gc, _ = router
    project = register(r)

    assert tg.topics == [("you", -1001, "bakery")]
    assert project.topics["you"] == {"chat_id": -1001, "thread_id": 101}
    assert (-1001, 101) in tg.closed, "a fresh topic starts closed: nobody is being asked yet"
    # nordice has no desk: the intro goes to the DM and the project is tracked without a topic.
    assert "nordice" not in project.topics
    dm = [m for m in tg.sent if m["user"] == "nordice"]
    assert dm and dm[0]["chat"] == 2 and "bakery" in dm[0]["text"]


def test_register_refuses_a_roster_member_missing_from_responsibilities(router):
    r, *_ = router
    with pytest.raises(fr.RosterError, match="stranger"):
        r.register(name="x", rig="x", workflow_id="x-1", roster={"members": {"stranger": ["requirements"]}, "admins": []})


def test_register_persists_and_reloads(router, tmp_path):
    r, tg, gc, store = router
    register(r)

    reloaded = fr.FactoryRouter(users=USERS, responsibilities=RESPONSIBILITIES, account_id="factory",
                                tg=tg, gc=gc, store=store)
    project = reloaded.project_for_conversation("bakery")
    assert project is not None
    assert project.workflow_id == "ba-1"
    assert project.topics["you"]["thread_id"] == 101
    assert project.roster.holders("code_review") == ["you"]


def test_conversation_ref_is_the_rig_as_a_room(router):
    r, *_ = router
    project = register(r)
    assert project.conversation() == {
        "provider": "telegram", "account_id": "factory", "conversation_id": "bakery",
        "scope_id": "city", "kind": "room",
    }


# --- Agent -> humans -------------------------------------------------------

QUESTION = "QUESTIONS: requirements\n1. Ship worldwide? — Recommended: no, Australia only\n2. Take card payments? — Recommended: no"


def test_questions_open_only_the_holders_topic_and_mark_them_awaited(router):
    r, tg, gc, _ = router
    project = register(r)
    tg.sent.clear()

    r.on_publish(project, QUESTION, session_id="gc-9")

    assert (-1001, 101) in tg.reopened
    asked = [m for m in tg.sent if m["user"] == "you"]
    assert len(asked) == 1 and asked[0]["thread"] == 101
    assert "Ship worldwide?" in asked[0]["text"] and "Recommended" in asked[0]["text"]
    assert not [m for m in tg.sent if m["user"] == "nordice"], "nordice does not hold requirements"
    assert project.awaiting["you"]["kind"] == "question"


def test_questions_for_a_responsibility_nobody_holds_go_to_the_admins(router):
    r, tg, gc, _ = router
    project = register(r)
    tg.sent.clear()

    r.on_publish(project, "QUESTIONS: legal\n1. Which licence? — Recommended: MIT", session_id="gc-9")

    admin_msgs = [m for m in tg.sent if m["user"] == "you"]
    assert admin_msgs and "legal" in admin_msgs[0]["text"] and "nobody" in admin_msgs[0]["text"].lower()
    assert "you" in project.awaiting, "the admin is asked to answer in the holder's place"


def test_a_plain_note_reaches_everyone_without_opening_topics(router):
    r, tg, gc, _ = router
    project = register(r)
    tg.sent.clear()
    tg.reopened.clear()

    r.on_publish(project, "Wrote docs/requirements.md and docs/wireframe.html; committed as 3f2a1c.", session_id="gc-9")

    assert {m["user"] for m in tg.sent} == {"you", "nordice"}
    assert tg.reopened == []
    assert project.awaiting == {}


# --- Humans -> agent -------------------------------------------------------

def test_an_answer_in_an_open_topic_reaches_the_agent_and_closes_the_topic(router):
    r, tg, gc, _ = router
    project = register(r)
    r.on_publish(project, QUESTION, session_id="gc-9")
    tg.closed.clear()

    handled = r.on_message(user="you", chat_id=-1001, thread_id=101, text="1. Australia only\n2. No payments")

    assert handled
    assert gc.inbound and gc.inbound[0]["conversation"]["conversation_id"] == "bakery"
    assert "Australia only" in gc.inbound[0]["text"]
    assert (-1001, 101) in tg.closed
    assert "you" not in project.awaiting


def test_a_message_while_nothing_is_asked_is_refused(router):
    r, tg, gc, _ = router
    register(r)
    tg.sent.clear()

    handled = r.on_message(user="you", chat_id=-1001, thread_id=101, text="how is it going?")

    assert handled, "the router owns messages in project topics even when it refuses them"
    assert gc.inbound == []
    assert tg.sent and "not waiting" in tg.sent[-1]["text"].lower()


def test_a_message_outside_any_project_topic_is_not_the_routers_business(router):
    r, tg, gc, _ = router
    register(r)
    assert not r.on_message(user="you", chat_id=1, thread_id=None, text="hello")


def test_dm_fallback_routes_a_reply_to_the_one_project_awaiting_that_person(router):
    r, tg, gc, _ = router
    project = register(r)
    r.on_publish(project, "QUESTIONS: security_review\n1. Store card data? — Recommended: no", session_id="gc-9")
    dm = [m for m in tg.sent if m["user"] == "nordice" and "Store card data" in m["text"]]
    assert dm and dm[0]["chat"] == 2 and dm[0]["thread"] is None

    handled = r.on_message(user="nordice", chat_id=2, thread_id=None, text="No card data.")

    assert handled
    assert gc.inbound[-1]["text"] == "No card data." and gc.inbound[-1]["conversation"]["conversation_id"] == "bakery"
    assert "nordice" not in project.awaiting


def test_dm_fallback_with_two_projects_awaiting_needs_the_project_named(router):
    r, tg, gc, _ = router
    a = register(r)
    b = r.register(name="florist", rig="florist", workflow_id="fl-1", roster=ROSTER)
    q = "QUESTIONS: security_review\n1. Anything? — Recommended: no"
    r.on_publish(a, q, session_id="gc-9")
    r.on_publish(b, q, session_id="gc-10")
    tg.sent.clear()

    assert r.on_message(user="nordice", chat_id=2, thread_id=None, text="no")
    assert gc.inbound == []
    assert "florist" in tg.sent[-1]["text"] and "bakery" in tg.sent[-1]["text"]

    assert r.on_message(user="nordice", chat_id=2, thread_id=None, text="florist: no")
    assert gc.inbound[-1]["conversation"]["conversation_id"] == "florist"
    assert gc.inbound[-1]["text"] == "no"


def test_dm_from_a_deskless_person_with_nothing_asked_is_left_to_the_legacy_flow(router):
    r, tg, gc, _ = router
    register(r)
    assert not r.on_message(user="nordice", chat_id=2, thread_id=None, text="hi")


# --- Approvals -------------------------------------------------------------

APPROVAL = "APPROVAL_NEEDED: code_review | bakery: first cut of the site | Built pages, tests pass. Commit: 3f2a1c"


def test_approval_goes_to_holders_with_buttons_and_a_press_reaches_the_agent(router):
    r, tg, gc, _ = router
    project = register(r)
    r.on_publish(project, APPROVAL, session_id="gc-12")

    asked = [m for m in tg.sent if m["buttons"]]
    assert len(asked) == 1 and asked[0]["user"] == "you" and asked[0]["thread"] == 101
    approval_id = asked[0]["buttons"][0][0]["callback_data"].split(":", 1)[1]
    assert project.awaiting["you"]["kind"] == "approval"

    assert r.on_button(user="you", callback_id="cb", approval_id=approval_id, approved=True)
    assert gc.inbound[-1]["text"].startswith("APPROVED by you")
    assert gc.inbound[-1]["conversation"]["conversation_id"] == "bakery"
    assert "you" not in project.awaiting
    assert (-1001, 101) in tg.closed


def test_a_rejection_carries_the_reason_asked_for_in_the_topic(router):
    r, tg, gc, _ = router
    project = register(r)
    r.on_publish(project, APPROVAL, session_id="gc-12")
    approval_id = next(iter(r.pending))

    assert r.on_button(user="you", callback_id="cb", approval_id=approval_id, approved=False)
    assert gc.inbound[-1]["text"].startswith("REJECTED by you")
    # After a rejection the topic stays open so the reviewer can say why.
    assert project.awaiting["you"]["kind"] == "question"
    assert r.on_message(user="you", chat_id=-1001, thread_id=101, text="The header overlaps on mobile.")
    assert "header overlaps" in gc.inbound[-1]["text"]


def test_multi_approver_threshold_waits_for_the_second_vote(router):
    r, tg, gc, _ = router
    project = register(r)
    r.on_publish(project, "APPROVAL_NEEDED: deployment_approval | ship it | to prod", session_id="gc-12")
    approval_id = next(iter(r.pending))

    assert r.on_button(user="you", callback_id="cb", approval_id=approval_id, approved=True)
    assert gc.inbound == []
    assert any("more needed" in c for c in tg.callbacks)
    assert r.on_button(user="nordice", callback_id="cb2", approval_id=approval_id, approved=True)
    assert gc.inbound[-1]["text"].startswith("APPROVED by nordice, you")


def test_an_unknown_approval_id_is_not_the_routers(router):
    r, *_ = router
    register(r)
    assert not r.on_button(user="you", callback_id="cb", approval_id="nope", approved=True)


# --- Delivery placeholder --------------------------------------------------

def test_deliver_is_answered_as_unavailable_until_delivery_exists(router):
    r, tg, gc, _ = router
    project = register(r)
    r.on_publish(project, "DELIVER: private", session_id="gc-30")
    assert gc.inbound[-1]["text"].startswith("DELIVERY_UNAVAILABLE")
    admin = [m for m in tg.sent if m["user"] == "you" and "deliver" in m["text"].lower()]
    assert admin


# --- Phase cues from the event stream ---------------------------------------

def test_step_started_and_step_closed_events_become_phase_notes(router):
    r, tg, gc, _ = router
    project = register(r)
    tg.sent.clear()

    r.on_event({"type": "execution.step_started", "run_id": "ba-1", "step_id": "website-factory.discover", "subject": "ba-2"})
    assert {m["user"] for m in tg.sent} == {"you", "nordice"}
    assert "discover" in tg.sent[0]["text"]
    assert project.phase == "website-factory.discover"

    tg.sent.clear()
    r.on_event({"type": "bead.updated", "subject": "ba-2",
                "payload": {"id": "ba-2", "status": "closed", "title": "Discover the requirements",
                            "metadata": {"gc.root_bead_id": "ba-1", "gc.step_ref": "website-factory.discover"}}})
    assert tg.sent and "Discover the requirements" in tg.sent[0]["text"]


def test_the_sse_stream_wraps_the_bead_and_is_still_understood(router):
    r, tg, gc, _ = router
    register(r)
    tg.sent.clear()
    r.on_event({"type": "bead.updated", "subject": "ba-2",
                "payload": {"bead": {"id": "ba-2", "status": "closed", "title": "Discover",
                                     "metadata": {"gc.root_bead_id": "ba-1"}}}})
    assert tg.sent and "Discover" in tg.sent[0]["text"]


def test_events_from_other_runs_are_ignored(router):
    r, tg, gc, _ = router
    register(r)
    tg.sent.clear()
    r.on_event({"type": "execution.step_started", "run_id": "zz-1", "step_id": "x.y"})
    r.on_event({"type": "bead.updated", "payload": {"id": "zz-2", "status": "closed", "metadata": {"gc.root_bead_id": "zz-1"}}})
    assert tg.sent == []


def test_a_closed_step_is_reported_once_even_if_the_event_repeats(router):
    r, tg, gc, _ = router
    register(r)
    tg.sent.clear()
    event = {"type": "bead.closed", "payload": {"id": "ba-2", "status": "closed", "title": "Discover",
                                                "metadata": {"gc.root_bead_id": "ba-1"}}}
    r.on_event(event)
    r.on_event(event)
    assert len(tg.sent) == 2  # once per member, not twice


# --- Restart ----------------------------------------------------------------

def test_restart_replaces_topics_and_forgets_open_asks(router):
    r, tg, gc, _ = router
    project = register(r)
    r.on_publish(project, QUESTION, session_id="gc-9")
    assert project.awaiting

    restarted = r.restart("bakery", workflow_id="ba-40")

    assert (-1001, 101) in tg.deleted
    assert restarted.topics["you"]["thread_id"] == 102
    assert restarted.awaiting == {}
    assert restarted.workflow_id == "ba-40"
    assert r.project_for_conversation("bakery") is restarted


def test_restart_of_an_unknown_project_is_an_error(router):
    r, *_ = router
    with pytest.raises(KeyError):
        r.restart("nope", workflow_id="x")


# --- Parsing -----------------------------------------------------------------

def test_parse_publish_classifies_each_protocol_line():
    assert fr.parse_publish(QUESTION) == ("questions", "requirements", QUESTION.split("\n", 1)[1])
    assert fr.parse_publish(APPROVAL) == ("approval", "code_review", "bakery: first cut of the site | Built pages, tests pass. Commit: 3f2a1c")
    assert fr.parse_publish("DELIVER: private") == ("deliver", "private", "")
    assert fr.parse_publish("just a note") == ("note", "", "just a note")


def test_project_store_writes_atomically_and_lists(tmp_path):
    store = fr.ProjectStore(str(tmp_path / "p"))
    project = fr.Project(name="a", rig="a", workflow_id="a-1", roster=fr.Roster.from_doc(ROSTER, known_users=set(USERS)))
    store.save(project)
    assert sorted(p.name for p in store.load_all()) == ["a"]
    with open(tmp_path / "p" / "a.json", encoding="utf-8") as fh:
        assert json.load(fh)["workflow_id"] == "a-1"
    assert not list((tmp_path / "p").glob("*.tmp*"))
