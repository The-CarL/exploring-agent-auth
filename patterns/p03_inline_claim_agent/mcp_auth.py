"""Pattern 3: Inline Claim-Based Authorization (agent-side).

The agent reads the user's JWT claims (role, department) and uses them to
construct narrower tool calls -- e.g. adding a department filter for
managers. The service still only receives the API key; it has no user
identity. The authz logic lives at the agent layer.

In practice, this narrowing would typically live in the agent itself -- in
its system prompt, tool-calling logic, or a pre-processing step that reads
claims and decides how to parameterize each tool call. We implement it here
in the MCP server's AuthHandler because that's where we have a code hook
in this teaching repo, but the effect is the same: the service is blind to
the narrowing and has no way to verify it happened.

This is the pattern's weakness: authz is scattered in agent-side code,
hard to audit, and bypassable if the agent or MCP server is compromised.
"""

from framework.auth_helpers import decode_jwt
from framework.config import SHARED_SERVICE_API_KEY
from framework.mcp.auth import AuthHandler


class InlineClaimAgentHandler(AuthHandler):
    """Reads JWT claims and narrows tool calls before they reach the service.

    In a real deployment, this logic would more likely live in the agent
    itself (e.g. the LLM is instructed to scope queries based on the
    user's role and department). We put it here to make it visible in
    code, but the pedagogical point is the same: the service never sees
    the JWT, only the narrowed query.
    """

    def __init__(self):
        self._last_extra_params: dict | None = None

    async def prepare_request(self, user_context, headers):
        headers["X-API-Key"] = SHARED_SERVICE_API_KEY

        jwt = user_context.get("jwt")
        if not jwt:
            return headers

        # Read claims from the JWT to narrow the tool call
        claims = decode_jwt(jwt)
        role = claims.get("role")
        department = claims.get("department")

        # Coarse, hand-coded narrowing rules -- the kind of thing that grows
        # unmaintainable in real codebases. In practice the agent's system
        # prompt or tool-calling logic would encode these rules.
        if role == "admin":
            pass  # admins see everything; don't narrow
        elif role == "manager" and department:
            self._last_extra_params = {"department": department}

        return headers


auth_handler = InlineClaimAgentHandler()
