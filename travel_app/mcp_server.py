"""Stdio MCP server for the decoupled aw-app-travel app.

Ported from agentic-workspace's ``src/mcp/aw_lifestyle.py`` — the flight
search half of it (that file's whole reason to exist). Wraps the ``fli``
library (PyPI distribution name ``flights``, see ``aw-app.json``'s
``pip_requires``) to answer "I have a fixed-ish start/end date, what's the
cheapest combination of departure/return dates nearby?" in a single tool
call, instead of the caller hand-rolling N separate flight searches.

Deliberately imports fli's low-level search API (``fli.core`` / ``fli.models``
/ ``fli.search``) instead of ``fli.mcp.server`` — same reasoning as the
monolith: the low-level API has no fastmcp dependency chain to collide with,
and it's what ``fli.mcp.server`` calls under the hood anyway.

**Not ported**: the monolith's on-completion callback (POST to
agents-platform's ``/api/telegram/inject`` to re-invoke the calling agent, or
a fallback push via awserv's ``/api/meta/push_alert``) — both are
``agentic-workspace``-specific integration points with no equivalent here.
A job's result is available purely by polling: ``search_flight_window_start``
returns a ``job_id`` immediately; call ``search_flight_window_status`` (or
``_wait``, which blocks) with it to read progress/results. See
``skills/aw-travel/SKILL.md`` for the calling convention this implies (a
ScheduleWakeup-style poll loop instead of a fire-and-forget callback).

Run: ``python3 -m travel_app.mcp_server`` (stdio). Registered via this repo's
root ``mcp.json`` — the gateway spawns it with cwd set to the app root.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import date, timedelta

from fli.core import (
    build_flight_segments,
    parse_cabin_class,
    parse_currency,
    parse_max_stops,
    parse_sort_by,
    resolve_airport,
)
from fli.core.parsers import ParseError
from fli.models import FlightSearchFilters, PassengerInfo, TripType
from fli.search import SearchFlights

_MAX_COMBINATIONS_HARD_CAP = 400


def _num(args: dict, key: str, default, cast=int):
    """args.get(key) or default breaks when 0 is a legitimate value — use this instead."""
    value = args.get(key)
    return cast(value) if value is not None else default


def _resolve_airports(codes: str) -> list:
    airports = [resolve_airport(code.strip()) for code in codes.split(",") if code.strip()]
    if not airports:
        raise ParseError(f"No valid airport codes found in: '{codes}'")
    return airports


def _airline_code(airline) -> str:
    return getattr(airline, "name", str(airline)).lstrip("_")


def _iso(value) -> str:
    """Normalize a leg's departure/arrival datetime to an ISO string.

    fli has returned this field as either a `datetime` or an already-formatted
    string across versions/paths — normalize once here instead of assuming.
    """
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _serialize_legs(flight_or_tuple, is_round_trip: bool) -> list[dict]:
    """Flatten a SearchFlights result (single flight or round-trip tuple) to a leg list."""
    segments = list(flight_or_tuple) if isinstance(flight_or_tuple, tuple) else [flight_or_tuple]
    legs = []
    for segment in segments:
        for leg in segment.legs:
            legs.append({
                "departure_time": _iso(leg.departure_datetime),
                "arrival_time": _iso(leg.arrival_datetime),
                "airline": _airline_code(leg.airline),
            })
    return legs


def _price_of(flight_or_tuple) -> tuple[float, str]:
    if isinstance(flight_or_tuple, tuple):
        segments = list(flight_or_tuple)
        priced = segments[0] if len(segments) == 2 else segments[-1]
    else:
        priced = flight_or_tuple
    return priced.price, priced.currency


def _date_window(center_iso: str, delta_days: int) -> list[str]:
    center = date.fromisoformat(center_iso)
    return [(center + timedelta(days=d)).isoformat() for d in range(-delta_days, delta_days + 1)]


def _split_legs(legs: list[dict], return_date: str) -> tuple[list[dict], list[dict]]:
    """Split a round-trip's flat leg list into (outbound, return) by date.

    The MCP's own serializer concatenates outbound + return legs with no
    marker between them. Airport *names* aren't reliable to split on (Google
    returns inconsistent labels for the same airport across legs), but the
    return leg's departure date always falls on/after `return_date` — that's
    guaranteed by construction, so splitting on it is exact.
    """
    split_idx = next(
        (i for i, leg in enumerate(legs) if leg["departure_time"][:10] >= return_date),
        len(legs),
    )
    return legs[:split_idx], legs[split_idx:]


def _leg_span_hours(legs: list[dict]) -> float:
    if not legs:
        return 0.0
    fmt = "%Y-%m-%dT%H:%M:%S"
    from datetime import datetime

    start = datetime.strptime(legs[0]["departure_time"], fmt)
    end = datetime.strptime(legs[-1]["arrival_time"], fmt)
    return round((end - start).total_seconds() / 3600, 1)


def _search_one_combo(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    cabin_class: str,
    max_stops: str,
    passengers: int,
    currency: str | None,
) -> dict | None:
    origins = _resolve_airports(origin)
    destinations = _resolve_airports(destination)
    segments, trip_type = build_flight_segments(
        origin=origins,
        destination=destinations,
        departure_date=departure_date,
        return_date=return_date,
        time_restrictions=None,
    )
    filters = FlightSearchFilters(
        trip_type=trip_type,
        passenger_info=PassengerInfo(adults=passengers),
        flight_segments=segments,
        stops=parse_max_stops(max_stops),
        seat_type=parse_cabin_class(cabin_class),
        sort_by=parse_sort_by("CHEAPEST"),
        show_all_results=False,
    )
    resolved_currency = parse_currency(currency)
    flights = SearchFlights().search(filters, currency=resolved_currency)
    if not flights:
        return None

    cheapest = flights[0]
    is_round_trip = trip_type == TripType.ROUND_TRIP
    legs = _serialize_legs(cheapest, is_round_trip)
    outbound, ret = _split_legs(legs, return_date)
    airlines = sorted({leg["airline"] for leg in legs if leg.get("airline")})
    price, price_currency = _price_of(cheapest)

    return {
        "departure_date": departure_date,
        "return_date": return_date,
        "stay_days": (date.fromisoformat(return_date) - date.fromisoformat(departure_date)).days,
        "price": price,
        "currency": price_currency,
        "airlines": airlines,
        "outbound_stops": max(len(outbound) - 1, 0),
        "outbound_duration_hours": _leg_span_hours(outbound),
        "return_stops": max(len(ret) - 1, 0),
        "return_duration_hours": _leg_span_hours(ret),
    }


# Fire-and-forget job state, same pattern as agents-platform's Run model
# (start -> job_id, status for an instant snapshot, wait to block up to a
# timeout). Terminal statuses: "success" / "cancelled" (with a `reason`, e.g.
# "rate_limited") — non-terminal is "running". This process stays alive for
# the lifetime of the gateway upstream, so an in-memory dict is enough to
# track jobs across separate tool calls — a gateway restart also kills any
# job in flight, so no persistence is needed across one.
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _run_search_job(job_id: str, origin, destination, combos, cabin_class, max_stops,
                     passengers, currency, sleep_seconds: float) -> None:
    job = _JOBS[job_id]
    for i, (dep, ret) in enumerate(combos):
        try:
            r = _search_one_combo(origin, destination, dep, ret, cabin_class, max_stops,
                                   passengers, currency)
        except Exception as e:
            msg = str(e)
            with _JOBS_LOCK:
                job["combinations_tried"] = i + 1
                if "429" in msg or "rate" in msg.lower() or "rate-limit" in msg.lower():
                    job["status"] = "cancelled"
                    job["reason"] = "rate_limited"
                    job["error"] = msg
                    job["stopped_at_combo"] = i
                    return
                job["combinations_failed"] += 1
                job["last_error"] = msg
        else:
            with _JOBS_LOCK:
                job["combinations_tried"] = i + 1
                if r is None:
                    job["combinations_failed"] += 1
                else:
                    job["results"].append(r)
                    job["results"].sort(key=lambda x: x["price"])
        if i < len(combos) - 1:
            time.sleep(sleep_seconds)
    with _JOBS_LOCK:
        if job["status"] == "running":
            job["status"] = "success"


def _search_flight_window_start(args: dict) -> tuple[str, bool]:
    origin = args.get("origin")
    destination = args.get("destination")
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    if not all([origin, destination, start_date, end_date]):
        return "origin, destination, start_date and end_date are required", True

    delta_days = _num(args, "delta_days", 7)
    min_stay_days = _num(args, "min_stay_days", 1)
    cabin_class = args.get("cabin_class") or "ECONOMY"
    max_stops = args.get("max_stops") or "ANY"
    passengers = _num(args, "passengers", 1)
    currency = args.get("currency")
    max_combinations = min(_num(args, "max_combinations", 200), _MAX_COMBINATIONS_HARD_CAP)
    sleep_seconds = _num(args, "sleep_seconds", 10, cast=float)

    try:
        departure_candidates = _date_window(start_date, delta_days)
        return_candidates = _date_window(end_date, delta_days)
    except ValueError as e:
        return f"Invalid date: {e}", True

    combos = [
        (dep, ret)
        for dep in departure_candidates
        for ret in return_candidates
        # discard before ever hitting fli — stay must clear min_stay_days
        if (date.fromisoformat(ret) - date.fromisoformat(dep)).days >= min_stay_days
    ]

    if len(combos) > max_combinations:
        return (
            f"{len(combos)} date combinations would be searched (delta_days={delta_days} on "
            f"both ends), which exceeds max_combinations={max_combinations}. Reduce delta_days, "
            f"or raise max_combinations explicitly (hard cap {_MAX_COMBINATIONS_HARD_CAP})."
        ), True

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "origin": origin,
        "destination": destination,
        "start_date": start_date,
        "end_date": end_date,
        "delta_days": delta_days,
        "min_stay_days": min_stay_days,
        "sleep_seconds": sleep_seconds,
        "status": "running",
        "reason": None,
        "combinations_total": len(combos),
        "combinations_tried": 0,
        "combinations_failed": 0,
        "error": None,
        "stopped_at_combo": None,
        "results": [],
    }
    _JOBS[job_id] = job

    thread = threading.Thread(
        target=_run_search_job,
        args=(job_id, origin, destination, combos, cabin_class, max_stops, passengers,
              currency, sleep_seconds),
        daemon=True,
    )
    thread.start()

    estimated_seconds = len(combos) * sleep_seconds
    payload = {
        "job_id": job_id,
        "combinations_total": len(combos),
        "sleep_seconds": sleep_seconds,
        "estimated_seconds_if_all_run": estimated_seconds,
        "note": (
            "Search is running in the background, one combination at a time, sleeping "
            f"{sleep_seconds}s between each to avoid bursting past Google's rate limit. "
            "It stops immediately (status=cancelled, reason=rate_limited) at the first "
            "429/rate-limit response instead of burning through the rest. There is no "
            "completion callback — call search_flight_window_status or _wait with this "
            "job_id to read progress/results."
        ),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False), False


def _job_snapshot(job_id: str, job: dict) -> dict:
    with _JOBS_LOCK:
        results = list(job["results"])
        return {
            "job_id": job_id,
            "status": job["status"],
            "reason": job["reason"],
            "combinations_total": job["combinations_total"],
            "combinations_tried": job["combinations_tried"],
            "combinations_failed": job["combinations_failed"],
            "stopped_at_combo": job["stopped_at_combo"],
            "error": job["error"],
            "cheapest_so_far": results[0] if results else None,
            "results_so_far": results,
        }


def _search_flight_window_status(args: dict) -> tuple[str, bool]:
    job_id = args.get("job_id")
    if not job_id:
        return "job_id is required", True
    job = _JOBS.get(job_id)
    if job is None:
        return f"Unknown job_id: {job_id}", True
    return json.dumps(_job_snapshot(job_id, job), indent=2, ensure_ascii=False), False


def _search_flight_window_wait(args: dict) -> tuple[str, bool]:
    """Block until the job reaches a terminal status or timeout_s elapses.

    Mirrors agents-platform's wait_run: on timeout, returns the current
    snapshot rather than raising — caller checks `status` to detect.
    """
    job_id = args.get("job_id")
    if not job_id:
        return "job_id is required", True
    job = _JOBS.get(job_id)
    if job is None:
        return f"Unknown job_id: {job_id}", True

    timeout_s = min(_num(args, "timeout_s", 300, cast=float), 1200)
    poll_interval_s = min(max(_num(args, "poll_interval_s", 2, cast=float), 0.25), 30)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with _JOBS_LOCK:
            status = job["status"]
        if status != "running":
            break
        time.sleep(poll_interval_s)

    return json.dumps(_job_snapshot(job_id, job), indent=2, ensure_ascii=False), False


TOOLS_SCHEMA = [
    {
        "name": "search_flight_window_start",
        "description": (
            "Fire-and-forget: given a rough (not necessarily fixed) start_date and "
            "end_date for a trip, kick off a BACKGROUND search across all departure/return "
            "date combinations within +/- delta_days of each (default 7). Combinations "
            "whose stay is shorter than min_stay_days are discarded before ever calling "
            "the flight search. Runs ONE combination at a time (not in parallel), sleeping "
            "sleep_seconds (default 10) between each to stay under Google Flights' rate "
            "limit, and STOPS IMMEDIATELY at the first 429/rate-limit response instead of "
            "burning through the rest. Returns a job_id right away — poll or block with "
            "search_flight_window_status / search_flight_window_wait to read results; "
            "there is no completion callback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Departure airport IATA code(s), comma-separated for multiple (e.g. 'GIG')."},
                "destination": {"type": "string", "description": "Arrival airport IATA code(s), comma-separated for multiple (e.g. 'LIS')."},
                "start_date": {"type": "string", "description": "Anchor departure date, YYYY-MM-DD. Actual searches span +/- delta_days around it."},
                "end_date": {"type": "string", "description": "Anchor return date, YYYY-MM-DD. Actual searches span +/- delta_days around it."},
                "delta_days": {"type": "integer", "description": "Window (in days) searched on both sides of start_date and end_date. Default 7."},
                "min_stay_days": {"type": "integer", "description": "Minimum nights/days between departure and return. Combinations below this are skipped before searching. Default 1 (just needs a positive stay)."},
                "sleep_seconds": {"type": "number", "description": "Seconds to sleep between each sequential search. Default 10."},
                "cabin_class": {"type": "string", "description": "ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST. Default ECONOMY."},
                "max_stops": {"type": "string", "description": "ANY, NON_STOP, ONE_STOP, or TWO_PLUS_STOPS. Default ANY."},
                "passengers": {"type": "integer", "description": "Number of adult passengers. Default 1."},
                "currency": {"type": "string", "description": "ISO 4217 currency code (e.g. 'EUR', 'BRL'). Omit to let Google pick."},
                "max_combinations": {"type": "integer", "description": f"Safety cap on total combinations searched. Default 200, hard cap {_MAX_COMBINATIONS_HARD_CAP}."},
            },
            "required": ["origin", "destination", "start_date", "end_date"],
        },
    },
    {
        "name": "search_flight_window_status",
        "description": (
            "Instant, non-blocking snapshot of a background job started by "
            "search_flight_window_start — same pattern as agents-platform's run_status. "
            "Returns status (running / success / cancelled — with `reason` set e.g. "
            "'rate_limited' when cancelled), progress counters, and the cheapest-first "
            "results found so far. Safe to call repeatedly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "job_id returned by search_flight_window_start."},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "search_flight_window_wait",
        "description": (
            "Block until a job started by search_flight_window_start reaches a terminal "
            "status (success / cancelled) or timeout_s elapses — same pattern as "
            "agents-platform's wait_run. On timeout, returns the current snapshot without "
            "erroring; check `status` to tell timeout from completion. Eliminates the "
            "polling loop for short-ish jobs; for longer ones (many combinations, long "
            "sleep_seconds) prefer repeated search_flight_window_status calls instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "job_id returned by search_flight_window_start."},
                "timeout_s": {"type": "integer", "description": "Max seconds to block. Default 300, hard cap 1200."},
                "poll_interval_s": {"type": "number", "description": "Internal poll cadence in seconds. Default 2."},
            },
            "required": ["job_id"],
        },
    },
]

_DISPATCH = {
    "search_flight_window_start": _search_flight_window_start,
    "search_flight_window_status": _search_flight_window_status,
    "search_flight_window_wait": _search_flight_window_wait,
}


def _tool_result(req_id, text: str, is_error: bool) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    }


def handle_request(request: dict) -> dict | None:
    method = request.get("method")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aw-travel", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS_SCHEMA},
        }

    if method == "tools/call":
        params = request.get("params") or {}
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        handler = _DISPATCH.get(tool_name)
        if not handler:
            return _tool_result(req_id, f"Unknown tool: {tool_name}", True)
        text, is_error = handler(tool_args)
        return _tool_result(req_id, text, is_error)

    return None


def main() -> None:
    import sys
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
