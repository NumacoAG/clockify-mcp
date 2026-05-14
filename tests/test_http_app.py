"""End-to-end tests for the HTTP app: OAuth metadata, /authorize, /token, /mcp auth."""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Iterator

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from clockify_mcp.http_app import create_app

PUBLIC_URL = "https://test.example.com"
CLOCKIFY_API = "https://api.clockify.me/api/v1"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SIGNING_KEY", "test-signing-key")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-encryption-key")


@pytest.fixture
def client(env: None) -> Iterator[TestClient]:
    # Reset the module-level FastMCP session manager so each test gets a clean
    # lifespan cycle. Production never re-enters the lifespan, so this is a
    # test-only concern.
    from clockify_mcp import server

    server.mcp._session_manager = None  # type: ignore[attr-defined]
    app = create_app(public_url=PUBLIC_URL)
    with TestClient(app) as c:
        yield c


# ---------- discovery ----------


def test_authorization_server_metadata(client: TestClient) -> None:
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert body["issuer"] == PUBLIC_URL
    assert body["authorization_endpoint"] == f"{PUBLIC_URL}/authorize"
    assert body["token_endpoint"] == f"{PUBLIC_URL}/token"
    assert body["registration_endpoint"] == f"{PUBLIC_URL}/register"
    assert "S256" in body["code_challenge_methods_supported"]


def test_protected_resource_metadata(client: TestClient) -> None:
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"] == f"{PUBLIC_URL}/mcp"
    assert PUBLIC_URL in body["authorization_servers"]


# ---------- registration ----------


def test_register_returns_credentials(client: TestClient) -> None:
    r = client.post(
        "/register",
        json={"client_name": "Anthropic", "redirect_uris": ["https://anthropic.test/cb"]},
    )
    assert r.status_code == 201
    body = r.json()
    assert "client_id" in body
    assert "client_secret" in body
    assert body["redirect_uris"] == ["https://anthropic.test/cb"]


# ---------- authorize ----------


def test_authorize_get_renders_form(client: TestClient) -> None:
    r = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "abc",
            "redirect_uri": "https://anthropic.test/cb",
            "state": "xyz",
            "code_challenge": "challenge",
            "code_challenge_method": "S256",
        },
    )
    assert r.status_code == 200
    assert "Connect Clockify" in r.text
    assert 'name="api_key"' in r.text
    assert "anthropic.test" in r.text  # redirect_uri preserved as hidden field


@respx.mock
def test_authorize_post_with_valid_key_redirects(client: TestClient) -> None:
    respx.get(f"{CLOCKIFY_API}/user").mock(
        return_value=httpx.Response(200, json={"id": "u1", "name": "Test"})
    )
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    r = client.post(
        "/authorize",
        data={
            "api_key": "valid-clockify-key",
            "client_id": "abc",
            "redirect_uri": "https://anthropic.test/cb",
            "state": "xyz",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://anthropic.test/cb?code=")
    assert "state=xyz" in location


@respx.mock
def test_authorize_post_with_bad_key_shows_error(client: TestClient) -> None:
    respx.get(f"{CLOCKIFY_API}/user").mock(
        return_value=httpx.Response(401, json={"code": "NO_AUTH", "message": "bad"})
    )
    r = client.post(
        "/authorize",
        data={
            "api_key": "bad-key",
            "redirect_uri": "https://anthropic.test/cb",
            "code_challenge": "c",
            "code_challenge_method": "plain",
        },
    )
    assert r.status_code == 400
    assert "Clockify rejected" in r.text


def test_authorize_post_without_key_shows_error(client: TestClient) -> None:
    r = client.post(
        "/authorize",
        data={"redirect_uri": "https://anthropic.test/cb", "code_challenge": "c"},
    )
    assert r.status_code == 400
    assert "Paste your Clockify API key" in r.text


# ---------- token ----------


@respx.mock
def test_token_full_flow(client: TestClient) -> None:
    """Authorize → extract code from redirect → exchange at /token."""
    respx.get(f"{CLOCKIFY_API}/user").mock(return_value=httpx.Response(200, json={"id": "u1"}))
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    redir = client.post(
        "/authorize",
        data={
            "api_key": "real-clockify-key",
            "client_id": "client-A",
            "redirect_uri": "https://anthropic.test/cb",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert redir.status_code == 302
    location = redir.headers["location"]
    code = location.split("code=", 1)[1].split("&", 1)[0]

    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://anthropic.test/cb",
            "client_id": "client-A",
            "code_verifier": verifier,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert "access_token" in body
    assert body["expires_in"] > 0


def test_token_rejects_bad_code(client: TestClient) -> None:
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": "not-a-real-jwt",
            "redirect_uri": "https://anthropic.test/cb",
            "code_verifier": "anything",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_token_unsupported_grant_type(client: TestClient) -> None:
    r = client.post(
        "/token",
        data={"grant_type": "password", "username": "x", "password": "y"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


# ---------- mcp gate ----------


def test_mcp_without_bearer_returns_401(client: TestClient) -> None:
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
    assert r.status_code == 401
    assert "www-authenticate" in {k.lower() for k in r.headers}
    auth = r.headers["www-authenticate"].lower()
    assert "bearer" in auth
    assert "resource_metadata=" in auth


def test_mcp_with_garbage_bearer_returns_401(client: TestClient) -> None:
    r = client.post(
        "/mcp",
        headers={"Authorization": "Bearer not-a-real-token"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    assert r.status_code == 401


# ---------- health ----------


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
