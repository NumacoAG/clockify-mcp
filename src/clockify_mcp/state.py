"""Per-request state via ContextVar.

In stdio mode the state is set once at process startup (single user, env-derived).
In HTTP/multi-user mode, ASGI middleware sets it per-request from the Bearer token.

Tools call `get_state()` which reads from the ContextVar — same code path for both
transports.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from threading import Lock
from typing import Any

from .cache import TTLCache
from .client import ClockifyClient
from .config import Settings
from .errors import ValidationError


class RequestState:
    """Per-request state: client + caches + the current user (memoised once per request)."""

    def __init__(self, settings: Settings, client: ClockifyClient | None = None) -> None:
        self.settings = settings
        self.client = client or ClockifyClient(settings)
        self._user: dict[str, Any] | None = None
        self._user_lock = Lock()
        self.projects: TTLCache[tuple[str, str], list[dict[str, Any]]] = TTLCache(
            settings.cache_ttl_seconds
        )
        self.tags: TTLCache[str, list[dict[str, Any]]] = TTLCache(settings.cache_ttl_seconds)

    def get_user(self) -> dict[str, Any]:
        with self._user_lock:
            if self._user is None:
                self._user = self.client.get_current_user()
            return self._user

    def resolve_workspace_id(self, workspace_id: str | None) -> str:
        if workspace_id:
            return workspace_id
        if self.settings.default_workspace_id:
            return self.settings.default_workspace_id
        user = self.get_user()
        wid = user.get("activeWorkspace") or user.get("defaultWorkspace")
        if not isinstance(wid, str) or not wid:
            raise ValidationError(
                "No workspace_id provided and user has no active/default workspace"
            )
        return wid

    def resolve_user_id(self, user_id: str | None) -> str:
        if user_id:
            return user_id
        uid = self.get_user().get("id")
        if not isinstance(uid, str) or not uid:
            raise ValidationError("Could not determine user ID from /user response")
        return uid

    def resolve_user_tz(self) -> str:
        if self.settings.timezone:
            return self.settings.timezone
        settings_obj = self.get_user().get("settings")
        if isinstance(settings_obj, dict):
            tz = settings_obj.get("timeZone")
            if isinstance(tz, str) and tz:
                return tz
        return "UTC"

    def close(self) -> None:
        self.client.close()


_current: ContextVar[RequestState | None] = ContextVar("clockify_request_state", default=None)


def get_state() -> RequestState:
    """Return the current request's state.

    In stdio mode this is the process-wide singleton; in HTTP mode it's per-request.
    Raises if no state has been installed (programming error).
    """
    state = _current.get()
    if state is None:
        raise RuntimeError(
            "No request state installed. In HTTP mode, the auth middleware should set "
            "one per request; in stdio mode, the CLI should set one at startup."
        )
    return state


def set_state(state: RequestState | None) -> Token[RequestState | None]:
    """Install (or clear) the current request state. Returns a token for reset()."""
    return _current.set(state)


def reset_state(token: Token[RequestState | None]) -> None:
    _current.reset(token)
