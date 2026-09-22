#!/usr/bin/env python3
"""Check which reviewer an incoming Telegram update is attributed to.

Two reviewers can share one Telegram account — the deployed config has both
pointing at the same phone, each reached through their own bot — so the sender's
Telegram id does not identify who acted. The bot that received the update does.

Getting this wrong is quiet and total: every press resolves to whichever user
appears first in responsibilities.json, so the other reviewer's exclusive
responsibilities can never be approved by anyone.

Run: python3 -m pytest bot/test_bridge_routing.py
"""

from __future__ import annotations

import pytest


@pytest.fixture
def capture(monkeypatch):
    """Silence Telegram and Gas City, recording what each was asked to do."""

    def wire(bridge):
        record = {"sent": [], "callbacks": [], "edits": [], "inbound": []}
        monkeypatch.setattr(bridge.tg, "send",
                            lambda user, chat, text, buttons=None:
                            record["sent"].append((user, text)) or 1)
        monkeypatch.setattr(bridge.tg, "answer_callback",
                            lambda user, cid, text:
                            record["callbacks"].append((user, text)))
        monkeypatch.setattr(bridge.tg, "edit",
                            lambda user, chat, mid, text: record["edits"].append(text))
        monkeypatch.setattr(bridge.gc, "send_inbound",
                            lambda text, actor_id, actor_name:
                            record["inbound"].append(text) or {"TargetAgentName": "builder"})
        return record

    return wire


def press(bridge, bot: str, telegram_id: int, approval_id: str, approve: bool = True) -> None:
    """Simulate a button press arriving on one reviewer's bot."""
    prefix = "a:" if approve else "r:"
    bridge._dispatch(bot, {
        "update_id": 1,
        "callback_query": {
            "id": "cb1",
            "from": {"id": telegram_id},
            "data": f"{prefix}{approval_id}",
        },
    })


def test_press_on_nordices_bot_is_attributed_to_nordice(shared_account_bridge, capture):
    """A security_review press on nordice's bot counts as nordice approving.

    Both reviewers share a Telegram account here, so resolving the actor from
    the sender's id would name "you" — who does not hold security_review — and
    the approval would be refused as not theirs.
    """
    bridge = shared_account_bridge
    record = capture(bridge)

    bridge._ask_approval("security_review | add a signup form | an email field in the footer")
    approval_id = next(iter(bridge.pending))
    press(bridge, "nordice", 7037289190, approval_id)

    assert record["inbound"], "the agent was never told the outcome"
    assert "APPROVED by nordice" in record["inbound"][0]


def test_press_on_the_wrong_bot_is_refused(shared_account_bridge, capture):
    """A reviewer cannot approve through a bot that is not theirs."""
    bridge = shared_account_bridge
    record = capture(bridge)

    bridge._ask_approval("security_review | add a signup form | an email field")
    approval_id = next(iter(bridge.pending))
    # security_review is nordice's; pressing on your bot must not satisfy it.
    press(bridge, "you", 7037289190, approval_id)

    assert not record["inbound"], "an unrelated reviewer's press reached the agent"
    assert any("responsibility" in text.lower() for _, text in record["callbacks"])


def test_a_stranger_cannot_act_through_a_reviewers_bot(shared_account_bridge, capture):
    """An update from an account the bot's reviewer does not own is refused."""
    bridge = shared_account_bridge
    record = capture(bridge)

    bridge._ask_approval("security_review | add a signup form | an email field")
    approval_id = next(iter(bridge.pending))
    press(bridge, "nordice", 999999, approval_id)

    assert not record["inbound"], "a stranger's press reached the agent"


def test_two_approver_responsibility_needs_both_bots(shared_account_bridge, capture):
    """deployment_approval resolves only after both reviewers press."""
    bridge = shared_account_bridge
    record = capture(bridge)

    bridge._ask_approval("deployment_approval | publish the page | push it live")
    approval_id = next(iter(bridge.pending))

    press(bridge, "you", 7037289190, approval_id)
    assert not record["inbound"], "one approval satisfied a two-approver responsibility"
    assert any("more needed" in text for _, text in record["callbacks"])

    press(bridge, "nordice", 7037289190, approval_id)
    assert record["inbound"], "the second approval never reached the agent"
    assert "APPROVED by nordice, you" in record["inbound"][0]


def test_one_rejection_resolves_immediately(shared_account_bridge, capture):
    """A single reject ends a two-approver request without the other vote."""
    bridge = shared_account_bridge
    record = capture(bridge)

    bridge._ask_approval("deployment_approval | publish the page | push it live")
    approval_id = next(iter(bridge.pending))
    press(bridge, "you", 7037289190, approval_id, approve=False)

    assert record["inbound"], "the rejection never reached the agent"
    assert "REJECTED by you" in record["inbound"][0]


def test_a_message_is_attributed_to_the_bot_it_arrived_on(shared_account_bridge, capture):
    """A request typed into nordice's bot is forwarded as nordice."""
    bridge = shared_account_bridge
    record = capture(bridge)

    bridge._dispatch("nordice", {
        "update_id": 2,
        "message": {"from": {"id": 7037289190}, "text": "add a contact form"},
    })

    assert record["inbound"] == ["add a contact form"]
    assert record["sent"], "the sender was never acknowledged"
    assert record["sent"][0][0] == "nordice"


def test_distinct_accounts_still_route_correctly(bridge, capture):
    """The common case, where each reviewer has their own Telegram account."""
    record = capture(bridge)

    bridge._ask_approval("security_review | add a signup form | an email field")
    approval_id = next(iter(bridge.pending))
    press(bridge, "nordice", 2, approval_id)

    assert "APPROVED by nordice" in record["inbound"][0]
