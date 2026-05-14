"""Exception types for the Clockify connector."""

from __future__ import annotations

from typing import Any


class ClockifyError(Exception):
    """Base for every error raised by this package."""


class ConfigError(ClockifyError):
    """Configuration is missing or invalid (no API key, malformed config file, etc.)."""


class ValidationError(ClockifyError):
    """A local pre-flight check failed before sending to the API."""


class ApiError(ClockifyError):
    """Clockify API returned an error response."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        body: Any = None,
    ) -> None:
        super().__init__(f"[{status} {code}] {message}")
        self.status = status
        self.code = code
        self.message = message
        self.body = body


class AuthError(ApiError):
    """401/403 — API key invalid or lacks permission."""


class NotFoundError(ApiError):
    """404 — resource not found."""


class RateLimitError(ApiError):
    """429 — rate limited (we retry once with backoff before raising)."""
