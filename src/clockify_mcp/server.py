"""FastMCP server: exposes Clockify operations as MCP tools.

Tool surface intentionally splits "listing" into two flavours because the API forces it:

  - `list_time_entries` (main API, single-user, single-project filter — fast, raw entries)
  - `report_summary` and `report_detailed` (Reports API, multi-project / multi-user,
    aggregated totals or hydrated detailed entries — the right tool for "how many hours
    on project X between A and B").
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import reports
from .config import Settings
from .errors import ValidationError
from .state import RequestState, get_state, set_state
from .time_parsing import format_iso_z, parse_to_utc

logger = logging.getLogger(__name__)

# `streamable_http_path="/"` so when we Mount this app at /mcp in http_app.py,
# the external path `/mcp/` reaches FastMCP's root (the JSON-RPC handler).
# Default would be /mcp internally → external /mcp/mcp, which is ugly and breaks
# clients that follow the standard `/mcp` URL.
#
# `transport_security=...(enable_dns_rebinding_protection=False)` because FastMCP
# auto-enables Host-header validation when its `host` setting is 127.0.0.1/localhost
# (its default), which would 421 every request whose Host doesn't match. Behind
# Cloud Run we receive the public hostname, and Google's frontend has already
# validated TLS + Host; rebinding protection at our layer is redundant and
# breaks the flow.
mcp: FastMCP = FastMCP(
    "clockify",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    # Stateless: every HTTP request is independent — no session IDs, no resumption.
    # Crucial on Cloud Run because scale-to-zero kills any in-memory session state;
    # clients that hold a session ID across container restarts would see their next
    # request fail with "client has been closed". With stateless_http, there's
    # nothing to lose between restarts.
    stateless_http=True,
)


# Backward-compatible aliases for tests (and for stdio mode bootstrap).
_State = RequestState


def _get_state() -> RequestState:
    """Stdio-mode bootstrap: lazily install a process-wide state if none is set yet."""
    try:
        return get_state()
    except RuntimeError:
        state = RequestState(Settings.load())
        set_state(state)
        return state


def _set_state_for_tests(state: RequestState | None) -> None:
    """Test hook: install (or clear) the current state."""
    set_state(state)


# ---------- shaping helpers ----------


def _shape_entry(e: dict[str, Any]) -> dict[str, Any]:
    """Reduce a hydrated Clockify time entry to a flat dict the model can reason over."""
    interval = e.get("timeInterval") or {}
    project = e.get("project") or {}
    task = e.get("task") or {}
    start = interval.get("start") if isinstance(interval, dict) else None
    end = interval.get("end") if isinstance(interval, dict) else None
    duration_s = 0
    if isinstance(start, str) and isinstance(end, str):
        try:
            s_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            e_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            duration_s = int((e_dt - s_dt).total_seconds())
        except ValueError:
            duration_s = 0
    return {
        "id": e.get("id"),
        "description": e.get("description"),
        "start": start,
        "end": end,
        "duration_seconds": duration_s,
        "duration": reports.seconds_to_hms(duration_s) if duration_s > 0 else None,
        "project_id": e.get("projectId"),
        "project_name": project.get("name") if isinstance(project, dict) else None,
        "task_id": e.get("taskId"),
        "task_name": task.get("name") if isinstance(task, dict) else None,
        "billable": e.get("billable"),
        "tag_ids": e.get("tagIds") or [],
    }


def _shape_summary(response: dict[str, Any], group_by: str) -> dict[str, Any]:
    totals = response.get("totals") or []
    total_s = 0
    if totals and isinstance(totals[0], dict):
        total_s = int(totals[0].get("totalTime") or 0)
    groups: list[dict[str, Any]] = []
    raw_groups = response.get("groupOne") or []
    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        s = int(g.get("duration") or 0)
        groups.append(
            {
                "id": g.get("_id"),
                "name": g.get("name"),
                "duration_seconds": s,
                "hours": round(s / 3600, 2),
                "duration": reports.seconds_to_hms(s),
                "amount": g.get("amount"),
            }
        )
    return {
        "group_by": group_by,
        "total_hours": round(total_s / 3600, 2),
        "total_duration": reports.seconds_to_hms(total_s),
        "groups": groups,
    }


def _shape_detailed_entry(e: dict[str, Any]) -> dict[str, Any]:
    interval = e.get("timeInterval") or {}
    return {
        "id": e.get("_id") or e.get("id"),
        "description": e.get("description"),
        "start": interval.get("start") if isinstance(interval, dict) else None,
        "end": interval.get("end") if isinstance(interval, dict) else None,
        "duration_seconds": int(
            (interval.get("duration") if isinstance(interval, dict) else 0) or 0
        ),
        "project_id": e.get("projectId"),
        "project_name": e.get("projectName"),
        "user_id": e.get("userId"),
        "user_name": e.get("userName"),
        "task_id": e.get("taskId"),
        "task_name": e.get("taskName"),
        "billable": e.get("billable"),
        "tags": e.get("tags"),
    }


# ---------- tools ----------


@mcp.tool()
def whoami() -> dict[str, Any]:
    """Validate the API key and return the current user identity and default workspace.

    Returns: {id, email, name, default_workspace_id, active_workspace_id, timezone}.
    """
    state = _get_state()
    user = state.get_user()
    tz = None
    if isinstance(user.get("settings"), dict):
        tz = user["settings"].get("timeZone")
    return {
        "id": user.get("id"),
        "email": user.get("email"),
        "name": user.get("name"),
        "default_workspace_id": user.get("defaultWorkspace"),
        "active_workspace_id": user.get("activeWorkspace"),
        "timezone": tz,
    }


@mcp.tool()
def list_workspaces() -> list[dict[str, Any]]:
    """List every workspace this user belongs to. Returns id, name, hourly_rate."""
    state = _get_state()
    return [
        {
            "id": w.get("id"),
            "name": w.get("name"),
            "hourly_rate": w.get("hourlyRate"),
        }
        for w in state.client.list_workspaces()
        if isinstance(w, dict)
    ]


@mcp.tool()
def list_projects(
    workspace_id: str | None = None,
    name_filter: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """List projects on a workspace.

    Args:
        workspace_id: Defaults to user's active workspace.
        name_filter: Substring match against project name.
        include_archived: When False (default) only active projects are returned.
    """
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    cache_key = (wid, name_filter or "")
    use_cache = not include_archived
    if use_cache:
        cached = state.projects.get(cache_key)
        if cached is not None:
            return _shape_projects(cached)
    projects = state.client.list_projects(
        wid,
        name=name_filter,
        archived=None if include_archived else False,
    )
    if use_cache:
        state.projects.set(cache_key, projects)
    return _shape_projects(projects)


def _shape_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "client_id": p.get("clientId"),
                "client_name": p.get("clientName"),
                "color": p.get("color"),
                "billable": p.get("billable"),
                "archived": p.get("archived"),
                "estimate_seconds": _project_estimate_seconds(p),
                "estimate_type": _project_estimate_type(p),
            }
        )
    return out


_ISO_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _project_estimate_seconds(project: dict[str, Any]) -> int | None:
    """Return the project's time estimate in seconds, or None if no estimate is set."""
    raw = project.get("timeEstimate") or project.get("estimate")
    if isinstance(raw, dict):
        raw = raw.get("estimate")
    if not isinstance(raw, str):
        return None
    m = _ISO_DURATION.match(raw)
    if not m:
        return None
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    total = h * 3600 + mi * 60 + s
    return total if total > 0 else None


