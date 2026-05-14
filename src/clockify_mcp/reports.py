"""Reports API body builders + helpers for shaping responses.

The Reports API is POST-only and takes a rich filter body. We expose two helpers:

  - `build_summary_body` — aggregations grouped by PROJECT / TASK / USER / DAY / WEEK / MONTH / TAG / CLIENT.
  - `build_detailed_body` — raw entries hydrated with project / user / task names, paginated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

GroupBy = Literal["PROJECT", "TASK", "USER", "DAY", "WEEK", "MONTH", "TAG", "CLIENT"]

VALID_GROUP_BY: frozenset[str] = frozenset(
    ["PROJECT", "TASK", "USER", "DAY", "WEEK", "MONTH", "TAG", "CLIENT"]
)


def build_summary_body(
    *,
    start: str,
    end: str,
    project_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    client_ids: list[str] | None = None,
    billable: bool | None = None,
    group_by: str = "PROJECT",
) -> dict[str, Any]:
    """Build a Summary Report request body."""
    body: dict[str, Any] = {
        "dateRangeStart": start,
        "dateRangeEnd": end,
        "summaryFilter": {"groups": [group_by]},
        "exportType": "JSON",
        "amountShown": "EARNED",
    }
    _attach_id_filter(body, "projects", project_ids)
    _attach_id_filter(body, "users", user_ids)
    _attach_id_filter(body, "tags", tag_ids)
    _attach_id_filter(body, "clients", client_ids)
    if billable is not None:
        body["billable"] = billable
    return body


def build_detailed_body(
    *,
    start: str,
    end: str,
    project_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    client_ids: list[str] | None = None,
    billable: bool | None = None,
    page: int = 1,
    page_size: int = 200,
) -> dict[str, Any]:
    """Build a Detailed Report request body."""
    body: dict[str, Any] = {
        "dateRangeStart": start,
        "dateRangeEnd": end,
        "detailedFilter": {
            "page": page,
            "pageSize": page_size,
            "sortColumn": "DATE",
        },
        "exportType": "JSON",
        "amountShown": "EARNED",
    }
    _attach_id_filter(body, "projects", project_ids)
    _attach_id_filter(body, "users", user_ids)
    _attach_id_filter(body, "tags", tag_ids)
    _attach_id_filter(body, "clients", client_ids)
    if billable is not None:
        body["billable"] = billable
    return body


def _attach_id_filter(body: dict[str, Any], key: str, ids: list[str] | None) -> None:
    if not ids:
        return
    body[key] = {"ids": list(ids), "contains": "CONTAINS", "status": "ALL"}


def total_seconds_from_entries(entries: list[dict[str, Any]]) -> int:
    """Sum duration in seconds over a list of time-entry dicts (from the main API)."""
    total = 0
    for e in entries:
        interval = e.get("timeInterval", {}) if isinstance(e, dict) else {}
        start = interval.get("start") if isinstance(interval, dict) else None
        end = interval.get("end") if isinstance(interval, dict) else None
        if not start or not end:
            continue
        try:
            s_dt = datetime.fromisoformat(_normalize(start))
            e_dt = datetime.fromisoformat(_normalize(end))
        except ValueError:
            continue
        total += int((e_dt - s_dt).total_seconds())
    return total


def _normalize(s: str) -> str:
    return s.replace("Z", "+00:00")


def seconds_to_hms(total: int) -> str:
    """Pretty-print a duration in seconds as e.g. "2h30m" or "45m" or "10s"."""
    if total < 0:
        return "-" + seconds_to_hms(-total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
