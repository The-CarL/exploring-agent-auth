# Restructure exploring-agent-auth: MCP Servers + Per-Pattern Isolation

## Context

This is a teaching repository that demonstrates 8 identity and authorization
patterns for AI agents calling tools. It currently works and has 8 Jupyter
notebooks, shared Python modules, two FastAPI backend services, Keycloak for
identity, and OPA for policy — all running locally via Docker Compose.

The current architecture uses a single agent codebase with a strategy pattern
(`shared/auth.py`) that swaps auth behavior via config. The backend services
auto-detect which auth method the caller used (JWT, API key, X-User-Id, etc.)
via a waterfall in their `auth.py`. This is elegant but has two problems
for learners:

1. **Tools are direct HTTP calls.** The agent calls FastAPI services directly
   via `httpx` in `shared/tools.py`. Real agent tooling uses MCP (Model Context
   Protocol). We want tool interactions to go through dedicated MCP servers to
   show a more realistic agent → MCP server → backend service architecture.

2. **The unified codebase obscures individual patterns.** A learner trying to
   understand token exchange must mentally filter out 7 other strategy classes,
   trace through the strategy pattern abstraction, and understand how the
   service's auth waterfall detects the auth method. The abstraction layer
   becomes the lesson instead of the auth pattern.

## Design Philosophy

We *intentionally* break patterns into isolated, per-pattern modules — even
though a production system would likely consolidate them. This is a deliberate
pedagogical choice: we want each authorization exchange to be fully visible
in a small number of focused files, not hidden behind a shared abstraction.
The README and notebook 00 should explain this choice explicitly:

> "In production, you'd likely use a single flexible auth layer that supports
> multiple methods. We deliberately didn't do that here. Each pattern has its
> own auth code — on both the MCP server side and the service side — so you
> can read exactly what happens at each boundary without tracing through
> abstractions. Once you understand each pattern individually, consolidating
> them is straightforward."

## Target Architecture

### Request Flow

```
Agent ←→ Expense MCP Server (HTTP) ←→ Expense FastAPI Service
Agent ←→ Document MCP Server (HTTP) ←→ Document FastAPI Service
```

The MCP servers are the auth boundary. They apply the pattern's auth logic
to outbound requests before forwarding to backend services.

### Directory Structure

```
framework/                              # Shared harness — explained in notebook 00
  mcp/
    expense_server.py                   # Base HTTP MCP server: get_expenses, approve_expense tools
    document_server.py                  # Base HTTP MCP server: search_documents tool
    auth.py                             # AuthHandler base class (2-method interface)
  services/
    expense/
      app.py                            # FastAPI routes: GET /expenses, POST /expenses/{id}/approve
      models.py                         # Expense data model + SQLite seed (8 expenses)
    document/
      app.py                            # FastAPI routes: GET /documents
      models.py                         # Document data model + SQLite seed (8 documents)
    auth_presets.py                      # 4 reusable service-side identity extractors
  runner.py                             # PatternRunner: wires pattern auth into MCP + services, starts them
  agent.py                              # Agent wrapper (connects to MCP servers, runs prompts)
  display.py                            # Rich terminal output (run_as, show_token, compare)
  config.py                             # Env vars, endpoints, client IDs, credentials

patterns/
  p01_service_credential/
    mcp_auth.py                         # Attaches X-API-Key, no user identity
    service_auth.py                     # Extracts API key → service-level identity
    notebook.ipynb
  p02_inline_claim/
    mcp_auth.py                         # Fetches user JWT, decodes claims, adds narrowing query params
    service_auth.py                     # Same as p01 (service still just sees API key)
    notebook.ipynb
  p03_external_authz_agent/
    mcp_auth.py                         # Queries OPA before tool call; attaches API key if allowed
    service_auth.py                     # Same as p01
    notebook.ipynb
  p04_identity_param/
    mcp_auth.py                         # Adds X-User-Id header (unproven string)
    service_auth.py                     # Trusts X-User-Id header → string identity
    notebook.ipynb
  p05_jwt_passthrough/
    mcp_auth.py                         # Fetches user JWT, attaches as Authorization: Bearer
    service_auth.py                     # Validates JWT via JWKS, extracts claims → identity
    notebook.ipynb
  p06_token_exchange/
    mcp_auth.py                         # Exchanges user JWT for narrow-audience token (RFC 8693)
    service_auth.py                     # Validates scoped JWT, checks audience, extracts identity
    notebook.ipynb
  p07_external_authz_tool/
    mcp_auth.py                         # JWT passthrough (same as p05 on MCP side)
    service_auth.py                     # Validates JWT + calls OPA tool_side for resource-level authz
    notebook.ipynb
  p08_three_legged_oauth/
    mcp_auth.py                         # Uses consent-obtained token (browser-based 3LO flow)
    service_auth.py                     # Validates JWT (same as p05 on service side)
    notebook.ipynb

infra/                                  # Unchanged from current repo
  keycloak/realm-export.json
  opa/
    agent_side.rego                     # Pattern 3: role-based tool access control
    tool_side.rego                      # Pattern 7: ReBAC resource access control
    data.json                           # Relationship data

docker-compose.yml                      # Keycloak + OPA only (services run as Python processes now)
```

