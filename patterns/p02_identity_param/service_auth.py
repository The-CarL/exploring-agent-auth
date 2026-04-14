"""Pattern 2: Identity Parameter (service side).

The service validates the API key (proves the caller is a trusted MCP
server), then reads X-User-Id as the asserted user identity. No crypto
proof, but trusted because the caller is authenticated.
"""

from fastapi import Request
from framework.services.identity import Identity

EXPECTED_API_KEY = "dev-shared-api-key"


async def get_expense_identity(request: Request) -> Identity:
    api_key = request.headers.get("x-api-key")
    if not api_key or api_key != EXPECTED_API_KEY:
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


# Document service uses the same auth
get_document_identity = get_expense_identity
