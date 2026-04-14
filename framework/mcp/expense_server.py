"""Expense MCP server factory.

create_expense_mcp(auth_handler, service_url) returns a FastMCP instance
that exposes get_expenses and approve_expense tools. Each tool call:
  1. Extracts user context from the MCP request _meta
  2. Calls auth_handler.before_tool_call() for pre-call gating
  3. Calls auth_handler.prepare_request() to get outbound headers
  4. Proxies the request to the backend FastAPI expense service
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


def create_expense_mcp(auth_handler: AuthHandler, service_url: str) -> FastMCP:
    """Create a FastMCP server with expense tools."""

    mcp = FastMCP("expense-mcp", stateless_http=True)

    @mcp.tool()
    async def get_expenses(department: str | None = None, ctx: Context = None) -> str:
        """List corporate expenses. Returns expenses scoped to the caller's
        identity. Optionally filter by department.

        Args:
            department: Optional department name to filter by.
        """
        user_context = _extract_user_context(ctx)

        try:
            await auth_handler.before_tool_call(user_context, "get_expenses")
        except AuthorizationDenied as e:
            return json.dumps({"_status": 403, "error": str(e), "_denied_by": "agent_side_opa"})

        headers = await auth_handler.prepare_request(user_context, {})

        params: dict[str, str] = {}
        if department:
            params["department"] = department

        # Check if auth_handler added extra_params (pattern 2 narrowing)
        extra_params = getattr(auth_handler, "_last_extra_params", None)
        if extra_params:
            params.update(extra_params)
            auth_handler._last_extra_params = None

        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{service_url}/expenses",
                    headers=headers,
                    params=params,
                    timeout=15.0,
                )
        except httpx.HTTPError as e:
            return json.dumps({"_status": 0, "error": f"transport error: {type(e).__name__}: {e}"})

        body = _parse_response(r)
        body["_status"] = r.status_code
        return json.dumps(body, default=str)

    @mcp.tool()
    async def approve_expense(expense_id: int, ctx: Context = None) -> str:
        """Approve a pending expense. Only managers (for their direct reports)
        and admins can approve expenses.

        Args:
            expense_id: The numeric ID of the expense to approve.
        """
        user_context = _extract_user_context(ctx)

        try:
            await auth_handler.before_tool_call(user_context, "approve_expense")
        except AuthorizationDenied as e:
            return json.dumps({"_status": 403, "error": str(e), "_denied_by": "agent_side_opa"})

        headers = await auth_handler.prepare_request(user_context, {})

        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{service_url}/expenses/{expense_id}/approve",
                    headers=headers,
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
