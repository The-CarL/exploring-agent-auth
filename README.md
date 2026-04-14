# exploring-agent-auth

Eight identity and authorization patterns for AI agents calling tools, in
runnable Jupyter notebooks. Keycloak, OPA, MCP servers, and FastAPI, all
local via Docker Compose + Python.

Across the eight notebooks, authz starts inside the agent and moves toward
the service. Each notebook ends by exposing a problem the next one fixes.
Same agent, same prompts, same three users (`alice`, `bob`, `dave`); the
only thing that changes is the auth code in two small files.

## Architecture

```
Agent <--> Expense MCP Server (HTTP) <--> Expense FastAPI Service
Agent <--> Document MCP Server (HTTP) <--> Document FastAPI Service
```

### Why MCP servers?

You don't strictly need MCP servers to demonstrate these auth patterns. The
agent could call the backend services directly. We include them for two
reasons:

1. **Realism.** In production agent deployments, tools are typically exposed
   through MCP servers rather than called as raw HTTP endpoints.

2. **MCP has first-class OAuth2 support.** The MCP specification includes a
   full authorization framework: servers can require OAuth2 Bearer tokens on
   the transport layer, advertise OAuth metadata endpoints, and validate
   tokens via introspection or JWKS. In this repo we keep MCP servers as
   simple pass-through proxies to focus on the auth patterns themselves, but
   in production you would likely configure OAuth2 at the MCP transport level.
   See the [MCP authorization spec](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization)
   for details.

### The MCP server's role

In this repo, the MCP server is infrastructure. It forwards credentials from
the agent to the backend service. It never makes authorization decisions. The
auth logic lives in two small files per pattern:

- **`mcp_auth.py`**: what credentials the MCP server attaches to outbound
  requests (API key, user header, JWT, exchanged token)
- **`service_auth.py`**: how the backend service extracts identity and makes
  authz decisions from the inbound request

The progression is about what the **backend service** can do with whatever
identity information reaches it.

### Why per-pattern isolation?

In production, you'd likely use a single flexible auth layer that supports
multiple methods. We deliberately didn't do that here. Each pattern has its
own auth code, on both the MCP server side and the service side, so you can
read exactly what happens at each boundary without tracing through
abstractions. Once you understand each pattern individually, consolidating
them is straightforward.

## Setup

