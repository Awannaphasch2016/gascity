#!/usr/bin/env python3
"""Check the bridge re-registers after the controller forgets it.

Adapter registrations live in the controller's memory. `gc stop` / `gc start`,
a supervisor restart, or a controller crash therefore drops every registration,
and a bridge that only registers at startup goes deaf without noticing: Gas City
accepts inbound turns, the agent works, and its replies come back 422 with the
human seeing nothing. Observed on the deployed host after a city restart.

Run: python3 -m pytest bot/test_bridge_registration.py
"""

from __future__ import annotations

import pytest


class FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_reports_registered_when_the_controller_has_it(bridge, monkeypatch):
    """An adapter matching this bridge's provider and account counts."""
    monkeypatch.setattr(bridge.gc.session, "get", lambda url, timeout=0: FakeResponse({
        "items": [{"provider": "telegram", "account_id": "factory", "name": "telegram-bridge"}],
        "total": 1,
    }))
    assert bridge.gc.adapter_registered() is True


def test_reports_unregistered_when_the_list_is_empty(bridge, monkeypatch):
    """An empty list is what a restarted controller returns."""
    monkeypatch.setattr(bridge.gc.session, "get",
                        lambda url, timeout=0: FakeResponse({"items": [], "total": 0}))
    assert bridge.gc.adapter_registered() is False


def test_another_accounts_adapter_does_not_count(bridge, monkeypatch):
    """A registration for a different account is not this bridge's."""
    monkeypatch.setattr(bridge.gc.session, "get", lambda url, timeout=0: FakeResponse({
        "items": [{"provider": "telegram", "account_id": "someone-else"}],
        "total": 1,
    }))
    assert bridge.gc.adapter_registered() is False


def test_ensure_registered_re_registers_when_missing(bridge, monkeypatch):
    """The reconcile step registers again once the controller has forgotten."""
    registered: list[bool] = []
    monkeypatch.setattr(bridge.gc, "adapter_registered", lambda: False)
    monkeypatch.setattr(bridge.gc, "register_adapter", lambda: registered.append(True))

    assert bridge.ensure_registered() is True
    assert registered == [True]


def test_ensure_registered_is_a_no_op_when_present(bridge, monkeypatch):
    """A healthy registration is left alone, so the check is cheap to repeat."""
    registered: list[bool] = []
    monkeypatch.setattr(bridge.gc, "adapter_registered", lambda: True)
    monkeypatch.setattr(bridge.gc, "register_adapter", lambda: registered.append(True))

    assert bridge.ensure_registered() is False
    assert registered == []


def test_ensure_registered_survives_an_unreachable_controller(bridge, monkeypatch):
    """A controller that is still starting must not kill the reconcile loop."""

    def boom() -> bool:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(bridge.gc, "adapter_registered", boom)
    # Returns rather than raising, so the caller's loop keeps running and the
    # bridge recovers on a later pass instead of staying deaf until a human
    # restarts it.
    assert bridge.ensure_registered() is False
