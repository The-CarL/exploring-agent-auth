"""Auth strategy classes, one per pattern.

The whole point of the eight notebooks is the *progression* between these
classes. Each strategy implements a single method:

    prepare(tool_name, user, args) -> PreparedRequest

`tools.py` calls `prepare()` to learn what headers/query-params to attach to
the outgoing HTTP request. Each strategy is the smallest amount of code that
demonstrates one identity/authz pattern, they share helpers at the top of
this file (token fetching, JWT decoding) but the strategies themselves stay
small enough to read in one screen.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from shared.config import (
    AGENT_CLIENT_ID,
    AGENT_CLIENT_SECRET,
    OPA_URL,
    SHARED_SERVICE_API_KEY,
    TOKEN_ENDPOINT,
    TOOL_TO_TARGET_CLIENT,
    USER_PASSWORD,
)


# ----- shared helpers -----


def fetch_user_jwt(username: str, password: str = USER_PASSWORD) -> str:
    """Direct grant: get an access token for a real user via the agent client.

    Used by every strategy that needs a real user identity. In production an
    agent would never have user passwords, it would receive an upstream-issued
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
    """Decode a JWT *without* signature verification, host-side display only.

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
      - aud is narrowed to just `target_audience`
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


# ----- the contract every strategy implements -----


@dataclass
class PreparedRequest:
    """What a strategy returns to tell tools.py how to attach auth to the call."""

    headers: dict[str, str] = field(default_factory=dict)
    extra_params: dict[str, Any] = field(default_factory=dict)


class AuthorizationDenied(Exception):
    """Raised by strategies that perform agent-side enforcement (pattern 3)."""


class AuthStrategy:
    name: str = "abstract"

    def prepare(self, tool_name: str, user: str, args: dict[str, Any]) -> PreparedRequest:
        raise NotImplementedError


# ----- pattern 1: shared service credential -----


class ServiceCredentialAuth(AuthStrategy):
    """The agent uses a single static API key for every tool call.
    The tool has no idea who the user is."""

    name = "service_credential"

    def prepare(self, tool_name: str, user: str, args: dict[str, Any]) -> PreparedRequest:
        return PreparedRequest(headers={"X-API-Key": SHARED_SERVICE_API_KEY})


# ----- pattern 2: inline claim-based authz -----


class InlineClaimAuth(AuthStrategy):
    """Agent fetches the user's JWT, reads claims out of it, and constructs
    scoped tool calls. The tool still gets only the API key, the *agent* is
    responsible for narrowing the call.

    For get_expenses we add ?department=<dep> when the user has a department
    claim. The narrative: authz logic is now scattered through agent code.
    """

    name = "inline_claim"

    def prepare(self, tool_name: str, user: str, args: dict[str, Any]) -> PreparedRequest:
        claims = decode_jwt(fetch_user_jwt(user))
        prepared = PreparedRequest(headers={"X-API-Key": SHARED_SERVICE_API_KEY})

        # Coarse, hand-coded narrowing rules, the kind of thing that grows
        # unmaintainable in real codebases.
        if tool_name == "get_expenses":
            role = claims.get("role")
            department = claims.get("department")
            username = claims.get("preferred_username")
            if role == "admin":
                pass  # admins see everything; don't narrow
            elif role == "manager" and department:
                prepared.extra_params["department"] = department
            elif username:
                # employees see only their own, but the service can't filter
                # by username from an api_key call, so this is a no-op narrowing
                # in practice. The pattern 2 punchline is exactly this gap.
                pass

        return prepared


# ----- pattern 3: external authz, agent-side -----


class AgentSideOPAAuth(AuthStrategy):
    """Agent asks OPA whether the user is permitted to invoke this tool.
    On deny, raise. On allow, fall through to the API-key call.

    Centralizes authz in OPA but the agent is still the sole enforcement
    point, a buggy or prompt-injected agent bypasses everything.
    """

    name = "agent_side_opa"

    def prepare(self, tool_name: str, user: str, args: dict[str, Any]) -> PreparedRequest:
        claims = decode_jwt(fetch_user_jwt(user))
        opa_input = {
            "input": {
                "user": {
                    "role": claims.get("role"),
                    "department": claims.get("department"),
                    "reports_to": claims.get("reports_to"),
                },
                "tool": tool_name,
                "action": "approve" if tool_name == "approve_expense" else "read",
            }
        }
        r = httpx.post(
            f"{OPA_URL}/v1/data/agentauth/agent_side/decision",
            json=opa_input,
            timeout=5.0,
        )
        r.raise_for_status()
        decision = r.json().get("result") or {}
        if not decision.get("allow"):
            raise AuthorizationDenied(
                f"OPA denied {user} calling {tool_name}: {decision.get('reason', 'no reason')}"
            )
        return PreparedRequest(headers={"X-API-Key": SHARED_SERVICE_API_KEY})


# ----- pattern 4: identity as parameter -----


class IdentityParamAuth(AuthStrategy):
    """Agent declares the user via an X-User-Id header. No proof.
    The service trusts whatever the agent says."""

    name = "identity_param"

    def prepare(self, tool_name: str, user: str, args: dict[str, Any]) -> PreparedRequest:
        return PreparedRequest(headers={"X-User-Id": user})


# ----- pattern 5: full JWT passthrough -----


class JWTPassthroughAuth(AuthStrategy):
    """Forward the user's full JWT to the tool. Cryptographically authentic
    but the audience is broad (all services the agent can call)."""

    name = "jwt_passthrough"

    def prepare(self, tool_name: str, user: str, args: dict[str, Any]) -> PreparedRequest:
        return PreparedRequest(
            headers={"Authorization": f"Bearer {fetch_user_jwt(user)}"}
        )


# ----- pattern 6: token exchange (act-on-behalf) -----


class TokenExchangeAuth(AuthStrategy):
    """Standard Token Exchange v2: exchange the broad user JWT for a token
    narrowed to just the target service. azp identifies the agent (audit
    trail). RFC 8693 also defines an `act` claim for delegation; Keycloak's
    supported v2 doesn't auto-populate it as of 26.x, see notebook 06's
    tradeoff cell."""

    name = "token_exchange"

    def prepare(self, tool_name: str, user: str, args: dict[str, Any]) -> PreparedRequest:
        target_audience = TOOL_TO_TARGET_CLIENT.get(tool_name)
        if target_audience is None:
            raise ValueError(f"no target audience configured for tool {tool_name!r}")
        user_jwt = fetch_user_jwt(user)
        exchanged = exchange_token(user_jwt, target_audience)
        return PreparedRequest(headers={"Authorization": f"Bearer {exchanged}"})


# ----- pattern 8: 3LO OAuth consent -----


class ThreeLeggedOAuthAuth(AuthStrategy):
    """Wraps a token previously obtained out-of-band by display.three_legged_login().

    The agent never holds the user's password, the user obtained this token
    by going through the Keycloak consent screen in their own browser. The
    agent just relays it.
    """

    name = "three_legged_oauth"

    def __init__(self, access_token: str):
        self.access_token = access_token

    def prepare(self, tool_name: str, user: str, args: dict[str, Any]) -> PreparedRequest:
        return PreparedRequest(headers={"Authorization": f"Bearer {self.access_token}"})
