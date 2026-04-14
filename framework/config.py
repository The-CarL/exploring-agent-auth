"""Endpoint and credential configuration.

All values are read from environment variables (typically loaded from .env via
python-dotenv) with sensible defaults that match docker-compose.yml. Import
values directly: `from framework.config import KEYCLOAK_URL`.
"""

import os

from dotenv import load_dotenv

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

# JWKS URL for service-side JWT validation (services run on the host now,
# so they reach Keycloak at the same URL as notebooks)
JWKS_URL: str = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
EXPECTED_ISSUER: str = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"

# Map tool name -> the Keycloak client_id that the target service is registered as.
# Used by token exchange (pattern 6) to pick the right audience.
TOOL_TO_TARGET_CLIENT: dict[str, str] = {
    "get_expenses": "expense-service-client",
    "approve_expense": "expense-service-client",
    "search_documents": "document-service-client",
}

# Service client IDs (used by jwt_identity to check audience on scoped tokens)
EXPENSE_SERVICE_CLIENT_ID: str = "expense-service-client"
DOCUMENT_SERVICE_CLIENT_ID: str = "document-service-client"


# ----- OPA -----

OPA_URL: str = os.getenv("OPA_URL", "http://localhost:8181")


# ----- Shared service credential (pattern 1) -----

SHARED_SERVICE_API_KEY: str = os.getenv("SHARED_SERVICE_API_KEY", "dev-shared-api-key")


# ----- OpenAI -----

OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5-nano")


# ----- User passwords (pre-seeded in Keycloak realm) -----

USER_PASSWORD: str = "password"
KNOWN_USERS: tuple[str, ...] = ("alice", "bob", "dave")
