#!/usr/bin/env python3
"""Check the routing ledger the bridge exposes at /state.

Routing is otherwise only observable as a message on somebody's phone, which
makes "the request reached the right reviewer" impossible to assert from
outside. This ledger is what the live routing drill reads to decide whether a
hop happened, so its shape matters as much as the routing itself.

Run: python3 -m pytest bot/test_bridge_state.py
"""

from __future__ import annotations

import json


def approval(bridge, index: int = 0) -> dict:
    """Return one approval's entry from the ledger."""
    return bridge.state()["approvals"][index]


def test_state_reports_which_reviewer_an_approval_went_to(shared_account_bridge, capture):
    """A single-approver responsibility names that one reviewer and nobody else."""
    bridge = shared_account_bridge
    capture(bridge)

    bridge._ask_approval("security_review | add a signup form | an email field in the footer")

    entry = approval(bridge)
    assert entry["responsibility"] == "security_review"
    assert entry["asked"] == ["nordice"]
    assert entry["required"] == 1
    assert entry["approved_by"] == []
    assert entry["resolved"] is False


def test_state_reports_a_quorum_and_the_votes_cast_so_far(shared_account_bridge, capture, press):
    """A two-approver responsibility stays unresolved with one vote recorded."""
    bridge = shared_account_bridge
    capture(bridge)

    bridge._ask_approval("deployment_approval | publish the page | push it live")
    approval_id = next(iter(bridge.pending))
    press(bridge, "you", 7037289190, approval_id)

    entry = approval(bridge)
    assert sorted(entry["asked"]) == ["nordice", "you"]
    assert entry["required"] == 2
    assert entry["approved_by"] == ["you"]
    assert entry["resolved"] is False


def test_state_reports_a_rejection_and_who_cast_it(shared_account_bridge, capture, press):
    """A reject resolves the entry and attributes itself."""
    bridge = shared_account_bridge
    capture(bridge)

    bridge._ask_approval("architecture_review | reword the headline | swap the hero copy")
    approval_id = next(iter(bridge.pending))
    press(bridge, "you", 7037289190, approval_id, approve=False)

    entry = approval(bridge)
    assert entry["rejected_by"] == "you"
    assert entry["resolved"] is True


def test_state_records_every_turn_the_agent_sent(shared_account_bridge, capture):
    """Both an approval request and a plain reply land in the turn log.

    The drill reads this to tell an approval that triggered an edit from one
    that was recorded and then quietly dropped.
    """
    bridge = shared_account_bridge
    capture(bridge)

    bridge.on_publish("APPROVAL_NEEDED: security_review | add a form | an email field")
    bridge.on_publish("EDIT_DONE: added the signup form under pricing")

    texts = [turn["text"] for turn in bridge.state()["turns"]]
    assert texts == [
        "APPROVAL_NEEDED: security_review | add a form | an email field",
        "EDIT_DONE: added the signup form under pricing",
    ]
    assert all(turn["at"] for turn in bridge.state()["turns"])


def test_state_is_json_serializable(shared_account_bridge, capture, press):
    """The ledger is served over HTTP, so it may hold no sets or objects."""
    bridge = shared_account_bridge
    capture(bridge)

    bridge._ask_approval("deployment_approval | publish the page | push it live")
    press(bridge, "you", 7037289190, next(iter(bridge.pending)))

    json.dumps(bridge.state())


def test_resolved_approvals_are_retired_once_history_fills(shared_account_bridge, capture, press, monkeypatch):
    """History is bounded, so a long-running bridge does not grow forever."""
    import bridge as mod

    bridge = shared_account_bridge
    capture(bridge)
    monkeypatch.setattr(mod, "APPROVAL_HISTORY", 3)

    for _ in range(5):
        bridge._ask_approval("architecture_review | a change | some detail")
        press(bridge, "you", 7037289190, list(bridge.pending)[-1], approve=False)

    assert len(bridge.pending) <= 3


def test_an_unanswered_approval_is_never_retired(shared_account_bridge, capture, press, monkeypatch):
    """Forgetting an open request would turn its press into "no longer open".

    Someone is waiting on that message. Evicting it because newer requests
    arrived discards their decision at the moment they make it.
    """
    import bridge as mod

    bridge = shared_account_bridge
    capture(bridge)
    monkeypatch.setattr(mod, "APPROVAL_HISTORY", 3)

    bridge._ask_approval("security_review | the open one | nobody answers this")
    open_id = next(iter(bridge.pending))

    for _ in range(6):
        bridge._ask_approval("architecture_review | a change | some detail")
        press(bridge, "you", 7037289190, list(bridge.pending)[-1], approve=False)

    assert open_id in bridge.pending
