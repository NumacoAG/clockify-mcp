"""OAuth 2.1 provider implementation for the MCP HTTP transport.

Tokens are JWTs (HS256) signed with a server secret. The Clockify API key is
Fernet-encrypted and embedded in the token, so the server is stateless — no
database needed. Per-user revocation = rotate the signing key.

Flow:
  1. Anthropic's backend calls /authorize with PKCE params.
  2. We render an HTML form. User pastes their Clockify API key.
  3. We validate the key (call Clockify /user), then issue a short-lived
     authorization code (JWT) and redirect to the client's redirect_uri.
  4. Anthropic POSTs to /token with code + code_verifier. We verify the PKCE
     challenge and issue a longer-lived access token (also a JWT, containing
     the same encrypted key).
  5. On MCP requests, the middleware extracts the API key from the Bearer
     token and installs it as the per-request state.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken


class OAuthError(Exception):
    """Base for OAuth-related errors raised by this module."""


class InvalidAuthCodeError(OAuthError):
    """The authorization code is invalid, expired, or fails PKCE check."""


class InvalidAccessTokenError(OAuthError):
    """The access token is invalid or expired."""


class AuthConfigError(OAuthError):
    """A required env var is missing."""


@dataclass(frozen=True)
class AuthConfig:
    signing_key: str
    encryption_key: str
    issuer: str
    # 6 months — long enough that users authorise once per half-year, not per
    # session. The Clockify API key embedded inside the JWT never expires
    # server-side, so this is purely our wrapper's freshness window. Rotate
    # JWT_SIGNING_KEY to revoke every issued token at once if needed.
    access_token_ttl: int = 180 * 24 * 3600
    auth_code_ttl: int = 120

    @classmethod
    def from_env(cls, issuer: str) -> AuthConfig:
        signing_key = os.environ.get("JWT_SIGNING_KEY")
        encryption_key = os.environ.get("ENCRYPTION_KEY")
        if not signing_key:
            raise AuthConfigError(
                "JWT_SIGNING_KEY env var not set. Generate one with: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        if not encryption_key:
            raise AuthConfigError(
                "ENCRYPTION_KEY env var not set. Generate one with: "
                "python -c 'import secrets; print(secrets.token_hex(32))'"
            )
        return cls(
            signing_key=signing_key,
            encryption_key=encryption_key,
            issuer=issuer,
        )


class AuthService:
    """JWT issuance/verification + symmetric encryption of the Clockify API key."""

    def __init__(self, config: AuthConfig) -> None:
        self.config = config
        self._fernet = Fernet(_derive_fernet_key(config.encryption_key))

    # ----- encryption -----

    def encrypt_api_key(self, api_key: str) -> str:
        return self._fernet.encrypt(api_key.encode()).decode()

    def decrypt_api_key(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise InvalidAccessTokenError("Could not decrypt embedded API key") from exc

    # ----- authorization code -----

    def issue_auth_code(
        self,
        *,
        api_key: str,
        code_challenge: str,
        code_challenge_method: str,
        redirect_uri: str,
        client_id: str | None,
    ) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "iss": self.config.issuer,
            "typ": "code",
            "key": self.encrypt_api_key(api_key),
            "cc": code_challenge,
            "ccm": code_challenge_method,
            "rdr": redirect_uri,
            "cid": client_id or "",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.config.auth_code_ttl)).timestamp()),
            "jti": secrets.token_urlsafe(8),
        }
        return jwt.encode(payload, self.config.signing_key, algorithm="HS256")

    def verify_auth_code(
        self,
        code: str,
        *,
        code_verifier: str,
        redirect_uri: str,
        client_id: str | None,
    ) -> str:
        """Validate an authorization code (with PKCE) and return the *encrypted* Clockify key."""
        try:
            payload = jwt.decode(
                code,
                self.config.signing_key,
                algorithms=["HS256"],
                issuer=self.config.issuer,
            )
        except jwt.PyJWTError as exc:
            raise InvalidAuthCodeError(f"Invalid auth code: {exc}") from exc
        if payload.get("typ") != "code":
            raise InvalidAuthCodeError("Wrong token type for auth code")
        if payload.get("rdr") != redirect_uri:
            raise InvalidAuthCodeError("redirect_uri mismatch")
        embedded_cid = payload.get("cid") or ""
        if embedded_cid and client_id and embedded_cid != client_id:
            raise InvalidAuthCodeError("client_id mismatch")
        method = payload.get("ccm", "plain")
        challenge = payload.get("cc", "")
        if not _verify_pkce(code_verifier, challenge, method):
            raise InvalidAuthCodeError("PKCE verification failed")
        key = payload.get("key")
        if not isinstance(key, str):
            raise InvalidAuthCodeError("auth code is missing the encrypted key")
        return key

    # ----- access token -----

    def issue_access_token(self, encrypted_api_key: str) -> tuple[str, int]:
        now = datetime.now(UTC)
        payload = {
            "iss": self.config.issuer,
            "typ": "access",
            "key": encrypted_api_key,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.config.access_token_ttl)).timestamp()),
        }
        token = jwt.encode(payload, self.config.signing_key, algorithm="HS256")
        return token, self.config.access_token_ttl

    def verify_access_token(self, token: str) -> str:
        """Validate an access token and return the *decrypted* Clockify API key."""
        try:
            payload = jwt.decode(
                token,
                self.config.signing_key,
                algorithms=["HS256"],
                issuer=self.config.issuer,
            )
        except jwt.PyJWTError as exc:
            raise InvalidAccessTokenError(f"Invalid access token: {exc}") from exc
        if payload.get("typ") != "access":
            raise InvalidAccessTokenError("Wrong token type for access token")
        key = payload.get("key")
        if not isinstance(key, str):
            raise InvalidAccessTokenError("access token is missing the encrypted key")
        return self.decrypt_api_key(key)


def _verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    if not verifier or not challenge:
        return False
    if method == "plain":
        return secrets.compare_digest(verifier, challenge)
    if method == "S256":
        digest = hashlib.sha256(verifier.encode()).digest()
        calc = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return secrets.compare_digest(calc, challenge)
    return False


def _derive_fernet_key(secret: str) -> bytes:
    """Accept any string and derive a Fernet-compatible 32-byte urlsafe-base64 key."""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)
