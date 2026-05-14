"""Tests for the Reports API body builders + shaping helpers."""

from __future__ import annotations

from clockify_mcp.reports import (
    build_detailed_body,
    build_summary_body,
    seconds_to_hms,
    total_seconds_from_entries,
)


def test_summary_body_minimal() -> None:
    body = build_summary_body(start="2026-05-01T00:00:00Z", end="2026-05-08T00:00:00Z")
    assert body["dateRangeStart"] == "2026-05-01T00:00:00Z"
    assert body["dateRangeEnd"] == "2026-05-08T00:00:00Z"
    assert body["summaryFilter"] == {"groups": ["PROJECT"]}
    assert body["exportType"] == "JSON"
    assert "projects" not in body
    assert "users" not in body


def test_summary_body_with_filters() -> None:
    body = build_summary_body(
        start="2026-05-01T00:00:00Z",
        end="2026-05-08T00:00:00Z",
        project_ids=["proj-1", "proj-2"],
        user_ids=["user-1"],
        tag_ids=["tag-1"],
        client_ids=["client-1"],
        billable=True,
        group_by="DAY",
    )
    assert body["summaryFilter"] == {"groups": ["DAY"]}
    assert body["projects"] == {
        "ids": ["proj-1", "proj-2"],
        "contains": "CONTAINS",
        "status": "ALL",
    }
    assert body["users"] == {"ids": ["user-1"], "contains": "CONTAINS", "status": "ALL"}
    assert body["tags"] == {"ids": ["tag-1"], "contains": "CONTAINS", "status": "ALL"}
    assert body["clients"] == {
        "ids": ["client-1"],
        "contains": "CONTAINS",
        "status": "ALL",
    }
    assert body["billable"] is True


def test_detailed_body_paging() -> None:
    body = build_detailed_body(
        start="2026-05-01T00:00:00Z",
        end="2026-05-08T00:00:00Z",
        page=2,
        page_size=50,
    )
    assert body["detailedFilter"] == {"page": 2, "pageSize": 50, "sortColumn": "DATE"}


def test_seconds_to_hms_zero() -> None:
    assert seconds_to_hms(0) == "0s"


def test_seconds_to_hms_seconds_only() -> None:
    assert seconds_to_hms(45) == "45s"


def test_seconds_to_hms_minutes() -> None:
    assert seconds_to_hms(120) == "2m00s"


def test_seconds_to_hms_hours_and_minutes() -> None:
    assert seconds_to_hms(3 * 3600 + 25 * 60) == "3h25m"


def test_seconds_to_hms_negative() -> None:
    assert seconds_to_hms(-65) == "-1m05s"


def test_total_seconds_from_entries() -> None:
    entries = [
        {
            "timeInterval": {
                "start": "2026-05-13T09:00:00Z",
                "end": "2026-05-13T10:30:00Z",
            }
        },
        {
            "timeInterval": {
                "start": "2026-05-13T14:00:00Z",
                "end": "2026-05-13T16:00:00Z",
            }
        },
    ]
    # 1h30m + 2h00m = 12600 seconds
    assert total_seconds_from_entries(entries) == 12600


def test_total_seconds_ignores_running_entry() -> None:
    entries = [
        {"timeInterval": {"start": "2026-05-13T09:00:00Z", "end": None}},
        {
            "timeInterval": {
                "start": "2026-05-13T10:00:00Z",
                "end": "2026-05-13T11:00:00Z",
            }
        },
    ]
    assert total_seconds_from_entries(entries) == 3600


def test_total_seconds_handles_offset_format() -> None:
    entries = [
        {
            "timeInterval": {
                "start": "2026-05-13T09:00:00+02:00",
                "end": "2026-05-13T10:00:00+02:00",
            }
        }
    ]
    assert total_seconds_from_entries(entries) == 3600
