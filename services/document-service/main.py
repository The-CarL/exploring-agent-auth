"""document-service, auth-flexible FastAPI app for the exploring-agent-auth demo.

Same auth-flexibility shape as the expense-service. The interesting difference
is that filtering happens via per-document `access_groups` rather than per-user
ownership, which makes the role/department story slightly different in the
notebook output.

Endpoints:
    GET  /healthz                       liveness
    GET  /debug/last-request            what auth context the previous call used
    GET  /documents?q=<query>           search documents, filtered by identity
"""

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI

from auth import RequestIdentity, get_identity, get_last_request
from seed_data import get_db, init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="document-service", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "document-service"}


@app.get("/debug/last-request")
def debug_last_request() -> dict[str, Any]:
    return get_last_request()


# ---------- /documents ----------


def _all_documents() -> list[dict[str, Any]]:
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM documents").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["access_groups"] = json.loads(d["access_groups"])
            out.append(d)
        return out
    finally:
        db.close()


def _allowed_groups_for(identity: RequestIdentity) -> set[str] | None:
    """Returns the set of access_groups the caller is allowed to read.
    Returns None to mean 'no filtering, see everything' (used by patterns
    1, 2 where the service has no real identity)."""

    # Patterns 1, 2: no identity → no filtering, see everything.
    if identity.method in ("none", "api_key"):
        return None

    # Pattern 4: bare X-User-Id. Map username → groups via a tiny table.
    # In a real system this would come from a directory service.
    if identity.method == "string_id" and identity.user_id:
        return _groups_for_username(identity.user_id)

    # Patterns 5, 6, 7: validated JWT.
    if identity.method in ("jwt", "scoped_jwt"):
        claims = identity.claims or {}
        role = claims.get("role")
        department = claims.get("department")
        groups: set[str] = {"public"}
        if department:
            groups.add(department)
        if role == "admin":
            groups.add("admin")
        return groups

    return set()


# Tiny lookup table used by the "string_id" path. The pedagogical point is
# that with a bare X-User-Id the service has to hardcode this kind of mapping
# locally, there's no claim it can read.
_USERNAME_TO_GROUPS: dict[str, set[str]] = {
    "alice": {"engineering", "public"},
    "bob": {"engineering", "public"},
    "dave": {"platform", "admin", "public"},
}


def _groups_for_username(username: str) -> set[str]:
    return _USERNAME_TO_GROUPS.get(username, {"public"})


def _filter_documents(
    docs: list[dict[str, Any]],
    allowed: set[str] | None,
    query: str | None,
) -> list[dict[str, Any]]:
    if allowed is not None:
        docs = [
            d for d in docs
            if any(g in allowed for g in d["access_groups"])
        ]
    if query:
        q = query.lower()
        docs = [
            d for d in docs
            if q in d["title"].lower() or q in d["body"].lower()
        ]
    return docs


@app.get("/documents")
def search_documents(
    q: str | None = None,
    identity: RequestIdentity = Depends(get_identity),
) -> dict[str, Any]:
    allowed = _allowed_groups_for(identity)
    matches = _filter_documents(_all_documents(), allowed, q)
    return {
        "identity_method": identity.method,
        "identity_detail": identity.detail,
        "allowed_groups": sorted(allowed) if allowed is not None else "all",
        "count": len(matches),
        "documents": matches,
    }
