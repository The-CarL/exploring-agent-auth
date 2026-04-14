"""Pattern 6: Token Exchange (RFC 8693).

The MCP server receives the user's JWT from the agent caller, exchanges it
with Keycloak for a narrowed token scoped to the target service, and
forwards the exchanged token. This is the one pattern where the MCP server
transforms the credential rather than just forwarding it.
"""

from framework.auth_helpers import exchange_token
from framework.config import TOOL_TO_TARGET_CLIENT
from framework.mcp.auth import AuthHandler


class TokenExchangeHandler(AuthHandler):
    def __init__(self):
        self._current_tool_name: str | None = None

    async def before_tool_call(self, user_context, tool_name):
        self._current_tool_name = tool_name
        return True

    async def prepare_request(self, user_context, headers):
        jwt = user_context.get("jwt")
        tool_name = self._current_tool_name or ""
        if jwt:
            target_audience = TOOL_TO_TARGET_CLIENT.get(tool_name)
            if target_audience is None:
                raise ValueError(f"no target audience configured for tool {tool_name!r}")
            exchanged = exchange_token(jwt, target_audience)
            headers["Authorization"] = f"Bearer {exchanged}"
        return headers


auth_handler = TokenExchangeHandler()
