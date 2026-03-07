# fastapi-a2a

**fastapi-a2a** turns any existing FastAPI application into a fully compliant [A2A Protocol](https://google.github.io/A2A) agent — no boilerplate, no rewrites. Drop it in, and your app instantly gets an agent card, task management, secure token handling, discovery registration, distributed tracing, and consent enforcement.

> This is a **library**, not an application. It has no `main.py`, no server runner, and no bundled migrations. You integrate it into your existing FastAPI app and own those choices.

---

## What problem does it solve?

The Agent-to-Agent (A2A) Protocol is Google's open standard for agents communicating with each other. Implementing it from scratch means wiring up agent card endpoints, task state machines, JWT security, registry heartbeats, rate limiting, consent enforcement, and more — easily weeks of work.

**fastapi-a2a** does all of that for you as a reusable library. You keep writing FastAPI routes the way you always have; the library introspects them and exposes a compliant A2A interface automatically.

---

## Quick Start

```bash
pip install fastapi-a2a
```

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi_a2a import FastApiA2A, SecurityHeadersMiddleware, RequestIdMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    await a2a.startup()
    yield
    await a2a.shutdown()

app = FastAPI(title="My Agent", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)   # OWASP security headers
app.add_middleware(RequestIdMiddleware)         # X-Request-ID tracing

a2a = FastApiA2A(
    app,
    name="My Agent",
    description="What this agent does",
    url="https://my-agent.example.com",
    version="1.0.0",
    database_url="postgresql+asyncpg://user:pass@host/mydb",
)
# Agent card is now live at /.well-known/agent.json
```

The library manages all SQLAlchemy ORM models internally. All 71 tables are available via the shared `Base` metadata — plug them into your existing Alembic migrations or call `Base.metadata.create_all()` in development.

---

## Public API

Everything importable from the top-level `fastapi_a2a` package:

```python
# Core bridge
from fastapi_a2a import FastApiA2A, FastApiA2AConfig, RegistryConfig

# A2A schemas (Pydantic models)
from fastapi_a2a import AgentCard, AgentSkill, AgentCapabilities, SkillSchema

# Database — SQLAlchemy Base + FastAPI Depends() factory
from fastapi_a2a import Base, get_db

# Exceptions — typed A2A error hierarchy
from fastapi_a2a import (
    A2AError,                    # base class
    A2AHTTPError,                # base HTTP error (has .status_code, .error_code)
    CardNotFoundError,           # 404 / 4000
    CardSignatureInvalidError,   # 401 / 4010
    ConsentMissingError,         # 403 / 4020
    ConsentExpiredError,         # 403 / 4021
    AccessDeniedError,           # 403 / 4040
    SkillCircuitOpenError,       # 503 / 4032
    SkillNotFoundError,          # 404 / 4033
    SkillTimeoutError,           # 504 / 4030
    TaskNotFoundError,           # 404 / 4070
    DatabaseUnavailableError,    # 503 / 5001
)

# Middleware
from fastapi_a2a import SecurityHeadersMiddleware, RequestIdMiddleware

# Logging
from fastapi_a2a import get_logger
logger = get_logger(__name__)
```

**Using `get_db` in your own routes:**
```python
from fastapi import Depends
from fastapi_a2a import get_db
from sqlalchemy.ext.asyncio import AsyncSession

@router.get("/my-endpoint")
async def my_route(db: AsyncSession = Depends(get_db(app.state.session_factory))):
    ...
```

## Configuration

Pass settings as constructor parameters or via `A2A_`-prefixed environment variables:

```python
a2a = FastApiA2A(
    app,
    name="My Agent",
    description="...",
    url="https://my-agent.example.com",
    version="1.0.0",                           # SemVer enforced at DB level
    database_url="postgresql+asyncpg://...",    # or A2A_DATABASE_URL env var
    icon_url="https://example.com/icon.png",    # optional
    min_sdk_version="1.0.0",                    # optional
    input_modes=["text/plain"],                 # optional
    output_modes=["text/plain"],                # optional
    supports_auth_schemes=["bearer"],           # optional
)
```

```bash
A2A_DATABASE_URL=postgresql+asyncpg://user:pass@host/mydb
A2A_REQUIRE_SIGNED_CARDS=true
A2A_DISCOVERY_MODE=auto    # auto | explicit | disabled
```

---

## Auto-mounted Endpoints

| Endpoint | Description |
|---|---|
| `GET /.well-known/agent.json` | A2A agent card (public skills only) |
| `GET /.well-known/agent-extended.json` | Extended card with quarantine/approval status |
| `POST /rpc` | JSON-RPC: `tasks/send`, `tasks/get`, `tasks/cancel`, `tasks/sendSubscribe` |
| `GET /tasks` | List all tasks |
| `GET /tasks/{id}` | Task detail — messages, artifacts, push config |
| `POST /tasks/{id}/cancel` | Cancel a running task |
| `GET /registry/agents` | Discover nearby A2A-compatible agents |
| `POST /registry/agents` | Self-register in the discovery index |
| `GET /admin/keys` | JWKS public key endpoint |
| `POST /admin/keys/rotate` | Rotate card signing key (KMS-backed) |
| `GET /consent/policies` | View data-use consent records |
| `GET /rpc/health` | Liveness probe → `{"status":"ok","protocol":"A2A/1.0"}` |

### Background Services

Started automatically on `startup()` — no extra config needed:

| Service | What it does |
|---|---|
| **Heartbeat scheduler** | Pings registry on interval; reports active `region` for multi-region failover |
| **Embedding pipeline** | Async job queue with `SELECT … FOR UPDATE SKIP LOCKED`; pluggable vector DB |
| **Consent cache GC** | Expires stale `consent_cache` entries; 300 s (allow/warn) / 60 s (deny) TTL |
| **Token TTL enforcer** | Cancels tasks whose `ttl_seconds` has elapsed |
| **SLA monitor** | Auto-escalates breached `approval_workflow` steps |
| **Reputation engine** | Scores agent safety from card scan history |
| **Dual-write worker** | Fans `token_audit_log` writes to remote regions for compliance |

---

## Domain Model

**71 ORM-mapped entities across 15 domains** — each unique, zero duplicates, all enforced with DB-level constraints:

| Domain | Tables |
|---|---|
| **Core A2A** | `agent_card`, `agent_capabilities`, `agent_skill`, `skill_schema`, `card_history` |
| **Task Lifecycle** | `task`, `session`, `message`, `message_part`, `artifact` |
| **Security** | `security_scheme`, `agent_token`, `push_notification_config`, `card_key_revocation_log`, `consent_proof_token` |
| **Registry & Discovery** | `registry_entry`, `heartbeat`, `agent_dependency` |
| **Access Control** | `access_policy`, `role_assignment`, `acl_entry`, `policy_cache`, `policy_cache_invalidation_event`, `policy_evaluation_log` |
| **Tracing** | `trace_span`, `trace_context` |
| **Token Hardening** | `token_family`, `token_audit_log`, `token_rate_limit`, `token_rate_limit_shard` |
| **Embedding** | `embedding_config`, `embedding_job`, `embedding_version`, `embedding_migration_plan` |
| **Consent & Governance** | `consent_record`, `governance_policy`, `approval_workflow`, `workflow_step`, `workflow_assignment`, `approver_delegation` |
| **Key Management** | `card_signing_key`, `card_signing_event` |
| **Execution Policy** | `executor_policy`, `trace_policy`, `consent_cache`, `trace_compliance_job`, `slo_definition`, `alert_rule`, `oncall_playbook`, `job_lease` |
| **FastAPI Bridge** | `route_mapping`, `fastapi_a2a_config_row`, `startup_audit_log`, `sdk_compatibility_matrix` |
| **Dynamic Capability** | `skill_query_log`, `nlp_analyzer_config` |
| **Federation & Crawler** | `federation_peer`, `crawler_source`, `crawler_job`, `crawler_import_permission`, `crawler_ownership_proof`, `crawler_takedown_request` |
| **Safety & Reputation** | `card_scan_result`, `sanitization_report`, `agent_reputation`, `synthetic_check`, `synthetic_check_result`, `schema_version`, `takedown_request`, `dual_write_queue` |

---

## Security

All constraints are enforced at the **PostgreSQL level** — bypassing application code is not possible:

| Constraint | What it enforces |
|---|---|
| `ck_agent_token_hash_format` | `token_hash ~ '^[a-f0-9]{64}$'` — SHA-256 hex only; plaintext never stored |
| `ck_agent_token_expiry` | `expires_at IS NULL OR expires_at > issued_at` |
| `ck_push_notification_webhook_https` | `webhook_url LIKE 'https://%'` — HTTP webhooks rejected at DB level |
| `ck_agent_card_semver` | `version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'` — malformed versions rejected |
| `uq_security_scheme_card_name` | `UNIQUE(agent_card_id, scheme_name)` — duplicate schemes blocked |
| `ck_consent_cache_result` | `result IN ('allow','warn','deny')` — no arbitrary values |
| `ck_consent_cache_ttl` | `expires_at > checked_at` — negative TTL impossible |
| `ck_trace_policy_sample_rate` | `trace_sample_rate BETWEEN 0.0 AND 1.0` |
| `ck_trace_policy_export_size` | `max_export_size_bytes BETWEEN 1024 AND 104857600` |
| `ck_token_rate_limit_positive` | `window_seconds > 0 AND max_requests > 0 AND max_burst > 0` |
| `ck_job_lease_ttl` | `lease_ttl_seconds BETWEEN 30 AND 86400` |
| `ck_approval_workflow_step_non_negative` | `current_step >= 0` |

**Additional security features:**
- **Token family revocation** — reuse attack detected → entire family revoked instantly
- **Signed agent cards** — JWS/JWKS with KMS key refs; fails with error `4010`
- **Execution isolation** — `executor_policy` circuit breaker with CPU/memory/timeout limits
- **Append-only audit logs** — `token_audit_log`, `startup_audit_log`, `card_signing_event`
- **Multi-region dual-write** — `data_region` column + worker for KMS-encrypted remote archiving
- **PII-safe tracing** — `trace_policy` redaction rules (JSONB regex), `attribute_allowlist`/`attribute_blocklist`, optional HMAC key hashing

### A2A Error Code Namespace

| Code | Meaning |
|---|---|
| `4010` | `card.signature_invalid` |
| `4020` | `consent.missing` |
| `4021` | `consent.expired` |
| `4022` | `consent.region_violation` |
| `4030` | `skill.timeout` |
| `4031` | `skill.memory_exceeded` |
| `4032` | `skill.circuit_open` |
| `4033` | `skill.not_found` |
| `4040` | `access.denied` |
| `4050` | `rate.window_exceeded` |
| `4051` | `rate.burst_exceeded` |
| `4060` | `governance.data_residency_violation` |
| `5001` | `platform.db_unavailable` |

---

## Project Structure

```
fastapi_a2a/
├── bridge/               # FastApiA2A class, config, route inspector, SDK compat
├── database.py           # Async engine + session factory (URL supplied by consumer)
└── domains/
    ├── core_a2a/         # AgentCard, AgentSkill, SkillSchema, JSON-RPC handler
    ├── task_lifecycle/   # Task, Session, Message, MessagePart, Artifact + REST API
    ├── security/         # SecurityScheme, AgentToken, PushNotificationConfig
    ├── registry/         # RegistryEntry, Heartbeat, AgentDependency
    ├── access_control/   # AccessPolicy, RoleAssignment, AclEntry, PolicyCache
    ├── tracing/          # TraceSpan, TraceContext (OTel-compatible)
    ├── token_hardening/  # TokenFamily, TokenAuditLog, RateLimit (dual-write)
    ├── embedding/        # EmbeddingConfig, EmbeddingJob, EmbeddingVersion
    ├── consent/          # ConsentRecord, GovernancePolicy, ApprovalWorkflow
    ├── key_management/   # CardSigningKey, CardSigningEvent (KMS-backed)
    ├── execution_policy/ # ExecutorPolicy, TracePolicy, ConsentCache, SLOs, Alerts
    ├── federation/       # FederationPeer, CrawlerJob, CrawlerImportPermission
    ├── dynamic_capability/ # SkillQueryLog, NlpAnalyzerConfig
    └── safety/           # CardScanResult, SanitizationReport, AgentReputation
```

---

## Requirements

- Python ≥ 3.11
- PostgreSQL with the `asyncpg` driver (`postgresql+asyncpg://...`)
- Redis *(optional — rate limiter falls back to in-memory if unreachable)*

---

## Development

```bash
git clone https://github.com/your-org/fastapi-a2a
cd fastapi-a2a

python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest tests/ -q          # → 66 passed
ruff check fastapi_a2a/   # → 0 errors
pyright fastapi_a2a/      # → 0 errors, 0 warnings
```

### Optional vector DB support

```bash
pip install "fastapi-a2a[vector-weaviate]"   # Weaviate
pip install "fastapi-a2a[vector-pinecone]"   # Pinecone
pip install "fastapi-a2a[vector-qdrant]"     # Qdrant
```

---

## Database Migrations

This is a **library, not an application** — it exports `Base` metadata but never runs migrations itself.

**Recommended — integrate with your Alembic setup:**

```python
# alembic/env.py
from fastapi_a2a.database import Base
target_metadata = Base.metadata
```

**Development only — create tables directly:**

```python
from fastapi_a2a.database import Base, engine
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
