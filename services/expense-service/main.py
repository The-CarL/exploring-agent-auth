"""expense-service, auth-flexible FastAPI app for the exploring-agent-auth demo.

The same endpoint code path serves all eight notebook patterns. The pedagogical
trick is that the *response content* depends on what identity the service was
able to extract from the inbound request, which depends on which auth method
the caller used.

Endpoints:
    GET  /healthz                       liveness
    GET  /debug/last-request            what auth context the previous call used
    GET  /expenses?department=<dep>     list expenses, filtered by identity
    POST /expenses/{id}/approve         approve an expense (manager+ only)

When TOOL_SIDE_AUTHZ=1 the approve endpoint additionally calls OPA's tool_side
package for fine-grained ReBAC ("can this user approve this user's expense?").
"""

import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException

from auth import RequestIdentity, get_identity, get_last_request
from seed_data import get_db, init_db

OPA_URL = os.getenv("OPA_URL", "http://opa:8181")
TOOL_SIDE_AUTHZ = os.getenv("TOOL_SIDE_AUTHZ", "0") == "1"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="expense-service", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "expense-service",
        "tool_side_authz": TOOL_SIDE_AUTHZ,
    }


@app.get("/debug/last-request")
def debug_last_request() -> dict[str, Any]:
    """The punchline of every notebook: what auth context did the service
    actually see on its most recent request?"""
    return get_last_request()


# ---------- /expenses ----------


def _all_expenses() -> list[dict[str, Any]]:
    db = get_db()
    try:
        return [dict(row) for row in db.execute("SELECT * FROM expenses").fetchall()]
    finally:
        db.close()


def _filter_by_identity(
    expenses: list[dict[str, Any]],
    identity: RequestIdentity,
) -> list[dict[str, Any]]:
    """The pedagogical heart of the demo: same endpoint, different filtering
    based on what identity the service got."""

    # Patterns 1, 2: no identity → return everything. The pattern 1 punchline.
    if identity.method in ("none", "api_key"):
        return expenses

    # Pattern 4: bare X-User-Id, no proof. Filter by the claimed user.
    if identity.method == "string_id" and identity.user_id:
        return [e for e in expenses if e["user_id"] == identity.user_id]

    # Patterns 5, 6, 7: validated JWT. Filter by claims.
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


@app.get("/expenses")
def list_expenses(
    department: str | None = None,
    identity: RequestIdentity = Depends(get_identity),
) -> dict[str, Any]:
    expenses = _filter_by_identity(_all_expenses(), identity)
    # Pattern 2 (inline claim authz): the AGENT scopes the call by passing
    # ?department=<dep> as a query param. We honor that as an additional filter.
    if department:
        expenses = [e for e in expenses if e["department"] == department]
    return {
        "identity_method": identity.method,
        "identity_detail": identity.detail,
        "count": len(expenses),
        "expenses": expenses,
    }


# ---------- POST /expenses/{id}/approve ----------


def _caller_username(identity: RequestIdentity) -> str | None:
    if identity.method in ("jwt", "scoped_jwt"):
        claims = identity.claims or {}
        return claims.get("preferred_username")
    if identity.method == "string_id":
        return identity.user_id
    return None


def _opa_tool_side_decision(
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
            f"{OPA_URL}/v1/data/agentauth/tool_side/decision",
            json=payload,
            timeout=2.0,
        )
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"opa unreachable: {e}") from e
    return r.json().get("result") or {"allow": False, "reason": "no decision returned"}


@app.post("/expenses/{expense_id}/approve")
def approve_expense(
    expense_id: int,
    identity: RequestIdentity = Depends(get_identity),
) -> dict[str, Any]:
    db = get_db()
    try:
        row = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"expense {expense_id} not found")
        target = dict(row)

        caller = _caller_username(identity)

        # Coarse role check: only managers and admins can approve.
        if identity.method in ("jwt", "scoped_jwt"):
            role = (identity.claims or {}).get("role")
            if role not in ("manager", "admin"):
                raise HTTPException(
                    status_code=403,
                    detail=f"role={role} cannot approve expenses",
                )
        elif identity.method == "string_id":
            # No role info from a bare X-User-Id; the demo deliberately allows
            # this through to make the pattern-4 weakness visible.
            pass
        elif identity.method == "api_key":
            caller = "<api-key-caller>"
        else:
            raise HTTPException(status_code=401, detail="auth required")

        # Fine-grained tool-side authz (pattern 7), gated by env var.
        if TOOL_SIDE_AUTHZ:
            if caller is None:
                raise HTTPException(
                    status_code=403,
                    detail="tool-side authz enabled but no caller identity available",
                )
            decision = _opa_tool_side_decision(
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
        "tool_side_authz_enabled": TOOL_SIDE_AUTHZ,
        "expense": updated,
    }
