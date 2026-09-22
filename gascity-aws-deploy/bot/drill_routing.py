#!/usr/bin/env python3
"""Walk one collaboration through every routing path the city has, and check it.

The city has exactly four ways a turn can be routed, and this drill exercises
all four in one continuous conversation about one feature:

  1. A request reaching one named reviewer          (architecture_review -> you)
  2. A request reaching a *different* named one     (security_review -> nordice)
  3. A request reaching several, needing a quorum   (deployment_approval -> both)
  4. A human answer reaching the agent, both ways   (reject -> no edit,
                                                     approve -> the edit lands)

Each step asserts the hop rather than describing it: who the request went to,
how many approvals it needs, who answered, whether the agent acted, and whether
index.html actually changed. A step that routes to the wrong person, or an
approval the agent never acts on, fails here instead of looking fine on a phone.

The drill sends each request through the same endpoint the bridge uses for a
typed Telegram message, so the whole path after that point is the real one. It
cannot press the buttons — only a Telegram client can — so it stops at each
decision and waits for you.

Run it on the host, with the bridge's environment loaded:

    set -a; . /opt/gascity/bridge.env; set +a
    python3 /opt/gascity/bot/drill_routing.py

Nothing is cleaned up afterwards: the approved edit is a real edit to the real
page, which is the point.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

import bridge as mod

# The agent is a cursor-agent session on a pinned model; a cold first turn has
# been measured at ~70s, and it re-reads the page before answering.
AGENT_TIMEOUT = 300.0
# A human is reading their phone, possibly for the first time in an hour. A
# step that times out is rerunnable on its own with --step, so this is generous
# rather than tight.
HUMAN_TIMEOUT = 1800.0
POLL_INTERVAL = 3.0


class DrillFailure(Exception):
    """A routing hop did not happen, or happened wrongly."""


@dataclass
class Step:
    """One request, the routing it must produce, and the answer it must get."""

    title: str
    sender: str
    request: str
    responsibility: str
    asked: set[str]
    required: int
    instruction: str
    approve: bool
    expect_turn: str | None
    # None where the drill has no business asserting: a sign-off need not
    # rewrite the page to have been routed correctly.
    page_changes: bool | None
    approved_by: set[str] = field(default_factory=set)


STEPS: list[Step] = [
    Step(
        title="one reviewer, rejected",
        sender="you",
        request=(
            "Change the hero headline to "
            "'Know what your data is doing before your customers do'."
        ),
        responsibility="architecture_review",
        asked={"you"},
        required=1,
        instruction="press REJECT in your own bot's chat",
        approve=False,
        expect_turn="EDIT_SKIPPED",
        page_changes=False,
    ),
    Step(
        title="a different reviewer, approved",
        sender="nordice",
        request=(
            "Add a newsletter signup form under the pricing section: "
            "an email input and a Subscribe button."
        ),
        responsibility="security_review",
        asked={"nordice"},
        required=1,
        instruction="press APPROVE in nordice's bot chat",
        approve=True,
        expect_turn="EDIT_DONE",
        page_changes=True,
    ),
    Step(
        title="two reviewers, quorum",
        sender="you",
        request=(
            "That is everything for tonight. Sign off on what we changed "
            "and publish the page."
        ),
        responsibility="deployment_approval",
        asked={"you", "nordice"},
        required=2,
        instruction="press APPROVE in BOTH bot chats — one is not enough",
        approve=True,
        expect_turn=None,
        page_changes=None,
        approved_by={"you", "nordice"},
    ),
]


class Ledger:
    """Reads the bridge's routing ledger."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def read(self) -> dict[str, Any]:
        resp = requests.get(f"{self.base_url}/state", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def approvals(self) -> list[dict[str, Any]]:
        return self.read()["approvals"]

    def turns(self) -> list[dict[str, str]]:
        return self.read()["turns"]


class Report:
    """Prints each assertion as it is made and remembers the failures."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, actual: Any, expected: Any) -> None:
        if actual == expected:
            print(f"  PASS  {label:<18} {actual}")
            return
        self.failures.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL  {label:<18} {actual!r} (expected {expected!r})")

    def note(self, text: str) -> None:
        print(f"        {text}")


def wait_for(probe: Callable[[], Any], timeout: float, what: str) -> Any:
    """Poll until probe returns something truthy, or give up loudly."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(POLL_INTERVAL)
    raise DrillFailure(f"timed out after {timeout:.0f}s waiting for {what}")


def page_digest(path: str) -> str:
    """Return a digest of the page, so an edit is detectable without diffing."""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run_step(step: Step, index: int, total: int, gc: Any, ledger: Ledger,
             cfg: Any, page: str, report: Report) -> None:
    """Send one request, then check every hop it should produce."""
    print(f"\n=== Step {index}/{total}: {step.title} "
          f"({step.responsibility}) ===")

    before_digest = page_digest(page)
    seen_ids = {entry["id"] for entry in ledger.approvals()}
    seen_turns = len(ledger.turns())

    actor = cfg.users[step.sender]
    gc.send_inbound(step.request, str(actor["telegram_id"]), step.sender)
    report.note(f'sent as {step.sender}: "{step.request}"')

    def new_approval() -> dict[str, Any] | None:
        for entry in ledger.approvals():
            if entry["id"] not in seen_ids:
                return entry
        return None

    report.note(f"waiting up to {AGENT_TIMEOUT:.0f}s for the agent to ask someone...")
    entry = wait_for(new_approval, AGENT_TIMEOUT, "the agent to request approval")

    report.check("responsibility", entry["responsibility"], step.responsibility)
    report.check("routed to", sorted(entry["asked"]), sorted(step.asked))
    report.check("delivered to", sorted(entry["delivered"]), sorted(step.asked))
    report.check("approvals needed", entry["required"], step.required)
    report.note(f'the agent described it as: "{entry["title"]}"')

    print(f"\n  >>> On your phone: {step.instruction}\n")

    approval_id = entry["id"]

    def resolved() -> dict[str, Any] | None:
        for candidate in ledger.approvals():
            if candidate["id"] == approval_id and candidate["resolved"]:
                return candidate
        return None

    report.note(f"waiting up to {HUMAN_TIMEOUT:.0f}s for your decision...")
    decided = wait_for(resolved, HUMAN_TIMEOUT, "your decision")

    if step.approve:
        expected = sorted(step.approved_by or step.asked)
        report.check("approved by", sorted(decided["approved_by"]), expected)
        report.check("rejected by", decided["rejected_by"], None)
    else:
        report.check("rejected by", decided["rejected_by"], sorted(step.asked)[0])
        report.check("approved by", sorted(decided["approved_by"]), [])

    report.note(f"waiting up to {AGENT_TIMEOUT:.0f}s for the agent to act...")
    turns = wait_for(
        lambda: (lambda t: t[seen_turns:] or None)(ledger.turns()),
        AGENT_TIMEOUT, "the agent to reply to the decision",
    )
    latest = turns[-1]["text"]
    if step.expect_turn is None:
        report.check("agent replied", bool(latest.strip()), True)
    else:
        report.check("agent replied", latest.split(":")[0], step.expect_turn)
    report.note(f"its reply: {latest[:160]}")

    if step.page_changes is None:
        return
    changed = page_digest(page) != before_digest
    report.check("page changed", changed, step.page_changes)


def main() -> int:
    """Run every step in order and report whether the routing held."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", default=os.getenv("BRIDGE_URL", "http://127.0.0.1:8081"),
                        help="base URL of the bridge's callback listener")
    parser.add_argument("--page", default="/opt/gascity/site/index.html",
                        help="the page the agent edits")
    parser.add_argument("--step", type=int, default=0,
                        help="run only this step (1-based); default runs all")
    args = parser.parse_args()

    # The bridge gets GC_API from its systemd unit rather than its env file, so
    # a shell that sourced only the env file is still missing it.
    os.environ.setdefault("GC_API", "http://127.0.0.1:8372")

    try:
        cfg = mod.Config()
    except (KeyError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        print("load the bridge's environment first: "
              "set -a; . /opt/gascity/bridge.env; set +a", file=sys.stderr)
        return 2

    ledger = Ledger(args.bridge)
    try:
        ledger.read()
    except Exception as exc:
        print(f"cannot read the bridge ledger at {args.bridge}/state: {exc}", file=sys.stderr)
        return 2

    gc = mod.GasCityClient(cfg)
    report = Report()
    steps = STEPS if args.step == 0 else [STEPS[args.step - 1]]

    print(f"Routing drill against {cfg.gc_api} (city {cfg.city}), page {args.page}")
    for offset, step in enumerate(steps, start=1):
        number = args.step or offset
        try:
            run_step(step, number, len(STEPS), gc, ledger, cfg, args.page, report)
        except DrillFailure as exc:
            report.failures.append(str(exc))
            print(f"  FAIL  {exc}")
            break

    print("\n" + "=" * 60)
    if report.failures:
        print(f"{len(report.failures)} failure(s):")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print("every routing path held: agent -> the right human, and back again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
