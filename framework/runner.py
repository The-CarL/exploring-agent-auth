"""PatternRunner: wires a pattern's auth into MCP servers + backend services.

This is the orchestrator that notebooks use. A single call to PatternRunner
starts backend services on ephemeral ports, starts MCP servers that proxy to
those services with the pattern's auth injected, and creates an agent that
connects to the MCP servers.

Usage in a notebook:
    runner = PatternRunner("p01_service_credential")
    await runner.start()
    await runner.run_as("alice", "What are my recent expenses?")
    runner.show_service_identity()
    await runner.stop()
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
import socket
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from agents.mcp.server import MCPServerStreamableHttp
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from framework.agent import Agent, AgentResult, _make_meta_resolver
from framework.auth_helpers import fetch_user_jwt
from framework.config import (
    EXPECTED_ISSUER,
    EXPENSE_SERVICE_CLIENT_ID,
    DOCUMENT_SERVICE_CLIENT_ID,
    JWKS_URL,
    OPA_URL,
    SHARED_SERVICE_API_KEY,
    USER_PASSWORD,
)
from framework.display import show_what_tool_saw
from framework.mcp.auth import AuthHandler
from framework.mcp.expense_server import create_expense_mcp
from framework.mcp.document_server import create_document_mcp
from framework.services.expense.app import create_app as create_expense_app
from framework.services.document.app import create_app as create_document_app

console = Console()

# Locate the project root (parent of the framework/ package)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_module(name: str, file_path: Path) -> Any:
    """Dynamically load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class PatternRunner:
    """Orchestrates a single pattern's full stack for notebook use."""

    def __init__(self, pattern: str):
        """
        Args:
            pattern: directory name under patterns/, e.g. "p01_service_credential"
        """
        self.pattern = pattern
        self._pattern_dir = _PROJECT_ROOT / "patterns" / pattern

        # Will be populated by start()
        self._expense_service_url: str | None = None
        self._document_service_url: str | None = None
        self._expense_mcp_url: str | None = None
        self._document_mcp_url: str | None = None
        self._servers: list[uvicorn.Server] = []
        self._tasks: list[asyncio.Task] = []
        self._mcp_clients: list[MCPServerStreamableHttp] = []
        self._agent: Agent | None = None
        self._started = False

    async def start(self) -> None:
        """Import pattern auth, start services + MCP servers, create agent."""
        if self._started:
            console.print("[yellow]Already started. Call stop() first.[/yellow]")
            return

        # 1. Load pattern modules
        mcp_auth_mod = _load_module(
            f"_pattern_{self.pattern}_mcp_auth",
            self._pattern_dir / "mcp_auth.py",
        )
        service_auth_mod = _load_module(
            f"_pattern_{self.pattern}_service_auth",
            self._pattern_dir / "service_auth.py",
        )

        auth_handler: AuthHandler = mcp_auth_mod.auth_handler
        get_expense_identity = service_auth_mod.get_expense_identity
        get_document_identity = service_auth_mod.get_document_identity

        # 2. Start backend services on ephemeral ports
        expense_port = _find_free_port()
        document_port = _find_free_port()

        # Check if the service_auth module provides opa_url for pattern 7
        opa_url = getattr(service_auth_mod, "opa_url", None)

        expense_app = create_expense_app(get_expense_identity, opa_url=opa_url)
        document_app = create_document_app(get_document_identity)

        await self._start_uvicorn(expense_app, expense_port)
        await self._start_uvicorn(document_app, document_port)

        self._expense_service_url = f"http://127.0.0.1:{expense_port}"
        self._document_service_url = f"http://127.0.0.1:{document_port}"

        # 3. Create and start MCP servers
        expense_mcp = create_expense_mcp(auth_handler, self._expense_service_url)
        document_mcp = create_document_mcp(auth_handler, self._document_service_url)

        expense_mcp_port = _find_free_port()
        document_mcp_port = _find_free_port()

        # Get the ASGI app from FastMCP and serve it via uvicorn
        expense_mcp_app = expense_mcp.streamable_http_app()
        document_mcp_app = document_mcp.streamable_http_app()

        await self._start_uvicorn(expense_mcp_app, expense_mcp_port)
        await self._start_uvicorn(document_mcp_app, document_mcp_port)

        self._expense_mcp_url = f"http://127.0.0.1:{expense_mcp_port}/mcp"
        self._document_mcp_url = f"http://127.0.0.1:{document_mcp_port}/mcp"

        # 4. Create MCP client connections
        meta_resolver = _make_meta_resolver()

        expense_mcp_client = MCPServerStreamableHttp(
            params={"url": self._expense_mcp_url},
            cache_tools_list=True,
            name="expense-mcp",
            client_session_timeout_seconds=60,
            tool_meta_resolver=meta_resolver,
        )
        document_mcp_client = MCPServerStreamableHttp(
            params={"url": self._document_mcp_url},
            cache_tools_list=True,
            name="document-mcp",
            client_session_timeout_seconds=60,
            tool_meta_resolver=meta_resolver,
        )

        await expense_mcp_client.connect()
        await document_mcp_client.connect()
        self._mcp_clients = [expense_mcp_client, document_mcp_client]

        # 5. Create agent
        self._agent = Agent(mcp_servers=self._mcp_clients)

        self._started = True
        console.print(
            Panel(
                f"[green]Pattern {self.pattern} started[/green]\n"
                f"  expense service: {self._expense_service_url}\n"
                f"  document service: {self._document_service_url}\n"
                f"  expense MCP: {self._expense_mcp_url}\n"
                f"  document MCP: {self._document_mcp_url}",
                title="PatternRunner",
                border_style="green",
                expand=False,
            )
        )

    async def stop(self) -> None:
        """Shutdown all servers and disconnect MCP clients."""
        for client in self._mcp_clients:
            try:
                await client.cleanup()
            except Exception:
                pass
        self._mcp_clients = []

        for server in self._servers:
            server.should_exit = True
        # Give servers a moment to shut down
        if self._tasks:
            await asyncio.sleep(0.3)
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks = []
        self._servers = []
        self._agent = None
        self._started = False
        console.print(f"[dim]Pattern {self.pattern} stopped.[/dim]")

    async def run_as(self, user: str, prompt: str, jwt: str | None = None) -> AgentResult:
        """Run an agent prompt as a given user and pretty-print the result.

        The JWT simulates a real-world flow where the agent is invoked with
        a user token from an upstream service. If not provided, one is fetched
        from Keycloak via direct grant (for convenience in this teaching repo).
        """
        if not self._started or self._agent is None:
            raise RuntimeError("Call start() first")

        # Simulate: the agent is invoked with the user's JWT
        if jwt is None:
            jwt = fetch_user_jwt(user)

        header = Text(f"[{user}] ", style="bold cyan") + Text(prompt, style="white")
        console.print(Panel(header, border_style="cyan", expand=False))

        result = await self._agent.run(user, prompt, jwt=jwt)

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

    def show_auth_code(self) -> None:
        """Pretty-print both mcp_auth.py and service_auth.py with syntax highlighting."""
        for filename in ("mcp_auth.py", "service_auth.py"):
            filepath = self._pattern_dir / filename
            if filepath.exists():
                code = filepath.read_text()
                console.print(
                    Panel(
                        Syntax(code, "python", theme="monokai", line_numbers=True),
                        title=f"{self.pattern}/{filename}",
                        border_style="blue",
                        expand=False,
                    )
                )

    async def show_service_identity(self) -> None:
        """Hit /debug/last-request on both backend services and display results."""
        if self._expense_service_url:
            await show_what_tool_saw(self._expense_service_url, "expense-service")
        if self._document_service_url:
            await show_what_tool_saw(self._document_service_url, "document-service")

    async def _start_uvicorn(self, app: Any, port: int) -> None:
        """Start a uvicorn server as a background asyncio task."""
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        self._servers.append(server)
        task = asyncio.create_task(server.serve())
        self._tasks.append(task)
        # Wait for the server to start
        while not server.started:
            await asyncio.sleep(0.05)