def _project_estimate_type(project: dict[str, Any]) -> str | None:
    raw = project.get("timeEstimate") or project.get("estimate")
    if isinstance(raw, dict):
        t = raw.get("type")
        return t if isinstance(t, str) else None
    return None


@mcp.tool()
def list_tasks(project_id: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
    """List tasks inside a project. Tasks are sub-buckets used for finer-grained tracking."""
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    tasks = state.client.list_tasks(wid, project_id)
    return [
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "status": t.get("status"),
            "project_id": t.get("projectId"),
        }
        for t in tasks
        if isinstance(t, dict)
    ]


@mcp.tool()
def list_tags(workspace_id: str | None = None) -> list[dict[str, Any]]:
    """List tags on a workspace. Cached for `cache_ttl_seconds`."""
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    cached = state.tags.get(wid)
    if cached is None:
        cached = state.client.list_tags(wid)
        state.tags.set(wid, cached)
    return [
        {"id": t.get("id"), "name": t.get("name"), "archived": t.get("archived")}
        for t in cached
        if isinstance(t, dict)
    ]


@mcp.tool()
def list_time_entries(
    start: str,
    end: str,
    project_id: str | None = None,
    description_contains: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    max_results: int = 5000,
) -> dict[str, Any]:
    """List time entries for a user within a date range. Auto-paginates and totals.

    Args:
        start: Range start. ISO-8601 ("2026-05-01T00:00:00Z") or natural ("yesterday 00:00", "2026-05-01").
        end: Range end. Same formats.
        project_id: Optional filter to a single project. For multi-project queries use `report_detailed`.
        description_contains: Optional substring filter on the entry description.
        workspace_id: Defaults to user's active workspace.
        user_id: Defaults to the authenticated user.
        max_results: Safety cap (default 5000).

    Returns: {entries: [...], count, total_hours, total_duration}.
    """
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    uid = state.resolve_user_id(user_id)
    tz = state.resolve_user_tz()
    start_utc = format_iso_z(parse_to_utc(start, tz))
    end_utc = format_iso_z(parse_to_utc(end, tz))
    entries = state.client.list_user_time_entries(
        wid,
        uid,
        start=start_utc,
        end=end_utc,
        project_id=project_id,
        description=description_contains,
        hydrated=True,
        max_results=max_results,
    )
    shaped = [_shape_entry(e) for e in entries if isinstance(e, dict)]
    total_s = reports.total_seconds_from_entries([e for e in entries if isinstance(e, dict)])
    return {
        "count": len(shaped),
        "total_hours": round(total_s / 3600, 2),
        "total_duration": reports.seconds_to_hms(total_s),
        "entries": shaped,
    }


