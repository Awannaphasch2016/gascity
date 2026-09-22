"""Shared fixtures for the bridge tests.

Builds a Bridge over config on disk with no network calls made, so tests can
drive its routing and message assembly directly.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Callable

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _config_doc(you_id: int, nordice_id: int) -> dict[str, Any]:
    """Mirror the shape of config/responsibilities.json."""
    return {
        "users": {
            "you": {
                "telegram_id": you_id,
                "telegram_username": "@you",
                "bot_token_env": "TOK_YOU",
                "responsibilities": ["architecture_review", "deployment_approval"],
            },
            "nordice": {
                "telegram_id": nordice_id,
                "telegram_username": "@nordice",
                "bot_token_env": "TOK_NORDICE",
                "responsibilities": ["security_review", "deployment_approval"],
            },
        },
        "responsibility_definitions": {
            "architecture_review": {
                "name": "Architecture Review",
                "icon": "\N{BUILDING CONSTRUCTION}",
                "requires_multiple": False,
            },
            "security_review": {
                "name": "Security Review",
                "icon": "\N{LOCK}",
                "requires_multiple": False,
            },
            "deployment_approval": {
                "name": "Deployment Approval",
                "icon": "\N{ROCKET}",
                "requires_multiple": True,
                "required_count": 2,
            },
        },
    }


@pytest.fixture
def build_bridge() -> Callable[..., Any]:
    """Return a factory building a Bridge with chosen Telegram account ids."""
    paths: list[str] = []

    def factory(you_id: int = 1, nordice_id: int = 2) -> Any:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(_config_doc(you_id, nordice_id), fh)
            paths.append(fh.name)

        os.environ.update({
            "GC_API": "http://127.0.0.1:8372",
            "GC_CITY_NAME": "test",
            "BRIDGE_CALLBACK_URL": "http://127.0.0.1:8099",
            "CONFIG_PATH": paths[-1],
            "TOK_YOU": "t1",
            "TOK_NORDICE": "t2",
            "PAGE_URL": "http://example.test:8080/",
        })
        import bridge as mod

        return mod.Bridge(mod.Config())

    yield factory

    for path in paths:
        os.unlink(path)


@pytest.fixture
def bridge(build_bridge) -> Any:
    """A Bridge whose two reviewers hold distinct Telegram accounts."""
    return build_bridge()


@pytest.fixture
def shared_account_bridge(build_bridge) -> Any:
    """A Bridge whose reviewers share one Telegram account.

    This is the deployed configuration: one phone plays both reviewers while
    testing, each through their own bot.
    """
    return build_bridge(you_id=7037289190, nordice_id=7037289190)


@pytest.fixture
def capture(monkeypatch) -> Callable[[Any], dict[str, list]]:
    """Silence Telegram and Gas City, recording what each was asked to do."""

    def wire(bridge: Any) -> dict[str, list]:
        record: dict[str, list] = {"sent": [], "callbacks": [], "edits": [], "inbound": []}
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


@pytest.fixture
def press() -> Callable[..., None]:
    """Return a helper simulating a button press arriving on one bot."""

    def act(bridge: Any, bot: str, telegram_id: int, approval_id: str,
            approve: bool = True) -> None:
        prefix = "a:" if approve else "r:"
        bridge._dispatch(bot, {
            "update_id": 1,
            "callback_query": {
                "id": "cb1",
                "from": {"id": telegram_id},
                "data": f"{prefix}{approval_id}",
            },
        })

    return act
