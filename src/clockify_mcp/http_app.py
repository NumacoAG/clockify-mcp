"""HTTP transport for the Clockify MCP server.

Builds a Starlette ASGI app that wraps FastMCP's streamable-HTTP app with:
  - OAuth 2.1 endpoints (/authorize, /token, /register, /.well-known/*)
  - Bearer-token middleware that decodes each request's access token and installs
    the per-request state (Clockify API key) into a ContextVar.

The MCP client (Anthropic's connector backend) discovers the auth flow via the
WWW-Authenticate header pointing at /.well-known/oauth-protected-resource.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from .auth import (
    AuthConfig,
    AuthService,
    InvalidAccessTokenError,
    InvalidAuthCodeError,
)
from .client import ClockifyClient
from .config import Settings
from .errors import AuthError as ClockifyAuthError
from .errors import ClockifyError
from .state import RequestState, reset_state, set_state

logger = logging.getLogger(__name__)


def create_app(public_url: str) -> Starlette:
    """Build the full HTTP app: OAuth provider + MCP, wired together.

    `public_url` is the externally-reachable URL of this server (no trailing slash),
    e.g. `https://clockify-mcp-abc.a.run.app`. It's used as the OAuth issuer and
    in the metadata responses.
    """
    public_url = public_url.rstrip("/")
    auth_config = AuthConfig.from_env(issuer=public_url)
    auth_service = AuthService(auth_config)

    from .server import mcp

    mcp_app = mcp.streamable_http_app()

    # ---------- handlers ----------

    async def well_known_authorization_server(request: Request) -> Response:
        return JSONResponse(
            {
                "issuer": public_url,
                "authorization_endpoint": f"{public_url}/authorize",
                "token_endpoint": f"{public_url}/token",
                "registration_endpoint": f"{public_url}/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"],
                "code_challenge_methods_supported": ["S256", "plain"],
                "token_endpoint_auth_methods_supported": [
                    "client_secret_post",
                    "client_secret_basic",
                    "none",
                ],
                "scopes_supported": ["mcp"],
            }
        )

    async def well_known_protected_resource(request: Request) -> Response:
        return JSONResponse(
            {
                "resource": f"{public_url}/mcp",
                "authorization_servers": [public_url],
                "scopes_supported": ["mcp"],
                "bearer_methods_supported": ["header"],
            }
        )

    async def register(request: Request) -> Response:
        try:
            body = await request.json()
        except (ValueError, RuntimeError):
            body = {}
        client_id = secrets.token_urlsafe(16)
        client_secret = secrets.token_urlsafe(32)
        response_body = {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": int(datetime.now(UTC).timestamp()),
            "redirect_uris": body.get("redirect_uris", []) if isinstance(body, dict) else [],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
            "client_name": body.get("client_name") if isinstance(body, dict) else None,
        }
        return JSONResponse(response_body, status_code=201)

    async def authorize_get(request: Request) -> Response:
        p = request.query_params
        return HTMLResponse(
            _authorize_form_html(
                client_id=p.get("client_id"),
                redirect_uri=p.get("redirect_uri") or "",
                state=p.get("state", ""),
                code_challenge=p.get("code_challenge", ""),
                code_challenge_method=p.get("code_challenge_method", "plain"),
                scope=p.get("scope", ""),
                error=None,
            )
        )

    async def authorize_post(request: Request) -> Response:
        form = await request.form()
        api_key = str(form.get("api_key", "")).strip()
        redirect_uri = str(form.get("redirect_uri", ""))
        state = str(form.get("state", ""))
        code_challenge = str(form.get("code_challenge", ""))
        code_challenge_method = str(form.get("code_challenge_method", "plain"))
        client_id = form.get("client_id")
        client_id_str = str(client_id) if client_id else None
        scope = str(form.get("scope", ""))

        def _err(msg: str, status: int = 400) -> Response:
            return HTMLResponse(
                _authorize_form_html(
                    client_id=client_id_str,
                    redirect_uri=redirect_uri,
                    state=state,
                    code_challenge=code_challenge,
                    code_challenge_method=code_challenge_method,
                    scope=scope,
                    error=msg,
                ),
                status_code=status,
            )

        if not redirect_uri:
            return _err("Missing redirect_uri")
        if not api_key:
            return _err("Paste your Clockify API key to continue.")

        # Validate the key against Clockify before issuing anything.
        try:
            with ClockifyClient(Settings(api_key=api_key)) as client:
                client.get_current_user()
        except ClockifyAuthError:
            return _err("Clockify rejected that API key. Double-check and try again.")
        except ClockifyError as exc:
            logger.warning("Clockify error during authorize: %s", exc)
            return _err(f"Could not reach Clockify: {exc}", status=502)

        code = auth_service.issue_auth_code(
            api_key=api_key,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            redirect_uri=redirect_uri,
            client_id=client_id_str,
        )
        sep = "&" if "?" in redirect_uri else "?"
        url = f"{redirect_uri}{sep}code={code}"
        if state:
            url = f"{url}&state={state}"
        return RedirectResponse(url, status_code=302)

    async def token(request: Request) -> Response:
        params = await _parse_token_request(request)
        grant_type = params.get("grant_type")
        if grant_type != "authorization_code":
            return JSONResponse(
                {"error": "unsupported_grant_type", "error_description": f"got {grant_type}"},
                status_code=400,
            )
        code = params.get("code")
        redirect_uri = params.get("redirect_uri")
        client_id = params.get("client_id")
        code_verifier = params.get("code_verifier")
        if not code or not redirect_uri or not code_verifier:
            return JSONResponse(
                {
                    "error": "invalid_request",
                    "error_description": "code, redirect_uri, code_verifier are required",
                },
                status_code=400,
            )
        try:
            encrypted_key = auth_service.verify_auth_code(
                code,
                code_verifier=code_verifier,
                redirect_uri=redirect_uri,
                client_id=client_id,
            )
        except InvalidAuthCodeError as exc:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": str(exc)},
                status_code=400,
            )
        access_token, ttl = auth_service.issue_access_token(encrypted_key)
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": ttl,
                "scope": "mcp",
            }
        )

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    # ---------- auth-checking ASGI wrapper around the MCP app ----------

    async def authed_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await mcp_app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth_header = headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            await _send_unauthorized(send, public_url)
            return

        token_str = auth_header[7:].strip()
        try:
            api_key = auth_service.verify_access_token(token_str)
        except InvalidAccessTokenError as exc:
            logger.info("Rejected token: %s", exc)
            await _send_unauthorized(send, public_url)
            return

        request_state = RequestState(Settings(api_key=api_key))
        reset_token = set_state(request_state)
        try:
            await mcp_app(scope, receive, send)
        finally:
            reset_state(reset_token)
            request_state.close()

    # ---------- assemble ----------

    routes = [
        Route("/health", health),
        Route(
            "/.well-known/oauth-authorization-server",
            well_known_authorization_server,
        ),
        Route(
            "/.well-known/oauth-protected-resource",
            well_known_protected_resource,
        ),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize_get, methods=["GET"]),
        Route("/authorize", authorize_post, methods=["POST"]),
        Route("/token", token, methods=["POST"]),
        Mount("/mcp", app=authed_mcp),
    ]

    # FastMCP's streamable_http_app() creates its own internal lifespan that starts
    # the StreamableHTTPSessionManager. When we Mount that app inside our outer
    # Starlette, the inner lifespan never fires — Starlette only runs the outer
    # one. Without a running session manager, every authenticated /mcp request
    # crashes inside FastMCP and the connector reports "Authorization with the
    # MCP server failed" even though the OAuth handshake was clean. Run the
    # session manager here as part of our outer lifespan.
    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    return Starlette(routes=routes, lifespan=lifespan)


async def _parse_token_request(request: Request) -> dict[str, str]:
    """Token endpoint accepts both application/x-www-form-urlencoded and JSON bodies."""
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        return {k: str(v) for k, v in form.items()}
    try:
        body = await request.json()
    except (ValueError, RuntimeError):
        return {}
    if not isinstance(body, dict):
        return {}
    return {str(k): str(v) for k, v in body.items() if v is not None}


async def _send_unauthorized(send: Send, public_url: str) -> None:
    body = (
        b'{"error":"unauthorized","error_description":'
        b'"Bearer token required. See WWW-Authenticate header for the OAuth metadata."}'
    )
    resource_metadata = f"{public_url}/.well-known/oauth-protected-resource"
    www_authenticate = f'Bearer realm="mcp", resource_metadata="{resource_metadata}"'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", www_authenticate.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _authorize_form_html(
    *,
    client_id: str | None,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    error: str | None,
) -> str:
    error_block = f'<div class="err">{_html_escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Connect Clockify</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
           background:#f6f7f9; color:#1f2328; margin:0; padding:0;
           display:flex; align-items:center; justify-content:center; min-height:100vh; }}
    .card {{ background:#fff; padding:32px 36px; border-radius:14px; max-width:480px; width:90%;
             box-shadow:0 10px 30px rgba(0,0,0,.08); }}
    h1 {{ margin:0 0 8px; font-size:22px; }}
    p  {{ margin:0 0 16px; color:#5b6573; font-size:14px; line-height:1.5; }}
    label {{ display:block; font-size:13px; font-weight:600; margin:14px 0 6px; }}
    input[type=text] {{ width:100%; padding:10px 12px; border:1px solid #d0d7de; border-radius:8px;
                         font-size:14px; box-sizing:border-box; font-family:ui-monospace, monospace; }}
    button {{ width:100%; padding:11px; margin-top:18px; background:#0969da; color:#fff;
              border:0; border-radius:8px; font-size:15px; font-weight:600; cursor:pointer; }}
    button:hover {{ background:#0860c7; }}
    .err {{ background:#ffeef0; color:#86181d; padding:10px 12px; border-radius:8px;
            font-size:13px; margin-bottom:8px; }}
    .hint {{ font-size:12px; color:#5b6573; margin-top:10px; }}
    a {{ color:#0969da; }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/authorize">
    <h1>Connect Clockify</h1>
    <p>Paste your Clockify personal API key. It stays encrypted inside the access token —
       this server doesn't store anything.</p>
    {error_block}
    <label for="api_key">Clockify API key</label>
    <input id="api_key" name="api_key" type="text" autocomplete="off" autofocus required
           placeholder="e.g. ZmExZTYyN2Yt…">
    <div class="hint">Get one from
       <a href="https://app.clockify.me/user/preferences#advanced" target="_blank" rel="noopener">
       Clockify → Preferences → Advanced → Generate</a>.</div>
    <input type="hidden" name="client_id" value="{_html_escape(client_id or "")}">
    <input type="hidden" name="redirect_uri" value="{_html_escape(redirect_uri)}">
    <input type="hidden" name="state" value="{_html_escape(state)}">
    <input type="hidden" name="code_challenge" value="{_html_escape(code_challenge)}">
    <input type="hidden" name="code_challenge_method" value="{_html_escape(code_challenge_method)}">
    <input type="hidden" name="scope" value="{_html_escape(scope)}">
    <button type="submit">Authorize</button>
  </form>
</body>
</html>
"""


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
