"""
Flexible auth dependency for the expense-service.

This module is the heart of the "auth-flexible service" design: a single FastAPI
dependency inspects the incoming request and extracts identity from whichever
auth mechanism the caller used. The same code path serves all eight notebook
patterns; the only thing that changes between patterns is which header(s) the
caller sets.

Methods recognized:
    none        - no auth provided
    api_key     - X-API-Key matched the shared service credential
    string_id   - X-User-Id header set (no cryptographic proof)
    jwt         - Authorization: Bearer <validated JWT>, audience NOT this service
    scoped_jwt  - Authorization: Bearer <validated JWT>, audience IS this service

Every request is recorded in the module-level _last_request store so the
notebook can hit /debug/last-request and show what the service actually saw.
This is the punchline of every pattern.
"""

import os
from dataclasses import asdict, dataclass
from typing import Any

import jwt
from fastapi import Request
from jwt import PyJWKClient

# ----- config -----

KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "agentauth")

# Issuer that appears in the `iss` claim of tokens minted by Keycloak. This is
# the URL the *user* sees, not the docker-network address — Keycloak embeds
# whatever frontchannel URL it was reached on into the `iss` claim. Since the
# notebook hits Keycloak via http://localhost:8080 to get the token, that's
# what `iss` will be.
EXPECTED_ISSUER = os.getenv(
    "KEYCLOAK_EXPECTED_ISSUER",
    f"http://localhost:8080/realms/{KEYCLOAK_REALM}",
)

# Inside the docker network we fetch JWKS via the service name, not localhost.
JWKS_URL = f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"

SHARED_SERVICE_API_KEY = os.getenv("SHARED_SERVICE_API_KEY", "dev-shared-api-key")

# This service's own client_id in Keycloak — used to decide if a JWT is
# "scoped" (audience matches us) or just a "broad" passthrough JWT.
SERVICE_CLIENT_ID = os.getenv("SERVICE_CLIENT_ID", "expense-service-client")


# ----- JWKS client (cached) -----

# PyJWKClient handles fetching, caching by `kid`, and refreshing the JWKS
# document automatically. Created lazily on first use so import doesn't fail
# if Keycloak isn't ready yet at container start.
_jwk_client: PyJWKClient | None = None


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(JWKS_URL, cache_keys=True, max_cached_keys=10)
    return _jwk_client


# ----- the identity object the rest of the service uses -----


@dataclass
class RequestIdentity:
    method: str  # one of: none, api_key, string_id, jwt, scoped_jwt
    user_id: str | None = None
    claims: dict[str, Any] | None = None
    raw_token: str | None = None
    detail: str | None = None  # human-readable explanation for /debug/last-request

    def to_dict(self) -> dict[str, Any]:
        # Strip None fields so the debug endpoint output is uncluttered.
        return {k: v for k, v in asdict(self).items() if v is not None}


# Module-level store for /debug/last-request. In a real service this would be
# observability; here it's the demo punchline.
_last_request: dict[str, Any] = {"method": "never", "detail": "no requests yet"}


def _record(identity: RequestIdentity) -> None:
    global _last_request
    _last_request = identity.to_dict()


def get_last_request() -> dict[str, Any]:
    return _last_request


# ----- the FastAPI dependency -----


def get_identity(request: Request) -> RequestIdentity:
    """Extract identity from whichever auth method the caller used.

    Order of precedence: bearer JWT → API key → X-User-Id → none. The first
    method that's present wins; we don't try to combine them.
    """
    headers = request.headers

    # 1) Bearer JWT
    auth_header = headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        identity = _try_parse_jwt(token)
        _record(identity)
        return identity

    # 2) Shared API key
    api_key = headers.get("x-api-key")
    if api_key:
        if api_key == SHARED_SERVICE_API_KEY:
            identity = RequestIdentity(
                method="api_key",
                detail="shared service credential — no user identity",
            )
        else:
            identity = RequestIdentity(
                method="none",
                detail=f"X-API-Key did not match expected value (received prefix: {api_key[:8]}...)",
            )
        _record(identity)
        return identity

    # 3) X-User-Id (string identity, no proof)
    user_id_hdr = headers.get("x-user-id")
    if user_id_hdr:
        identity = RequestIdentity(
            method="string_id",
            user_id=user_id_hdr,
            detail="X-User-Id header — no cryptographic proof, anyone could send this",
        )
        _record(identity)
        return identity

    # 4) Nothing
    identity = RequestIdentity(method="none", detail="no auth provided")
    _record(identity)
    return identity


def _try_parse_jwt(token: str) -> RequestIdentity:
    """Validate a Bearer token. Never raises — falls back to method=none on
    any failure so the service stays predictable for the demo."""
    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=EXPECTED_ISSUER,
            options={"verify_aud": False},  # we inspect aud manually below
        )
    except Exception as e:
        return RequestIdentity(
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

    # "scoped_jwt" means the token was minted for THIS service specifically:
    # the only audience is us. A token that lists multiple audiences (e.g. a
    # broad passthrough user JWT that names every service the agent might
    # call) is reported as plain "jwt" — that's the pattern 5 vs pattern 6
    # contrast. Pattern 6 narrows the aud via token exchange.
    is_scoped = audiences == [SERVICE_CLIENT_ID]
    method = "scoped_jwt" if is_scoped else "jwt"

    return RequestIdentity(
        method=method,
        user_id=claims.get("preferred_username"),
        claims=claims,
        raw_token=token,
        detail=(
            f"validated JWT issued by {EXPECTED_ISSUER}; "
            f"aud={audiences or '<none>'}; azp={claims.get('azp')}"
        ),
    )
