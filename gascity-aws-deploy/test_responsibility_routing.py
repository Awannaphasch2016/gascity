#!/usr/bin/env python3
"""Check the agent picks the responsibility from what a change touches.

A newsletter signup box is a form, so it must route to security_review even
though the request describes it in cosmetic terms. If the agent routed on
wording it would say architecture_review like the previous request did.
"""
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

GC, CITY, PORT = "http://127.0.0.1:8372", "citytest", 8099
inbox: list[str] = []
app = Flask(__name__)


@app.post("/publish")
def publish():
    body = request.get_json(silent=True) or {}
    inbox.append(body.get("text", ""))
    print(f"\n<<< {body.get('text','')}\n", flush=True)
    return jsonify({"message_id": uuid.uuid4().hex,
                    "conversation": body.get("conversation", {}),
                    "delivered": True}), 200


threading.Thread(target=lambda: app.run(host="127.0.0.1", port=PORT, threaded=True),
                 daemon=True).start()
time.sleep(2)

S = requests.Session()
S.headers.update({"Content-Type": "application/json", "X-GC-Request": "1"})
base = f"{GC}/v0/city/{CITY}/extmsg"
# A fresh conversation id so this is judged on its own, not on the prior thread.
conv_id = f"landing-page-{uuid.uuid4().hex[:6]}"

S.post(f"{base}/adapters", json={
    "provider": "telegram", "account_id": "factory", "name": "routing",
    "callback_url": f"http://127.0.0.1:{PORT}",
    "capabilities": {"MaxMessageLength": 4096, "SupportsAttachments": False,
                     "SupportsChildConversations": False},
}, timeout=30).raise_for_status()

text = ("Can you drop a little newsletter signup box in the footer? "
        "Just an email field and a subscribe button, nothing fancy.")
S.post(f"{base}/inbound", json={"message": {
    "provider_message_id": str(uuid.uuid4()),
    "received_at": datetime.now(timezone.utc).isoformat(),
    "text": text,
    "actor": {"id": "7037289190", "display_name": "you", "is_bot": False},
    "conversation": {"provider": "telegram", "account_id": "factory",
                     "conversation_id": conv_id, "scope_id": "city", "kind": "dm"},
}}, timeout=60).raise_for_status()
print(f">>> SENT: {text}")

deadline = time.time() + 300
while time.time() < deadline:
    for t in list(inbox):
        if "APPROVAL_NEEDED" in t:
            got = [p.strip() for p in t.split("APPROVAL_NEEDED:", 1)[1].split("|")][0]
            print(f"\nresponsibility chosen: {got!r}")
            if got == "security_review":
                print("PASS: routed on what the change touches (a form), not on wording")
                sys.exit(0)
            print(f"FAIL: expected 'security_review', got {got!r}")
            sys.exit(1)
    time.sleep(3)

print("FAIL: no APPROVAL_NEEDED within 300s")
sys.exit(1)
