# Landing-page approval factory

A Gas City city where an agent edits a live web page, but only after the person
responsible for that kind of change approves it in Telegram.

Nothing about which section changes, or what the edit is, is wired in advance.
The agent reads the page, decides what the request implies, names the
responsibility that must sign off, and waits. `agents/builder/prompt.template.md`
holds all of that reasoning. The Go code and this config carry transport only.

## Pieces

| Piece | Role |
|---|---|
| `city.toml` | Declares the cursor-backed `builder` agent, its named session, and the route from Telegram to it |
| `agents/builder/prompt.template.md` | The approval protocol and the rules for choosing a responsibility |
| `../bot/bridge.py` | Carries turns between Telegram and the city; resolves a responsibility to people |
| `../config/responsibilities.json` | Who holds which responsibility, and how many must agree |
| `../site/index.html` | The page the agent edits; served as-is from the agent's work_dir |
| `../nginx/factory-page.conf` | Serves that page on port 8080 |
| `../test_extmsg_protocol.py` | Checks the transport without Telegram or an agent |

## The page is a plain page, not a Telegram Mini App

An earlier plan called for a Mini App, and the leftover name misled readers into
looking for one. There is none: the page is static HTML served over plain HTTP,
linked from Telegram as an ordinary URL, and opened in whatever browser the
phone hands it to.

That is the better fit for what the page is for. A Mini App has to be served
over HTTPS and is only reachable from inside Telegram, whereas a public URL is
stronger evidence that the agent changed something real. The cost is that the
page is anonymous and world-readable, so nothing user-specific can go on it.

A Mini App would earn its place at the approval step rather than here. Telegram
signs an `initData` payload identifying the viewer, so a Mini App could show the
diff, or a rendered before/after, and know which approvals belong to whoever is
looking — replacing a two-button keyboard and a one-line description with
something you can actually review before deciding. That needs TLS, `initData`
verification in the bridge, and an endpoint exposing pending approvals per
verified user. None of it is built.

## What has to be true before it works

`cursor-agent` must be authenticated. Gas City drives an existing coding-agent
CLI and ships no model of its own; without a credential the agent session boots
and stops at a browser login prompt, the transport delivers the turn, and no
reply ever comes. Set `CURSOR_API_KEY` in the environment the controller
inherits.

Cursor is a first-class provider (`cursor-agent -f --trust`, `CURSOR_API_KEY`,
`AGENTS.md`), but it is absent from the readiness probe `gc init` uses, so the
wizard will not offer it and startup logs
`provider-health registry unavailable for "cursor"; treating as green`. Declare
it directly instead, as `city.toml` does.

## Bringing it up

```bash
# Bootstrap the runtime without discarding the committed config. Do NOT use
# `gc doctor --fix` here: it rewrites city.toml and drops [[agent]] blocks.
gc init --file city.toml --preserve-existing --no-start .
gc start

# Confirm the transport before involving Telegram. Prints the agent the default
# route resolved to and whether the binding and transcript persisted.
python3 ../test_extmsg_protocol.py --city <city-name>
```

Then start the bridge, pointing it at the supervisor's listener:

```bash
export GC_API=http://127.0.0.1:8372      # supervisor port, NOT city.toml's [api] port
export GC_CITY_NAME=<city-name>
export BRIDGE_CALLBACK_URL=http://127.0.0.1:8081
export CONFIG_PATH=../config/responsibilities.json
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_BOT_TOKEN_NORDICE=...
export PAGE_URL=http://<host>:8080/      # optional, linked from EDIT_DONE
python3 ../bot/bridge.py
```

`GC_API` is the detail most likely to waste time. The extmsg routes are served
on the supervisor listener that `gc start` prints, not on the port in
`city.toml`'s `[api]` block.

## What a run looks like

Someone messages a bot: "add a testimonials section". The bridge posts it as an
inbound turn; the default route binds the conversation to `builder` and the
binding persists as a bead, so it survives a restart. The agent reads
`index.html`, decides this is visible content, and replies:

```
APPROVAL_NEEDED: architecture_review | Testimonials section | Add a three-column block below features with placeholder quotes
```

The bridge resolves `architecture_review` to whoever holds it and sends that
person Approve and Reject buttons. On approve it posts `APPROVED by <name>` back
into the same conversation. The agent edits `index.html` and replies
`EDIT_DONE: ...`. Reload the page to see it.

Ask instead for a newsletter signup box and the agent should route it to
`security_review` — a different person — because it adds a form. That choice is
the agent's, made from the prompt's criteria and the actual request.

`deployment_approval` needs two people, so both must press Approve before the
agent is told.

## When something does not happen

Work outward from the agent:

```bash
gc status                                    # is the named session awake?
tmux -L <city-name> capture-pane -p -t builder   # what is the agent showing?
tail -f ~/.gc/supervisor.log | grep extmsg   # did the turn route?
curl -s http://127.0.0.1:8081/health         # is the bridge holding open approvals?
```

A turn that routes but draws no reply almost always means the agent is stopped
at a login prompt, which the pane capture shows directly.

`ConversationKind` is a closed enum — `dm`, `room`, `thread`. Any other value
returns 422 on inbound and 500 on the transcript read.
