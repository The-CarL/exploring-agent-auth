"""Pattern 3: Inline Claim-Based Authorization (service side).

The service only sees the API key. It has no user identity -- all the claim
reading and narrowing happened at the MCP server before the request arrived.
This is intentionally identical to pattern 1's service auth.
"""

from fastapi import Request
from framework.services.identity import Identity

EXPECTED_API_KEY = "dev-shared-api-key"


async def get_expense_identity(request: Request) -> Identity:
    api_key = request.headers.get("x-api-key")
    if not api_key:
        return Identity(method="none", detail="no auth provided")
    if api_key == EXPECTED_API_KEY:
        return Identity(
            method="api_key",
            detail="shared service credential, no user identity",
        )
    return Identity(
        method="none",
        detail=f"X-API-Key did not match (received prefix: {api_key[:8]}...)",
    )


get_document_identity = get_expense_identity
