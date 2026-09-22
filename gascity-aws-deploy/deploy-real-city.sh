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
# --break-system-packages only exists on pip >= 23; this host predates it.
# jq is a hard gc init dependency; the agent's reply script needs curl and
# python3; tmux is the session runtime; git lets the agent's edits be diffable.
"${SSH[@]}" 'sudo apt-get update -qq && sudo apt-get install -y -qq tmux python3-pip curl jq git >/dev/null
pip3 install --quiet --break-system-packages flask requests 2>/dev/null \
  || pip3 install --quiet flask requests
python3 -c "import flask, requests; print(\"python deps ok\")"'

echo "==> stopping the Flask mock"
# The mock owns port 7375 and would answer routes the bridge no longer calls.
# The bracket in [s]imple_gc_api keeps the pattern from matching pkill's own
# command line, which otherwise kills this very SSH shell and ends the deploy
# silently. sudo because the mock was started as root; || true because pkill
# exits 1 when nothing matches, the normal case on a rerun.
"${SSH[@]}" 'sudo pkill -f "[s]imple_gc_api" || true; sudo pkill -f "[t]elegram_bot.py" || true; echo "mock stopped"'

echo "==> stopping any previous bridge"
# Telegram allows one getUpdates consumer per bot token, so a stray bridge left
# over from an earlier deploy steals updates from the new one at random. Stop the
# service first, otherwise systemd restarts what pkill just killed.
"${SSH[@]}" 'systemctl --user stop factory-bridge 2>/dev/null || true
pkill -f "[b]ridge.py" || true
echo "bridge stopped"'

echo "==> uploading gc binary"
# Built from this repo rather than fetched: there is no published release for
# this fork, and the raw-CDN path served stale files during earlier deploys.
# CGO_ENABLED=0 is required, not a preference. A default build links ICU
# dynamically and the target host runs Ubuntu 22.04 (libicu70) while a current
# build host has libicu74, so the binary dies on startup with
# "libicui18n.so.74: cannot open shared object file".
if [ ! -x ./gc ]; then
  echo "error: ./gc not found. Build it first:" >&2
  echo "  CGO_ENABLED=0 go build -o gascity-aws-deploy/gc ./cmd/gc" >&2
  exit 1
fi
if ldd ./gc >/dev/null 2>&1; then
  echo "error: ./gc is dynamically linked and will not start on the target host." >&2
  echo "  Rebuild with: CGO_ENABLED=0 go build -o gascity-aws-deploy/gc ./cmd/gc" >&2
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

# Seed the page the agent edits, but never overwrite it on a re-deploy: the
# agent's approved edits live in this file and are the point of the exercise.
# Delete it on the host to reset to the baseline deliberately.
echo "==> seeding the landing page if absent"
scp "${SSH_OPTS[@]}" ./site/index.html "ubuntu@${HOST}:/tmp/index.html.baseline"
"${SSH[@]}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
sudo mkdir -p ${REMOTE}/site
if [ -s ${REMOTE}/site/index.html ] && grep -q 'Northwind' ${REMOTE}/site/index.html; then
  echo "page already seeded; leaving the agent's edits in place"
else
  sudo cp -f /tmp/index.html.baseline ${REMOTE}/site/index.html
  echo "page seeded from baseline"
fi
sudo chown -R ubuntu:ubuntu ${REMOTE}/site
# git makes the agent's edits diffable, which is how you audit what it changed.
if [ ! -d ${REMOTE}/site/.git ]; then
  cd ${REMOTE}/site
  git init -q
  git config user.email factory@localhost
  git config user.name "Gas City factory"
  git add -A && git commit -qm "baseline landing page"
  echo "git initialized for the page"
fi
REMOTE_SCRIPT

# Previously configured by hand on the host, which left the page's only public
# surface out of the repo and carried a dead alias to a docker-era status
# directory that does not exist here.
echo "==> serving the page"
scp "${SSH_OPTS[@]}" ./nginx/factory-page.conf "ubuntu@${HOST}:/tmp/factory-page.conf"
"${SSH[@]}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
sudo apt-get install -y -qq nginx >/dev/null
sudo mv -f /tmp/factory-page.conf /etc/nginx/sites-available/factory-page
sudo ln -sfn /etc/nginx/sites-available/factory-page /etc/nginx/sites-enabled/factory-page
# The earlier hand-written site claims port 8080 too; two servers on one port
# makes which root wins depend on config load order.
sudo rm -f /etc/nginx/sites-enabled/miniapp
sudo nginx -t
sudo systemctl reload nginx
echo "page served on 8080 from ${REMOTE}/site"
REMOTE_SCRIPT

