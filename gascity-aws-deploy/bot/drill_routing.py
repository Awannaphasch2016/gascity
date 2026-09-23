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
from dataclasses import dataclass
from typing import Any, Callable

import requests

import bridge as mod

# The agent is a cursor-agent session on a pinned model; a cold first turn has
# been measured at ~70s, and it re-reads the page before answering.
AGENT_TIMEOUT = 300.0
# A human is reading their phone, possibly for the first time in an hour. Raise
# it with --human-timeout; a rerun adopts the request already waiting rather
# than sending a second one, so a timeout costs nothing but the wait.
HUMAN_TIMEOUT = 3600.0
POLL_INTERVAL = 3.0
HEARTBEAT_INTERVAL = 300.0


class DrillFailure(Exception):
    """A routing hop did not happen, or happened wrongly."""


@dataclass
class Step:
    """One request, the routing it must produce, and what must follow the answer.

    `suggest` is a suggestion and nothing more. The reviewer's decision is this
    drill's input, not its script: whichever button they press, what has to hold
    is that the agent obeys *that* answer. Suggesting one exists only so a
    single run covers a refusal and an approval rather than one of them twice.
    """

    title: str
    sender: str
    request: str
    responsibility: str
    asked: set[str]
    required: int
    suggest: str
    # Whether an approval here should rewrite index.html. A sign-off need not, so
    # the drill does not assert an edit it has no reason to expect. A *refusal*
    # must leave the page alone either way, and that is always checked.
    edits_the_page: bool


