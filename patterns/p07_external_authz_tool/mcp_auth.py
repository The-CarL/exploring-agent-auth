"""Pattern 7: Tool-Side External Authorization (OPA ReBAC).

On the MCP side, this is identical to pattern 5: forward the user's JWT
(received from the agent caller) as a Bearer token. The fine-grained OPA
check happens on the service side.
"""

from framework.mcp.auth import AuthHandler


class JWTPassthroughHandler(AuthHandler):
    async def prepare_request(self, user_context, headers):
        jwt = user_context.get("jwt")
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"
        return headers


auth_handler = JWTPassthroughHandler()
