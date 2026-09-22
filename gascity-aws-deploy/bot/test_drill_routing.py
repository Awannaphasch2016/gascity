#!/usr/bin/env python3
"""Check the routing drill's expectations against the deployed config.

The drill asserts that security_review reaches nordice and nobody else, and
that deployment_approval needs two approvals. Those are literals in the drill,
so editing responsibilities.json silently turns the drill from a test into a
test of last week's config — and it fails in a way that looks like a routing
bug rather than a stale expectation.

Run: python3 -m pytest bot/test_drill_routing.py
"""

from __future__ import annotations

import json
import os

import pytest

import drill_routing

CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "responsibilities.json",
)


@pytest.fixture(scope="module")
def deployed() -> dict:
    """The responsibilities file the bridge actually runs on."""
    with open(CONFIG, encoding="utf-8") as fh:
        return json.load(fh)


def holders(doc: dict, responsibility: str) -> set[str]:
    """Who holds a responsibility according to the deployed config."""
    return {
        name for name, user in doc["users"].items()
        if responsibility in user.get("responsibilities", [])
    }


def quorum(doc: dict, responsibility: str) -> int:
    """How many approvals the deployed config requires, as the bridge counts."""
    spec = doc["responsibility_definitions"][responsibility]
    required = int(spec.get("required_count", 2)) if spec.get("requires_multiple") else 1
    return min(required, len(holders(doc, responsibility)))


@pytest.mark.parametrize("step", drill_routing.STEPS,
                         ids=lambda step: step.responsibility)
def test_step_routes_to_who_the_config_says(step, deployed) -> None:
    """Each step expects exactly the reviewers the config assigns."""
    assert step.asked == holders(deployed, step.responsibility)


@pytest.mark.parametrize("step", drill_routing.STEPS,
                         ids=lambda step: step.responsibility)
def test_step_expects_the_configured_quorum(step, deployed) -> None:
    """Each step expects the approval count the config produces."""
    assert step.required == quorum(deployed, step.responsibility)


def test_the_drill_covers_every_routing_shape(deployed) -> None:
    """One reviewer, a different reviewer, and a quorum of several.

    Dropping one of these leaves a routing shape untested, which is how the
    attribution bug survived: only the reviewer listed first was ever exercised.
    """
    shapes = {(frozenset(step.asked), step.required) for step in drill_routing.STEPS}
    assert len({size for _, size in shapes}) > 1, "no quorum step"
    single = [asked for asked, size in shapes if size == 1]
    assert len(single) >= 2, "only one single-reviewer route is exercised"
    assert len(set(single)) == len(single), "two steps route to the same lone reviewer"


def test_the_drill_exercises_both_answers(deployed) -> None:
    """A rejection and an approval are different code paths for the agent."""
    answers = {step.approve for step in drill_routing.STEPS}
    assert answers == {True, False}
