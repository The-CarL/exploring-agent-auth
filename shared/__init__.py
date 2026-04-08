"""Shared host-side code for the exploring-agent-auth notebooks.

This package is installed as an editable wheel by `uv sync` (via hatchling),
so notebooks can `from shared.X import ...` regardless of the current working
directory.

Module overview:

    config.py    -- environment-driven endpoints and client IDs
    auth.py      -- one strategy class per pattern (1-8)
    tools.py     -- get_expenses, approve_expense, search_documents
    agent.py     -- minimal OpenAI chat.completions tool-calling loop
    display.py   -- run_as, show_token, compare_tokens, show_what_tool_saw,
                    three_legged_login
"""
