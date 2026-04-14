"""Pattern 5: JWT Passthrough.

The MCP server forwards the user's JWT (received from the agent caller)
as a Bearer token. No API key. The service validates the JWT signature
via JWKS and extracts cryptographically proven claims.

This is the first pattern where the service verifies identity
independently -- it doesn't rely on the caller being trusted.
"""

from framework.mcp.auth import AuthHandler


class JWTPassthroughHandler(AuthHandler):
    async def prepare_request(self, user_context, headers):
        jwt = user_context.get("jwt")
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"
        return headers


auth_handler = JWTPassthroughHandler()
