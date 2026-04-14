"""Identity dataclass used across all backend services.

Every pattern's service_auth.py returns an Identity from its get_identity()
function. The service routes use this to filter data and record what the
service saw for the /debug/last-request endpoint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Identity:
    """What the service extracted from the inbound request."""

    method: str  # one of: none, api_key, string_id, jwt, scoped_jwt
    user_id: str | None = None
    claims: dict[str, Any] | None = None
    raw_token: str | None = None
    detail: str | None = None  # human-readable explanation for /debug/last-request

    def to_dict(self) -> dict[str, Any]:
        """Strip None fields so the debug endpoint output is uncluttered."""
        return {k: v for k, v in asdict(self).items() if v is not None}
