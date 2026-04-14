"""AuthHandler base class for MCP servers.

Every pattern's mcp_auth.py subclasses AuthHandler and overrides one or both
methods. This is the entire MCP-side plugin interface a learner must understand:

    prepare_request   -- add auth headers to the outbound request (most patterns)
    before_tool_call  -- pre-call gate, used by pattern 3 (agent-side OPA)
"""

from __future__ import annotations

from typing import Any


class AuthorizationDenied(Exception):
    """Raised by before_tool_call when the user is not permitted to invoke a tool."""


class AuthHandler:
    """Customize how the MCP server authenticates to backend services."""

    async def prepare_request(self, user_context: dict[str, Any], headers: dict[str, str]) -> dict[str, str]:
        """Add auth credentials to outbound request headers.

        Args:
            user_context: dict with at least "user" key (username string).
                          Patterns that need tokens add them here.
            headers: the current outbound headers dict.

        Returns:
            The modified headers dict.
        """
        return headers

    async def before_tool_call(self, user_context: dict[str, Any], tool_name: str) -> bool:
        """Pre-call authorization gate. Return True to proceed.

        Raise AuthorizationDenied to deny with a reason message.
        Default: always allow.
        """
        return True
