"""Tool definitions for the demo.

A `Tool` is a thin wrapper around an HTTP endpoint plus an OpenAI-format
function schema. The tool's `call()` method asks an `AuthStrategy` to prepare
the request, then makes the HTTP call. Three tool instances at the bottom:
get_expenses, approve_expense, search_documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from shared.auth import AuthStrategy
from shared.config import DOCUMENT_SERVICE_URL, EXPENSE_SERVICE_URL


@dataclass
class Tool:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    service_url: str
    path_template: str  # e.g. "/expenses/{expense_id}/approve"
    http_method: str = "GET"

    @property
    def openai_spec(self) -> dict[str, Any]:
        """The OpenAI tool-spec dict that goes in `tools=[...]`."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    def call(self, strategy: AuthStrategy, user: str, **args: Any) -> dict[str, Any]:
        """Invoke the tool. The strategy decides how to attach auth."""
        # Copy args so the strategy can mutate without affecting the caller.
        args = dict(args)
        prepared = strategy.prepare(self.name, user, args)

        # Substitute path template params, e.g. /expenses/{expense_id}/approve.
        path = self.path_template
        path_param_names = re.findall(r"\{(\w+)\}", path)
        for name in path_param_names:
            if name not in args:
                raise ValueError(f"tool {self.name!r} requires path param {name!r}")
            path = path.replace("{" + name + "}", str(args.pop(name)))

        # Remaining args go into the query string for GET, or merge with
        # extra_params from the strategy. POST endpoints in this repo don't
        # take a JSON body — approve_expense is path-only.
        query_params = {**args, **prepared.extra_params}

        url = self.service_url + path
        method = self.http_method.upper()
        if method == "GET":
            r = httpx.get(url, headers=prepared.headers, params=query_params, timeout=15.0)
        elif method == "POST":
            r = httpx.post(url, headers=prepared.headers, params=query_params, timeout=15.0)
        else:
            raise ValueError(f"unsupported http method {method!r}")

        # Don't raise on non-2xx — the LLM benefits from seeing the error body
        # so it can react. Return both status and body.
        try:
            body = r.json()
        except Exception:
            body = {"raw_text": r.text}
        return {
            "_status": r.status_code,
            **body,
        }


# ----- the three demo tools -----


get_expenses = Tool(
    name="get_expenses",
    description=(
        "List corporate expenses. Returns expenses scoped to the caller's "
        "identity (or all of them if the caller is unauthenticated). "
        "Optionally filter by department."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "department": {
                "type": "string",
                "description": "Optional: only return expenses for this department",
            },
        },
    },
    service_url=EXPENSE_SERVICE_URL,
    path_template="/expenses",
    http_method="GET",
)


approve_expense = Tool(
    name="approve_expense",
    description=(
        "Approve a pending expense. Only managers (for their direct reports) "
        "and admins can approve expenses."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "expense_id": {
                "type": "integer",
                "description": "ID of the expense to approve",
            },
        },
        "required": ["expense_id"],
    },
    service_url=EXPENSE_SERVICE_URL,
    path_template="/expenses/{expense_id}/approve",
    http_method="POST",
)


search_documents = Tool(
    name="search_documents",
    description=(
        "Search internal documents by full-text query. Returns documents the "
        "caller is allowed to see based on access groups and role."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "q": {
                "type": "string",
                "description": "Search query (matches title and body)",
            },
        },
    },
    service_url=DOCUMENT_SERVICE_URL,
    path_template="/documents",
    http_method="GET",
)


# Convenience: the canonical tool list every notebook uses.
ALL_TOOLS: list[Tool] = [get_expenses, approve_expense, search_documents]
