"""Thin wrapper around the OpenAI Agents SDK.

The underlying framework does the loop: it calls the LLM, executes tool
calls, feeds results back, and terminates when the model produces a final
answer. This file adds only two things on top:

    1. A convenience class (Agent) that pairs an SDK agent with an
       AuthStrategy, so notebook code can say
           agent = Agent(strategy=ServiceCredentialAuth(), tools=ALL_TOOLS)
       and then reuse the same object across three run_as() calls.
    2. An AgentResult shape with flat ToolCallTrace entries that
       shared.display.run_as() knows how to render.

The auth strategy is threaded through to each tool call via the SDK's
local-context mechanism (RunContextWrapper). Tools in shared.tools read
the strategy + user from the context and call strategy.prepare() before
making HTTP requests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agents import Agent as SDKAgent, Runner
from agents.items import ToolCallItem, ToolCallOutputItem

from shared.auth import AuthStrategy
from shared.config import OPENAI_MODEL
from shared.tools import ALL_TOOLS, AgentAuthContext

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


class Agent:
    """Pairs an SDK Agent with an AuthStrategy, for notebook convenience.

    The SDK Agent itself is reusable across runs; we only create it once
    per Agent() instantiation. Each call to .run() builds a fresh
    AgentAuthContext carrying (strategy, user) and hands it to
    Runner.run_sync() via the context= kwarg.
    """

    def __init__(
        self,
        strategy: AuthStrategy,
        tools: list | None = None,
        instructions: str = DEFAULT_INSTRUCTIONS,
        model: str = OPENAI_MODEL,
    ):
        self.strategy = strategy
        self._sdk_agent = SDKAgent[AgentAuthContext](
            name="agentauth-assistant",
            instructions=instructions,
            model=model,
            tools=tools if tools is not None else ALL_TOOLS,
        )

    def run(self, user: str, prompt: str, max_turns: int = 6) -> AgentResult:
        context = AgentAuthContext(strategy=self.strategy, user=user)
        sdk_result = Runner.run_sync(
            self._sdk_agent,
            input=prompt,
            context=context,
            max_turns=max_turns,
        )
        return AgentResult(
            content=sdk_result.final_output or "",
            tool_calls=_extract_traces(sdk_result),
        )


def _extract_traces(sdk_result: Any) -> list[ToolCallTrace]:
    """Walk sdk_result.new_items and pull out (name, args, status, summary)
    for every tool invocation the run produced.

    The SDK emits ToolCallItem (the request) and ToolCallOutputItem (the
    response) as separate items. We match them by call_id where possible
    and build one flat ToolCallTrace per request.
    """
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
