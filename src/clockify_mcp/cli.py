"""CLI entry point.

Three modes:
  - default (stdio): `clockify-mcp` — for Claude Code / local MCP clients.
  - `--check`: validate the API key and exit.
  - `--http`: run the HTTP server (OAuth + MCP over streamable HTTP) for the
    Claude desktop app's custom-connector dialog and other remote MCP clients.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .errors import ClockifyError
from .server import _get_state, mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clockify-mcp",
        description=(
            "Clockify MCP server. Default: stdio for a local MCP client. "
            "Use --check to validate your API key, or --http to run the multi-user "
            "OAuth-protected HTTP server."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the API key, print user info, and exit.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run the HTTP server with OAuth (multi-user). Requires JWT_SIGNING_KEY, "
        "ENCRYPTION_KEY, and PUBLIC_URL env vars (or --public-url).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="HTTP bind host (default: 0.0.0.0; env: HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="HTTP bind port (default: 8080; env: PORT — set by Cloud Run).",
    )
    parser.add_argument(
        "--public-url",
        default=os.environ.get("PUBLIC_URL"),
        help="Externally-reachable URL for this server, no trailing slash. "
        "Used in OAuth metadata. Env: PUBLIC_URL.",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (writes to stderr; default INFO).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        if args.check:
            return _run_check()
        if args.http:
            return _run_http(host=args.host, port=args.port, public_url=args.public_url)
        mcp.run()
        return 0
    except ClockifyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _run_check() -> int:
    state = _get_state()
    user = state.get_user()
    settings = user.get("settings") if isinstance(user.get("settings"), dict) else {}
    tz = settings.get("timeZone") if isinstance(settings, dict) else None
    print(f"OK. Authenticated as {user.get('name')} <{user.get('email')}>")
    print(f"  user_id:              {user.get('id')}")
    print(f"  default_workspace_id: {user.get('defaultWorkspace')}")
    print(f"  active_workspace_id:  {user.get('activeWorkspace')}")
    print(f"  timezone:             {tz}")
    return 0


def _run_http(*, host: str, port: int, public_url: str | None) -> int:
    if not public_url:
        print(
            "Error: --public-url (or PUBLIC_URL env var) is required in --http mode.\n"
            "  Examples:\n"
            "    --public-url=http://localhost:8080   (for local smoke testing)\n"
            "    --public-url=https://clockify-mcp-xyz.a.run.app  (Cloud Run)\n",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from .http_app import create_app

    app = create_app(public_url=public_url)
    logging.getLogger(__name__).info(
        "Starting HTTP server on %s:%d (public_url=%s)", host, port, public_url
    )
    # proxy_headers=True so Cloud Run's X-Forwarded-Proto=https is honored when
    # Starlette builds redirect/self-reference URLs.
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
