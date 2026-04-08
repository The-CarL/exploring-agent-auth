# exploring-agent-auth

Eight identity and authorization patterns for AI agents calling tools, in
runnable Jupyter notebooks. Keycloak, OPA, and FastAPI, all local via Docker
Compose.

Across the eight notebooks, authz starts inside the agent and moves toward
the service. Each notebook ends by exposing a problem the next one fixes.
Same agent, same prompts, same three users (`alice`, `bob`, `dave`); the
only thing that changes is the auth strategy.

## Setup

You need Docker (with Compose v2), [`uv`](https://docs.astral.sh/uv/),
Python 3.12, and an OpenAI API key.

```bash
cp .env.example .env
$EDITOR .env                 # paste your OPENAI_API_KEY

docker compose up -d         # Keycloak, OPA, expense-service, document-service
uv sync                      # installs deps and the editable `shared` package

uv run jupyter lab
```

Run `notebooks/00_setup_verification.ipynb` first. If every cell ends in
`[PASS]` the local infrastructure is working and you can walk through the
eight pattern notebooks in order.

## The eight patterns

| # | Notebook | Pattern | Authz lives | Tool sees real user | Crypto proof | Least privilege | Audit trail |
|---|---|---|---|---|---|---|---|
| 1 | `01_service_credential` | Shared service credential | nowhere | no | no | no | weak |
| 2 | `02_inline_claim_authz` | Inline claim-based authz | scattered in agent code | no | no | partial | weak |
| 3 | `03_external_authz_agent` | External authz, agent-side (OPA) | OPA, enforced by agent | no | no | partial | OPA decisions logged |
| 4 | `04_identity_param` | Identity as parameter | tool, on agent's word | yes (unproven) | no | yes | recorded but spoofable |
| 5 | `05_jwt_passthrough` | Full JWT passthrough | tool, validated JWT | yes (proven) | yes | no (broad audience) | strong |
| 6 | `06_token_exchange` | Token exchange | tool, audience-narrowed JWT | yes (proven) | yes | yes | strongest with std OAuth |
| 7 | `07_external_authz_tool` | External authz, tool-side (ReBAC) | both: token + OPA in service | yes (proven) | yes | yes | per-resource decisions |
| 8 | `08_three_legged_oauth` | 3-legged OAuth consent | tool, JWT issued via user consent | yes, user-consented | yes | yes | agent out of credential chain |

Each row fixes a specific failure of the row above it. The "what's missing"
cell at the end of each notebook says which weakness motivates the next one.

## What's in the repo

```
exploring-agent-auth/
├── docker-compose.yml         four containers on a shared network
├── .env.example               OPENAI_API_KEY etc.
├── pyproject.toml             uv project, hatchling build, shared/ as editable wheel
│
├── infra/
│   ├── keycloak/
│   │   └── realm-export.json  realm with 3 users, 4 clients, custom claims,
│   │                          audience mappers, Standard Token Exchange v2
│   └── opa/
│       ├── agent_side.rego    pattern 3: role-based "can user invoke tool?"
│       ├── tool_side.rego     pattern 7: ReBAC "can user X act on Y's resource?"
│       └── data.json          relationships (alice reports to bob, etc.)
│
├── services/
│   ├── expense-service/       FastAPI, SQLite, auth-flexible identity dep
│   └── document-service/      same shape, different seed data
│
├── shared/                    host-side Python package, editable install
│   ├── config.py              endpoints, client IDs, env loading
│   ├── auth.py                seven AuthStrategy classes, one per pattern
│   ├── tools.py               get_expenses, approve_expense, search_documents
│   ├── agent.py               OpenAI chat.completions tool-calling loop
│   └── display.py             run_as, show_token, compare_tokens, etc.
│
└── notebooks/
    ├── 00_setup_verification.ipynb
    └── 01_service_credential.ipynb ... 08_three_legged_oauth.ipynb
```

The `auth.py` files in `services/expense-service/` and
`services/document-service/` are intentionally a literal copy of each other.
The services share no Python code at runtime (each has its own Dockerfile that
copies only its own folder).

The two FastAPI services accept any of {no auth, API key, `X-User-Id`, full
JWT, scoped JWT} on every endpoint, and record what they got to
`/debug/last-request`. That endpoint is the punchline of every notebook: no
matter how clever the agent's auth strategy is, the service either does or
does not have a proven user identity to work with.

## Users and seed data

Three users in the Keycloak realm `agentauth`, all with password `password`:

| User    | Role     | Department  | Reports to |
|---------|----------|-------------|------------|
| `alice` | employee | engineering | bob        |
| `bob`   | manager  | engineering | (nobody)   |
| `dave`  | admin    | platform    | (nobody)   |

The expense-service has eight expenses across the three users. One of alice's
expenses is in `pending` status and gets approved by bob in pattern 7. The
document-service has eight documents tagged with access groups (`engineering`,
`platform`, `admin`, `public`).

The same query, *"what expenses do I have?"*, returns very different results
depending on the pattern and the user. That contrast is the project.

## Out of scope

Real and important parts of agent security that are deliberately not in this
repo, because each one is its own teaching artifact:

- **Capability tokens** (Biscuit, macaroons). An alternative to bearer JWTs
  where authority is encoded as attenuable, offline-verifiable claims.
  Theoretically cleaner than the bearer-token model for some agent use cases,
  but with limited adoption as of 2026.
- **Mutual identity at the transport layer** (mTLS, SPIFFE/SPIRE). Sits
  beneath every pattern in this repo. We use cleartext HTTP and assume the
  network boundary is trusted, because running SPIRE locally is more friction
  than the teaching value.
- **Gateway-mediated identity injection.** An API gateway terminates the
  user's auth at the edge and injects a verified identity header to internal
  services. Common in service-mesh setups.

These (and the eight in this repo) are part of a broader taxonomy in the
*Eight Dimensions of AI Agent Security* post on
[carloperottino.com](https://carloperottino.com).

## Pattern 6 and the `act` claim

Pattern 6 uses Keycloak's Standard Token Exchange v2, which is the supported,
GA token-exchange feature in Keycloak 26.2+. It implements RFC 8693
internal-to-internal exchange and produces tokens with narrowed `aud` and a
correctly populated `azp` (Authorized Party) identifying the requesting agent.

RFC 8693 also defines an `act` claim for delegation tokens: a structured
field that says "this token represents the user, and was minted at the
request of party Y". Keycloak's Standard Token Exchange v2 does not
auto-populate `act` as of 26.x. The equivalent audit information lives in
`azp`. Production deployments are split: some use `azp`, some use custom
claims, some inject `act` via a custom protocol mapper. There's no universal
practice yet.

This repo uses `azp` because that's what the supported Keycloak feature gives
you natively. Notebook 06 has a tradeoff cell that explains the distinction.
The substantive story (the exchanged token preserves the user identity AND
identifies the intermediate agent) holds either way.

## Useful commands

```bash
docker compose up -d                    # start all services
docker compose ps                       # check status
docker compose logs -f keycloak         # tail Keycloak logs
docker compose down                     # stop and remove

uv run jupyter lab                      # notebooks
uv run jupyter nbconvert --execute --to notebook --output /tmp/out.ipynb \
    notebooks/00_setup_verification.ipynb

# Restart one service after editing its source:
docker compose up -d --no-deps --build expense-service
```

## License

MIT. See `LICENSE`.
