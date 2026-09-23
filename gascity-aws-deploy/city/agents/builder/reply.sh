#!/usr/bin/env bash
#
# Publish an agent's reply back to the bound external conversation.
#
# Reads the message body on stdin:
#
#   echo "APPROVAL_NEEDED: ..." | "$GC_CITY"/agents/builder/reply.sh
#
# This exists because the system reminder Gas City appends to a delivered
# inbound message tells the agent to run
#
#   gc <provider> reply-current --conversation-id <id> --body-file <path>
#
# and no such command exists for any provider (internal/api/handler_extmsg.go
# interpolates the provider name into a command name that was never built). An
# agent following that instruction searches for the command, fails, and its
# reply is lost — the conversation simply goes quiet. The working path is the
# outbound endpoint this script posts to.

set -euo pipefail

: "${GC_SESSION_ID:?GC_SESSION_ID is not set; this must run inside a gc session}"
: "${GC_REPLY_URL:?GC_REPLY_URL is not set; add it to [workspace.env] in city.toml}"
: "${GC_CONVERSATION_ID:?GC_CONVERSATION_ID is not set; add it to [workspace.env]}"
: "${GC_ACCOUNT_ID:?GC_ACCOUNT_ID is not set; add it to [workspace.env]}"
: "${GC_PROVIDER_NAME:?GC_PROVIDER_NAME is not set; add it to [workspace.env]}"

body=$(cat)
if [ -z "${body//[[:space:]]/}" ]; then
  echo "reply.sh: refusing to publish an empty message" >&2
  exit 1
fi

payload=$(
  BODY="$body" \
  SESSION_ID="$GC_SESSION_ID" \
  PROVIDER="$GC_PROVIDER_NAME" \
  ACCOUNT="$GC_ACCOUNT_ID" \
  CONVERSATION="$GC_CONVERSATION_ID" \
  python3 -c '
import json, os
print(json.dumps({
    "session_id": os.environ["SESSION_ID"],
    "text": os.environ["BODY"],
    "conversation": {
        "provider": os.environ["PROVIDER"],
        "account_id": os.environ["ACCOUNT"],
        "conversation_id": os.environ["CONVERSATION"],
        "scope_id": "city",
        "kind": "dm",
    },
}))'
)

# --fail-with-body so a rejected publish surfaces the reason instead of exiting
# 0 with an error document the agent never sees.
curl -sS --fail-with-body -X POST "$GC_REPLY_URL" \
  -H 'Content-Type: application/json' \
  -H 'X-GC-Request: 1' \
  --data-binary "$payload"
