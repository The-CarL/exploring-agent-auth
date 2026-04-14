"""Agent wrapper using the OpenAI Agents SDK with MCP server support.

Instead of @function_tool definitions, the agent discovers and calls tools
exposed by MCP servers. User context flows from the agent to MCP servers
via the _meta field on each tools/call request, using MCPToolMetaResolver.

Agent.run() is async so it stays in the same event loop as the MCP servers
started by PatternRunner. This is critical: MCP client sessions are bound to
the event loop they were created in. Running them from a separate thread's
loop (as _run_coroutine did) causes timeouts because the session's response
stream is on a different loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agents import Agent as SDKAgent, Runner
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.mcp.server import MCPServerStreamableHttp
from agents.mcp.util import MCPToolMetaContext

from framework.config import OPENAI_MODEL

DEFAULT_INSTRUCTIONS = """\
You are an internal company assistant for the agentauth platform. You help
users get information about their expenses and search internal documents.
You have access to tools; use them when the user asks for data. Be concise
in your answers and reflect the actual data the tools return. Do not
invent expenses or documents that you didn't see.

If a tool returns an authorization error, briefly explain to the user
what happened. Don't pretend the call succeeded.
"""


@dataclass
class AgentAuthContext:
    """Per-run context passed to the SDK via Runner.run(context=...).

    The MCPToolMetaResolver reads this to populate _meta on each tool call.
    The jwt field carries the user's token, simulating a real-world flow
    where the agent is invoked with a JWT from an upstream service.
    """
    user: str
    jwt: str | None = None


@dataclass
class ToolCallTrace:
    name: str
    args: dict[str, Any]
    status: int | None
    result_summary: str
    error: str | None = None


@dataclass
class AgentResult:
    content: str
    tool_calls: list[ToolCallTrace] = field(default_factory=list)


def _make_meta_resolver():
    """Create a MCPToolMetaResolver that passes user context to MCP servers."""

    def resolver(meta_ctx: MCPToolMetaContext) -> dict[str, Any] | None:
        ctx = meta_ctx.run_context.context  # AgentAuthContext
        meta: dict[str, Any] = {"user": ctx.user}
        if ctx.jwt:
            meta["jwt"] = ctx.jwt
        return meta

    return resolver


class Agent:
    """Wraps the OpenAI Agents SDK with MCP server tool sources.

    Usage:
        mcp_servers = [MCPServerStreamableHttp(...), ...]
        agent = Agent(mcp_servers=mcp_servers)
        result = agent.run("alice", "show my expenses")
    """

    def __init__(
        self,
        mcp_servers: list[MCPServerStreamableHttp],
        instructions: str = DEFAULT_INSTRUCTIONS,
        model: str = OPENAI_MODEL,
    ):
        self.mcp_servers = mcp_servers
        self._instructions = instructions
        self._model = model

    async def run(self, user: str, prompt: str, jwt: str | None = None, max_turns: int = 6) -> AgentResult:
        context = AgentAuthContext(user=user, jwt=jwt)
        sdk_agent = SDKAgent[AgentAuthContext](
            name="agentauth-assistant",
            instructions=self._instructions,
            model=self._model,
            mcp_servers=self.mcp_servers,
        )
        sdk_result = await Runner.run(
            sdk_agent,
            input=prompt,
            context=context,
            max_turns=max_turns,
        )
        return AgentResult(
            content=sdk_result.final_output or "",
            tool_calls=_extract_traces(sdk_result),
        )


def _extract_traces(sdk_result: Any) -> list[ToolCallTrace]:
    """Walk sdk_result.new_items and build flat ToolCallTrace entries."""
    traces: list[ToolCallTrace] = []
    by_call_id: dict[str, ToolCallTrace] = {}

    for item in sdk_result.new_items:
        if isinstance(item, ToolCallItem):
            name, args_dict, call_id = _parse_tool_call(item)
            trace = ToolCallTrace(
                name=name,
                args=args_dict,
                status=None,
                result_summary="(pending)",
            )
            traces.append(trace)
            if call_id:
                by_call_id[call_id] = trace
        elif isinstance(item, ToolCallOutputItem):
            call_id = _call_id_of(item.raw_item)
            target = by_call_id.get(call_id) if call_id else (traces[-1] if traces else None)
            if target is None:
                continue
            output = item.output
            if not isinstance(output, str):
                output = str(output)
            status, error = _parse_output_status(output)
            target.status = status
            target.result_summary = output[:300]
            target.error = error

    return traces


def _parse_tool_call(item: ToolCallItem) -> tuple[str, dict[str, Any], str | None]:
    raw = item.raw_item
    name = _attr_or_key(raw, "name") or "unknown"
    args_raw = _attr_or_key(raw, "arguments")
    if isinstance(args_raw, str):
        try:
            args_dict = json.loads(args_raw) if args_raw else {}
        except json.JSONDecodeError:
            args_dict = {"_raw": args_raw}
    elif isinstance(args_raw, dict):
        args_dict = args_raw
    else:
        args_dict = {}
    return name, args_dict, _call_id_of(raw)


def _call_id_of(raw: Any) -> str | None:
    for key in ("call_id", "id", "tool_call_id"):
        v = _attr_or_key(raw, key)
        if v:
            return v
    return None


def _attr_or_key(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key)
    return None


def _parse_output_status(output: str) -> tuple[int | None, str | None]:
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    status = parsed.get("_status")
    if not isinstance(status, int):
        status = None
    error = parsed.get("error")
    if error is not None and not isinstance(error, str):
        error = str(error)
    return status, error
