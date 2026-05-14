"""Tests for the OAuth provider: JWT, encryption, PKCE."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time

import pytest

from clockify_mcp.auth import (
    AuthConfig,
    AuthService,
    InvalidAccessTokenError,
    InvalidAuthCodeError,
)


@pytest.fixture
def service() -> AuthService:
    return AuthService(
        AuthConfig(
            signing_key="signing-secret-for-tests",
            encryption_key="encryption-secret-for-tests",
            issuer="https://test.example/",
            access_token_ttl=3600,
            auth_code_ttl=120,
        )
    )


def test_api_key_encrypt_roundtrip(service: AuthService) -> None:
    cipher = service.encrypt_api_key("hello-clockify-key")
    assert cipher != "hello-clockify-key"
    assert service.decrypt_api_key(cipher) == "hello-clockify-key"


def test_auth_code_roundtrip_with_s256(service: AuthService) -> None:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    code = service.issue_auth_code(
        api_key="ck-key",
        code_challenge=challenge,
        code_challenge_method="S256",
        redirect_uri="https://anthropic.test/callback",
        client_id="client-A",
    )
    encrypted = service.verify_auth_code(
        code,
        code_verifier=verifier,
        redirect_uri="https://anthropic.test/callback",
        client_id="client-A",
    )
    assert service.decrypt_api_key(encrypted) == "ck-key"


def test_auth_code_roundtrip_with_plain(service: AuthService) -> None:
    verifier = "the-same-string"
    code = service.issue_auth_code(
        api_key="ck-key",
        code_challenge=verifier,
        code_challenge_method="plain",
        redirect_uri="https://x.test/cb",
        client_id=None,
    )
    encrypted = service.verify_auth_code(
        code,
        code_verifier=verifier,
        redirect_uri="https://x.test/cb",
        client_id=None,
    )
    assert service.decrypt_api_key(encrypted) == "ck-key"


def test_auth_code_pkce_fail(service: AuthService) -> None:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    code = service.issue_auth_code(
        api_key="ck-key",
        code_challenge=challenge,
        code_challenge_method="S256",
        redirect_uri="https://x.test/cb",
        client_id="A",
    )
    with pytest.raises(InvalidAuthCodeError):
        service.verify_auth_code(
            code,
            code_verifier="WRONG-VERIFIER",
            redirect_uri="https://x.test/cb",
            client_id="A",
        )


def test_auth_code_redirect_uri_mismatch(service: AuthService) -> None:
    code = service.issue_auth_code(
        api_key="k",
        code_challenge="cc",
        code_challenge_method="plain",
        redirect_uri="https://a.test",
        client_id=None,
    )
    with pytest.raises(InvalidAuthCodeError):
        service.verify_auth_code(
            code, code_verifier="cc", redirect_uri="https://b.test", client_id=None
        )


def test_auth_code_client_id_mismatch(service: AuthService) -> None:
    code = service.issue_auth_code(
        api_key="k",
        code_challenge="cc",
        code_challenge_method="plain",
        redirect_uri="https://a.test",
        client_id="alpha",
    )
    with pytest.raises(InvalidAuthCodeError):
        service.verify_auth_code(
            code, code_verifier="cc", redirect_uri="https://a.test", client_id="beta"
        )


def test_access_token_roundtrip(service: AuthService) -> None:
    encrypted = service.encrypt_api_key("ck-secret")
    token, ttl = service.issue_access_token(encrypted)
    assert ttl == 3600
    assert service.verify_access_token(token) == "ck-secret"


def test_access_token_rejects_auth_code(service: AuthService) -> None:
    code = service.issue_auth_code(
        api_key="k",
        code_challenge="cc",
        code_challenge_method="plain",
        redirect_uri="https://a.test",
        client_id=None,
    )
    with pytest.raises(InvalidAccessTokenError):
        service.verify_access_token(code)


def test_expired_auth_code_rejected() -> None:
    svc = AuthService(
        AuthConfig(
            signing_key="s",
            encryption_key="e",
            issuer="https://t.test/",
            auth_code_ttl=1,
            access_token_ttl=3600,
        )
    )
    code = svc.issue_auth_code(
        api_key="k",
        code_challenge="cc",
        code_challenge_method="plain",
        redirect_uri="https://a.test",
        client_id=None,
    )
    time.sleep(1.5)
    with pytest.raises(InvalidAuthCodeError):
        svc.verify_auth_code(
            code, code_verifier="cc", redirect_uri="https://a.test", client_id=None
        )


def test_signing_key_rotation_invalidates_tokens(service: AuthService) -> None:
    token, _ = service.issue_access_token(service.encrypt_api_key("k"))
    rotated = AuthService(
        AuthConfig(
            signing_key="DIFFERENT-SIGNING-KEY",
            encryption_key="encryption-secret-for-tests",
            issuer="https://test.example/",
        )
    )
    with pytest.raises(InvalidAccessTokenError):
        rotated.verify_access_token(token)
