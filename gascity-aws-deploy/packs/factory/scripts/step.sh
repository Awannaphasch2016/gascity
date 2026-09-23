#!/usr/bin/env bash
#
# Find, read, and close the formula step an agent is working.
#
#   step.sh claim            claim the step routed to this agent; prints its id
#   step.sh show <id>        print the step's title, status, and instructions
#   step.sh close <id> [note]  close the step as passed
#   step.sh fail <id> [note]   close the step as failed, so the orchestrator
#                              stops the workflow instead of running the next
#                              step on top of broken work
#
# The city runs on the file-backed bead store (GC_BEADS=file), which has no bd
# binary behind it, so `gc bd show` and `gc bd update` are unavailable. Reads
# and writes go through the supervisor's API instead; claiming goes through
# `gc hook`, which runs in process and is the one sanctioned way to find routed
# work (see the core pack's claim protocol).

set -euo pipefail

: "${FACTORY_API:?FACTORY_API is not set; add it to [workspace.env] in city.toml}"

api() {
  local method=$1 path=$2 body=${3:-}
  if [ -n "$body" ]; then
    curl -sS --fail-with-body -X "$method" "$FACTORY_API$path" \
      -H 'Content-Type: application/json' -H 'X-GC-Request: 1' \
      --data-binary "$body"
  else
    curl -sS --fail-with-body -X "$method" "$FACTORY_API$path" -H 'X-GC-Request: 1'
  fi
}

usage() {
  sed -n '3,12p' "$0" >&2
  exit 2
}

cmd=${1:-}
case "$cmd" in
  claim)
    result=$(gc hook --claim --drain-ack --json)
    action=$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("action",""))')
    if [ "$action" != "work" ]; then
      echo "step.sh: no step is routed to this agent ($action)" >&2
      printf '%s\n' "$result" >&2
      exit 1
    fi
    printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bead_id"])'
    ;;
  show)
    id=${2:?usage: step.sh show <id>}
    api GET "/bead/$id" | python3 -c '
import json, sys
bead = json.load(sys.stdin)
meta = bead.get("metadata") or {}
print("id:", bead["id"])
print("title:", bead.get("title", ""))
print("status:", bead.get("status", ""))
print("step:", meta.get("gc.step_ref", ""))
print("workflow:", meta.get("gc.root_bead_id", ""))
print()
print(bead.get("description", ""))
'
    ;;
  close|fail)
    id=${2:?usage: step.sh $cmd <id> [note]}
    note=${3:-}
    outcome=pass
    [ "$cmd" = fail ] && outcome=fail
    body=$(OUTCOME="$outcome" NOTE="$note" python3 -c '
import json, os
body = {"status": "closed", "metadata": {"gc.outcome": os.environ["OUTCOME"]}}
if os.environ["NOTE"]:
    body["metadata"]["factory.note"] = os.environ["NOTE"]
print(json.dumps(body))
')
    api POST "/bead/$id/update" "$body" >/dev/null
    echo "closed $id ($outcome)"
    ;;
  *)
    usage
    ;;
esac
