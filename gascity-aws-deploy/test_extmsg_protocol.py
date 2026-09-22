#!/usr/bin/env python3
"""Check the Gas City external-messaging transport against a running city.

Exercises adapter registration, inbound routing, and the transcript without
involving Telegram or a coding agent, so a failure points at one layer instead
of the whole stack. Run it after `gc start` and before starting the bridge.

    python3 test_extmsg_protocol.py --api http://127.0.0.1:8372 --city citytest

A pass proves the default route resolves to the configured agent and that the
conversation binding and transcript are persisted. It does not prove the agent
replies: that additionally needs the provider CLI to be authenticated, and the
agent's reply arrives as a separate POST to the callback URL.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Flask, jsonify, request

received: list[dict[str, Any]] = []


def build_listener() -> Flask:
    """Build a listener that records the callbacks Gas City sends."""
    app = Flask(__name__)

    @app.post("/publish")
    def publish() -> Any:
        body = request.get_json(silent=True) or {}
        received.append(body)
        print(f"  callback: {body.get('text', '')[:160]!r}", flush=True)
        return jsonify({
            "message_id": uuid.uuid4().hex,
            "conversation": body.get("conversation", {}),
            "delivered": True,
        }), 200

    return app


def main() -> int:
    """Run the protocol checks and report which layer failed."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8372",
                    help="supervisor API base URL (note: not the city's [api] port)")
    ap.add_argument("--city", required=True, help="registered city name")
    ap.add_argument("--port", type=int, default=8099, help="local callback listener port")
    ap.add_argument("--expect-agent", default="builder",
                    help="agent name the default route should resolve to")
    ap.add_argument("--wait", type=int, default=20,
                    help="seconds to wait for an agent reply callback")
    args = ap.parse_args()

    app = build_listener()
    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=args.port, threaded=True),
        daemon=True,
    ).start()
    time.sleep(2)

    session = requests.Session()
    session.headers.update({"Content-Type": "application/json", "X-GC-Request": "1"})
    base = f"{args.api.rstrip('/')}/v0/city/{args.city}/extmsg"
    conversation = {
        "provider": "telegram",
        "account_id": "factory",
        "conversation_id": f"protocol-check-{uuid.uuid4().hex[:8]}",
        "scope_id": "city",
        "kind": "dm",
    }
    failures: list[str] = []

    print("1. registering adapter")
    resp = session.post(f"{base}/adapters", json={
        "provider": "telegram",
        "account_id": "factory",
        "name": "protocol-check",
        "callback_url": f"http://127.0.0.1:{args.port}",
        "capabilities": {
            "MaxMessageLength": 4096,
            "SupportsAttachments": False,
            "SupportsChildConversations": False,
        },
    }, timeout=30)
    if resp.status_code != 201:
        failures.append(f"adapter registration returned {resp.status_code}: {resp.text[:300]}")
    else:
        print(f"   registered: {resp.json()}")

    print("2. posting inbound")
    resp = session.post(f"{base}/inbound", json={
        "message": {
            "provider_message_id": str(uuid.uuid4()),
            "received_at": datetime.now(timezone.utc).isoformat(),
            "text": "Protocol check. No action needed.",
            "actor": {"id": "0", "display_name": "protocol-check", "is_bot": True},
            "conversation": conversation,
        }
    }, timeout=60)
    if resp.status_code != 200:
        failures.append(f"inbound returned {resp.status_code}: {resp.text[:300]}")
    else:
        decision = resp.json()
        agent = decision.get("TargetAgentName") or ""
        print(f"   routed to agent: {agent!r}")
        if agent != args.expect_agent:
            failures.append(
                f"default route resolved to {agent!r}, expected {args.expect_agent!r}. "
                "Check that [[extmsg.default_route]].provider matches the adapter's "
                "provider, that the agent has a [[named_session]], and that the running "
                "controller loaded the current city.toml."
            )
        binding = decision.get("Binding") or {}
        if binding:
            print(f"   binding bead: {binding.get('ID')} status={binding.get('Status')}")

    print("3. reading transcript")
    resp = session.get(f"{base}/transcript", params={
        "provider": conversation["provider"],
        "account_id": conversation["account_id"],
        "conversation_id": conversation["conversation_id"],
        "scope_id": conversation["scope_id"],
        "kind": conversation["kind"],
    }, timeout=30)
    if resp.status_code != 200:
        failures.append(f"transcript returned {resp.status_code}: {resp.text[:300]}")
    else:
        total = resp.json().get("total", 0)
        print(f"   transcript entries: {total}")
        if total < 1:
            failures.append("transcript recorded no entry for the inbound message")

    print(f"4. waiting {args.wait}s for an agent reply")
    time.sleep(args.wait)
    print(f"   callbacks received: {len(received)}")

    print()
    if failures:
        print("TRANSPORT FAILED:")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("TRANSPORT OK: adapter, routing, binding, and transcript all work.")
    if not received:
        print(
            "No agent reply arrived. That is expected when the provider CLI is not\n"
            "authenticated — check the agent's pane with\n"
            f"  tmux -L {args.city} capture-pane -p -t {args.expect_agent}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
