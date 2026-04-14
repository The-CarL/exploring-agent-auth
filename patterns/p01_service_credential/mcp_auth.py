"""Pattern 1: Service Credential.

The MCP server uses a single static API key for every tool call.
No user identity is forwarded to the backend service.
"""

from framework.config import SHARED_SERVICE_API_KEY
from framework.mcp.auth import AuthHandler


class ServiceCredentialHandler(AuthHandler):
    async def prepare_request(self, user_context, headers):
        headers["X-API-Key"] = SHARED_SERVICE_API_KEY
        return headers


auth_handler = ServiceCredentialHandler()