You need Docker (with Compose v2), [`uv`](https://docs.astral.sh/uv/),
Python 3.12, and an OpenAI API key.

```bash
cp .env.example .env
$EDITOR .env                 # paste your OPENAI_API_KEY

docker compose up -d         # Keycloak + OPA
uv sync                      # installs deps and the editable `framework` package

uv run jupyter lab
```

Run `patterns/p00_setup/notebook.ipynb` first. If the health checks pass and
the smoke test returns data, the local infrastructure is working and you can
walk through the eight pattern notebooks in order.

## The eight patterns

| # | Pattern | What MCP sends | Service authz | Crypto proof | Audit trail |
|---|---|---|---|---|---|
| 1 | Service credential | API key | none (no user identity) | no | weak |
| 2 | Identity parameter | API key + X-User-Id | filters by trusted username | no | username recorded |
| 3 | Inline claim authz (agent-side) | API key (MCP reads JWT claims, narrows calls) | none (just API key) | no | none at service |
| 4 | External authz, agent-side (OPA) | API key (MCP checks OPA first) | none (just API key) | no | OPA logged, not at service |
| 5 | JWT passthrough | Bearer JWT | validates signature via JWKS | yes | strong |
| 6 | Token exchange (RFC 8693) | Bearer scoped JWT | validates narrow-audience JWT | yes | strongest with std OAuth |
| 7 | Tool-side OPA (ReBAC) | Bearer JWT | validates JWT + OPA per-resource | yes | per-resource decisions |
| 8 | 3-legged OAuth consent | Bearer consent JWT | validates consent-obtained JWT | yes | agent out of credential chain |

Three tiers:
- **Agent-side authz (1-4):** service trusts the MCP server via API key. Authz decisions happen at the agent/MCP layer, from no user context (1) to asserted username (2) to claim-based narrowing (3) to externalized OPA policy (4). The service is blind to user identity in all four.
- **Service-verified identity (5-6):** service validates the JWT independently via JWKS. No shared secret needed. Audience narrows from broad (5) to service-scoped (6).
- **Fine-grained + consent (7-8):** per-resource authz via OPA ReBAC, then user consent removes the agent from the credential chain.

Each row fixes a specific weakness of the row above it.

## What's in the repo

```
exploring-agent-auth/
├── docker-compose.yml           Keycloak + OPA (services run as Python processes)
├── .env.example                 OPENAI_API_KEY etc.
├── pyproject.toml               uv project, hatchling build
│
├── framework/                   shared harness (editable wheel via uv sync)
│   ├── config.py                endpoints, client IDs, env loading
│   ├── auth_helpers.py          fetch_user_jwt, decode_jwt, exchange_token
│   ├── agent.py                 OpenAI Agents SDK wrapper with MCP support
│   ├── runner.py                PatternRunner: wires pattern auth, starts everything
│   ├── display.py               show_token, compare_tokens, show_what_tool_saw
│   ├── mcp/
│   │   ├── auth.py              AuthHandler base class (2-method interface)
│   │   ├── expense_server.py    FastMCP: get_expenses, approve_expense tools
│   │   └── document_server.py   FastMCP: search_documents tool
│   └── services/
│       ├── identity.py          Identity dataclass
│       ├── auth_presets.py      4 reusable service-side identity extractors
│       ├── expense/app.py       FastAPI factory (no built-in auth)
│       └── document/app.py      FastAPI factory (no built-in auth)
│
├── patterns/
│   ├── p00_setup/notebook.ipynb           setup + orientation
│   ├── p01_service_credential/            mcp_auth.py, service_auth.py, notebook.ipynb
│   ├── p02_identity_param/                ...
│   ├── p03_inline_claim_agent/             ...
│   ├── p04_external_authz_agent/          ...
│   ├── p05_jwt_passthrough/               ...
│   ├── p06_token_exchange/                ...
│   ├── p07_external_authz_tool/           ...
│   └── p08_three_legged_oauth/            ...
│
└── infra/
    ├── keycloak/realm-export.json   realm with 3 users, 4 clients, custom claims,
    │                                audience mappers, Standard Token Exchange v2
    └── opa/
        ├── agent_side.rego          pattern 3: role-based "can user invoke tool?"
        ├── tool_side.rego           pattern 7: ReBAC "can user X act on Y's resource?"
        └── data.json                relationships (alice reports to bob, etc.)
```

### The plugin interface

Each pattern implements two files:

**mcp_auth.py** subclasses `AuthHandler`:
```python
class AuthHandler:
    async def prepare_request(self, user_context, headers) -> dict:
        """Add auth credentials to outbound headers."""
        return headers
    async def before_tool_call(self, user_context, tool_name) -> bool:
        """Pre-call gate. Default: always allow."""
        return True
```

**service_auth.py** exports identity extractors:
```python
async def get_identity(request: Request) -> Identity:
    """Extract caller identity from the inbound request."""
```

Two methods on the MCP side. One function on the service side.

## Users and seed data

Three users in the Keycloak realm `agentauth`, all with password `password`:

| User    | Role     | Department  | Reports to |
|---------|----------|-------------|------------|
| `alice` | employee | engineering | bob        |
| `bob`   | manager  | engineering | (nobody)   |
| `dave`  | admin    | platform    | (nobody)   |

The expense service has eight expenses across the three users. One of alice's
expenses is in `pending` status and gets approved by bob in pattern 7. The
document service has eight documents tagged with access groups (`engineering`,
`platform`, `admin`, `public`).

The same query, *"what expenses do I have?"*, returns very different results
depending on the pattern and the user. That contrast is the project.

## Out of scope

Real and important parts of agent security that are deliberately not in this
repo, because each one is its own teaching artifact:

- **Capability tokens** (Biscuit, macaroons). An alternative to bearer JWTs
  where authority is encoded as attenuable, offline-verifiable claims.
- **Mutual identity at the transport layer** (mTLS, SPIFFE/SPIRE). Sits
  beneath every pattern in this repo. We use cleartext HTTP and assume the
  network boundary is trusted.
- **Gateway-mediated identity injection.** An API gateway terminates the
  user's auth at the edge and injects a verified identity header to internal
  services.
- **OAuth2 scope-based restrictions.** This repo uses custom JWT claims
  (role, department) for authorization. OAuth2 scopes (e.g. `expenses:read`,
  `expenses:approve`) are a complementary mechanism that restricts what a
  token can do regardless of the user's role. From the service's perspective,
  the mechanics are the same as claims: read a field from the token, enforce
  it in code. Scopes are most relevant in patterns 6 (token exchange, where
  you'd request narrow scopes) and 8 (3LO consent, where the user chooses
  what to authorize). We omit them to keep the focus on identity progression.

These (and the eight in this repo) are part of a broader taxonomy in the
*Eight Dimensions of AI Agent Security* post on
[carloperottino.com](https://carloperottino.com).

## Pattern 6 and the `act` claim

Pattern 6 uses Keycloak's Standard Token Exchange v2, which is the supported,
GA token-exchange feature in Keycloak 26.2+. It implements RFC 8693
internal-to-internal exchange and produces tokens with narrowed `aud` and a
correctly populated `azp` (Authorized Party) identifying the requesting agent.

RFC 8693 also defines an `act` claim for delegation tokens. Keycloak's
Standard Token Exchange v2 does not auto-populate `act` as of 26.x. The
equivalent audit information lives in `azp`. This repo uses `azp` because
that's what Keycloak gives you natively.

## Useful commands

```bash
docker compose up -d                    # start Keycloak + OPA
docker compose ps                       # check status
docker compose logs -f keycloak         # tail Keycloak logs
docker compose down                     # stop and remove

uv sync                                 # install/update deps
uv run jupyter lab                      # open notebooks
```

## License

MIT. See `LICENSE`.
