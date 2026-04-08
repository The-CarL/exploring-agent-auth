"""Minimal OpenAI tool-calling agent.

The thinnest loop that demonstrates an LLM choosing tools and acting on the
results — no framework, no LangChain, no agent SDK. The whole point of this
file is that it's small enough to read in one screen so the auth pattern,
not the framework, is the thing readers focus on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from shared.auth import AuthorizationDenied, AuthStrategy
from shared.config import OPENAI_MODEL
from shared.tools import Tool

DEFAULT_SYSTEM_PROMPT = """\
You are an internal company assistant for the agentauth platform. You help
users get information about their expenses and search internal documents.
You have access to tools — use them when the user asks for data. Be concise
in your answers and reflect the actual data the tools return; do not invent
expenses or documents that you didn't see.

If a tool returns an authorization error, briefly explain to the user what
happened — don't pretend the call succeeded.
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
    iterations: int = 0


class Agent:
    """A tool-calling agent that uses a single AuthStrategy for every call.

    The strategy is what the eight notebooks vary; everything else stays
    constant. Each notebook constructs an Agent with a different strategy
    instance and runs the same prompts as alice / bob / carlo.
    """

    def __init__(
        self,
        strategy: AuthStrategy,
        tools: list[Tool],
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model: str = OPENAI_MODEL,
    ):
        self.client = OpenAI()  # reads OPENAI_API_KEY from env
        self.strategy = strategy
        self.tools = {t.name: t for t in tools}
        self.tool_specs = [t.openai_spec for t in tools]
        self.system_prompt = system_prompt
        self.model = model

    def run(self, user: str, prompt: str, max_iterations: int = 5) -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        traces: list[ToolCallTrace] = []

        for iteration in range(1, max_iterations + 1):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tool_specs,
            )
            message = response.choices[0].message

            if not message.tool_calls:
                return AgentResult(
                    content=message.content or "",
                    tool_calls=traces,
                    iterations=iteration,
                )

            # Append the assistant message (with tool_calls) to history.
            messages.append(message.model_dump(exclude_none=True))

            # Execute every tool call the model requested.
            for tc in message.tool_calls:
                if tc.type != "function":
                    continue
                tool = self.tools.get(tc.function.name)
                if tool is None:
                    trace_result = {"error": f"unknown tool: {tc.function.name}"}
                    error_msg = trace_result["error"]
                else:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError as e:
                        args = {}
                        trace_result = {"error": f"bad json args: {e}"}
                        error_msg = trace_result["error"]
                    else:
                        try:
                            trace_result = tool.call(self.strategy, user, **args)
                            error_msg = None
                        except AuthorizationDenied as e:
                            # The strategy refused the call (pattern 3 demo).
                            trace_result = {"_status": 403, "error": str(e), "_denied_by": "agent_side_opa"}
                            error_msg = str(e)
                        except Exception as e:
                            trace_result = {"_status": 0, "error": f"{type(e).__name__}: {e}"}
                            error_msg = str(e)

                # Tool result back to the model — must include tool_call_id.
                result_str = json.dumps(trace_result, default=str)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    }
                )
                traces.append(
                    ToolCallTrace(
                        name=tc.function.name,
                        args=json.loads(tc.function.arguments or "{}"),
                        status=trace_result.get("_status") if isinstance(trace_result, dict) else None,
                        result_summary=result_str[:300],
                        error=error_msg,
                    )
                )

        # Hit max_iterations without a final assistant message.
        return AgentResult(
            content="(agent exceeded max iterations without producing a final answer)",
            tool_calls=traces,
            iterations=max_iterations,
        )
