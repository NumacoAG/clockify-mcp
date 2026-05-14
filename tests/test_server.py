"""Tests for the MCP tool functions in server.py.

These talk to a MagicMock ClockifyClient — they verify the tool-layer behaviour:
input parsing, defaulting (workspace_id / user_id from /user), shaping the result,
and routing to the right client method.
"""

from __future__ import annotations

from typing import Any

import pytest

from clockify_mcp import server
from clockify_mcp.errors import ValidationError


@pytest.fixture
def fake_entry() -> dict[str, Any]:
    return {
        "id": "te-1",
        "description": "Coding session",
        "timeInterval": {
            "start": "2026-05-13T09:00:00Z",
            "end": "2026-05-13T10:30:00Z",
        },
        "projectId": "p-1",
        "project": {"name": "Demo Project"},
        "taskId": None,
        "task": None,
        "billable": True,
        "tagIds": ["t-1"],
    }


# ---------- whoami / list_workspaces ----------


def test_whoami(mocked_state: server._State) -> None:
    result = server.whoami()
    assert result == {
        "id": "user-1",
        "name": "Test User",
        "email": "test@example.com",
        "default_workspace_id": "ws-1",
        "active_workspace_id": "ws-1",
        "timezone": "Europe/Zurich",
    }


def test_list_workspaces(mocked_state: server._State) -> None:
    mocked_state.client.list_workspaces.return_value = [  # type: ignore[attr-defined]
        {"id": "ws-1", "name": "Acme Workspace", "hourlyRate": {"amount": 0, "currency": "USD"}},
        {"id": "ws-2", "name": "Personal"},
    ]
    result = server.list_workspaces()
    assert len(result) == 2
    assert result[0]["id"] == "ws-1"
    assert result[0]["name"] == "Acme Workspace"
    assert result[0]["hourly_rate"] == {"amount": 0, "currency": "USD"}


# ---------- list_projects (with cache) ----------


def test_list_projects_uses_active_workspace(mocked_state: server._State) -> None:
    mocked_state.client.list_projects.return_value = [  # type: ignore[attr-defined]
        {
            "id": "p-1",
            "name": "P1",
            "billable": True,
            "archived": False,
            "timeEstimate": {"estimate": "PT40H", "type": "MANUAL"},
        },
        {"id": "p-2", "name": "P2", "billable": False, "archived": False},
    ]
    result = server.list_projects()
    mocked_state.client.list_projects.assert_called_once()  # type: ignore[attr-defined]
    args, kwargs = mocked_state.client.list_projects.call_args  # type: ignore[attr-defined]
    assert args == ("ws-1",)
    assert kwargs == {"name": None, "archived": False}
    assert result[0]["estimate_seconds"] == 40 * 3600
    assert result[0]["estimate_type"] == "MANUAL"
    assert result[1]["estimate_seconds"] is None
    assert result[1]["estimate_type"] is None


def test_list_projects_caches_results(mocked_state: server._State) -> None:
    mocked_state.client.list_projects.return_value = []  # type: ignore[attr-defined]
    server.list_projects()
    server.list_projects()
    assert mocked_state.client.list_projects.call_count == 1  # type: ignore[attr-defined]


def test_list_projects_skips_cache_when_including_archived(
    mocked_state: server._State,
) -> None:
    mocked_state.client.list_projects.return_value = []  # type: ignore[attr-defined]
    server.list_projects(include_archived=True)
    server.list_projects(include_archived=True)
    assert mocked_state.client.list_projects.call_count == 2  # type: ignore[attr-defined]


# ---------- list_time_entries ----------


def test_list_time_entries_parses_and_totals(
    mocked_state: server._State, fake_entry: dict[str, Any]
) -> None:
    mocked_state.client.list_user_time_entries.return_value = [fake_entry]  # type: ignore[attr-defined]
    result = server.list_time_entries(
        start="2026-05-13",
        end="2026-05-14",
        project_id="p-1",
    )
    assert result["count"] == 1
    assert result["total_hours"] == 1.5
    assert result["total_duration"] == "1h30m"
    entry = result["entries"][0]
    assert entry["id"] == "te-1"
    assert entry["project_name"] == "Demo Project"
    assert entry["duration_seconds"] == 5400

    # Verify the start / end got normalized to UTC Z format, in user's Zurich tz
    _args, kwargs = mocked_state.client.list_user_time_entries.call_args  # type: ignore[attr-defined]
    assert kwargs["start"] == "2026-05-12T22:00:00Z"  # midnight Zurich -> 22:00 UTC prev day
    assert kwargs["end"] == "2026-05-13T22:00:00Z"
    assert kwargs["project_id"] == "p-1"
    assert kwargs["hydrated"] is True


# ---------- add_time_entry ----------


def test_add_time_entry_normalizes_times_and_routes(
    mocked_state: server._State, fake_entry: dict[str, Any]
) -> None:
    mocked_state.client.add_time_entry.return_value = fake_entry  # type: ignore[attr-defined]
    result = server.add_time_entry(
        start="2026-05-13T11:00:00+02:00",
        end="2026-05-13T12:30:00+02:00",
        project_id="p-1",
        description="Coding session",
    )
    assert result["id"] == "te-1"
    _args, kwargs = mocked_state.client.add_time_entry.call_args  # type: ignore[attr-defined]
    assert kwargs["start"] == "2026-05-13T09:00:00Z"
    assert kwargs["end"] == "2026-05-13T10:30:00Z"
    assert kwargs["project_id"] == "p-1"


