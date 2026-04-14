"""Reusable service-side identity extractors.

Each factory function returns an async get_identity(request) -> Identity callable
that a pattern's service_auth.py can use directly or customize.

Presets (API-key tier, patterns 1-4):
    api_key_identity                -- validates X-API-Key only (pattern 1)
    api_key_with_user_header        -- validates X-API-Key + reads X-User-Id (pattern 2)
    api_key_with_unverified_jwt     -- validates X-API-Key + reads JWT claims without
                                       JWKS verification (pattern 3)
    api_key_with_unverified_jwt_opa -- same + delegates authz to OPA (pattern 4)

Presets (cryptographic tier, patterns 5-8):
    jwt_identity          -- validates Bearer JWT via JWKS (patterns 5, 6, 8)
    jwt_with_opa_identity -- validates JWT via JWKS + OPA tool_side check (pattern 7)
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Awaitable
from typing import Any

import httpx
import jwt as pyjwt
from fastapi import Request
from jwt import PyJWKClient

from framework.services.identity import Identity


# ---------------------------------------------------------------------------
# API-key tier: service trusts the caller via shared secret
# ---------------------------------------------------------------------------


def api_key_identity(expected_key: str) -> Callable[[Request], Awaitable[Identity]]:
    """Validates X-API-Key. No user identity extracted. (Pattern 1)"""

    async def get_identity(request: Request) -> Identity:
        api_key = request.headers.get("x-api-key")
        if not api_key:
            return Identity(method="none", detail="no auth provided")
        if api_key == expected_key:
            return Identity(
                method="api_key",
                detail="shared service credential, no user identity",
            )
        return Identity(
            method="none",
            detail=f"X-API-Key did not match (received prefix: {api_key[:8]}...)",
        )

    return get_identity


def api_key_with_user_header(expected_key: str) -> Callable[[Request], Awaitable[Identity]]:
    """Validates X-API-Key + reads X-User-Id header. (Pattern 2)

    The API key proves the caller is a trusted MCP server. The X-User-Id
    header asserts who the user is. No cryptographic proof of identity,
    but the service trusts it because it trusts the caller.
    """

    async def get_identity(request: Request) -> Identity:
        api_key = request.headers.get("x-api-key")
        if not api_key or api_key != expected_key:
            return Identity(method="none", detail="invalid or missing API key")
        user_id = request.headers.get("x-user-id")
        if not user_id:
            return Identity(
                method="api_key",
                detail="valid API key but no X-User-Id header",
            )
        return Identity(
            method="string_id",
            user_id=user_id,
            detail="API key verified caller; X-User-Id accepted on trust (no crypto proof)",
        )

    return get_identity


def _decode_jwt_unverified(token: str) -> dict[str, Any]:
    """Decode a JWT without signature verification."""
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def api_key_with_unverified_jwt(expected_key: str) -> Callable[[Request], Awaitable[Identity]]:
    """Validates X-API-Key + reads JWT claims without JWKS verification. (Pattern 3)

    The API key proves the caller is trusted. The Bearer token's claims are
    read but NOT cryptographically verified -- the service trusts the token
    content because it trusts the caller. This gives the service user context
    (role, department, username) for inline authz decisions, but without the
    overhead or independence of JWKS validation.
    """

    async def get_identity(request: Request) -> Identity:
        api_key = request.headers.get("x-api-key")
        if not api_key or api_key != expected_key:
            return Identity(method="none", detail="invalid or missing API key")

        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return Identity(
                method="api_key",
                detail="valid API key but no Bearer token",
            )

        token = auth_header.split(" ", 1)[1].strip()
        try:
            claims = _decode_jwt_unverified(token)
        except Exception as e:
            return Identity(
                method="api_key",
                detail=f"valid API key but JWT decode failed: {e}",
            )

        return Identity(
            method="unverified_jwt",
            user_id=claims.get("preferred_username"),
            claims=claims,
            raw_token=token,
            detail=(
                "API key verified caller; JWT claims read without signature verification"
            ),
        )

    return get_identity


def api_key_with_unverified_jwt_opa(
    expected_key: str,
    opa_url: str,
) -> Callable[[Request], Awaitable[Identity]]:
    """Validates X-API-Key + reads JWT claims (unverified) + stores OPA URL. (Pattern 4)

    Same as api_key_with_unverified_jwt, but the OPA URL is stored in
    identity.claims["_opa_url"] so the service route can call OPA for
    authz decisions.
    """
    base = api_key_with_unverified_jwt(expected_key)

    async def get_identity(request: Request) -> Identity:
        identity = await base(request)
        if identity.claims is not None:
            identity.claims["_opa_url"] = opa_url
        return identity

    return get_identity


# ---------------------------------------------------------------------------
# Cryptographic tier: service verifies identity independently via JWKS
# ---------------------------------------------------------------------------


def jwt_identity(
    jwks_url: str,
    expected_issuer: str,
    service_client_id: str,
) -> Callable[[Request], Awaitable[Identity]]:
    """Validates Bearer JWT signature via JWKS. (Patterns 5, 6, 8)

    Distinguishes broad-audience tokens (method="jwt") from narrow-audience
    tokens (method="scoped_jwt") based on whether aud matches service_client_id.
    """
    jwk_client: PyJWKClient | None = None

    def _get_jwk_client() -> PyJWKClient:
        nonlocal jwk_client
        if jwk_client is None:
            jwk_client = PyJWKClient(jwks_url, cache_keys=True, max_cached_keys=10)
        return jwk_client

    async def get_identity(request: Request) -> Identity:
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return Identity(method="none", detail="no auth provided")

        token = auth_header.split(" ", 1)[1].strip()
        try:
            signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
            claims = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=expected_issuer,
                options={"verify_aud": False},
            )
        except Exception as e:
            return Identity(
                method="none",
                detail=f"jwt validation failed: {type(e).__name__}: {e}",
            )

        aud_value = claims.get("aud")
        audiences: list[str]
        if isinstance(aud_value, list):
            audiences = aud_value
        elif isinstance(aud_value, str):
            audiences = [aud_value]
        else:
            audiences = []

        is_scoped = audiences == [service_client_id]
        method = "scoped_jwt" if is_scoped else "jwt"

        return Identity(
            method=method,
            user_id=claims.get("preferred_username"),
            claims=claims,
            raw_token=token,
            detail=(
                f"validated JWT issued by {expected_issuer}; "
                f"aud={audiences or '<none>'}; azp={claims.get('azp')}"
            ),
        )

    return get_identity


def jwt_with_opa_identity(
    jwks_url: str,
    expected_issuer: str,
    service_client_id: str,
    opa_url: str,
) -> Callable[[Request], Awaitable[Identity]]:
    """Validates JWT via JWKS + stores OPA URL for tool-side authz. (Pattern 7)

    The OPA call happens in the service route (approve_expense), not here,
    because it needs the resource being accessed (target user, action).
    """
    base = jwt_identity(jwks_url, expected_issuer, service_client_id)

    async def get_identity(request: Request) -> Identity:
        identity = await base(request)
        if identity.claims is not None:
            identity.claims["_opa_url"] = opa_url
        return identity

    return get_identity
