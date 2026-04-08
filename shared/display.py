"""Notebook display helpers — pretty-printers built on `rich`.

The functions here are what every notebook calls to make output legible:
    run_as(user, prompt, agent)        run an agent and pretty-print the trace
    show_token(jwt, label=...)         decode + display a JWT
    compare_tokens(t1, t2, ...)        side-by-side claim diff
    show_what_tool_saw(service_url)    GET /debug/last-request and pretty-print
    three_legged_login(client_id, ...) consent flow for pattern 8
"""

from __future__ import annotations

import secrets
import urllib.parse
import webbrowser
from typing import Any

import httpx
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from shared.agent import Agent, AgentResult
from shared.auth import decode_jwt
from shared.config import (
    AUTH_ENDPOINT,
    DOCUMENT_SERVICE_URL,
    EXPENSE_SERVICE_URL,
    KEYCLOAK_REALM,
    KEYCLOAK_URL,
    TOKEN_ENDPOINT,
    USER_DIRECT_CLIENT_ID,
)

console = Console()


# ----- run_as -----


def run_as(user: str, prompt: str, agent: Agent) -> AgentResult:
    """Run a prompt as a given user and pretty-print the result."""
    header = Text(f"[{user}] ", style="bold cyan") + Text(prompt, style="white")
    console.print(Panel(header, border_style="cyan", expand=False))

    result = agent.run(user, prompt)

    if result.tool_calls:
        tbl = Table(title="tool calls", show_header=True, header_style="bold")
        tbl.add_column("#", style="dim", width=3)
        tbl.add_column("tool")
        tbl.add_column("args")
        tbl.add_column("status", justify="right")
        tbl.add_column("result", overflow="fold")
        for i, tc in enumerate(result.tool_calls, 1):
            status_str = str(tc.status) if tc.status is not None else "-"
            status_style = (
                "green" if tc.status and 200 <= tc.status < 300 else "red"
            )
            result_text = tc.error if tc.error else tc.result_summary
            tbl.add_row(
                str(i),
                tc.name,
                str(tc.args),
                Text(status_str, style=status_style),
                result_text,
            )
        console.print(tbl)

    console.print(Panel(result.content or "(no content)", title="answer", border_style="green", expand=False))
    return result


# ----- token display -----


_HIGHLIGHT_CLAIMS = ("sub", "preferred_username", "aud", "azp", "iss",
                     "role", "department", "reports_to", "scope")


def show_token(token: str, label: str = "JWT") -> None:
    """Decode (without verifying) and pretty-print a JWT."""
    claims = decode_jwt(token)
    tbl = Table(title=label, show_header=True, header_style="bold")
    tbl.add_column("claim", style="cyan")
    tbl.add_column("value")
    # Highlighted claims first, in canonical order. Wrap values in Text() so
    # square-bracketed values like list-encoded `aud` aren't interpreted as
    # rich markup tags.
    for k in _HIGHLIGHT_CLAIMS:
        if k in claims:
            tbl.add_row(k, Text(_format_value(claims[k])))
    # Everything else after.
    for k in sorted(claims):
        if k not in _HIGHLIGHT_CLAIMS:
            tbl.add_row(k, Text(_format_value(claims[k])))
    console.print(tbl)


def compare_tokens(
    token_a: str,
    token_b: str,
    label_a: str = "token A",
    label_b: str = "token B",
) -> None:
    """Side-by-side comparison of two tokens, highlighting where they differ."""
    a = decode_jwt(token_a)
    b = decode_jwt(token_b)

    tbl = Table(title=f"{label_a}  vs  {label_b}", show_header=True, header_style="bold")
    tbl.add_column("claim", style="cyan")
    tbl.add_column(label_a)
    tbl.add_column(label_b)

    keys = []
    for k in _HIGHLIGHT_CLAIMS:
        if k in a or k in b:
            keys.append(k)
    for k in sorted(set(a) | set(b)):
        if k not in _HIGHLIGHT_CLAIMS:
            keys.append(k)

    for k in keys:
        va_str = _format_value(a.get(k, "<missing>"))
        vb_str = _format_value(b.get(k, "<missing>"))
        if a.get(k) != b.get(k):
            va = Text(va_str, style="bold yellow")
            vb = Text(vb_str, style="bold yellow")
        else:
            # Wrap in Text() to prevent rich from parsing square brackets
            # (e.g. list-encoded `aud`) as markup tags.
            va = Text(va_str)
            vb = Text(vb_str)
        tbl.add_row(k, va, vb)

    console.print(tbl)