def test_add_time_entry_rejects_end_before_start(
    mocked_state: server._State,
) -> None:
    with pytest.raises(ValidationError):
        server.add_time_entry(
            start="2026-05-13T10:00:00Z",
            end="2026-05-13T09:00:00Z",
        )


def test_add_time_entry_rejects_zero_duration(
    mocked_state: server._State,
) -> None:
    with pytest.raises(ValidationError):
        server.add_time_entry(
            start="2026-05-13T10:00:00Z",
            end="2026-05-13T10:00:00Z",
        )


# ---------- update_time_entry (one-sided change pulls existing) ----------


def test_update_time_entry_fetches_existing_when_only_start_passed(
    mocked_state: server._State, fake_entry: dict[str, Any]
) -> None:
    mocked_state.client.get_time_entry.return_value = fake_entry  # type: ignore[attr-defined]
    mocked_state.client.update_time_entry.return_value = fake_entry  # type: ignore[attr-defined]
    server.update_time_entry(entry_id="te-1", start="2026-05-13T08:00:00Z")
    _args, kwargs = mocked_state.client.update_time_entry.call_args  # type: ignore[attr-defined]
    assert kwargs["start"] == "2026-05-13T08:00:00Z"
    # end should have been backfilled from the existing entry
    assert kwargs["end"] == "2026-05-13T10:30:00Z"


# ---------- delete_time_entry ----------


def test_delete_time_entry(mocked_state: server._State) -> None:
    result = server.delete_time_entry(entry_id="te-1")
    mocked_state.client.delete_time_entry.assert_called_once_with("ws-1", "te-1")  # type: ignore[attr-defined]
    assert result == {"status": "deleted", "entry_id": "te-1"}


# ---------- report_summary ----------


def test_report_summary_only_me_injects_user_id(mocked_state: server._State) -> None:
    mocked_state.client.report_summary.return_value = {  # type: ignore[attr-defined]
        "totals": [{"totalTime": 9000}],
        "groupOne": [
            {"_id": "p-1", "name": "Demo", "duration": 9000, "amount": 0},
        ],
    }
    result = server.report_summary(
        start="2026-05-01",
        end="2026-05-08",
        project_ids=["p-1"],
        only_me=True,
    )
    _args, _kwargs = mocked_state.client.report_summary.call_args  # type: ignore[attr-defined]
    body = mocked_state.client.report_summary.call_args[0][1]  # type: ignore[attr-defined]
    assert body["users"] == {
        "ids": ["user-1"],
        "contains": "CONTAINS",
        "status": "ALL",
    }
    assert body["projects"] == {
        "ids": ["p-1"],
        "contains": "CONTAINS",
        "status": "ALL",
    }
    assert body["summaryFilter"] == {"groups": ["PROJECT"]}
    assert result["total_hours"] == 2.5
    assert result["groups"][0]["name"] == "Demo"
    assert result["groups"][0]["hours"] == 2.5


def test_report_summary_rejects_invalid_group_by(mocked_state: server._State) -> None:
    with pytest.raises(ValidationError):
        server.report_summary(
            start="2026-05-01",
            end="2026-05-08",
            group_by="NONSENSE",
        )


# ---------- report_detailed ----------


def test_report_detailed_shapes_entries(mocked_state: server._State) -> None:
    mocked_state.client.report_detailed.return_value = {  # type: ignore[attr-defined]
        "totals": [{"totalTime": 5400}],
        "timeentries": [
            {
                "_id": "te-1",
                "description": "Work",
                "projectId": "p-1",
                "projectName": "Demo",
                "userId": "user-1",
                "userName": "Test User",
                "billable": True,
                "timeInterval": {
                    "start": "2026-05-13T09:00:00Z",
                    "end": "2026-05-13T10:30:00Z",
                    "duration": 5400,
                },
            }
        ],
    }
    result = server.report_detailed(
        start="2026-05-01",
        end="2026-05-08",
        project_ids=["p-1"],
    )
    assert result["count"] == 1
    assert result["total_hours"] == 1.5
    assert result["entries"][0]["project_name"] == "Demo"
    assert result["entries"][0]["duration_seconds"] == 5400


# ---------- get_running_timer / stop_running_timer ----------


def test_get_running_timer_returns_none(mocked_state: server._State) -> None:
    mocked_state.client.get_running_timer.return_value = None  # type: ignore[attr-defined]
    assert server.get_running_timer() is None


def test_get_running_timer_shapes(mocked_state: server._State, fake_entry: dict[str, Any]) -> None:
    running = dict(fake_entry)
    running["timeInterval"] = {"start": "2026-05-13T09:00:00Z", "end": None}
    mocked_state.client.get_running_timer.return_value = running  # type: ignore[attr-defined]
    result = server.get_running_timer()
    assert result is not None
    assert result["id"] == "te-1"
    assert result["end"] is None
    assert result["duration_seconds"] == 0


def test_stop_running_timer_uses_now_by_default(
    mocked_state: server._State, fake_entry: dict[str, Any]
) -> None:
    mocked_state.client.stop_running_timer.return_value = fake_entry  # type: ignore[attr-defined]
    server.stop_running_timer()
    args, _kwargs = mocked_state.client.stop_running_timer.call_args  # type: ignore[attr-defined]
    # Third positional is the `end` timestamp; just check it's UTC Z format
    assert args[2].endswith("Z")
