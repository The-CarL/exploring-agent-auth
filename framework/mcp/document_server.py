"""Document MCP server factory.

create_document_mcp(auth_handler, service_url) returns a FastMCP instance
that exposes a search_documents tool. Same pattern as the expense server:
extract user context, check auth, proxy to backend.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

from framework.mcp.auth import AuthHandler, AuthorizationDenied


def _extract_user_context(ctx: Context) -> dict[str, Any]:
    """Read user context from the MCP request _meta field."""
    meta = ctx.request_context.meta if ctx.request_context else None
    if meta is None:
        return {}
    return dict(meta.model_extra or {})


def create_document_mcp(auth_handler: AuthHandler, service_url: str) -> FastMCP:
    """Create a FastMCP server with document tools."""

    mcp = FastMCP("document-mcp", stateless_http=True)

    @mcp.tool()
    async def search_documents(q: str | None = None, ctx: Context = None) -> str:
        """Search internal documents by full-text query. Returns documents the
        caller is allowed to see based on access groups and role.

        Args:
            q: Search query that matches against document title and body.
        """
        user_context = _extract_user_context(ctx)

        try:
            await auth_handler.before_tool_call(user_context, "search_documents")
        except AuthorizationDenied as e:
            return json.dumps({"_status": 403, "error": str(e), "_denied_by": "agent_side_opa"})

        headers = await auth_handler.prepare_request(user_context, {})

        params: dict[str, str] = {}
        if q:
            params["q"] = q

        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{service_url}/documents",
                    headers=headers,
                    params=params,
                    timeout=15.0,
                )
        except httpx.HTTPError as e:
            return json.dumps({"_status": 0, "error": f"transport error: {type(e).__name__}: {e}"})

        body = _parse_response(r)
        body["_status"] = r.status_code
        return json.dumps(body, default=str)

    return mcp


def _parse_response(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        body = {"raw_text": r.text}
    if not isinstance(body, dict):
        body = {"body": body}
    return body
