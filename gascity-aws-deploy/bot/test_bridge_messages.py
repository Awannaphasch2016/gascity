#!/usr/bin/env python3
"""Check every Telegram message the bridge builds survives agent-written text.

The agent chooses its own wording, so any message the bridge assembles from that
wording is a parse hazard. Telegram rejects a whole message whose markup does
not parse, which is silent by nature: the edit lands on disk and the person who
approved it is told nothing. The literal text "EDIT_DONE:" was enough to trigger
it under Markdown.

Run: python3 -m pytest bot/test_bridge_messages.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import json
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Tags Telegram accepts in HTML parse mode, restricted to the ones the bridge
# emits. Anything else reaching Telegram is a rejected message.
ALLOWED_TAGS = {"b", "i", "code", "a"}

# Text shaped like what an agent actually produces, plus every character that
# means something to a markup parser.
HOSTILE = (
    'EDIT_DONE: wrapped <div class="x"> & <script>alert(1)</script> in '
    "snake_case_names with *stars*, _unclosed italic, `backtick and 5 > 3 < 7"
)


class Markup(HTMLParser):
    """Collect the tags and text a Telegram message would be parsed into."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self.tags.append(tag)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def parse(message: str) -> Markup:
    """Parse a built message the way Telegram's HTML mode would."""
    m = Markup()
    m.feed(message)
    m.close()
    return m


@pytest.fixture
def bridge():
    """A Bridge wired to config on disk, with no network calls made."""
    doc = {
        "users": {
            "you": {
                "telegram_id": 1,
                "telegram_username": "@you",
                "bot_token_env": "TOK_YOU",
                "responsibilities": ["architecture_review"],
            },
            "nordice": {
                "telegram_id": 2,
                "telegram_username": "@nordice",
                "bot_token_env": "TOK_NORDICE",
                "responsibilities": ["security_review"],
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
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(doc, fh)
        path = fh.name

    os.environ.update({
        "GC_API": "http://127.0.0.1:8372",
        "GC_CITY_NAME": "test",
        "BRIDGE_CALLBACK_URL": "http://127.0.0.1:8099",
        "CONFIG_PATH": path,
        "TOK_YOU": "t1",
        "TOK_NORDICE": "t2",
        "PAGE_URL": "http://example.test:8080/",
    })
    import bridge as mod

    yield mod.Bridge(mod.Config())
    os.unlink(path)


def test_agent_turn_keeps_underscores_literal(bridge):
    """The exact text that Markdown rejected must survive as literal text."""
    built = bridge._agent_turn_html("EDIT_DONE: added a testimonials_section")
    got = parse(built)
    assert "EDIT_DONE: added a testimonials_section" in "".join(got.text)
    assert set(got.tags) <= ALLOWED_TAGS


def test_agent_turn_neutralizes_markup(bridge):
    """Agent text containing markup is shown, not interpreted."""
    got = parse(bridge._agent_turn_html(HOSTILE))
    # The <div> and <script> the agent wrote are text, not tags. Only the
    # bridge's own link tag may appear.
    assert set(got.tags) <= {"a"}
    joined = "".join(got.text)
    assert "<script>alert(1)</script>" in joined
    assert "5 > 3 < 7" in joined
    assert "&" in joined


def test_edit_done_carries_the_page_link(bridge):
    """A completed edit links to the page, so the outcome is one tap away."""
    assert 'href="http://example.test:8080/"' in bridge._agent_turn_html("EDIT_DONE: x")
    assert "href" not in bridge._agent_turn_html("EDIT_SKIPPED: x")


def test_approval_request_is_parseable(bridge, monkeypatch):
    """An approval request built from hostile agent text still parses."""
    sent: list[str] = []
    monkeypatch.setattr(bridge.tg, "send",
                        lambda user, chat, text, buttons=None: sent.append(text) or 1)

    bridge._ask_approval(f" architecture_review | {HOSTILE} | rewrote {HOSTILE}")

    assert sent, "an approval request was never delivered"
    for message in sent:
        got = parse(message)
        assert set(got.tags) <= ALLOWED_TAGS
        assert "Architecture Review" in "".join(got.text)
        assert "<script>" not in "".join(got.tags)


def test_resolution_edit_is_parseable(bridge, monkeypatch):
    """The message rewritten when an approval resolves also parses."""
    sent, edited = [], []
    monkeypatch.setattr(bridge.tg, "send",
                        lambda user, chat, text, buttons=None: sent.append(text) or 7)
    monkeypatch.setattr(bridge.tg, "answer_callback",
                        lambda user, cid, text: None)
    monkeypatch.setattr(bridge.tg, "edit",
                        lambda user, chat, mid, text: edited.append(text))
    monkeypatch.setattr(bridge.gc, "send_inbound",
                        lambda text, actor_id, actor_name: {"TargetAgentName": "builder"})

    bridge._ask_approval(f"architecture_review | {HOSTILE} | detail_with_underscores")
    approval_id = next(iter(bridge.pending))
    bridge.on_button("you", "cb1", f"a:{approval_id}")

    assert edited, "the approval message was never rewritten on resolution"
    for message in edited:
        got = parse(message)
        assert set(got.tags) <= ALLOWED_TAGS
        assert "Approved by you." in "".join(got.text)


def test_malformed_request_reports_verbatim(bridge, monkeypatch):
    """A malformed request is reported with its text intact, not dropped."""
    sent: list[str] = []
    monkeypatch.setattr(bridge.tg, "send",
                        lambda user, chat, text, buttons=None: sent.append(text) or 1)

    bridge._ask_approval(" architecture_review | missing the third field")

    assert sent
    for message in sent:
        got = parse(message)
        assert set(got.tags) <= ALLOWED_TAGS
        assert "missing the third field" in "".join(got.text)


def test_unknown_responsibility_reports_instead_of_silently_dropping(bridge, monkeypatch):
    """Naming a responsibility nobody holds tells the humans, and asks no one."""
    sent: list[str] = []
    monkeypatch.setattr(bridge.tg, "send",
                        lambda user, chat, text, buttons=None: sent.append(text) or 1)

    bridge._ask_approval("legal_review | a thing | some_detail")

    assert len(sent) == len(bridge.cfg.users), "not every user was told"
    assert not bridge.pending, "an unroutable request must not be left pending"
    for message in sent:
        assert set(parse(message).tags) <= ALLOWED_TAGS
