"""Notebook display helpers built on rich.

The functions here are what every notebook calls to make output legible:
    show_token(jwt, label=...)         decode + display a JWT
    compare_tokens(t1, t2, ...)        side-by-side claim diff
    show_what_tool_saw(service_url)    GET /debug/last-request and pretty-print
    three_legged_login(client_id, ...) consent flow for pattern 8
"""

from __future__ import annotations

import base64
import secrets
import urllib.parse
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from framework.auth_helpers import decode_jwt
from framework.config import (
    AUTH_ENDPOINT,
    KEYCLOAK_REALM,
    KEYCLOAK_URL,
    TOKEN_ENDPOINT,
    USER_DIRECT_CLIENT_ID,
)

console = Console()


# ----- token display -----


_HIGHLIGHT_CLAIMS = ("sub", "preferred_username", "aud", "azp", "iss",
                     "role", "department", "reports_to", "scope")


def show_token(token: str, label: str = "JWT") -> None:
    """Decode (without verifying) and pretty-print a JWT."""
    claims = decode_jwt(token)
    tbl = Table(title=label, show_header=True, header_style="bold")
    tbl.add_column("claim", style="cyan")
    tbl.add_column("value")
    for k in _HIGHLIGHT_CLAIMS:
        if k in claims:
            tbl.add_row(k, Text(_format_value(claims[k])))
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


async def show_what_tool_saw(service_url: str, service_name: str | None = None) -> dict[str, Any]:
    """GET /debug/last-request from a service and pretty-print what auth context
    it actually received on its most recent request.

    This is the punchline of every notebook: regardless of how clever the
    agent's auth strategy is, the service either DOES or DOES NOT have a
    proven user identity to work with.

    Async because the backend services run as asyncio tasks in the same event
    loop (Jupyter's). A sync httpx.get() would block the loop and prevent the
    servers from responding.
    """
    name = service_name or service_url
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{service_url}/debug/last-request", timeout=5.0)
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
    through Keycloak's consent screen in their own browser, copies the code
    query parameter from the redirect URL, and pastes it back into this
    function. The agent never sees the user's password.

    The redirect URI is fake (no callback server). The browser will show a
    page-load error after consent; that's expected. The user just needs to
    read the code=... query parameter out of the URL bar.

    Uses PKCE (RFC 7636) as required by Keycloak 26.x.
    """
    import hashlib

    state = secrets.token_urlsafe(16)

    # PKCE: generate code_verifier and code_challenge (S256)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": scope,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )

    # Print the URL as plain text so it's easy to copy/paste
    console.print("\n[bold magenta]3-legged OAuth consent[/bold magenta]\n")
    console.print("1. Open this URL in your browser (alice / bob / dave, password: password):\n")
    console.print(auth_url, soft_wrap=True)
    console.print(
        "\n2. After consenting, the browser will redirect to a URL like:\n"
        f"   {redirect_uri}?state=...&code=<CODE>\n\n"
        "3. The browser will show a connection-refused page (expected, no callback server).\n"
        "4. Copy the code=<...> value from the URL bar and paste it below.\n"
    )

    code = input("Paste code: ").strip()

    r = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        timeout=10.0,
    )
    r.raise_for_status()
    access_token = r.json()["access_token"]
    console.print(f"\n[green]got access token (length={len(access_token)})[/green]")
    return access_token
