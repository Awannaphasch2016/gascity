#!/usr/bin/env bash
#
# Publish a factory agent's message to its project's conversation.
#
# Reads the message on stdin:
#
#   printf 'QUESTIONS: requirements\n1. ...\n' | "$FACTORY_HOME"/scripts/reply.sh
#
# The conversation is the project: one rig is one project, and the rig name
# the controller exports as GC_RIG is the conversation id the bridge registered
# for it. The bridge fans the message out to every roster member's topic and
# routes QUESTIONS: and APPROVAL_NEEDED: lines to whoever holds the named
# responsibility.
#
# Several agents speak in one project over its life — the discoverer, then the
# builder and tester, then the deliverer — and Gas City lets only the session
# bound to a conversation publish into it. So before publishing, this script
# rebinds the conversation to the calling session (a handoff, `replace`). That
# is also what makes the human's answer land with the agent that asked: an
# inbound turn is delivered to whoever holds the binding.
#
# This deliberately ignores GC_CONVERSATION_ID: the city sets that for its own
# landing-page agent, and a factory agent inheriting it would post project
# traffic into the wrong conversation.

set -euo pipefail

: "${GC_SESSION_ID:?GC_SESSION_ID is not set; this must run inside a gc session}"
: "${GC_RIG:?GC_RIG is not set; factory agents run inside a rig}"
: "${GC_REPLY_URL:?GC_REPLY_URL is not set; add it to [workspace.env] in city.toml}"
: "${GC_ACCOUNT_ID:?GC_ACCOUNT_ID is not set; add it to [workspace.env]}"
: "${GC_PROVIDER_NAME:?GC_PROVIDER_NAME is not set; add it to [workspace.env]}"

body=$(cat)
if [ -z "${body//[[:space:]]/}" ]; then
  echo "reply.sh: refusing to publish an empty message" >&2
  exit 1
fi

# GC_REPLY_URL ends in /extmsg/outbound; bind lives next to it.
bind_url="${GC_REPLY_URL%/outbound}/bind"

conversation=$(
  PROVIDER="$GC_PROVIDER_NAME" ACCOUNT="$GC_ACCOUNT_ID" CONVERSATION="$GC_RIG" \
  python3 -c '
import json, os
print(json.dumps({
    "provider": os.environ["PROVIDER"],
    "account_id": os.environ["ACCOUNT"],
    "conversation_id": os.environ["CONVERSATION"],
    "scope_id": "city",
    "kind": "room",
}))'
)

post() {
  # --fail-with-body so a rejected call surfaces the reason instead of exiting
  # 0 with an error document the agent never sees.
  curl -sS --fail-with-body -X POST "$1" \
    -H 'Content-Type: application/json' \
    -H 'X-GC-Request: 1' \
    --data-binary "$2"
}

post "$bind_url" "$(
  CONV="$conversation" SESSION_ID="$GC_SESSION_ID" python3 -c '
import json, os
print(json.dumps({
    "conversation": json.loads(os.environ["CONV"]),
    "session_id": os.environ["SESSION_ID"],
    "replace": True,
}))'
)" >/dev/null

post "$GC_REPLY_URL" "$(
  CONV="$conversation" SESSION_ID="$GC_SESSION_ID" BODY="$body" python3 -c '
import json, os
print(json.dumps({
    "session_id": os.environ["SESSION_ID"],
    "text": os.environ["BODY"],
    "conversation": json.loads(os.environ["CONV"]),
}))'
)"
