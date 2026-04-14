"""Pattern 7: Tool-Side External Authorization (service side).

Same JWKS-based JWT validation as pattern 5. The addition: the OPA URL is
stored in the identity claims so the service route (approve_expense) can
call OPA for per-resource authorization before executing the action.

OPA evaluates relationship-based rules (tool_side.rego): admin override,
self-access, manager-of-target, department peer read.
"""

import jwt as pyjwt
from fastapi import Request
from jwt import PyJWKClient

from framework.config import (
    EXPECTED_ISSUER,
    EXPENSE_SERVICE_CLIENT_ID,
    DOCUMENT_SERVICE_CLIENT_ID,
    JWKS_URL,
    OPA_URL,
)
from framework.services.identity import Identity

_expense_jwk_client: PyJWKClient | None = None
_document_jwk_client: PyJWKClient | None = None


async def get_expense_identity(request: Request) -> Identity:
    """Validate JWT + store OPA URL for tool-side authz."""
    identity = await _validate_jwt(request, EXPENSE_SERVICE_CLIENT_ID, "_expense")
    if identity.claims is not None:
        identity.claims["_opa_url"] = OPA_URL
    return identity


async def get_document_identity(request: Request) -> Identity:
    """Validate JWT only (no OPA for document reads)."""
    return await _validate_jwt(request, DOCUMENT_SERVICE_CLIENT_ID, "_document")


async def _validate_jwt(request: Request, service_client_id: str, cache_key: str) -> Identity:
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return Identity(method="none", detail="no auth provided")

    token = auth_header.split(" ", 1)[1].strip()

    global _expense_jwk_client, _document_jwk_client
    if cache_key == "_expense":
        if _expense_jwk_client is None:
            _expense_jwk_client = PyJWKClient(JWKS_URL, cache_keys=True, max_cached_keys=10)
        jwk_client = _expense_jwk_client
    else:
        if _document_jwk_client is None:
            _document_jwk_client = PyJWKClient(JWKS_URL, cache_keys=True, max_cached_keys=10)
        jwk_client = _document_jwk_client

    try:
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        claims = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=EXPECTED_ISSUER,
            options={"verify_aud": False},
        )
    except Exception as e:
        return Identity(
            method="none",
            detail=f"jwt validation failed: {type(e).__name__}: {e}",
        )

    aud_value = claims.get("aud")
    audiences = aud_value if isinstance(aud_value, list) else [aud_value] if isinstance(aud_value, str) else []
    is_scoped = audiences == [service_client_id]
    method = "scoped_jwt" if is_scoped else "jwt"

    return Identity(
        method=method,
        user_id=claims.get("preferred_username"),
        claims=claims,
        raw_token=token,
        detail=(
            f"validated JWT issued by {EXPECTED_ISSUER}; "
            f"aud={audiences or '<none>'}; azp={claims.get('azp')}"
        ),
    )
