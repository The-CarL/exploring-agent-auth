"""SQLite seed data for the document-service.

The DB lives at /tmp/documents.db inside the container and is wiped + reseeded
on every container start. Documents are tagged with `access_groups` so the
filtering rules in main.py can show "alice sees engineering docs, carlo sees
admin-only HR + board materials" through real data.

Access group convention:
    engineering   - alice, bob can read
    platform      - carlo can read
    admin         - admin role only
    public        - everyone can read
"""

import json
import os
import sqlite3

DB_PATH = "/tmp/documents.db"

DOCUMENTS = [
    # Engineering team docs
    {"id": 1, "title": "Architecture decision records — Q1",
     "body": "ADR-014 — moved authentication to Keycloak. ADR-015 — adopted OPA for policy.",
     "owner": "bob", "access_groups": ["engineering"]},
    {"id": 2, "title": "Migration runbook: legacy auth → keycloak",
     "body": "Step 1: stand up Keycloak. Step 2: import realm. Step 3: rotate client secrets weekly.",
     "owner": "alice", "access_groups": ["engineering"]},
    {"id": 3, "title": "Engineering onboarding",
     "body": "Welcome! Read the README, set up your dev env, ping bob in #eng with questions.",
     "owner": "bob", "access_groups": ["engineering", "public"]},

    # Platform team docs
    {"id": 4, "title": "Production incident postmortem — 2026-03-12",
     "body": "Root cause: stale JWKS cache caused token validation to fail for 18 minutes.",
     "owner": "carlo", "access_groups": ["platform"]},
    {"id": 5, "title": "On-call rotation rules",
     "body": "Primary: 7d. Secondary: backup. Escalate to carlo for sev1.",
     "owner": "carlo", "access_groups": ["platform"]},

    # HR / admin only
    {"id": 6, "title": "Compensation bands H1 2026",
     "body": "L3 75-95k, L4 95-130k, L5 130-180k, L6 180-240k. Confidential.",
     "owner": "carlo", "access_groups": ["admin"]},
    {"id": 7, "title": "Performance review guidelines",
     "body": "Calibration meeting third week of every quarter. Manager submits drafts 2w prior.",
     "owner": "carlo", "access_groups": ["admin"]},

    # Board materials
    {"id": 8, "title": "Board memo: 2026 strategy",
     "body": "Three-pillar strategy: agentic platform, identity infrastructure, developer tools.",
     "owner": "carlo", "access_groups": ["admin"]},
]


def init_db() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE documents (
                id            INTEGER PRIMARY KEY,
                title         TEXT NOT NULL,
                body          TEXT NOT NULL,
                owner         TEXT NOT NULL,
                access_groups TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO documents (id, title, body, owner, access_groups)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (d["id"], d["title"], d["body"], d["owner"], json.dumps(d["access_groups"]))
                for d in DOCUMENTS
            ],
        )
        conn.commit()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
