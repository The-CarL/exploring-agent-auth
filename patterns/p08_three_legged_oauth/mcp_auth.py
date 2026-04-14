"""Pattern 8: Three-Legged OAuth.

The MCP server uses a token obtained out-of-band through the user's
browser via authorization code flow with PKCE. The user explicitly
consented to the agent acting on their behalf.
"""

from framework.mcp.auth import AuthHandler


class ThreeLeggedOAuthHandler(AuthHandler):
    def __init__(self, access_token: str | None = None):
        self.access_token = access_token

    async def prepare_request(self, user_context, headers):
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers


# The notebook sets auth_handler.access_token after the consent flow.
auth_handler = ThreeLeggedOAuthHandler()