STEPS: list[Step] = [
    Step(
        title="one reviewer",
        sender="you",
        request=(
            "Change the hero headline to "
            "'Know what your data is doing before your customers do'."
        ),
        responsibility="architecture_review",
        asked={"you"},
        required=1,
        suggest="press REJECT in your own bot's chat",
        edits_the_page=True,
    ),
    Step(
        title="a different reviewer",
        sender="nordice",
        request=(
            "Add a newsletter signup form under the pricing section: "
            "an email input and a Subscribe button."
        ),
        responsibility="security_review",
        asked={"nordice"},
        required=1,
        suggest="press APPROVE in nordice's bot chat",
        edits_the_page=True,
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
        suggest="press APPROVE in BOTH bot chats — one is not enough",
        edits_the_page=False,
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
    """Poll until probe returns something truthy, or give up loudly.

    Reports that it is still alive while it waits, because the log is the only
    way to tell a drill waiting on a person from one that has hung.
    """
    start = time.monotonic()
    next_beat = start + HEARTBEAT_INTERVAL
    while True:
        value = probe()
        if value:
            return value
        now = time.monotonic()
        if now - start >= timeout:
            raise DrillFailure(f"timed out after {timeout:.0f}s waiting for {what}")
        if now >= next_beat:
            print(f"        still waiting for {what} "
                  f"({(now - start) / 60:.0f}m elapsed)")
            next_beat = now + HEARTBEAT_INTERVAL
        time.sleep(POLL_INTERVAL)


def select_steps(spec: str) -> list[int]:
    """Parse a --steps value into 1-based step numbers, in the order given."""
    if not spec.strip():
        return list(range(1, len(STEPS) + 1))
    numbers = []
    for field_text in spec.split(","):
        text = field_text.strip()
        if not text.isdigit() or not 1 <= int(text) <= len(STEPS):
            raise ValueError(f"{text!r} is not a step between 1 and {len(STEPS)}")
        numbers.append(int(text))
    return numbers


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
    existing = ledger.approvals()
    seen_ids = {entry["id"] for entry in existing}

    # A step that already has an unanswered request adopts it rather than asking
    # again. Reruns are the normal way to use this drill, and a second identical
    # approval message — with only one of the two that counts — is worse for the
    # person deciding than no drill at all.
    entry = next(
        (candidate for candidate in existing
         if candidate["responsibility"] == step.responsibility
         and not candidate["resolved"]
         and candidate["delivery_complete"]),
        None,
    )
    if entry is not None:
        report.note(f'adopting the {step.responsibility} request already open: '
                    f'"{entry["title"]}"')
    else:
        actor = cfg.users[step.sender]
        gc.send_inbound(step.request, str(actor["telegram_id"]), step.sender)
        report.note(f'sent as {step.sender}: "{step.request}"')

        def new_approval() -> dict[str, Any] | None:
            # Judged only once every send has been attempted. Read between two
            # sends, `delivered` names one reviewer and looks like a refusal.
            for candidate in ledger.approvals():
                if candidate["id"] not in seen_ids and candidate["delivery_complete"]:
                    return candidate
            return None

        report.note(f"waiting up to {AGENT_TIMEOUT:.0f}s for the agent to ask someone...")
        entry = wait_for(new_approval, AGENT_TIMEOUT, "the agent to request approval")

    report.check("responsibility", entry["responsibility"], step.responsibility)
    report.check("routed to", sorted(entry["asked"]), sorted(step.asked))
    report.check("delivered to", sorted(entry["delivered"]), sorted(step.asked))
    report.check("approvals needed", entry["required"], step.required)
    report.note(f'the agent described it as: "{entry["title"]}"')

    print(f"\n  >>> On your phone: {step.suggest}\n"
          f"      (either answer is fine — the drill checks the agent obeys "
          f"the one you give)\n")

    approval_id = entry["id"]
    # Counted here rather than at the top of the step: the turn that asked for
    # this approval is already in the log, and what matters is what the agent
    # does *after* the decision.
    seen_turns = len(ledger.turns())

    def resolved() -> dict[str, Any] | None:
        for candidate in ledger.approvals():
            if candidate["id"] == approval_id and candidate["resolved"]:
                return candidate
        return None

    report.note(f"waiting up to {HUMAN_TIMEOUT:.0f}s for your decision...")
    decided = wait_for(resolved, HUMAN_TIMEOUT, "your decision")

    approved = decided["rejected_by"] is None
    if approved:
        report.note("you approved it; checking the agent carries it out")
        # Every vote must come from someone who holds the responsibility, and
        # there must be as many as the responsibility demands. A quorum met by
        # one reviewer voting twice, or by someone never asked, is not a quorum.
        report.check("votes from unasked", sorted(set(decided["approved_by"]) - step.asked), [])
        report.check("approvals recorded", len(set(decided["approved_by"])), step.required)
    else:
        report.note(f"{decided['rejected_by']} rejected it; "
                    "checking the agent leaves the page alone")
        report.check("rejected by a reviewer", decided["rejected_by"] in step.asked, True)

    report.note(f"waiting up to {AGENT_TIMEOUT:.0f}s for the agent to act...")
    turns = wait_for(
        lambda: (lambda t: t[seen_turns:] or None)(ledger.turns()),
        AGENT_TIMEOUT, "the agent to reply to the decision",
    )
    latest = turns[-1]["text"]
    if not step.edits_the_page:
        report.check("agent replied", bool(latest.strip()), True)
    else:
        report.check("agent replied", latest.split(":")[0],
                     "EDIT_DONE" if approved else "EDIT_SKIPPED")
    report.note(f"its reply: {latest[:160]}")

    changed = page_digest(page) != before_digest
    if not approved:
        # Unconditional: an edit made after a refusal is the single failure this
        # whole system exists to prevent, whatever the step was about.
        report.check("page changed", changed, False)
    elif step.edits_the_page:
        report.check("page changed", changed, True)


def main() -> int:
    """Run every step in order and report whether the routing held."""
    global HUMAN_TIMEOUT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", default=os.getenv("BRIDGE_URL", "http://127.0.0.1:8081"),
                        help="base URL of the bridge's callback listener")
    parser.add_argument("--page", default="/opt/gascity/site/index.html",
                        help="the page the agent edits")
    parser.add_argument("--steps", default="",
                        help="1-based steps to run, comma separated "
                             "(e.g. 2,3 to resume); default runs all")
    parser.add_argument("--human-timeout", type=float, default=HUMAN_TIMEOUT,
                        help="seconds to wait for each decision")
    args = parser.parse_args()
    HUMAN_TIMEOUT = args.human_timeout

    try:
        chosen = select_steps(args.steps)
    except ValueError as exc:
        print(f"--steps: {exc}", file=sys.stderr)
        return 2

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

    print(f"Routing drill against {cfg.gc_api} (city {cfg.city}), page {args.page}")
    for number in chosen:
        try:
            run_step(STEPS[number - 1], number, len(STEPS), gc, ledger, cfg,
                     args.page, report)
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
