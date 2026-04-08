"""SQLite seed data for the expense-service.

The DB lives at /tmp/expenses.db inside the container and is wiped + reseeded
on every container start. The seed is intentionally small and inspectable so
the notebook tradeoff cells can show that "alice sees X expenses, bob sees Y,
carlo sees Z" through real data, not magic filtering.
"""

import os
import sqlite3

DB_PATH = "/tmp/expenses.db"

EXPENSES = [
    # alice — engineering employee, several approved + one pending
    {"id": 1, "user_id": "alice", "department": "engineering", "amount": 42.50,
     "category": "software", "description": "JetBrains AI assistant subscription",
     "status": "approved"},
    {"id": 2, "user_id": "alice", "department": "engineering", "amount": 156.00,
     "category": "travel", "description": "Train ticket to client offsite",
     "status": "approved"},
    {"id": 3, "user_id": "alice", "department": "engineering", "amount": 89.00,
     "category": "books", "description": "Designing Data-Intensive Applications",
     "status": "approved"},
    {"id": 4, "user_id": "alice", "department": "engineering", "amount": 1450.00,
     "category": "hardware", "description": "External 4K monitor",
     "status": "pending"},  # the one bob will approve in pattern 7
    # bob — engineering manager
    {"id": 5, "user_id": "bob", "department": "engineering", "amount": 320.00,
     "category": "training", "description": "OAuth 2.0 deep-dive workshop",
     "status": "approved"},
    {"id": 6, "user_id": "bob", "department": "engineering", "amount": 67.00,
     "category": "meals", "description": "Team lunch after the migration shipped",
     "status": "approved"},
    # carlo — platform admin
    {"id": 7, "user_id": "carlo", "department": "platform", "amount": 980.00,
     "category": "training", "description": "KubeCon ticket",
     "status": "approved"},
    {"id": 8, "user_id": "carlo", "department": "platform", "amount": 240.00,
     "category": "software", "description": "Datadog license seat",
     "status": "approved"},
]


def init_db() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE expenses (
                id          INTEGER PRIMARY KEY,
                user_id     TEXT NOT NULL,
                department  TEXT NOT NULL,
                amount      REAL NOT NULL,
                category    TEXT NOT NULL,
                description TEXT NOT NULL,
                status      TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO expenses
                (id, user_id, department, amount, category, description, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (e["id"], e["user_id"], e["department"], e["amount"],
                 e["category"], e["description"], e["status"])
                for e in EXPENSES
            ],
        )
        conn.commit()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
