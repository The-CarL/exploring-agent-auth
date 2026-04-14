"""Expense service FastAPI app factory.

create_app(get_identity_fn) returns a FastAPI app with the given identity
extractor bound into its routes. The app has NO built-in auth; identity
extraction is entirely determined by the function passed in.

Endpoints:
    GET  /healthz                       liveness
    GET  /debug/last-request            what auth context the previous call used
    GET  /expenses?department=<dep>     list expenses, filtered by identity
    POST /expenses/{expense_id}/approve approve an expense (manager+ only)
"""

from __future__ import annotations

from collections.abc import Callable, Awaitable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from framework.services.identity import Identity
from framework.services.expense.models import get_db, init_db


def create_app(
    get_identity_fn: Callable[..., Awaitable[Identity]],
    opa_url: str | None = None,
) -> FastAPI:
    """Create an expense service app with the given identity extractor.

    Args:
        get_identity_fn: async function(Request) -> Identity
        opa_url: if set, the approve endpoint calls OPA for tool-side authz
    """

    last_request: dict[str, Any] = {"method": "never", "detail": "no requests yet"}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        yield

    app = FastAPI(title="expense-service", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": "expense-service"}

    @app.get("/debug/last-request")
    def debug_last_request() -> dict[str, Any]:
        return last_request

    @app.get("/expenses")
    async def list_expenses(
        request: Request,
        department: str | None = None,
    ) -> dict[str, Any]:
        identity = await get_identity_fn(request)
        last_request.clear()
        last_request.update(identity.to_dict())

        expenses = _all_expenses()
        expenses = _filter_by_identity(expenses, identity)
        if department:
            expenses = [e for e in expenses if e["department"] == department]
        return {
            "identity_method": identity.method,
            "identity_detail": identity.detail,
            "count": len(expenses),
            "expenses": expenses,
        }

    @app.post("/expenses/{expense_id}/approve")
    async def approve_expense(
        request: Request,
        expense_id: int,
    ) -> dict[str, Any]:
        identity = await get_identity_fn(request)
        last_request.clear()
        last_request.update(identity.to_dict())

        db = get_db()
        try:
            row = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"expense {expense_id} not found")
            target = dict(row)

            caller = _caller_username(identity)

            if identity.method in ("jwt", "scoped_jwt"):
                role = (identity.claims or {}).get("role")
                if role not in ("manager", "admin"):
                    raise HTTPException(
                        status_code=403,
                        detail=f"role={role} cannot approve expenses",
                    )
            elif identity.method == "string_id":
                pass
            elif identity.method == "api_key":
                caller = "<api-key-caller>"
            else:
                raise HTTPException(status_code=401, detail="auth required")

            # Tool-side OPA authz (pattern 7)
            effective_opa_url = opa_url
            if effective_opa_url is None and identity.claims:
                effective_opa_url = identity.claims.get("_opa_url")

            if effective_opa_url:
                if caller is None:
                    raise HTTPException(
                        status_code=403,
                        detail="tool-side authz enabled but no caller identity available",
                    )
                decision = _opa_tool_side_decision(
                    effective_opa_url,
                    caller=caller,
                    target=target["user_id"],
                    action="approve",
                    resource_type="expense",
                )
                if not decision.get("allow"):
                    raise HTTPException(
                        status_code=403,
                        detail=f"opa denied: {decision.get('reason', 'no reason given')}",
                    )

            db.execute(
                "UPDATE expenses SET status = 'approved' WHERE id = ?",
                (expense_id,),
            )
            db.commit()
            updated = dict(
                db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
            )
        finally:
            db.close()

        return {
            "identity_method": identity.method,
            "approved_by": caller,
            "tool_side_authz_enabled": effective_opa_url is not None,
            "expense": updated,
        }

    return app


# ---------- helpers ----------


def _all_expenses() -> list[dict[str, Any]]:
    db = get_db()
    try:
        return [dict(row) for row in db.execute("SELECT * FROM expenses").fetchall()]
    finally:
        db.close()


def _filter_by_identity(
    expenses: list[dict[str, Any]],
    identity: Identity,
) -> list[dict[str, Any]]:
    if identity.method in ("none", "api_key"):
        return expenses
    if identity.method == "string_id" and identity.user_id:
        return [e for e in expenses if e["user_id"] == identity.user_id]
    if identity.method in ("jwt", "scoped_jwt"):
        claims = identity.claims or {}
        role = claims.get("role")
        username = claims.get("preferred_username")
        department = claims.get("department")
        if role == "admin":
            return expenses
        if role == "manager" and department:
            return [e for e in expenses if e["department"] == department]
        if username:
            return [e for e in expenses if e["user_id"] == username]
    return []


def _caller_username(identity: Identity) -> str | None:
    if identity.method in ("jwt", "scoped_jwt"):
        claims = identity.claims or {}
        return claims.get("preferred_username")
    if identity.method == "string_id":
        return identity.user_id
    return None


def _opa_tool_side_decision(
    opa_url: str,
    caller: str,
    target: str,
    action: str,
    resource_type: str,
) -> dict[str, Any]:
    payload = {
        "input": {
            "user_id": caller,
            "target_user_id": target,
            "action": action,
            "resource_type": resource_type,
        }
    }
    try:
        r = httpx.post(
            f"{opa_url}/v1/data/agentauth/tool_side/decision",
            json=payload,
            timeout=2.0,
        )
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"opa unreachable: {e}") from e
    return r.json().get("result") or {"allow": False, "reason": "no decision returned"}
