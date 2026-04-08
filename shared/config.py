"""Endpoint and credential configuration for the host-side notebook code.

All values are read from environment variables (typically loaded from .env via
python-dotenv) with sensible defaults that match docker-compose.yml. Notebooks
can `from shared.config import ...` and the values will be live for the
running Python process.
"""

import os

from dotenv import load_dotenv

# Load .env from the repo root if it exists. Notebooks can be launched from
# anywhere — find_dotenv walks upward to locate it.
from dotenv import find_dotenv
load_dotenv(find_dotenv(usecwd=True))


# ----- Keycloak -----

KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM: str = os.getenv("KEYCLOAK_REALM", "agentauth")

AGENT_CLIENT_ID: str = os.getenv("AGENT_CLIENT_ID", "agent-client")
AGENT_CLIENT_SECRET: str = os.getenv("AGENT_CLIENT_SECRET", "agent-client-secret")

USER_DIRECT_CLIENT_ID: str = os.getenv("USER_DIRECT_CLIENT_ID", "user-direct-client")

# Convenience derived URLs
TOKEN_ENDPOINT: str = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
AUTH_ENDPOINT: str = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth"


# ----- Backend services -----

EXPENSE_SERVICE_URL: str = os.getenv("EXPENSE_SERVICE_URL", "http://localhost:8001")
DOCUMENT_SERVICE_URL: str = os.getenv("DOCUMENT_SERVICE_URL", "http://localhost:8002")

# Map tool name -> the keycloak client_id that the target service is registered as.
# Used by TokenExchangeAuth to pick the right `audience` parameter.
TOOL_TO_TARGET_CLIENT: dict[str, str] = {
    "get_expenses": "expense-service-client",
    "approve_expense": "expense-service-client",
    "search_documents": "document-service-client",
}


# ----- OPA -----

OPA_URL: str = os.getenv("OPA_URL", "http://localhost:8181")


# ----- Shared service credential (pattern 1) -----

SHARED_SERVICE_API_KEY: str = os.getenv("SHARED_SERVICE_API_KEY", "dev-shared-api-key")


# ----- OpenAI (used by shared/agent.py) -----

OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# ----- User passwords (pre-seeded in Keycloak realm) -----

# All three demo users have the same dev password. The plaintext lives in the
# realm export and here, intentionally — this is a local-only teaching repo.
USER_PASSWORD: str = "password"
KNOWN_USERS: tuple[str, ...] = ("alice", "bob", "carlo")
