#!/usr/bin/env python3
"""Check the drill's own control flow, without Telegram or an agent.

A live run costs a person three button presses and the wait between them, so
the drill's sequencing has to be right before anyone is asked to sit through it.
The behaviour that matters most is adoption: a rerun must answer the request
already waiting on somebody's phone rather than sending another one, because two
identical approval messages with only one that counts is worse than no drill.

Run: python3 -m pytest bot/test_drill_steps.py
"""

from __future__ import annotations

import os

import pytest

import drill_routing as drill


class FakeLedger:
    """Replays a scripted ledger, advancing each view on its own cursor.

    Separate cursors for approvals and turns keep a scenario readable: a test
    says what the second approvals read returns without having to count how many
    times the drill happened to consult the turn log in between. The last state
    of each sequence repeats, so a scenario only lists the transitions it cares
    about.
    """

    def __init__(self, approvals: list[list[dict]], turns: list[list[dict]]) -> None:
        self._approvals = approvals
        self._turns = turns
        self.approval_reads = 0
        self.turn_reads = 0

    @staticmethod
    def _at(states: list, index: int):
        return states[min(index, len(states) - 1)]

    def approvals(self) -> list[dict]:
        state = self._at(self._approvals, self.approval_reads)
        self.approval_reads += 1
        return state

    def turns(self) -> list[dict]:
        state = self._at(self._turns, self.turn_reads)
        self.turn_reads += 1
        return state


class FakeGasCity:
    """Records the turns the drill sends, sending nothing."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_inbound(self, text: str, actor_id: str, actor_name: str) -> dict:
        self.sent.append((actor_name, text))
        return {"TargetAgentName": "builder"}


class FakeConfig:
    """Just enough config for the drill to address a sender."""

    users = {
        "you": {"telegram_id": 1, "telegram_username": "@you"},
        "nordice": {"telegram_id": 2, "telegram_username": "@nordice"},
    }
    gc_api = "http://127.0.0.1:8372"
    city = "test"


def approval(responsibility: str, asked: list[str], required: int, **overrides) -> dict:
    """Build one ledger approval entry."""
    entry = {
        "id": "abc123",
        "responsibility": responsibility,
        "title": "some change",
        "required": required,
        "asked": asked,
        "delivered": asked,
        "approved_by": [],
        "rejected_by": None,
        "resolved": False,
    }
    entry.update(overrides)
    return entry


def turn(text: str) -> dict:
    return {"at": "2026-09-22T23:00:00+00:00", "text": text}


@pytest.fixture(autouse=True)
def instant_polling(monkeypatch):
    """Collapse the waits so a test does not sit through production timeouts.

    The fake ledger advances on every read, so a state change that takes the
    real agent a minute happens on the next poll here.
    """
    monkeypatch.setattr(drill, "POLL_INTERVAL", 0.0)
    monkeypatch.setattr(drill, "AGENT_TIMEOUT", 1.0)
    monkeypatch.setattr(drill, "HUMAN_TIMEOUT", 1.0)


@pytest.fixture
def page(tmp_path):
    """A stand-in for index.html that tests can leave alone or rewrite."""
    path = tmp_path / "index.html"
    path.write_text("<h1>before</h1>", encoding="utf-8")
    return str(path)


def test_an_open_approval_is_adopted_rather_than_asked_again(page) -> None:
    """A rerun answers the request already on the reviewer's phone."""
    step = drill.STEPS[0]
    open_entry = approval(step.responsibility, ["you"], 1)
    rejected = dict(open_entry, resolved=True, rejected_by="you")
    ledger = FakeLedger(
        approvals=[[open_entry], [rejected]],
        turns=[[], [turn("EDIT_SKIPPED: left the headline alone")]],
    )
    gc = FakeGasCity()
    report = drill.Report()

    drill.run_step(step, 1, 3, gc, ledger, FakeConfig(), page, report)

    assert gc.sent == [], "a second approval request was sent for the same decision"
    assert report.failures == []


