# aw-app-travel

Cheapest-date-window flight search for every agent in this workspace: give a
rough departure/return date and it fans a background search across every
nearby date combination to find the cheapest fare.

Ports agentic-workspace's `aw-lifestyle` MCP server (`src/mcp/aw_lifestyle.py`)
— the flight-search half of it, which was the whole file's reason to exist.
3 tools, gateway-prefixed `aw__travel__*`, backed by the
[`fli`](https://github.com/punitarani/fli) library (PyPI distribution name
`flights`): direct Google Flights API access, no scraping, no browser.

| Monolith | This app |
|---|---|
| `agentic-workspace/src/mcp/aw_lifestyle.py` (stdio MCP, hand-rolled JSON-RPC) | `travel_app/mcp_server.py` — same search logic, relocated into the app's own package |
| `src/config/mcp.json`'s `aw-lifestyle` entry | this repo's root `mcp.json` — a static, committed file (no secret, no per-install host/port to bake in) |
| *(no dedicated skill in the monolith)* | `skills/aw-travel/SKILL.md` — new, teaches an agent the start/status/wait calling convention |

## What's different from the monolith

**No completion callback.** The monolith's job, on finishing, called back
into the Telegram-calling agent via agents-platform's
`/api/telegram/inject` (or fell back to a raw phone push through awserv's
`/api/meta/push_alert`) — both are `agentic-workspace`-specific integration
points with no equivalent in aw-workspace. This app drops `bot_id`/`chat_id`
entirely: `search_flight_window_start` returns a `job_id`, and the caller is
expected to poll `search_flight_window_status` or block on
`search_flight_window_wait` — see the skill for the recommended pattern
(`ScheduleWakeup` for a long search instead of blocking a chat turn).

## Why no self-registration, no HTTP, no Settings page

Same reasoning as `aw-app-weather`: `fli` talks straight to Google's own
(reverse-engineered) API, no key, no account, nothing to configure. So this
follows the plainer `aw-app-code-server` / `aw-app-mcp-tools` pattern: a
static `mcp.json` committed to the repo, spawned by `aw-mcp-gateway` as a
stdio subprocess with `cwd` set to the installed app directory. The Tier-1
plugin (`travel_app/plugin.py`) has nothing to register through `ctx` — no
routes, no CLIs, no config.

## Install

```bash
aw-workspace-cli marketplace install travel
```

All three tools work immediately, no Settings step.

## Tools

| Tool | Purpose |
|---|---|
| `search_flight_window_start(origin, destination, start_date, end_date, ...)` | Kick off a background search across a date window, returns `job_id`. |
| `search_flight_window_status(job_id)` | Instant snapshot: status, progress, cheapest-so-far. |
| `search_flight_window_wait(job_id, timeout_s=300)` | Block until the job finishes or the timeout hits. |

See `skills/aw-travel/SKILL.md` for the full calling convention, parameter
reference, and rate-limit behavior.

## Tests

```bash
python3 tests/validate_manifest.py aw-app.json
python3 -m pytest tests -q
```
