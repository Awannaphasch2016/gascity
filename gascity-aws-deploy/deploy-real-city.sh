#!/usr/bin/env bash
#
# Deploy the real Gas City landing-page factory to the EC2 host.
#
# Replaces the Flask mock with the actual gc controller, a cursor-agent-backed
# builder agent, and the extmsg bridge. Run from gascity-aws-deploy/.
#
#   ./deploy-real-city.sh <host> [ssh-key]
#
# CURSOR_API_KEY must be exported, or the agent will boot to a browser login
# prompt and never reply. Every other layer works without it, which is why this
# script checks for it up front instead of letting the failure surface later as
# silence in Telegram.

set -euo pipefail

HOST="${1:?usage: deploy-real-city.sh <host> [ssh-key]}"
KEY="${2:-gascity-key.pem}"
REMOTE="/opt/gascity"
SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=no)
SSH=(ssh "${SSH_OPTS[@]}" "ubuntu@${HOST}")

for var in CURSOR_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_BOT_TOKEN_NORDICE; do
  if [ -z "${!var:-}" ]; then
    echo "error: $var is not set. Export it (or source it from Doppler) first." >&2
    exit 1
  fi
done

if [ ! -f "$KEY" ]; then
  echo "error: ssh key $KEY not found" >&2
  exit 1
fi
chmod 600 "$KEY"

echo "==> checking connectivity"
"${SSH[@]}" 'echo connected as $(whoami) on $(hostname)'

echo "==> installing cursor-agent"
"${SSH[@]}" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
if ! command -v cursor-agent >/dev/null 2>&1; then
  curl -fsS https://cursor.com/install | bash
fi
export PATH="$HOME/.local/bin:$PATH"
cursor-agent --version
REMOTE_SCRIPT

echo "==> installing tmux and python deps"
"${SSH[@]}" 'sudo apt-get update -qq && sudo apt-get install -y -qq tmux python3-pip >/dev/null && pip3 install --quiet --break-system-packages flask requests'

echo "==> stopping the Flask mock"
# The mock owns port 7375 and would answer routes the bridge no longer calls.
"${SSH[@]}" 'pkill -f simple_gc_api || true; pkill -f telegram_bot.py || true'

echo "==> uploading gc binary"
# Built from this repo rather than fetched: there is no published release for
# this fork, and the raw-CDN path served stale files during earlier deploys.
if [ ! -x ./gc ]; then
  echo "error: ./gc not found. Build it first: go build -o gascity-aws-deploy/gc ./cmd/gc" >&2
  exit 1
fi
scp "${SSH_OPTS[@]}" ./gc "ubuntu@${HOST}:/tmp/gc"
"${SSH[@]}" "sudo mv -f /tmp/gc ${REMOTE}/gc && sudo chmod +x ${REMOTE}/gc"

echo "==> uploading city config and bridge"
"${SSH[@]}" "sudo mkdir -p ${REMOTE}/city ${REMOTE}/config ${REMOTE}/bot && sudo chown -R ubuntu:ubuntu ${REMOTE}"
scp "${SSH_OPTS[@]}" -r ./city/. "ubuntu@${HOST}:${REMOTE}/city/"
scp "${SSH_OPTS[@]}" ./config/responsibilities.json "ubuntu@${HOST}:${REMOTE}/config/"
scp "${SSH_OPTS[@]}" ./bot/bridge.py ./bot/requirements.txt "ubuntu@${HOST}:${REMOTE}/bot/"
scp "${SSH_OPTS[@]}" ./test_extmsg_protocol.py "ubuntu@${HOST}:${REMOTE}/"

echo "==> writing environment"
"${SSH[@]}" "cat > ${REMOTE}/factory.env" <<ENV_FILE
CURSOR_API_KEY=${CURSOR_API_KEY}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_BOT_TOKEN_NORDICE=${TELEGRAM_BOT_TOKEN_NORDICE}
GC_CITY_NAME=factory
GC_ACCOUNT_ID=factory
GC_CONVERSATION_ID=landing-page
CONFIG_PATH=${REMOTE}/config/responsibilities.json
BRIDGE_CALLBACK_URL=http://127.0.0.1:8081
BRIDGE_PORT=8081
PAGE_URL=http://${HOST}:8080/
ENV_FILE
"${SSH[@]}" "chmod 600 ${REMOTE}/factory.env"

echo "==> starting the city"
"${SSH[@]}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
export PATH="\$HOME/.local/bin:\$PATH"
set -a; . ${REMOTE}/factory.env; set +a
cd ${REMOTE}/city

# --preserve-existing keeps the committed city.toml and prompt template.
# gc doctor --fix is deliberately not used: it rewrites city.toml and drops
# [[agent]] blocks.
if [ ! -d .gc ]; then
  ${REMOTE}/gc init --file city.toml --preserve-existing --no-start .
fi
${REMOTE}/gc start
${REMOTE}/gc status
REMOTE_SCRIPT

echo "==> resolving the supervisor API port"
API_PORT=$("${SSH[@]}" "grep -oE 'Dashboard: *http://127.0.0.1:[0-9]+' ~/.gc/supervisor.log | tail -1 | grep -oE '[0-9]+$'" || true)
API_PORT="${API_PORT:-8372}"
echo "    supervisor API on port ${API_PORT}"

echo "==> verifying the transport"
"${SSH[@]}" "cd ${REMOTE} && python3 test_extmsg_protocol.py --api http://127.0.0.1:${API_PORT} --city factory --wait 25"

echo "==> starting the bridge"
"${SSH[@]}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
set -a; . ${REMOTE}/factory.env; set +a
export GC_API=http://127.0.0.1:${API_PORT}
cd ${REMOTE}/bot
nohup python3 bridge.py > ${REMOTE}/bridge.log 2>&1 &
sleep 5
tail -20 ${REMOTE}/bridge.log
REMOTE_SCRIPT

cat <<DONE

Deployed. Message either bot and ask for a change to the page, for example:

  "Add a testimonials section with three placeholder quotes"   -> architecture_review
  "Add a newsletter signup box to the footer"                  -> security_review
  "Publish the page"                                           -> deployment_approval (needs both)

The page is at http://${HOST}:8080/

If a request routes but nothing comes back, check the agent's pane:
  ssh -i ${KEY} ubuntu@${HOST} 'tmux -L factory capture-pane -p -t builder'
DONE