@mcp.tool()
def add_time_entry(
    start: str,
    end: str,
    project_id: str | None = None,
    description: str | None = None,
    task_id: str | None = None,
    billable: bool | None = None,
    tag_ids: list[str] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Create a new time entry.

    Args:
        start: Entry start time. ISO-8601 or natural ("today 09:00", "yesterday 14:30", "2h ago").
        end: Entry end time. Same formats.
        project_id: Recommended. Omit for unassigned entries.
        description: What you worked on.
        task_id: Optional sub-task within the project.
        billable: Optional override; defaults to the project's billable flag.
        tag_ids: Optional list of tag IDs.
        workspace_id: Defaults to user's active workspace.
    """
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    tz = state.resolve_user_tz()
    start_utc = format_iso_z(parse_to_utc(start, tz))
    end_utc = format_iso_z(parse_to_utc(end, tz))
    if end_utc <= start_utc:
        raise ValidationError(f"end ({end_utc}) must be after start ({start_utc})")
    result = state.client.add_time_entry(
        wid,
        start=start_utc,
        end=end_utc,
        project_id=project_id,
        task_id=task_id,
        description=description,
        billable=billable,
        tag_ids=tag_ids,
    )
    return _shape_entry(result)


@mcp.tool()
def update_time_entry(
    entry_id: str,
    start: str | None = None,
    end: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    description: str | None = None,
    billable: bool | None = None,
    tag_ids: list[str] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Update fields on an existing time entry. Pass only what you want to change.

    Note: Clockify's PUT replaces the resource — when you pass `start` or `end`, you must
    re-supply *both* if the entry is "complete" (has an end). This tool handles that for
    you by fetching the entry first when you change only one of the two boundaries.
    """
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    tz = state.resolve_user_tz()
    start_utc = format_iso_z(parse_to_utc(start, tz)) if start else None
    end_utc = format_iso_z(parse_to_utc(end, tz)) if end else None
    if (start_utc and not end_utc) or (end_utc and not start_utc):
        existing = state.client.get_time_entry(wid, entry_id)
        interval = existing.get("timeInterval") or {}
        if start_utc is None:
            start_utc = interval.get("start") if isinstance(interval, dict) else None
        if end_utc is None:
            end_utc = interval.get("end") if isinstance(interval, dict) else None
    result = state.client.update_time_entry(
        wid,
        entry_id,
        start=start_utc,
        end=end_utc,
        project_id=project_id,
        task_id=task_id,
        description=description,
        billable=billable,
        tag_ids=tag_ids,
    )
    return _shape_entry(result)


@mcp.tool()
def delete_time_entry(entry_id: str, workspace_id: str | None = None) -> dict[str, str]:
    """Delete a time entry. Returns {status: 'deleted', entry_id}."""
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    state.client.delete_time_entry(wid, entry_id)
    return {"status": "deleted", "entry_id": entry_id}


@mcp.tool()
def get_running_timer(
    workspace_id: str | None = None, user_id: str | None = None
) -> dict[str, Any] | None:
    """Return the user's currently running time entry, or None if no timer is running."""
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    uid = state.resolve_user_id(user_id)
    timer = state.client.get_running_timer(wid, uid)
    return _shape_entry(timer) if timer else None


@mcp.tool()
def stop_running_timer(
    end: str = "now",
    workspace_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Stop the currently running timer. `end` defaults to "now"."""
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    uid = state.resolve_user_id(user_id)
    tz = state.resolve_user_tz()
    end_utc = format_iso_z(parse_to_utc(end, tz))
    result = state.client.stop_running_timer(wid, uid, end_utc)
    return _shape_entry(result)


@mcp.tool()
def report_summary(
    start: str,
    end: str,
    project_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    client_ids: list[str] | None = None,
    billable: bool | None = None,
    group_by: str = "PROJECT",
    only_me: bool = False,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Aggregated totals from the Reports API. The right tool for "how many hours…" questions.

    Examples:
        # "How many hours did I log on project X between A and B?"
        report_summary(start=A, end=B, project_ids=[X], only_me=True)

        # "Hours per project this month, just me"
        report_summary(start="2026-05-01", end="2026-06-01", group_by="PROJECT", only_me=True)

        # "Hours per day on project X last week"
        report_summary(start=..., end=..., project_ids=[X], group_by="DAY")

    Args:
        start, end: Date range. ISO-8601 or natural.
        project_ids: Restrict to these projects. Pass an empty list / omit for all.
        user_ids: Restrict to these users.
        only_me: Convenience flag — when True, restricts to the authenticated user.
        tag_ids, client_ids: Further restrictions.
        billable: Restrict to billable / non-billable.
        group_by: One of PROJECT, TASK, USER, DAY, WEEK, MONTH, TAG, CLIENT. Default PROJECT.
        workspace_id: Defaults to user's active workspace.

    Returns: {group_by, total_hours, total_duration, groups: [{id, name, hours, duration, amount}]}.
    """
    if group_by not in reports.VALID_GROUP_BY:
        raise ValidationError(
            f"group_by must be one of {sorted(reports.VALID_GROUP_BY)}; got {group_by!r}"
        )
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    tz = state.resolve_user_tz()
    start_utc = format_iso_z(parse_to_utc(start, tz))
    end_utc = format_iso_z(parse_to_utc(end, tz))
    effective_user_ids = list(user_ids) if user_ids else None
    if only_me:
        me = state.resolve_user_id(None)
        if effective_user_ids is None:
            effective_user_ids = [me]
        elif me not in effective_user_ids:
            effective_user_ids.append(me)
    body = reports.build_summary_body(
        start=start_utc,
        end=end_utc,
        project_ids=project_ids,
        user_ids=effective_user_ids,
        tag_ids=tag_ids,
        client_ids=client_ids,
        billable=billable,
        group_by=group_by,
    )
    return _shape_summary(state.client.report_summary(wid, body), group_by)


@mcp.tool()
def report_detailed(
    start: str,
    end: str,
    project_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    client_ids: list[str] | None = None,
    billable: bool | None = None,
    only_me: bool = False,
    page: int = 1,
    page_size: int = 200,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Hydrated raw entries from the Reports API — multi-project and multi-user capable.

    Use when `list_time_entries` is too limited (which is single-user, single-project).

    Returns: {page, page_size, count, has_more, total_hours, total_duration, entries: [...]}.
    """
    state = _get_state()
    wid = state.resolve_workspace_id(workspace_id)
    tz = state.resolve_user_tz()
    start_utc = format_iso_z(parse_to_utc(start, tz))
    end_utc = format_iso_z(parse_to_utc(end, tz))
    effective_user_ids = list(user_ids) if user_ids else None
    if only_me:
        me = state.resolve_user_id(None)
        if effective_user_ids is None:
            effective_user_ids = [me]
        elif me not in effective_user_ids:
            effective_user_ids.append(me)
    body = reports.build_detailed_body(
        start=start_utc,
        end=end_utc,
        project_ids=project_ids,
        user_ids=effective_user_ids,
        tag_ids=tag_ids,
        client_ids=client_ids,
        billable=billable,
        page=page,
        page_size=page_size,
    )
    response = state.client.report_detailed(wid, body)
    raw_entries = response.get("timeentries") or []
    entries = [_shape_detailed_entry(e) for e in raw_entries if isinstance(e, dict)]
    totals = response.get("totals") or []
    total_s = 0
    if totals and isinstance(totals[0], dict):
        total_s = int(totals[0].get("totalTime") or 0)
    return {
        "page": page,
        "page_size": page_size,
        "count": len(entries),
        "has_more": len(raw_entries) >= page_size,
        "total_hours": round(total_s / 3600, 2),
        "total_duration": reports.seconds_to_hms(total_s),
        "entries": entries,
    }
