# fastapi-a2a

**fastapi-a2a** turns any existing FastAPI application into a fully compliant [A2A Protocol](https://google.github.io/A2A) agent — no boilerplate, no rewrites. Drop it in, and your app instantly gets an agent card, task management, secure token issuance, discovery registration, distributed tracing, consent enforcement, and much more.

---

## What problem does it solve?

The Agent-to-Agent (A2A) Protocol is Google's open standard for agents communicating with each other. Implementing it from scratch means wiring up agent card endpoints, task state machines, JWT security, registry heartbeats, rate limiting, consent enforcement, and more — easily weeks of work.

**fastapi-a2a** does all of that for you. You keep writing FastAPI routes the way you always have. The library introspects them and exposes a compliant A2A interface automatically.

---

## Quick Start

```bash
pip install fastapi-a2a
```

```python
from fastapi import FastAPI
from fastapi_a2a import FastApiA2A

app = FastAPI(title="My Agent")

a2a = FastApiA2A(
    app,
    name="My Agent",
    description="What this agent does",
    url="https://my-agent.example.com",
    version="1.0.0",
    database_url="postgresql+asyncpg://user:pass@host/mydb",
)
# Your agent card is now at /.well-known/agent.json
```

The library manages its own SQLAlchemy ORM models internally. All required tables are available via the shared `Base` metadata — integrate them into your existing Alembic migrations or use `Base.metadata.create_all()` in development.

No hardcoded database URLs, IP addresses, or application-level bootstrap code exists inside the library. Every configuration value is supplied by the consumer at construction time or via environment variables.

---

## Configuration

All settings are passed as constructor parameters or via `A2A_`-prefixed environment variables:

```python
from fastapi_a2a import FastApiA2A

a2a = FastApiA2A(
    app,
    name="My Agent",
    description="...",
    url="https://my-agent.example.com",
    version="1.0.0",                        # SemVer enforced
    database_url="postgresql+asyncpg://...", # or set A2A_DATABASE_URL
    icon_url="https://my-agent.example.com/icon.png",  # optional
    min_sdk_version="1.0.0",                # optional
    input_modes=["text/plain"],             # optional; default: []
    output_modes=["text/plain"],            # optional; default: []
    supports_auth_schemes=["bearer"],       # optional; default: []
)
```

Or via environment:

```bash
A2A_DATABASE_URL=postgresql+asyncpg://user:pass@host/mydb
A2A_REQUIRE_SIGNED_CARDS=true
A2A_DISCOVERY_MODE=auto    # auto | explicit | disabled
```

---

## Auto-mounted Endpoints

Once you wrap your app with `FastApiA2A`, these endpoints are automatically mounted:

| Endpoint | Description |
|---|---|
| `GET /.well-known/agent.json` | A2A agent card (public skills only) |
| `GET /.well-known/agent-extended.json` | Extended card with quarantine/approval status |
| `POST /rpc` | A2A JSON-RPC: `tasks/send`, `tasks/get`, `tasks/cancel`, `tasks/sendSubscribe` |
| `GET /tasks` | List all tasks with status |
| `GET /tasks/{id}` | Task detail — messages, artifacts, push config |
| `POST /tasks/{id}/cancel` | Cancel a running task |
| `GET /registry/agents` | Discover nearby A2A-compatible agents |
| `POST /registry/agents` | Self-register in the discovery index |
| `GET /admin/keys` | JWKS public key endpoint |
| `POST /admin/keys/rotate` | Rotate card signing key (KMS-backed) |
| `GET /consent/policies` | View data-use consent records for this agent |
| `GET /rpc/health` | Liveness probe — `{"status":"ok","protocol":"A2A/1.0"}` |

### Background services (run automatically)
- **Heartbeat scheduler** — pings the registry every `heartbeat_interval_seconds` to stay discoverable; reports active region for multi-region failover
- **Embedding pipeline** — async job queue; workers use `SELECT … FOR UPDATE SKIP LOCKED`; vectors stored in external vector DB (Weaviate, Pinecone, FAISS)
- **Token TTL enforcer** — cancels tasks where `(NOW() - created_at) > ttl_seconds` every 60 s
- **Consent cache GC** — deletes expired `consent_cache` entries every 60 s
- **SLA monitor** — detects breached `approval_workflow` SLAs and triggers escalation
- **Reputation engine** — scores agent safety from scan history

---

## Domain Model

The library implements the full A2A v0.6.0 spec — **72 ORM-mapped entities across 15 domains** (all unique, zero duplicates):

| Domain | Key Entities |
|---|---|
| **Core A2A** | `agent_card`, `agent_capabilities`, `agent_skill`, `skill_schema`, `card_history` |
| **Task Lifecycle** | `task`, `session`, `message`, `message_part`, `artifact` |
| **Security** | `security_scheme`, `agent_token`, `push_notification_config`, `card_key_revocation_log`, `consent_proof_token` |
| **Registry & Discovery** | `registry_entry`, `heartbeat`, `agent_dependency` |
| **Access Control** | `access_policy`, `role_assignment`, `acl_entry`, `policy_cache`, `policy_cache_invalidation_event`, `policy_evaluation_log` |
| **Distributed Tracing** | `trace_span`, `trace_context` |
| **Token Hardening** | `token_family`, `token_audit_log`, `token_rate_limit`, `token_rate_limit_shard` |
| **Embedding Pipeline** | `embedding_config`, `embedding_job`, `embedding_version`, `embedding_migration_plan` |
| **Consent & Governance** | `consent_record`, `governance_policy`, `approval_workflow`, `workflow_step`, `workflow_assignment`, `approver_delegation` |
| **Key Management** | `card_signing_key`, `card_signing_event` |
| **Execution Policy** | `executor_policy`, `trace_policy`, `consent_cache`, `trace_compliance_job`, `slo_definition`, `alert_rule`, `oncall_playbook`, `job_lease` |
| **FastAPI Bridge** | `route_mapping`, `fastapi_a2a_config_row`, `startup_audit_log`, `sdk_compatibility_matrix` |
| **Dynamic Capability** | `skill_query_log`, `nlp_analyzer_config` |
| **Federation & Crawler** | `federation_peer`, `crawler_source`, `crawler_job`, `crawler_import_permission`, `crawler_ownership_proof`, `crawler_takedown_request` |
| **Safety & Reputation** | `card_scan_result`, `sanitization_report`, `agent_reputation`, `synthetic_check`, `synthetic_check_result`, `schema_version`, `takedown_request`, `dual_write_queue` |

---

## Security

- **SHA-256 token hashing** — tokens stored only as SHA-256 hex hash (`ck_agent_token_hash_format` enforces `^[a-f0-9]{64}$`); plaintext never persists
- **Token families + replay detection** — refresh token rotation with full reuse-attack detection; family compromise immediately revokes all sibling tokens
- **Sliding-window rate limiting** — per-token `burst_count`/`max_burst` (micro-window) + `request_count`/`max_requests` (rolling window); returns HTTP 429
- **HTTPS enforcement** — `push_notification_config.webhook_url` has DB-level `CHECK(webhook_url LIKE 'https://%')` constraint
- **SemVer validation** — `agent_card.version` has DB-level `CHECK(version ~ '^[0-9]+\.[0-9]+\.[0-9]+$')` constraint
- **UNIQUE auth scheme per agent** — `UNIQUE(agent_card_id, scheme_name)` on `security_scheme` prevents duplicate scheme registration
- **GDPR consent enforcement** — `consent_cache` with TTL (300 s allow/warn, 60 s deny); runtime `consent_service.check()` blocks unapproved skill invocations
- **PII-safe tracing** — `trace_policy.redaction_rules` (JSONB regex patterns), `attribute_allowlist`/`attribute_blocklist`, HMAC identifier hashing; applied at INSERT time before storage
- **Signed agent cards** — optional JWS/JWKS-backed card signing with KMS key references; verification failure returns A2A error `4010`
- **Execution isolation** — `executor_policy` circuit breaker; CPU/memory/timeout limits via cgroups/RLIMIT with automatic SIGKILL on breach
- **Immutable audit logs** — `token_audit_log`, `startup_audit_log`, `card_signing_event` are append-only; no UPDATE/DELETE permitted
- **Multi-region dual-write** — `token_audit_log` and `consent_record` support `data_region` for jurisdiction-aligned storage and KMS-encrypted remote archive

### A2A Error Code Namespace

| Range | Domain | Example |
|---|---|---|
| 4000–4009 | `card.*` | `4010` card.signature_invalid |
| 4020–4022 | `consent.*` | `4020` consent.missing |
| 4030–4033 | `skill.*` | `4030` skill.timeout, `4032` skill.circuit_open |
| 4040–4041 | `access.*` | `4040` access.denied |
| 4050–4051 | `rate.*` | `4050` rate.window_exceeded |
| 4060–4061 | `governance.*` | `4060` governance.data_residency_violation |
| 5000–5002 | `platform.*` | `5001` platform.db_unavailable |

---

## Project Structure

```
fastapi_a2a/
├── bridge/               # FastApiA2A class, config, route inspector, SDK compat
├── database.py           # SQLAlchemy async engine + session factory (consumer-provided URL)
└── domains/
    ├── core_a2a/         # AgentCard, AgentSkill, SkillSchema, JSON-RPC handler
    ├── task_lifecycle/   # Task, Session, Message, MessagePart, Artifact + REST API
    ├── security/         # SecurityScheme, AgentToken, PushNotificationConfig
    ├── registry/         # RegistryEntry, Heartbeat, AgentDependency
    ├── access_control/   # AccessPolicy, RoleAssignment, AclEntry, PolicyCache
    ├── tracing/          # TraceSpan, TraceContext (OTel-compatible)
    ├── token_hardening/  # TokenFamily, TokenAuditLog, TokenRateLimit (dual-write)
    ├── embedding/        # EmbeddingConfig, EmbeddingJob, EmbeddingVersion
    ├── consent/          # ConsentRecord, GovernancePolicy, ApprovalWorkflow + sub-tables
    ├── key_management/   # CardSigningKey, CardSigningEvent (KMS-backed)
    ├── execution_policy/ # ExecutorPolicy, TracePolicy, ConsentCache, SLOs, AlertRules
    ├── federation/       # FederationPeer, CrawlerJob, CrawlerImportPermission
    ├── dynamic_capability/ # SkillQueryLog, NlpAnalyzerConfig
    └── safety/           # CardScanResult, SanitizationReport, AgentReputation
```

---

## Requirements

- Python 3.11+ (tested on 3.11, 3.12, 3.14)
- PostgreSQL with the `asyncpg` driver (`postgresql+asyncpg://...`)
- Redis (optional — rate limiter falls back to in-memory if Redis isn't reachable)

---

## Development

```bash
# Clone
git clone https://github.com/your-org/fastapi-a2a
cd fastapi-a2a

# Install with dev extras
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -q            # → 66 passed

# Lint (zero errors expected)
ruff check fastapi_a2a/
pyright fastapi_a2a/
```

---

## Database Migrations

`fastapi-a2a` is a **library, not an application**. It exports SQLAlchemy `Base` metadata but does not create tables itself.

**Recommended integration:**

```python
from fastapi_a2a.database import Base

# In your Alembic env.py — import Base and let Alembic auto-generate migrations:
target_metadata = Base.metadata
```

Or in development:
```python
from fastapi_a2a.database import Base, engine
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
