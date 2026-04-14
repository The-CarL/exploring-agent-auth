"""Auth helper functions shared across patterns and framework code.

These are the building blocks that pattern-specific mcp_auth.py files
use to fetch tokens, decode JWTs, and perform token exchange.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from framework.config import (
    AGENT_CLIENT_ID,
    AGENT_CLIENT_SECRET,
    TOKEN_ENDPOINT,
    USER_PASSWORD,
)


def fetch_user_jwt(username: str, password: str = USER_PASSWORD) -> str:
    """Direct grant: get an access token for a real user via the agent client.

    Used by every pattern that needs a real user identity. In production an
    agent would never have user passwords; it would receive an upstream-issued
    user token via OAuth flows. For this teaching repo, direct grant is the
    simplest way to materialize a user JWT inside a notebook.
    """
    r = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": AGENT_CLIENT_ID,
            "client_secret": AGENT_CLIENT_SECRET,
            "grant_type": "password",
            "username": username,
            "password": password,
            "scope": "openid",
        },
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def decode_jwt(token: str) -> dict[str, Any]:
    """Decode a JWT *without* signature verification (display only).

    Services do real signature verification via PyJWKClient. Notebooks just
    want to read claims for display, so we skip the JWKS fetch dance here.
    """
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def exchange_token(user_jwt: str, target_audience: str) -> str:
    """Standard Token Exchange v2: exchange a user JWT for a narrowed token.

    The agent client authenticates with its client_secret and asks Keycloak
    to mint a new token where:
      - sub stays the same (the original user)
      - aud is narrowed to just target_audience
      - azp is set to agent-client (the requester, audit trail)
    """
    r = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": AGENT_CLIENT_ID,
            "client_secret": AGENT_CLIENT_SECRET,
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": user_jwt,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "audience": target_audience,
        },
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]
