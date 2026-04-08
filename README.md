# exploring-agent-auth

Eight identity and authorization patterns for AI agents calling tools, in
runnable Jupyter notebooks. Keycloak, OPA, and FastAPI, all local via Docker
Compose.

The pedagogical arc is that authz starts inside the agent and progressively
moves toward the service where it belongs — each notebook ends by exposing a
gap that the next one fixes. Same agent, same prompts, same three users
(`alice`, `bob`, `carlo`); only the auth strategy changes.

## Setup

Requirements: Docker (with Compose v2), [`uv`](https://docs.astral.sh/uv/),
Python 3.12, and an OpenAI API key.

```bash
cp .env.example .env
$EDITOR .env                 # paste your OPENAI_API_KEY

docker compose up -d         # Keycloak, OPA, expense-service, document-service
uv sync                      # installs deps and the editable `shared` package

uv run jupyter lab           # opens the notebook UI
```

Then run `notebooks/00_setup_verification.ipynb` top to bottom. If every cell
ends in `[PASS]`, the local infrastructure is good and you can start working
through the eight pattern notebooks in order.

## The eight patterns

| # | Notebook | Pattern | Where authz lives | Tool sees real user | Cryptographic proof | Least privilege | Audit trail |
|---|---|---|---|---|---|---|---|
| 1 | `01_service_credential` | Shared service credential | nowhere | no | no | no | weak |
| 2 | `02_inline_claim_authz` | Inline claim-based authz | scattered in agent code | no | no | partial | weak |
| 3 | `03_external_authz_agent` | External authz, agent-side (OPA) | OPA, enforced by agent | no | no | partial | OPA decisions logged |
| 4 | `04_identity_param` | Identity as parameter | tool, on agent's word | yes (unproven) | no | yes | recorded but spoofable |
| 5 | `05_jwt_passthrough` | Full JWT passthrough | tool, validated JWT | yes (proven) | yes | no (broad audience) | strong |
| 6 | `06_token_exchange` | Token exchange (act-on-behalf) | tool, validated + audience-narrowed JWT | yes (proven) | yes | yes | strongest with std OAuth |
| 7 | `07_external_authz_tool` | External authz, tool-side (ReBAC) | both: token + OPA in service | yes (proven) | yes | yes | strongest, with per-resource decisions |
| 8 | `08_three_legged_oauth` | 3-legged OAuth consent | tool, JWT issued via user consent | yes (proven, user-consented) | yes | yes | strongest, agent out of credential chain |

The progression is real, not arbitrary: each row fixes a specific failure
of the previous row. The "what's still missing" cell at the end of each
notebook spells out which weakness motivates the next pattern.

## What's in the box

- **`docker-compose.yml`** — four services on a shared network:
  - `keycloak` (`:8080`) — OIDC, JWT minting, Standard Token Exchange v2
  - `opa` (`:8181`) — Rego policies for agent-side and tool-side authz
  - `expense-service` (`:8001`) — auth-flexible FastAPI app, SQLite seed data,
    optional OPA hook
  - `document-service` (`:8002`) — same shape, different seed data
- **`infra/keycloak/realm-export.json`** — single realm `agentauth` with three
  pre-seeded users (alice / bob / carlo), four clients, custom claims, audience
  mappers, and Standard Token Exchange v2 enabled on the agent client. Imported
  on first boot via `--import-realm`.
- **`infra/opa/`** — `agent_side.rego`, `tool_side.rego`, `data.json` for
  the ReBAC relationships (alice → reports_to → bob, etc.).
- **`services/{expense,document}-service/`** — self-contained FastAPI apps.
  Each has an `auth.py` with a single flexible-identity dependency that
  inspects the inbound request and extracts identity from whichever method
  the caller used (none / API key / `X-User-Id` / full JWT / scoped JWT).
  Every request is recorded to `/debug/last-request` so notebooks can show
  what the service actually received.
- **`shared/`** — host-side Python package importable from notebooks
  (installed editable via `uv sync`):
  - `config.py` — endpoints and credentials from `.env`
  - `auth.py` — one strategy class per pattern
  - `tools.py` — `get_expenses`, `approve_expense`, `search_documents`
  - `agent.py` — minimal OpenAI `chat.completions` tool-calling loop, no
    framework
  - `display.py` — `run_as`, `show_token`, `compare_tokens`,
    `show_what_tool_saw`, `three_legged_login`
- **`notebooks/`** — `00_setup_verification.ipynb` plus the eight pattern
  notebooks.

## Users and seed data

Three users in the Keycloak realm `agentauth`, all with password `password`:

| User | Role | Department | Reports to |
|---|---|---|---|
| `alice` | employee | engineering | bob |
| `bob` | manager | engineering | — |
| `carlo` | admin | platform | — |

The expense-service has eight expenses across the three users (one of alice's
expenses is in `pending` status — it's the one bob approves in pattern 7's
demo). The document-service has eight documents tagged with access groups
(`engineering`, `platform`, `admin`, `public`).

The same query — *"what expenses do I have?"* — returns radically different
data depending on the pattern and the user, and that contrast is the whole
point of the project.

## Out of scope

These are real and important parts of agent security but deliberately not in
this repo, because each one is its own teaching artifact:

- **Capability tokens** (Biscuit, macaroons): an alternative to bearer JWTs
  where authority is encoded as attenuable, offline-verifiable claims.
  Theoretically cleaner than the bearer-token model for many agent use cases,
  but with limited adoption in 2026.
- **Mutual identity at the transport layer** (mTLS, SPIFFE/SPIRE): sits
  beneath every pattern in this repo. We use cleartext HTTP and assume the
  network boundary is trusted, because running SPIRE locally is more friction
  than the teaching value.
- **Gateway-mediated identity injection**: an API gateway terminates the
  user's auth at the edge and injects a verified identity header to internal
  services. Common in service-mesh setups.

These (and the eight in this repo) are covered as a broader taxonomy in the
*Eight Dimensions of AI Agent Security* post on
[carloperottino.com](https://carloperottino.com).

## A note on Pattern 6 and the `act` claim

Pattern 6 uses Keycloak's **Standard Token Exchange v2**, which is the
supported, GA token-exchange feature in Keycloak 26.2+. It implements RFC 8693
internal-to-internal exchange and produces tokens with narrowed `aud` and a
correctly-populated `azp` (Authorized Party) identifying the requesting agent.

RFC 8693 also defines an `act` claim for delegation tokens — a structured
field that says *"this token represents the user, and was minted at the
request of party Y"*. Keycloak's Standard Token Exchange v2 does not
auto-populate `act` as of 26.x — the equivalent audit information lives in
`azp`. Production deployments are split: some use `azp`, some use custom
claims, some inject `act` via a custom protocol mapper.

This repo deliberately uses `azp` because that's what the supported Keycloak
feature gives you natively. Notebook 06's tradeoff cell explains the
distinction. The conceptual story — *the exchanged token preserves the user
identity AND identifies the intermediate agent* — holds either way.

## Useful commands

```bash
docker compose up -d                                       # start all services
docker compose ps                                          # check status
docker compose logs -f keycloak                            # tail Keycloak logs
docker compose down                                        # stop and remove

uv run jupyter lab                                         # notebooks
uv run jupyter nbconvert --execute --to notebook --output /tmp/out.ipynb \
    notebooks/00_setup_verification.ipynb                  # smoke-test infra

# Restart just one service after editing its source:
docker compose up -d --no-deps --build expense-service
```

## License

MIT — see `LICENSE`.
