"""Framework for the exploring-agent-auth teaching repo.

This package is installed as an editable wheel by `uv sync` (via hatchling),
so notebooks can `from framework.X import ...` regardless of the current
working directory.

Module overview:

    config.py        -- environment-driven endpoints and client IDs
    auth_helpers.py  -- fetch_user_jwt, decode_jwt, exchange_token
    agent.py         -- OpenAI Agents SDK wrapper with MCP server support
    runner.py        -- PatternRunner: wires pattern auth into MCP + services
    display.py       -- run_as, show_token, compare_tokens, show_what_tool_saw

    mcp/             -- MCP server factories (expense, document) + AuthHandler
    services/        -- FastAPI service factories (expense, document) + auth presets
"""
