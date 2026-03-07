# fastapi-a2a

**fastapi-a2a** turns any existing FastAPI application into a fully compliant [A2A Protocol](https://google.github.io/A2A) agent — no boilerplate, no rewrites. Drop it in, and your app instantly gets an agent card, task management, secure token issuance, discovery registration, and much more.

---

## What problem does it solve?

The Agent-to-Agent (A2A) Protocol is Google's open standard for agents communicating with each other. Implementing it from scratch means wiring up agent card endpoints, task state machines, JWT security, registry heartbeats, rate limiting, policy evaluation, and more — easily weeks of work.

**fastapi-a2a** does all of that for you. You keep writing FastAPI routes the way you always have. The library introspects them and exposes a compliant A2A interface automatically.

---

## Quick Start

```bash
pip install fastapi-a2a
```

```python
from fastapi import FastAPI
from fastapi_a2a import FastApiA2A, RegistryConfig

app = FastAPI(title="My Agent")

a2a = FastApiA2A(
    app,
    url="https://my-agent.example.com",
    # registry= is optional; omit it and the library auto-discovers via DNS-SD
    # registry=RegistryConfig(url="https://registry.example.com"),
)
# That's it. Your agent card is served at /.well-known/agent.json
```

The library manages its own SQLAlchemy models internally. On first startup, `FastApiA2A` creates a database engine from your `database_url` and all required tables are available via the shared `Base` metadata — integrate them into your existing Alembic migrations if you use one, or let SQLAlchemy create them directly in development.

---

## What you get out of the box

Once you wrap your app with `FastApiA2A`, these endpoints are automatically mounted:

| Endpoint | What it does |
|----------|-------------|
| `GET /.well-known/agent.json` | Serves your agent card (auto-generated from your routes) |
| `GET /.well-known/agent-extended.json` | Extended capability metadata |
| `POST /rpc` | A2A JSON-RPC: `tasks/send`, `tasks/get`, `tasks/cancel`, `tasks/sendSubscribe` |
| `GET /tasks` | List all tasks |
| `GET /tasks/{id}` | Task detail with messages and artifacts |
| `POST /tasks/{id}/cancel` | Cancel a running task |
| `GET /registry/agents` | Discover nearby agents |
| `POST /registry/agents` | Register as an agent |
| `GET /admin/keys` | JWKS key management |
| `POST /admin/keys/rotate` | Rotate signing keys |
| `GET /consent/policies` | Data consent policies |

And in the background:
- **Heartbeat scheduler** — pings the registry every 60 seconds to stay discoverable
- **Rate limiter** — Redis-backed sliding window (falls back to in-memory)
- **Embedding processor** — vector DB upserts for semantic search (Weaviate, Pinecone, Qdrant, FAISS)
- **Reputation engine** — scores agent safety based on scan history
- **Federation crawler** — discovers and syncs peer agents
- **Execution policy runtime** — evaluates formal access control policies

---

## Configuration

Everything is controlled via environment variables (prefix: `A2A_`) or by passing a `FastApiA2AConfig`:

```python
from fastapi_a2a import FastApiA2A, FastApiA2AConfig, RegistryConfig

config = FastApiA2AConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/mydb",
    discovery_mode="auto",           # auto | explicit | disabled
    scan_mode="async",               # sync | async
    require_signed_cards=True,
    dual_write_enabled=True,
)

a2a = FastApiA2A(app, url="http://myagent.example.com", config=config)
```

Or via `.env` / environment:

```bash
A2A_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/mydb
A2A_DISCOVERY_MODE=auto
A2A_REQUIRE_SIGNED_CARDS=true
```

---

## Project Structure

```
fastapi_a2a/
├── bridge/               # Core: FastApiA2A class, config, route inspector
├── database.py           # SQLAlchemy async engine + session factory
└── domains/
    ├── core_a2a/         # Agent card, agent info, RPC handler
    ├── task_lifecycle/   # Task, Message, Artifact, TaskSession models + REST API
    ├── security/         # JWT middleware, token issuance & rotation
    ├── registry/         # Agent registration, discovery, heartbeat
    ├── access_control/   # Policy models, formal evaluator (I1–I5 invariants)
    ├── tracing/          # OpenTelemetry middleware, span models
    ├── token_hardening/  # Token families, rate limiter, dual-write audit log
    ├── embedding/        # Embedding pipeline, vector DB adapters
    ├── consent/          # Data consent policies, runtime enforcement
    ├── key_management/   # JWKS key rotation, kid management
    ├── execution_policy/ # Execution gate, policy evaluation runtime
    ├── federation/       # Peer federation, crawler, takedown requests
    └── safety/           # Prompt sanitizer, card scanner, reputation engine
```

---

## Domain Model

The library implements the full A2A v0.6.0 spec — **78 entities across 15 domains**:

| Domain | Entities |
|--------|----------|
| Core A2A | AgentCard, AgentInfo, AgentSkill, SkillSchema, Capabilities |
| Task Lifecycle | Task, TaskSession, Message, MessagePart, Artifact |
| Security | AgentToken, JWTConfig, KeyPair, SigningKey, AuthPolicy |
| Registry & Discovery | RegistryEntry, AgentDiscovery, Heartbeat |
| Access Control | PolicyRule, PolicySet, Principal, Resource, Action, Effect, Evaluation |
| Distributed Tracing | TraceSpan, TraceContext |
| Token Hardening | TokenFamily, TokenAuditLog, RateLimitBucket, SlidingWindow, DualWriteEvent, RevocationRecord |
| Embedding Pipeline | EmbeddingJob, EmbeddingVector, VectorIndex, ChunkMap, EmbeddingMetadata |
| Consent & Governance | ConsentPolicy, DataCategory, RetentionRule, PurposeBinding, ConsentRecord, PolicyVersion |
| Key Management | SigningKeyRecord, JWKSCacheBust |
| Execution Policy | PolicyGate, ExecutionContext, PolicyVariable, PolicyExpression, PolicyResult, PolicyAudit, PolicyVersion, PolicySet |
| Federation & Crawler | FederationPeer, CrawlJob, CrawlResult, TakedownRequest, SyncRecord, PeerCapability, FederationAudit |
| Safety & Reputation | CardScanResult, SyntheticCheckResult, ReputationScore, ThreatIndicator, SafetyAuditEvent, PromptSanitizeLog, ContentPolicy |

---

## Security

- **JWT-based authentication** — ES256/RS256 keypairs, per-agent token families
- **Token rotation** — automatic refresh token rotation with reuse-attack detection
- **Rate limiting** — Redis sliding window (in-memory fallback)
- **Prompt sanitizer** — 8 rules (PII, injection, bidi, script tags, etc.) across 8 surfaces
- **Signed agent cards** — optional JWKS-backed card signing
- **Audit log** — every token event recorded (issued, rotated, revoked, family_revoked)

---

## Requirements

- Python 3.11+ (tested on 3.11, 3.12, 3.14)
- PostgreSQL with the `asyncpg` driver
- Redis (optional — the rate limiter falls back to in-memory if Redis isn't reachable)

---

## Development

```bash
# Clone
git clone <your-repo-url>   # e.g. https://github.com/your-org/fastapi-a2a
cd fastapi-a2a

# Install with dev extras
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -q            # → 66 passed

# Lint (zero errors expected)
ruff check fastapi_a2a
pyright fastapi_a2a
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