def test_a_step_with_nothing_open_sends_its_request(page) -> None:
    """With no matching approval outstanding, the drill asks for one."""
    step = drill.STEPS[0]
    entry = approval(step.responsibility, ["you"], 1)
    rejected = dict(entry, resolved=True, rejected_by="you")
    ledger = FakeLedger(
        approvals=[[], [entry], [rejected]],
        turns=[[], [turn("EDIT_SKIPPED: left it alone")]],
    )
    gc = FakeGasCity()
    report = drill.Report()

    drill.run_step(step, 1, 3, gc, ledger, FakeConfig(), page, report)

    assert [name for name, _ in gc.sent] == ["you"]
    assert report.failures == []


def test_an_approval_routed_to_the_wrong_reviewer_fails(page) -> None:
    """The drill's whole purpose: a misrouted request must not pass."""
    step = drill.STEPS[1]  # security_review, nordice's alone
    entry = approval(step.responsibility, ["you"], 1)
    approved = dict(entry, resolved=True, approved_by=["you"])
    ledger = FakeLedger(
        approvals=[[entry], [approved]],
        turns=[[], [turn("EDIT_DONE: added the form")]],
    )
    report = drill.Report()

    drill.run_step(step, 2, 3, FakeGasCity(), ledger, FakeConfig(), page, report)

    assert any("routed to" in failure for failure in report.failures)


def test_an_approval_the_agent_ignores_fails(page, monkeypatch) -> None:
    """An approval that changes nothing is the failure this system prevents."""
    monkeypatch.setattr(drill, "AGENT_TIMEOUT", 0.2)
    step = drill.STEPS[1]
    entry = approval(step.responsibility, ["nordice"], 1)
    approved = dict(entry, resolved=True, approved_by=["nordice"])
    ledger = FakeLedger(approvals=[[entry], [approved]], turns=[[]])
    report = drill.Report()

    with pytest.raises(drill.DrillFailure):
        drill.run_step(step, 2, 3, FakeGasCity(), ledger, FakeConfig(), page, report)


def test_an_edit_that_never_reaches_the_page_fails(page) -> None:
    """The agent may claim EDIT_DONE; the page is the evidence."""
    step = drill.STEPS[1]
    entry = approval(step.responsibility, ["nordice"], 1)
    approved = dict(entry, resolved=True, approved_by=["nordice"])
    ledger = FakeLedger(
        approvals=[[entry], [approved]],
        turns=[[], [turn("EDIT_DONE: added the signup form")]],
    )
    report = drill.Report()

    drill.run_step(step, 2, 3, FakeGasCity(), ledger, FakeConfig(), page, report)

    assert any("page changed" in failure for failure in report.failures)


def test_a_quorum_needs_every_configured_approver(page) -> None:
    """One vote on a two-vote responsibility is a failure, not a pass."""
    step = drill.STEPS[2]
    entry = approval(step.responsibility, ["you", "nordice"], 2)
    half = dict(entry, resolved=True, approved_by=["you"])
    ledger = FakeLedger(
        approvals=[[entry], [half]],
        turns=[[], [turn("Publishing now")]],
    )
    report = drill.Report()

    drill.run_step(step, 3, 3, FakeGasCity(), ledger, FakeConfig(), page, report)

    assert any("approved by" in failure for failure in report.failures)


def test_the_page_is_read_before_the_request_not_after(page) -> None:
    """An edit made during the step must register as a change."""
    step = drill.STEPS[1]
    entry = approval(step.responsibility, ["nordice"], 1)
    approved = dict(entry, resolved=True, approved_by=["nordice"])

    class EditingLedger(FakeLedger):
        """Writes the page at the moment the agent reports the edit."""

        def turns(self) -> list[dict]:
            turns = super().turns()
            if turns:
                with open(page, "w", encoding="utf-8") as fh:
                    fh.write("<h1>before</h1><form></form>")
            return turns

    ledger = EditingLedger(
        approvals=[[entry], [approved]],
        turns=[[], [turn("EDIT_DONE: added the form")]],
    )
    report = drill.Report()

    drill.run_step(step, 2, 3, FakeGasCity(), ledger, FakeConfig(), page, report)

    assert report.failures == []
    assert os.path.getsize(page) > len("<h1>before</h1>")