### What Runs Where

- **Docker Compose** (always running): Keycloak (port 8080), OPA (port 8181)
- **Python processes** (started by PatternRunner per pattern): MCP servers, backend services
- **Jupyter** (user's session): Notebooks that wire and run everything

Services no longer need Dockerfiles. They're lightweight FastAPI apps assembled
and started by the PatternRunner with the pattern's auth injected.

## The Plugin Interface

The entire abstraction a learner must understand:

### MCP-side (how the MCP server authenticates TO the backend service)

```python
# framework/mcp/auth.py
class AuthHandler:
    """Customize how the MCP server authenticates to backend services."""

    async def prepare_request(self, user_context: dict, headers: dict) -> dict:
        """Add auth credentials to outbound request headers. Return headers."""
        return headers

    async def before_tool_call(self, user_context: dict, tool_name: str) -> bool:
        """Gate: should this tool call proceed? Default: yes.
        Used by pattern 3 (agent-side OPA) to check policy before calling."""
        return True
```

Two methods. `prepare_request` handles outbound auth (patterns 1, 2, 4, 5, 6, 8).
`before_tool_call` handles agent-side authorization gates (pattern 3).

### Service-side (how the backend service validates inbound requests)

Each pattern's `service_auth.py` exports a single function:

```python
async def get_identity(request: Request) -> Identity:
    """Extract caller identity from the inbound request."""
    ...
```

This is injected into the FastAPI app as a dependency. The base service has
NO built-in auth — it's pure business logic that receives an Identity.

## Service Auth Presets (for code reuse)

`framework/services/auth_presets.py` provides 4 reusable implementations
so per-pattern `service_auth.py` files don't duplicate crypto logic:

1. **api_key_identity**: Checks X-API-Key header → returns service-level identity.
   Used by patterns 1, 2, 3.
2. **header_identity**: Reads X-User-Id header → returns unproven string identity.
   Used by pattern 4.
3. **jwt_identity**: Validates Bearer JWT via Keycloak JWKS → returns proven
   identity with claims. Distinguishes broad-audience (`method="jwt"`) from
   narrow-audience (`method="scoped_jwt"`) based on `aud` claim.
   Used by patterns 5, 6, 8.
4. **jwt_with_opa_identity**: Validates JWT + checks OPA tool_side policy for
   resource-level authorization before returning identity.
   Used by pattern 7.

Per-pattern `service_auth.py` files can be as short as:
```python
from framework.services.auth_presets import jwt_identity
get_identity = jwt_identity
```

Or provide custom logic when the preset doesn't fit.

## The Notebook Experience

### Notebook 00 — Setup + Orientation

1. Verify Docker Compose is running (Keycloak + OPA health checks)
2. Explain the framework:
   - "Base MCP servers define tools (get_expenses, approve_expense, search_documents)"
   - "Base services define routes and data (no auth built in)"
   - "PatternRunner wires a pattern's auth into both layers and starts everything"
3. Show the AuthHandler interface (2 methods) and the service auth contract (1 function)
4. **Explain the pedagogical choice**: why patterns are isolated, not unified.
   Acknowledge that consolidation is the production approach. We chose isolation
   so each auth exchange is fully visible without tracing through abstractions.
5. Quick smoke test with a no-auth baseline to confirm the plumbing works

### Pattern Notebooks (01–08) — Each Follows This Structure

```python
# Cell 1 — Setup (minimal wiring, ~2 lines)
from framework.runner import PatternRunner
runner = PatternRunner("p06_token_exchange")
```

*(Markdown cell: explain what this pattern is, where it sits in the progression,
what problem it solves that the previous pattern didn't)*

```python
# Cell 2 — Show the auth code for this pattern
runner.show_auth_code()
# Pretty-prints BOTH mcp_auth.py and service_auth.py with syntax highlighting
# This IS the lesson — the focused auth logic for this pattern
```

*(Markdown cell: walk through the code line by line, explain the key decisions)*

```python
# Cell 3 — Start the pattern
await runner.start()
# Starts both MCP servers + both backend services with this pattern's auth wired in
```

```python
# Cell 4+ — Run scenarios with different users
await runner.run_as("alice", "What are my recent expenses?")
await runner.run_as("bob", "Show me all expenses and approve expense 3")
await runner.run_as("dave", "Search for platform architecture documents")
```

```python
# Cell N — Inspect what the service actually received
runner.show_service_identity()
# Shows what identity each service extracted from the request — the punchline
# Side-by-side comparison of what different users' requests looked like
```

*(Markdown cell: explain what we learned, what weakness remains, teaser for next pattern)*

```python
# Cell N+1 — Cleanup
await runner.stop()
```

## The 8 Patterns — Detailed Auth Logic

### Pattern 1 — Service Credential
**MCP server (`mcp_auth.py`)**: Attaches `X-API-Key` header with shared secret. No user context.
**Service (`service_auth.py`)**: Checks API key → returns `Identity(subject="agent-service", method="api_key")`.
**What the service sees**: "the agent called me" — cannot distinguish users.
**Weakness → next**: No user-level filtering or audit trail.

### Pattern 2 — Inline Claim Authorization
**MCP server (`mcp_auth.py`)**: Fetches user JWT from Keycloak (direct grant), decodes claims locally, adds narrowing query params (e.g., `?department=engineering` for a manager). Still sends API key to service.
**Service (`service_auth.py`)**: Same as pattern 1 — just sees API key.
**What the service sees**: API key + narrowed query. Trusts the agent's filtering.
**Weakness → next**: Authz logic is scattered in the MCP server code. Hard to maintain. Gap between what the agent narrows and what the service enforces.

### Pattern 3 — Agent-Side External Authorization (OPA)
**MCP server (`mcp_auth.py`)**: Implements `before_tool_call()` — queries OPA endpoint `POST /v1/data/agentauth/agent_side/decision` with `{user: {role, department, reports_to}, tool, action}`. If OPA denies, raises error. If allowed, proceeds with API key (same as pattern 1 on the wire).
**Service (`service_auth.py`)**: Same as pattern 1 — just sees API key.
**What the service sees**: API key. The OPA check happened before the call.
**Weakness → next**: Agent is the sole enforcement point. A prompt injection attack could instruct the agent to skip the OPA check. The service has no way to verify that authorization occurred.

### Pattern 4 — Identity Parameter
**MCP server (`mcp_auth.py`)**: Adds `X-User-Id: <username>` header to requests.
**Service (`service_auth.py`)**: Reads X-User-Id → returns `Identity(subject=header_value, method="string_id")`. Filters data by that user.
**What the service sees**: A claimed username. Filters correctly. But no proof.
**Weakness → next**: Spoofable. Any caller can set X-User-Id to any value. No cryptographic proof that the user is who they claim to be.

### Pattern 5 — JWT Passthrough
**MCP server (`mcp_auth.py`)**: Fetches user's JWT from Keycloak via direct grant (resource owner password credentials flow). Attaches as `Authorization: Bearer <token>`.
**Service (`service_auth.py`)**: Validates JWT signature via Keycloak JWKS endpoint. Extracts `preferred_username`, `realm_access.roles`, custom claims. Returns `Identity(subject=username, method="jwt", claims=claims)`.
**What the service sees**: A cryptographically proven user identity with full claims.
**Weakness → next**: JWT audience is broad — the token is valid for ALL services, not just this one. If the expense service is compromised, the attacker gets a token that also works against the document service.

### Pattern 6 — Token Exchange (RFC 8693)
**MCP server (`mcp_auth.py`)**: Fetches user JWT, then calls Keycloak's token endpoint with `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `subject_token=<user_jwt>`, `audience=<target-service-client-id>`. Receives a narrow-audience token where `aud` is scoped to just this service and `azp` identifies the agent.
**Service (`service_auth.py`)**: Validates JWT, checks that `aud` includes this service's client_id → returns `Identity(method="scoped_jwt")`. Can distinguish from broad JWT.
**What the service sees**: A cryptographically proven, audience-narrowed token. Least privilege.
**Weakness → next**: No fine-grained resource-level authorization. The user can access all resources they're "allowed" to by role, but per-resource decisions (e.g., "can alice approve THIS specific expense?") aren't enforced.

### Pattern 7 — Tool-Side External Authorization (OPA ReBAC)
**MCP server (`mcp_auth.py`)**: JWT passthrough — same as pattern 5 on the MCP side.
**Service (`service_auth.py`)**: Validates JWT (like pattern 5) AND before returning data, calls OPA `POST /v1/data/agentauth/tool_side/decision` with `{user_id, target_user_id, action, resource_type}`. OPA evaluates relationship-based rules: admin override, self-access, manager-of-target, department peer read.
**What the service sees**: Proven identity + per-resource authorization decisions.
**Weakness → next**: The agent obtained the user's token via direct grant (password flow). The user never explicitly consented to the agent acting on their behalf.

### Pattern 8 — Three-Legged OAuth
**MCP server (`mcp_auth.py`)**: Uses a token obtained through the user's browser via authorization code flow with PKCE. The user explicitly consented in a browser redirect. The MCP server stores this consent token and attaches it as `Authorization: Bearer`.
**Service (`service_auth.py`)**: Validates JWT — same as pattern 5 on the service side. The token is indistinguishable from pattern 5/6 on the wire; the difference is how it was obtained.
**What the service sees**: A consent-obtained JWT. The agent is fully out of the credential chain.
**No further weakness** — this is the gold standard for delegated agent authorization.

## Preserved Unchanged

### Keycloak Realm
- Realm: `agentauth`
- Users: alice (employee/engineering, reports to bob), bob (manager/engineering), dave (admin/platform)
- All passwords: `password`
- Clients: agent-client, user-direct-client, expense-service-client, document-service-client
- Token exchange enabled (v2 standard)
- Realm export at `infra/keycloak/realm-export.json`

### OPA Policies
- `agent_side.rego`: role-based tool access (admin > manager > employee). Default deny.
- `tool_side.rego`: ReBAC resource access (admin override, self-access, manager-of-target, dept peer-read). Default deny.
- `data.json`: relationship graph (manages, members, admins)

### Backend Data
- 8 expenses across alice/bob/dave with varying amounts, statuses, departments
- 8 documents with access_groups (engineering, platform, admin, public)
- Recreated fresh on each service startup (ephemeral SQLite in /tmp)

### Config Values
- `KEYCLOAK_URL` (default: http://localhost:8080), `KEYCLOAK_REALM` (default: agentauth)
- `AGENT_CLIENT_ID` (default: agent-client), `AGENT_CLIENT_SECRET` (default: agent-client-secret)
- `USER_DIRECT_CLIENT_ID` (default: user-direct-client)
- `EXPENSE_SERVICE_URL`, `DOCUMENT_SERVICE_URL` (ports assigned by PatternRunner)
- `OPA_URL` (default: http://localhost:8181)
- `SHARED_SERVICE_API_KEY` (default: dev-shared-api-key)
- `OPENAI_API_KEY` (required), `OPENAI_MODEL` (default: gpt-5-nano)
- `TOOL_TO_TARGET_CLIENT`: maps tool name → Keycloak client_id (for token exchange)
- `KNOWN_USERS`: alice, bob, dave (all use password `password`)

## Implementation Notes

1. **MCP servers should use the `mcp` Python SDK (FastMCP)** with HTTP transport
   (not stdio). Each server exposes tools the agent discovers and calls via
   MCP protocol.

2. **The agent should use the OpenAI Agents SDK** as it does today, but configured
   with MCP servers as tool providers instead of local `@function_tool` functions.

3. **PatternRunner** handles all orchestration:
   - Imports the pattern's `mcp_auth.py` and `service_auth.py`
   - Creates MCP server instances configured with the pattern's AuthHandler
   - Creates FastAPI service instances with the pattern's `get_identity` injected
   - Starts everything on available ports (no port conflicts between patterns)
   - Configures the agent to point at the running MCP servers
   - Provides helpers: `run_as()`, `show_auth_code()`, `show_service_identity()`, `stop()`

4. **Services run as Python processes** (uvicorn on ephemeral ports), NOT in
   Docker containers. Only Keycloak and OPA remain in Docker Compose.

5. **Each pattern's notebook** should be self-contained: a reader can open just
   that notebook (after running notebook 00 for setup) and understand the pattern
   fully without having read other pattern notebooks.

6. **Preserve the pedagogical progression**: each notebook ends by explaining
   what weakness remains, motivating the next pattern. The README's comparison
   table (showing identity proof, least privilege, audit trail, etc.) should
   be preserved and updated.

7. **The README** should be updated to reflect the new architecture, explain
   the MCP server layer, and prominently include the rationale for per-pattern
   isolation vs. unified codebase.

8. **Remove the old shared/auth.py strategy classes**, old shared/tools.py,
   old service Dockerfiles, and old per-service auth.py files. The new
   architecture replaces all of these.
