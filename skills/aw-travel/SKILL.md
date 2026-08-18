---
name: aw-travel
description: Cheapest-date-window flight search — given a rough departure/return date, fans a background search across nearby date combinations to find the cheapest fare via the aw-travel MCP server contributed by aw-app-travel. Use whenever the user asks to find/compare flights across a range of dates, or wants the cheapest combination of departure/return dates for a trip.
---

# aw-travel — cheapest-window flight search

Three tools, gateway-prefixed `aw__travel__*`. Ported from
agentic-workspace's `src/mcp/aw_lifestyle.py` (2026-08-18) — same search
logic and result shape, direct Google Flights API access via the `fli`
library (no scraping, no browser). **Not ported**: the monolith's
on-completion Telegram callback — see "No completion callback" below.

## Which tool to call, in what order

1. **`search_flight_window_start`** — kicks off a background job, returns a
   `job_id` immediately. Required: `origin`, `destination` (IATA codes,
   comma-separated for multiple), `start_date`, `end_date` (anchor dates,
   `YYYY-MM-DD`). The actual search spans `delta_days` (default 7) on both
   sides of each anchor — e.g. `start_date=2026-09-10, delta_days=7` searches
   departures from 09-03 to 09-17.
2. **`search_flight_window_status`** — instant, non-blocking snapshot: given
   `job_id`, returns `status` (`running` / `success` / `cancelled`),
   progress counters, and cheapest-first results found so far. Safe to call
   repeatedly.
3. **`search_flight_window_wait`** — blocks (up to `timeout_s`, default 300,
   hard cap 1200) until the job finishes or the timeout hits, then returns
   the same snapshot shape as `_status`. Prefer this over polling `_status`
   in a loop for a search you expect to finish in a couple of minutes.

## No completion callback — you must check back yourself

The monolith version posted back to the calling Telegram agent when a job
finished (`agents-platform`'s `/api/telegram/inject`). This app has no
equivalent wired up: once you call `search_flight_window_start`, nothing
will re-invoke you. If the job will run long (many combinations, or the
default `sleep_seconds=10` between each), don't block synchronously — use
`ScheduleWakeup` (or your platform's equivalent) to come back and call
`search_flight_window_status`/`_wait` yourself, or call `_wait` once with a
generous `timeout_s` if you're fine blocking the turn.

## Reading the estimate before you wait

`search_flight_window_start`'s response includes
`estimated_seconds_if_all_run` (`combinations_total * sleep_seconds`). Use
it to decide whether to `_wait` inline or schedule a check-back — a job with
50+ combinations at the default 10s sleep is ~8+ minutes, too long to block
a chat turn on.

## Combination budget

Every combination of (departure date × return date) within the two
`delta_days` windows that clears `min_stay_days` gets searched — that's
`(2·delta_days+1)²` before the stay filter. `max_combinations` (default 200,
hard cap 400) rejects the call up front with a clear count instead of
silently truncating; if you hit it, narrow `delta_days` or raise
`min_stay_days` rather than raising the cap blindly.

## Rate-limit behavior

The job runs combinations sequentially (never in parallel) and stops
immediately at the first 429/rate-limit response — `status` becomes
`cancelled`, `reason` becomes `"rate_limited"`, and whatever results were
already found are still in `results_so_far`. Don't retry the same window
immediately; space out re-attempts.

## Other parameters

`cabin_class` (ECONOMY/PREMIUM_ECONOMY/BUSINESS/FIRST, default ECONOMY),
`max_stops` (ANY/NON_STOP/ONE_STOP/TWO_PLUS_STOPS, default ANY),
`passengers` (default 1), `currency` (ISO 4217, e.g. `EUR`/`BRL` — omit to
let Google pick).

## Presenting results

`results_so_far` (and `cheapest_so_far`) are sorted cheapest-first, each
with `departure_date`, `return_date`, `stay_days`, `price`, `currency`,
`airlines`, and per-leg stop count/duration. When reporting back to the
user, lead with the cheapest option and note how many combinations were
actually tried versus the total.