def _format_value(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    if isinstance(v, str) and len(v) > 80:
        return v[:80] + "..."
    return str(v)


# ----- show_what_tool_saw -----


_SERVICE_NAMES = {
    EXPENSE_SERVICE_URL: "expense-service",
    DOCUMENT_SERVICE_URL: "document-service",
}


def show_what_tool_saw(service_url: str = EXPENSE_SERVICE_URL) -> dict[str, Any]:
    """GET /debug/last-request from a service and pretty-print what auth context
    it actually received on its most recent request.

    This is the punchline of every notebook: regardless of how clever the
    agent's auth strategy is, the service either DOES or DOES NOT have a
    proven user identity to work with.
    """
    name = _SERVICE_NAMES.get(service_url, service_url)
    r = httpx.get(f"{service_url}/debug/last-request", timeout=5.0)
    r.raise_for_status()
    body = r.json()

    method = body.get("method", "unknown")
    detail = body.get("detail", "")
    user_id = body.get("user_id")
    claims = body.get("claims") or {}

    method_color = {
        "none": "red",
        "api_key": "yellow",
        "string_id": "yellow",
        "jwt": "blue",
        "scoped_jwt": "green",
    }.get(method, "white")

    panel_body = Text()
    panel_body.append("method:  ", style="dim")
    panel_body.append(f"{method}\n", style=f"bold {method_color}")
    panel_body.append("user_id: ", style="dim")
    panel_body.append(f"{user_id or '<none>'}\n")
    panel_body.append("detail:  ", style="dim")
    panel_body.append(f"{detail}\n")
    if claims:
        panel_body.append("\nclaims:  ", style="dim")
        panel_body.append(
            f"sub={claims.get('sub','-')[:8] if claims.get('sub') else '-'}..., "
            f"role={claims.get('role','-')}, "
            f"department={claims.get('department','-')}, "
            f"aud={claims.get('aud','-')}, "
            f"azp={claims.get('azp','-')}"
        )

    console.print(
        Panel(
            panel_body,
            title=f"{name} /debug/last-request",
            border_style=method_color,
            expand=False,
        )
    )
    return body


# ----- 3LO consent flow (pattern 8) -----


def three_legged_login(
    client_id: str = USER_DIRECT_CLIENT_ID,
    scope: str = "openid",
    redirect_uri: str = "http://localhost:8765/callback",
) -> str:
    """Walk the user through a Keycloak OAuth consent flow and return the
    resulting access token.

    Pattern 8: the agent is OUT OF THE CREDENTIAL CHAIN. The user goes
    through Keycloak's consent screen in their own browser, copies the `code`
    query parameter from the redirect URL, and pastes it back into this
    function. The agent never sees the user's password.

    The redirect URI is fake — there's no callback server. The browser will
    show a page-load error after consent; that's expected. The user just
    needs to read the `code=...` query parameter out of the URL bar.
    """
    state = secrets.token_urlsafe(16)
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": scope,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )

    console.print(
        Panel(
            Text(
                "1. Open this URL in your browser (any user from the realm works:\n"
                "   alice / bob / carlo, password: password):\n\n",
                style="white",
            )
            + Text(auth_url, style="bold cyan")
            + Text(
                "\n\n2. After consenting, the browser will redirect to a URL that "
                "looks like:\n\n",
                style="white",
            )
            + Text(
                f"      {redirect_uri}?state={state[:8]}...&code=<CODE>\n\n",
                style="dim",
            )
            + Text(
                "3. The browser will show a connection-refused page (expected — "
                "there is no callback server).\n",
                style="white",
            )
            + Text(
                "4. Copy the `code=<...>` value from the URL bar and paste it below.",
                style="white",
            ),
            title="3-legged OAuth consent",
            border_style="magenta",
            expand=False,
        )
    )

    code = input("Paste code: ").strip()

    r = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=10.0,
    )
    r.raise_for_status()
    access_token = r.json()["access_token"]
    console.print(
        Panel(
            f"got access token (length={len(access_token)})",
            title="success",
            border_style="green",
            expand=False,
        )
    )
    return access_token
