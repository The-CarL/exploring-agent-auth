"""Tool definitions for the demo, using the OpenAI Agents SDK.

Three `@function_tool`-decorated functions (`get_expenses`, `approve_expense`,
`search_documents`) and one context dataclass (`AgentAuthContext`) that
carries the per-run `AuthStrategy` + username through to each tool call.

The pattern is:

    1. shared.agent.Agent.run() builds an AgentAuthContext and passes it to
       Runner.run_sync() via the context= kwarg.
    2. The SDK invokes our tools with a RunContextWrapper wrapping that
       context.
    3. Each tool function calls strategy.prepare(tool_name, user, args) to
       learn what headers and extra params to attach, then makes the HTTP call.

That last step is where the auth pattern lives. The rest of the file is
plumbing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from agents import RunContextWrapper, function_tool

from shared.auth import AuthorizationDenied, AuthStrategy
from shared.config import DOCUMENT_SERVICE_URL, EXPENSE_SERVICE_URL


@dataclass
class AgentAuthContext:
    """Local, per-run context passed to every tool call.

    `strategy` determines how outgoing HTTP requests are authenticated.
    `user` is the username the agent is acting for in this run.
    """
    strategy: AuthStrategy
    user: str


def _call_service(
    method: str,
    url: str,
    ctx: RunContextWrapper[AgentAuthContext],
    tool_name: str,
    strategy_args: dict[str, Any],
    query_args: dict[str, Any] | None = None,
) -> str:
    """Shared HTTP helper used by every tool.

    Asks the strategy to prepare the request, makes the call, and returns a
    JSON-serialized response that the LLM can reason over. Returns an error
    JSON (rather than raising) on authorization denial and on transport
    failures, so the LLM can explain the outcome to the user.
    """
    strategy = ctx.context.strategy
    user = ctx.context.user
    if query_args is None:
        query_args = dict(strategy_args)

    try:
        prepared = strategy.prepare(tool_name, user, strategy_args)
    except AuthorizationDenied as e:
        return json.dumps({
            "_status": 403,
            "error": str(e),
            "_denied_by": "agent_side_opa",
        })

    params = {**query_args, **prepared.extra_params}
    try:
        if method == "GET":
            r = httpx.get(url, headers=prepared.headers, params=params, timeout=15.0)
        elif method == "POST":
            r = httpx.post(url, headers=prepared.headers, params=params, timeout=15.0)
        else:
            return json.dumps({"_status": 0, "error": f"unsupported method {method}"})
    except httpx.HTTPError as e:
        return json.dumps({"_status": 0, "error": f"transport error: {type(e).__name__}: {e}"})

    try:
        body = r.json()
    except ValueError:
        body = {"raw_text": r.text}

    body_out: dict[str, Any] = {"_status": r.status_code}
    if isinstance(body, dict):
        body_out.update(body)
    else:
        body_out["body"] = body
    return json.dumps(body_out, default=str)


@function_tool
def get_expenses(
    ctx: RunContextWrapper[AgentAuthContext],
    department: str | None = None,
) -> str:
    """List corporate expenses. Returns expenses scoped to the caller's
    identity (or all of them if the caller is unauthenticated). Optionally
    filter by department.

    Args:
        department: Optional department name. Only expenses from this
            department will be returned.
    """
    args: dict[str, Any] = {}
    if department:
        args["department"] = department
    return _call_service(
        "GET",
        f"{EXPENSE_SERVICE_URL}/expenses",
        ctx,
        "get_expenses",
        strategy_args=args,
    )


@function_tool
def approve_expense(
    ctx: RunContextWrapper[AgentAuthContext],
    expense_id: int,
) -> str:
    """Approve a pending expense. Only managers (for their direct reports)
    and admins can approve expenses.

    Args:
        expense_id: The numeric ID of the expense to approve.
    """
    # expense_id lives in the path; it's passed to the strategy so the
    # strategy has full context (none of the current strategies use it,
    # but future ones might) and empty query args are sent on the wire.
    return _call_service(
        "POST",
        f"{EXPENSE_SERVICE_URL}/expenses/{expense_id}/approve",
        ctx,
        "approve_expense",
        strategy_args={"expense_id": expense_id},
        query_args={},
    )


@function_tool
def search_documents(
    ctx: RunContextWrapper[AgentAuthContext],
    q: str | None = None,
) -> str:
    """Search internal documents by full-text query. Returns documents the
    caller is allowed to see based on access groups and role.

    Args:
        q: Search query that matches against document title and body.
    """
    args: dict[str, Any] = {}
    if q:
        args["q"] = q
    return _call_service(
        "GET",
        f"{DOCUMENT_SERVICE_URL}/documents",
        ctx,
        "search_documents",
        strategy_args=args,
    )


# Canonical tool list every notebook uses.
ALL_TOOLS = [get_expenses, approve_expense, search_documents]
