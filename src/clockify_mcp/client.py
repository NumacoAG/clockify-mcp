"""HTTP client for the Clockify v1 API and the separate Reports API.

Hides:
  - Two base URLs (main + reports), selectable per call.
  - X-Api-Key auth header.
  - Pagination differences (some endpoints use `page-size`, others `pageSize`).
  - Retries on transient network errors and 429 / 5xx responses.
  - Error normalisation: 401 -> AuthError, 404 -> NotFoundError, anything else -> ApiError.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

import httpx

from . import __version__
from .config import Settings
from .errors import ApiError, AuthError, NotFoundError, RateLimitError

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]
JsonList = list[Any]

Base = Literal["main", "reports"]


class ClockifyClient:
    """Synchronous Clockify HTTP client. Use as a context manager to ensure cleanup."""

    def __init__(self, settings: Settings, *, http: httpx.Client | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.Client(
            timeout=settings.request_timeout,
            headers={
                "X-Api-Key": settings.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"clockify-mcp/{__version__}",
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ClockifyClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- user -----
    def get_current_user(self) -> JsonDict:
        result = self._request("GET", "/user", base="main")
        if not isinstance(result, dict):
            raise ApiError(0, "UNEXPECTED_RESPONSE", "Expected dict from /user", result)
        return result

    # ----- workspaces -----
    def list_workspaces(self) -> JsonList:
        result = self._request("GET", "/workspaces", base="main")
        if not isinstance(result, list):
            raise ApiError(0, "UNEXPECTED_RESPONSE", "Expected list from /workspaces", result)
        return result

    # ----- projects -----
    def list_projects(
        self,
        workspace_id: str,
        *,
        name: str | None = None,
        archived: bool | None = None,
    ) -> JsonList:
        params: dict[str, Any] = {}
        if name is not None:
            params["name"] = name
        if archived is not None:
            params["archived"] = "true" if archived else "false"
        return self._paginate(
            f"/workspaces/{workspace_id}/projects",
            base="main",
            params=params,
            page_param="page",
            page_size_param="page-size",
        )

    # ----- tasks -----
    def list_tasks(self, workspace_id: str, project_id: str) -> JsonList:
        return self._paginate(
            f"/workspaces/{workspace_id}/projects/{project_id}/tasks",
            base="main",
            page_param="page",
            page_size_param="page-size",
        )

    # ----- tags -----
    def list_tags(self, workspace_id: str) -> JsonList:
        return self._paginate(
            f"/workspaces/{workspace_id}/tags",
            base="main",
            page_param="page",
            page_size_param="page-size",
        )

    # ----- time entries -----
    def get_time_entry(
        self,
        workspace_id: str,
        entry_id: str,
        *,
        hydrated: bool = True,
    ) -> JsonDict:
        result = self._request(
            "GET",
            f"/workspaces/{workspace_id}/time-entries/{entry_id}",
            base="main",
            params={"hydrated": "true" if hydrated else "false"},
        )
        if not isinstance(result, dict):
            raise ApiError(0, "UNEXPECTED_RESPONSE", "Expected dict for time entry", result)
        return result

    def get_running_timer(
        self,
        workspace_id: str,
        user_id: str,
        *,
        hydrated: bool = True,
    ) -> JsonDict | None:
        """Return the in-progress entry for `user_id`, or None if no timer is running."""
        result = self._request(
            "GET",
            f"/workspaces/{workspace_id}/user/{user_id}/time-entries",
            base="main",
            params={
                "in-progress": "true",
                "hydrated": "true" if hydrated else "false",
            },
        )
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                return first
        return None

    def list_user_time_entries(
        self,
        workspace_id: str,
        user_id: str,
        *,
        start: str,
        end: str,
        project_id: str | None = None,
        description: str | None = None,
        hydrated: bool = True,
        page_size: int = 1000,
        max_results: int = 5000,
    ) -> JsonList:
        """List a user's time entries in a date range, paginating through all pages."""
        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "hydrated": "true" if hydrated else "false",
        }
        if project_id:
            params["project"] = project_id
        if description:
            params["description"] = description
        return self._paginate(
            f"/workspaces/{workspace_id}/user/{user_id}/time-entries",
            base="main",
            params=params,
            page_param="page",
            page_size_param="page-size",
            page_size=page_size,
            max_results=max_results,
        )

    def add_time_entry(
        self,
        workspace_id: str,
        *,
        start: str,
        end: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        description: str | None = None,
        billable: bool | None = None,
        tag_ids: list[str] | None = None,
    ) -> JsonDict:
        body: JsonDict = {"start": start}
        if end is not None:
            body["end"] = end
        if project_id is not None:
            body["projectId"] = project_id
        if task_id is not None:
            body["taskId"] = task_id
        if description is not None:
            body["description"] = description
        if billable is not None:
            body["billable"] = billable
        if tag_ids:
            body["tagIds"] = tag_ids
        result = self._request(
            "POST",
            f"/workspaces/{workspace_id}/time-entries",
            base="main",
            json_body=body,
        )
        if not isinstance(result, dict):
            raise ApiError(0, "UNEXPECTED_RESPONSE", "Expected dict for created entry", result)
        return result

    def update_time_entry(
        self,
        workspace_id: str,
        entry_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        description: str | None = None,
        billable: bool | None = None,
        tag_ids: list[str] | None = None,
    ) -> JsonDict:
        body: JsonDict = {}
        if start is not None:
            body["start"] = start
        if end is not None:
            body["end"] = end
        if project_id is not None:
            body["projectId"] = project_id
        if task_id is not None:
            body["taskId"] = task_id
        if description is not None:
            body["description"] = description
        if billable is not None:
            body["billable"] = billable
        if tag_ids is not None:
            body["tagIds"] = tag_ids
        result = self._request(
            "PUT",
            f"/workspaces/{workspace_id}/time-entries/{entry_id}",
            base="main",
            json_body=body,
        )
        if not isinstance(result, dict):
            raise ApiError(0, "UNEXPECTED_RESPONSE", "Expected dict for updated entry", result)
        return result

    def delete_time_entry(self, workspace_id: str, entry_id: str) -> None:
        self._request(
            "DELETE",
            f"/workspaces/{workspace_id}/time-entries/{entry_id}",
            base="main",
            expect_json=False,
        )

    def stop_running_timer(self, workspace_id: str, user_id: str, end: str) -> JsonDict:
        result = self._request(
            "PATCH",
            f"/workspaces/{workspace_id}/user/{user_id}/time-entries",
            base="main",
            json_body={"end": end},
        )
        if not isinstance(result, dict):
            raise ApiError(0, "UNEXPECTED_RESPONSE", "Expected dict from stop timer", result)
        return result

    # ----- reports -----
    def report_summary(self, workspace_id: str, body: JsonDict) -> JsonDict:
        result = self._request(
            "POST",
            f"/workspaces/{workspace_id}/reports/summary",
            base="reports",
            json_body=body,
        )
        if not isinstance(result, dict):
            raise ApiError(0, "UNEXPECTED_RESPONSE", "Expected dict from summary report", result)
        return result

    def report_detailed(self, workspace_id: str, body: JsonDict) -> JsonDict:
        result = self._request(
            "POST",
            f"/workspaces/{workspace_id}/reports/detailed",
            base="reports",
            json_body=body,
        )
        if not isinstance(result, dict):
            raise ApiError(0, "UNEXPECTED_RESPONSE", "Expected dict from detailed report", result)
        return result

    # ----- helpers -----
    def _paginate(
        self,
        path: str,
        *,
        base: Base = "main",
        params: dict[str, Any] | None = None,
        page_param: str = "page",
        page_size_param: str = "page-size",
        page_size: int = 100,
        max_results: int = 5000,
    ) -> JsonList:
        """Page through a list endpoint until empty or short page, respecting `max_results`."""
        params = dict(params or {})
        params[page_size_param] = page_size
        results: JsonList = []
        page = 1
        while True:
            params[page_param] = page
            batch = self._request("GET", path, base=base, params=params)
            if not isinstance(batch, list):
                raise ApiError(
                    0,
                    "UNEXPECTED_RESPONSE",
                    f"Expected list from paginated GET {path}",
                    batch,
                )
            results.extend(batch)
            if len(batch) < page_size or len(results) >= max_results:
                break
            page += 1
        return results[:max_results]

    def _request(
        self,
        method: str,
        path: str,
        *,
        base: Base = "main",
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        expect_json: bool = True,
        retries: int = 3,
    ) -> Any:
        base_url = self._settings.api_base if base == "main" else self._settings.reports_api_base
        url = f"{base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self._http.request(
                    method,
                    url,
                    params=params,
                    json=json_body if json_body is not None else None,
                )
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning("Network error on attempt %d: %s", attempt + 1, exc)
                time.sleep(0.5 * (attempt + 1))
                continue

            if resp.status_code == 429 and attempt < retries - 1:
                wait = _retry_after_seconds(resp)
                logger.warning("Rate limited; sleeping %.2fs", wait)
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600 and attempt < retries - 1:
                logger.warning("Server error %d; retrying", resp.status_code)
                time.sleep(0.5 * (attempt + 1))
                continue

            self._raise_for_status(resp)

            if not expect_json or resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

        raise ApiError(
            0, "NETWORK", f"Request to {url} failed after {retries} attempts", None
        ) from last_exc

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
            code = body.get("code", "UNKNOWN") if isinstance(body, dict) else "UNKNOWN"
            message = body.get("message", resp.text) if isinstance(body, dict) else resp.text
        except ValueError:
            body = resp.text
            code = "UNKNOWN"
            message = resp.text
        if resp.status_code in (401, 403):
            raise AuthError(resp.status_code, code, message, body)
        if resp.status_code == 404:
            raise NotFoundError(resp.status_code, code, message, body)
        if resp.status_code == 429:
            raise RateLimitError(resp.status_code, code, message, body)
        raise ApiError(resp.status_code, code, message, body)


def _retry_after_seconds(resp: httpx.Response) -> float:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return 1.0
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 1.0
