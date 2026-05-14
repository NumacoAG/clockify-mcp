"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from clockify_mcp import server
from clockify_mcp.client import ClockifyClient
from clockify_mcp.config import Settings


@pytest.fixture
def settings() -> Settings:
    # `timezone=None` so server tests exercise the fallback to the user's `settings.timeZone`
    return Settings(
        api_key="test-api-key",
        api_base="https://api.example.test/api/v1",
        reports_api_base="https://reports.api.example.test/v1",
        timezone=None,
    )


@pytest.fixture
def http_client() -> Iterator[httpx.Client]:
    client = httpx.Client(
        timeout=10.0,
        headers={"X-Api-Key": "test-api-key", "Accept": "application/json"},
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def clockify_client(settings: Settings, http_client: httpx.Client) -> ClockifyClient:
    return ClockifyClient(settings, http=http_client)


@pytest.fixture
def fake_user() -> dict[str, Any]:
    return {
        "id": "user-1",
        "name": "Test User",
        "email": "test@example.com",
        "activeWorkspace": "ws-1",
        "defaultWorkspace": "ws-1",
        "settings": {"timeZone": "Europe/Zurich"},
    }


@pytest.fixture
def mocked_state(settings: Settings, fake_user: dict[str, Any]) -> Iterator[server._State]:
    """Install a server `_State` whose client is a MagicMock, ready for stubbing.

    Cleans up the module-level state at teardown.
    """
    mock_client = MagicMock(spec=ClockifyClient)
    mock_client.get_current_user.return_value = fake_user
    state = server._State(settings, client=mock_client)
    server._set_state_for_tests(state)
    try:
        yield state
    finally:
        server._set_state_for_tests(None)