echo "==> writing environment"
"${SSH[@]}" "cat > ${REMOTE}/factory.env" <<ENV_FILE
CURSOR_API_KEY=${CURSOR_API_KEY}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_BOT_TOKEN_NORDICE=${TELEGRAM_BOT_TOKEN_NORDICE}
# The host has no bd binary, so use the file-backed bead store rather than
# bootstrapping Dolt on a 2-vCPU box.
GC_BEADS=file
GC_CITY_NAME=factory
GC_ACCOUNT_ID=factory
GC_CONVERSATION_ID=landing-page
CONFIG_PATH=${REMOTE}/config/responsibilities.json
BRIDGE_CALLBACK_URL=http://127.0.0.1:8081
BRIDGE_PORT=8081
PAGE_URL=http://${HOST}:8080/
ENV_FILE
"${SSH[@]}" "chmod 600 ${REMOTE}/factory.env"

# gc installs itself as a systemd --user service, which inherits nothing from
# this SSH session. Without this drop-in, CURSOR_API_KEY never reaches the
# agents the supervisor spawns and every one of them boots to a login prompt.
# A drop-in rather than an edit, because gc regenerates the unit on install.
echo "==> giving the supervisor service its environment"
"${SSH[@]}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
dropin="\$HOME/.config/systemd/user/gascity-supervisor.service.d"
mkdir -p "\$dropin"
cat > "\$dropin/factory.conf" <<UNIT
[Service]
EnvironmentFile=${REMOTE}/factory.env
UNIT
systemctl --user daemon-reload 2>/dev/null || true
echo "drop-in installed"
REMOTE_SCRIPT

echo "==> starting the city"
"${SSH[@]}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
export PATH="\$HOME/.local/bin:\$PATH"
set -a; . ${REMOTE}/factory.env; set +a
cd ${REMOTE}/city

# --preserve-existing keeps the committed city.toml and prompt template.
# gc doctor --fix is deliberately not used: it rewrites city.toml and drops
# [[agent]] blocks.
# --name factory is load-bearing: the runtime city name otherwise comes from the
# directory basename ("city"), and city.toml's GC_REPLY_URL addresses the city by
# name, so a mismatch makes every agent reply 404.
if [ ! -d .gc ]; then
  ${REMOTE}/gc init --file city.toml --preserve-existing --no-start --name factory .
fi

# Restart the service so it re-reads the drop-in. A supervisor already running
# from a previous deploy still holds the old, key-less environment.
systemctl --user restart gascity-supervisor 2>/dev/null || true
sleep 5

${REMOTE}/gc start
${REMOTE}/gc status
REMOTE_SCRIPT

echo "==> resolving the supervisor API port"
API_PORT=$("${SSH[@]}" "grep -oE 'Dashboard: *http://127.0.0.1:[0-9]+' ~/.gc/supervisor.log | tail -1 | grep -oE '[0-9]+$'" || true)
API_PORT="${API_PORT:-8372}"
echo "    supervisor API on port ${API_PORT}"

echo "==> verifying the transport"
"${SSH[@]}" "cd ${REMOTE} && python3 test_extmsg_protocol.py --api http://127.0.0.1:${API_PORT} --city factory --wait 25"

# A user service rather than nohup. A backgrounded process started over SSH is
# a child of the login session and dies with it, which is invisible until an
# approval goes unanswered: the controller keeps routing turns and the callbacks
# have nowhere to land. systemd also restarts it after a crash, and -u keeps
# python from buffering the log that is the only way to debug delivery.
echo "==> installing the bridge service"
"${SSH[@]}" bash -s <<REMOTE_SCRIPT
set -euo pipefail
unitdir="\$HOME/.config/systemd/user"
mkdir -p "\$unitdir"
cat > "\$unitdir/factory-bridge.service" <<UNIT
[Unit]
Description=Telegram bridge for the Gas City approval factory
After=gascity-supervisor.service
Wants=gascity-supervisor.service

[Service]
Type=simple
WorkingDirectory=${REMOTE}/bot
EnvironmentFile=${REMOTE}/factory.env
Environment=GC_API=http://127.0.0.1:${API_PORT}
ExecStart=/usr/bin/python3 -u ${REMOTE}/bot/bridge.py
Restart=always
RestartSec=5
StandardOutput=append:${REMOTE}/bridge.log
StandardError=append:${REMOTE}/bridge.log

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now factory-bridge
sleep 6
systemctl --user is-active factory-bridge
curl -sS -m 10 http://127.0.0.1:8081/health; echo
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
