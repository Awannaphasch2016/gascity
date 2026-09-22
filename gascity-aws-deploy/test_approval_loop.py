#!/usr/bin/env python3
"""Drive the full approval loop: request -> approval ask -> approve -> edit."""
import hashlib
import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

GC, CITY, PORT = "http://127.0.0.1:8372", "citytest", 8099
PAGE = "/tmp/site-local/index.html"
CONV = {
    "provider": "telegram", "account_id": "factory",
    "conversation_id": "landing-page", "scope_id": "city", "kind": "dm",
}

inbox: list[str] = []
app = Flask(__name__)


@app.post("/publish")
def publish():
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")
    inbox.append(text)
    print(f"\n<<< AGENT SAID:\n{text}\n", flush=True)
    return jsonify({"message_id": uuid.uuid4().hex,
                    "conversation": body.get("conversation", {}),
                    "delivered": True}), 200


threading.Thread(target=lambda: app.run(host="127.0.0.1", port=PORT, threaded=True),
                 daemon=True).start()
time.sleep(2)

S = requests.Session()
S.headers.update({"Content-Type": "application/json", "X-GC-Request": "1"})
base = f"{GC}/v0/city/{CITY}/extmsg"

S.post(f"{base}/adapters", json={
    "provider": "telegram", "account_id": "factory", "name": "e2e",
    "callback_url": f"http://127.0.0.1:{PORT}",
    "capabilities": {"MaxMessageLength": 4096, "SupportsAttachments": False,
                     "SupportsChildConversations": False},
}, timeout=30).raise_for_status()
print("adapter registered")


def send(text: str, who: str = "you") -> None:
    r = S.post(f"{base}/inbound", json={"message": {
        "provider_message_id": str(uuid.uuid4()),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "actor": {"id": "7037289190", "display_name": who, "is_bot": False},
        "conversation": CONV,
    }}, timeout=60)
    r.raise_for_status()
    print(f">>> SENT ({who}): {text}")


def wait_for(prefix: str, limit: int = 300) -> str | None:
    deadline = time.time() + limit
    seen = len(inbox)
    while time.time() < deadline:
        for text in inbox[seen:]:
            if prefix in text:
                return text
        seen = len(inbox)
        time.sleep(3)
    return None


before = hashlib.sha256(open(PAGE, "rb").read()).hexdigest()
print(f"page sha before: {before[:16]}")

send("Please add a testimonials section to the landing page with three short "
     "placeholder quotes from customers.")

ask = wait_for("APPROVAL_NEEDED", 300)
if not ask:
    print("\nFAIL: no APPROVAL_NEEDED within 300s")
    print(f"messages received: {inbox}")
    sys.exit(1)

body = ask.split("APPROVAL_NEEDED:", 1)[1]
parts = [p.strip() for p in body.split("|")]
print(f"\n  responsibility: {parts[0]!r}")
print(f"  title:          {parts[1] if len(parts) > 1 else '?'!r}")

mid = hashlib.sha256(open(PAGE, "rb").read()).hexdigest()
if mid != before:
    print("\nFAIL: the agent edited the page BEFORE approval")
    sys.exit(1)
print("  page unchanged before approval: correct")

send("APPROVED by nordice — for: " + (parts[1] if len(parts) > 1 else "the change"),
     "nordice")

done = wait_for("EDIT_DONE", 420)
after = hashlib.sha256(open(PAGE, "rb").read()).hexdigest()
print(f"\npage sha after: {after[:16]}")

if after == before:
    print("FAIL: page never changed after approval")
    print(f"messages: {inbox}")
    sys.exit(1)

print("PASS: page changed only after approval")
print(f"\nEDIT_DONE reported: {done}")
print("\n=== diff ===")
subprocess.run(["git", "-C", "/tmp/site-local", "diff", "--stat"], check=False)
subprocess.run(["git", "-C", "/tmp/site-local", "diff"], check=False)
