"""Pattern 2: Identity Parameter.

The MCP server sends the shared API key (proves it's a trusted caller) plus
an X-User-Id header with the username. The service trusts the identity
because it trusts the caller.
"""

from framework.config import SHARED_SERVICE_API_KEY
from framework.mcp.auth import AuthHandler


class IdentityParamHandler(AuthHandler):
    async def prepare_request(self, user_context, headers):
        headers["X-API-Key"] = SHARED_SERVICE_API_KEY
        user = user_context.get("user")
        if user:
            headers["X-User-Id"] = user
        return headers


auth_handler = IdentityParamHandler()
