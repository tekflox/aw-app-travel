"""Unit tests for travel_app/mcp_server.py's pure helpers and dispatch — no
network calls, no fli/Google Flights calls, no running workspace."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from travel_app import mcp_server  # noqa: E402


def test_date_window_spans_both_sides():
    window = mcp_server._date_window("2026-09-10", 2)
    assert window == ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12"]


def test_split_legs_by_return_date():
    legs = [
        {"departure_time": "2026-09-10T08:00:00", "arrival_time": "2026-09-10T11:00:00"},
        {"departure_time": "2026-09-17T09:00:00", "arrival_time": "2026-09-17T12:00:00"},
    ]
    outbound, ret = mcp_server._split_legs(legs, "2026-09-17")
    assert len(outbound) == 1 and len(ret) == 1


def test_leg_span_hours():
    legs = [
        {"departure_time": "2026-09-10T08:00:00", "arrival_time": "2026-09-10T09:30:00"},
        {"departure_time": "2026-09-10T11:00:00", "arrival_time": "2026-09-10T13:00:00"},
    ]
    assert mcp_server._leg_span_hours(legs) == 5.0


def test_leg_span_hours_empty():
    assert mcp_server._leg_span_hours([]) == 0.0


def test_num_zero_is_not_default():
    assert mcp_server._num({"passengers": 0}, "passengers", 1) == 0


def test_num_missing_uses_default():
    assert mcp_server._num({}, "passengers", 1) == 1


def test_start_requires_all_fields():
    text, is_error = mcp_server._search_flight_window_start({"origin": "GIG"})
    assert is_error is True
    assert "required" in text


def test_start_rejects_invalid_date():
    text, is_error = mcp_server._search_flight_window_start({
        "origin": "GIG", "destination": "LIS",
        "start_date": "not-a-date", "end_date": "2026-09-17",
    })
    assert is_error is True
    assert "Invalid date" in text


def test_start_enforces_max_combinations():
    text, is_error = mcp_server._search_flight_window_start({
        "origin": "GIG", "destination": "LIS",
        "start_date": "2026-09-10", "end_date": "2026-09-17",
        "delta_days": 10, "min_stay_days": 1, "max_combinations": 5,
    })
    assert is_error is True
    assert "exceeds max_combinations" in text


def test_status_requires_job_id():
    text, is_error = mcp_server._search_flight_window_status({})
    assert is_error is True
    assert "required" in text


def test_status_unknown_job_id():
    text, is_error = mcp_server._search_flight_window_status({"job_id": "nope"})
    assert is_error is True
    assert "Unknown job_id" in text


def test_wait_unknown_job_id():
    text, is_error = mcp_server._search_flight_window_wait({"job_id": "nope"})
    assert is_error is True
    assert "Unknown job_id" in text


def test_job_snapshot_reports_cheapest_first():
    # _job_snapshot trusts `results` is already cheapest-first — the job
    # runner keeps it sorted on every append (mcp_server._run_search_job).
    job = {
        "status": "success", "reason": None,
        "combinations_total": 2, "combinations_tried": 2, "combinations_failed": 0,
        "stopped_at_combo": None, "error": None,
        "results": [
            {"price": 300, "departure_date": "2026-09-10"},
            {"price": 500, "departure_date": "2026-09-11"},
        ],
    }
    snapshot = mcp_server._job_snapshot("job1", job)
    assert snapshot["cheapest_so_far"]["price"] == 300


def test_tools_list_exposes_all_three_tools():
    result = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in result["result"]["tools"]]
    assert names == [
        "search_flight_window_start",
        "search_flight_window_status",
        "search_flight_window_wait",
    ]


def test_unknown_tool_call_is_an_error():
    result = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    })
    assert result["result"]["isError"] is True


def test_initialize_reports_server_name():
    result = mcp_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert result["result"]["serverInfo"]["name"] == "aw-travel"
