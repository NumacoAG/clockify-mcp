"""HTTP-level tests for ClockifyClient using respx to mock httpx."""

from __future__ import annotations

import httpx
import pytest
import respx

from clockify_mcp.client import ClockifyClient
from clockify_mcp.errors import ApiError, AuthError, NotFoundError

API = "https://api.example.test/api/v1"
REPORTS = "https://reports.api.example.test/v1"


@respx.mock
def test_get_current_user(clockify_client: ClockifyClient) -> None:
    respx.get(f"{API}/user").mock(
        return_value=httpx.Response(200, json={"id": "u1", "name": "Test"})
    )
    user = clockify_client.get_current_user()
    assert user["id"] == "u1"


@respx.mock
def test_x_api_key_header_sent(clockify_client: ClockifyClient) -> None:
    route = respx.get(f"{API}/user").mock(return_value=httpx.Response(200, json={"id": "u1"}))
    clockify_client.get_current_user()
    assert route.calls.last.request.headers["X-Api-Key"] == "test-api-key"


@respx.mock
def test_401_raises_auth_error(clockify_client: ClockifyClient) -> None:
    respx.get(f"{API}/user").mock(
        return_value=httpx.Response(401, json={"code": "NO_AUTH", "message": "bad key"})
    )
    with pytest.raises(AuthError) as exc_info:
        clockify_client.get_current_user()
    assert exc_info.value.status == 401
    assert exc_info.value.code == "NO_AUTH"


@respx.mock
def test_404_raises_not_found(clockify_client: ClockifyClient) -> None:
    respx.get(f"{API}/workspaces/ws-1/time-entries/missing").mock(
        return_value=httpx.Response(404, json={"code": "NOT_FOUND", "message": "no such entry"})
    )
    with pytest.raises(NotFoundError):
        clockify_client.get_time_entry("ws-1", "missing")


@respx.mock
def test_list_projects_paginates(clockify_client: ClockifyClient) -> None:
    page1 = [{"id": f"p{i}", "name": f"P{i}"} for i in range(100)]
    page2 = [{"id": "p100", "name": "P100"}]
    route = respx.get(f"{API}/workspaces/ws-1/projects").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]
    )
    projects = clockify_client.list_projects("ws-1")
    assert len(projects) == 101
    assert route.call_count == 2
    page_params = [call.request.url.params.get("page") for call in route.calls]
    assert page_params == ["1", "2"]


@respx.mock
def test_list_projects_max_results_caps(clockify_client: ClockifyClient) -> None:
    big_page = [{"id": f"p{i}", "name": f"P{i}"} for i in range(100)]
    respx.get(f"{API}/workspaces/ws-1/projects").mock(
        return_value=httpx.Response(200, json=big_page),
    )
    # Every page is full, so the loop would run forever — max_results=5000 caps it.
    projects = clockify_client.list_projects("ws-1")
    assert len(projects) == 5000


@respx.mock
def test_add_time_entry_posts_body(clockify_client: ClockifyClient) -> None:
    created = {
        "id": "te-1",
        "projectId": "p-1",
        "description": "Worked on stuff",
        "timeInterval": {"start": "2026-05-13T09:00:00Z", "end": "2026-05-13T10:30:00Z"},
        "billable": True,
    }
    route = respx.post(f"{API}/workspaces/ws-1/time-entries").mock(
        return_value=httpx.Response(201, json=created)
    )
    result = clockify_client.add_time_entry(
        "ws-1",
        start="2026-05-13T09:00:00Z",
        end="2026-05-13T10:30:00Z",
        project_id="p-1",
        description="Worked on stuff",
        billable=True,
    )
    assert result["id"] == "te-1"
    sent = route.calls.last.request.read()
    import json

    body = json.loads(sent)
    assert body == {
        "start": "2026-05-13T09:00:00Z",
        "end": "2026-05-13T10:30:00Z",
        "projectId": "p-1",
        "description": "Worked on stuff",
        "billable": True,
    }


@respx.mock
def test_delete_time_entry_returns_none(clockify_client: ClockifyClient) -> None:
    respx.delete(f"{API}/workspaces/ws-1/time-entries/te-1").mock(return_value=httpx.Response(204))
    assert clockify_client.delete_time_entry("ws-1", "te-1") is None


@respx.mock
def test_report_summary_uses_reports_base(clockify_client: ClockifyClient) -> None:
    route = respx.post(f"{REPORTS}/workspaces/ws-1/reports/summary").mock(
        return_value=httpx.Response(200, json={"totals": [{"totalTime": 7200}]})
    )
    result = clockify_client.report_summary("ws-1", {"dateRangeStart": "2026-05-01T00:00:00Z"})
    assert route.call_count == 1
    assert result["totals"][0]["totalTime"] == 7200


@respx.mock
def test_list_user_time_entries_passes_project_filter(
    clockify_client: ClockifyClient,
) -> None:
    route = respx.get(
        f"{API}/workspaces/ws-1/user/u-1/time-entries",
    ).mock(return_value=httpx.Response(200, json=[]))
    clockify_client.list_user_time_entries(
        "ws-1",
        "u-1",
        start="2026-05-01T00:00:00Z",
        end="2026-05-08T00:00:00Z",
        project_id="p-1",
    )
    params = route.calls.last.request.url.params
    assert params.get("project") == "p-1"
    assert params.get("start") == "2026-05-01T00:00:00Z"
    assert params.get("hydrated") == "true"


@respx.mock
def test_get_running_timer_returns_first_or_none(
    clockify_client: ClockifyClient,
) -> None:
    respx.get(
        f"{API}/workspaces/ws-1/user/u-1/time-entries",
    ).mock(return_value=httpx.Response(200, json=[]))
    assert clockify_client.get_running_timer("ws-1", "u-1") is None

    respx.get(
        f"{API}/workspaces/ws-1/user/u-1/time-entries",
    ).mock(return_value=httpx.Response(200, json=[{"id": "te-running"}]))
    result = clockify_client.get_running_timer("ws-1", "u-1")
    assert result is not None
    assert result["id"] == "te-running"


@respx.mock
def test_retries_on_500_then_succeeds(clockify_client: ClockifyClient) -> None:
    route = respx.get(f"{API}/user").mock(
        side_effect=[
            httpx.Response(500, json={"code": "BOOM", "message": "server"}),
            httpx.Response(200, json={"id": "u1"}),
        ]
    )
    user = clockify_client.get_current_user()
    assert user["id"] == "u1"
    assert route.call_count == 2


@respx.mock
def test_500_exhausts_retries(clockify_client: ClockifyClient) -> None:
    respx.get(f"{API}/user").mock(
        return_value=httpx.Response(500, json={"code": "BOOM", "message": "server"})
    )
    with pytest.raises(ApiError) as exc_info:
        clockify_client.get_current_user()
    assert exc_info.value.status == 500
