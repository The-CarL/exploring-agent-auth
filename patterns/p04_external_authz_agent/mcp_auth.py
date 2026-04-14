"""Pattern 4: External Authorization, agent-side (OPA).

The MCP server checks OPA before making each tool call. OPA evaluates
role-based rules (agent_side.rego): admins can do anything, managers can
read and approve, employees can only read. If OPA denies, the tool call
never reaches the service.

The service still only receives the API key -- it has no user identity
and no knowledge that an OPA check occurred.

This is the pattern's weakness: the agent/MCP server is the sole
enforcement point. A compromised MCP server can skip the OPA check
entirely. The service cannot verify that authorization happened.
"""

import httpx

from framework.auth_helpers import decode_jwt
from framework.config import OPA_URL, SHARED_SERVICE_API_KEY
from framework.mcp.auth import AuthHandler, AuthorizationDenied


class AgentSideOPAHandler(AuthHandler):
    async def before_tool_call(self, user_context, tool_name):
        jwt = user_context.get("jwt")
        if not jwt:
            raise AuthorizationDenied("no JWT provided")

        claims = decode_jwt(jwt)
        opa_input = {
            "input": {
                "user": {
                    "role": claims.get("role"),
                    "department": claims.get("department"),
                    "reports_to": claims.get("reports_to"),
                },
                "tool": tool_name,
                "action": "approve" if tool_name == "approve_expense" else "read",
            }
        }
        r = httpx.post(
            f"{OPA_URL}/v1/data/agentauth/agent_side/decision",
            json=opa_input,
            timeout=5.0,
        )
        r.raise_for_status()
        decision = r.json().get("result") or {}
        if not decision.get("allow"):
            user = user_context.get("user", "unknown")
            raise AuthorizationDenied(
                f"OPA denied {user} calling {tool_name}: {decision.get('reason', 'no reason')}"
            )
        return True

    async def prepare_request(self, user_context, headers):
        headers["X-API-Key"] = SHARED_SERVICE_API_KEY
        return headers


auth_handler = AgentSideOPAHandler()
