# fastapi-a2a

[![PyPI](https://img.shields.io/badge/pypi-v1.0.1-blue?logo=pypi&logoColor=white)](https://pypi.org/project/fastapi-a2a/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue?logo=python&logoColor=white)](https://pypi.org/project/fastapi-a2a/)
[![A2A Protocol](https://img.shields.io/badge/A2A-1.0.0-blue.svg)](https://github.com/saikumargudelly/fastapi-a2a)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**fastapi-a2a** turns any existing [FastAPI](https://fastapi.tiangolo.com/) application into a standards-compliant **Agent-to-Agent (A2A)** node.

Instead of writing custom RPC dispatchers, message brokers, or separate microservices to connect AI agents, `fastapi-a2a` introspects your FastAPI route topology and exposes your endpoints as discoverable A2A skills through an ASGI-native dispatch pipeline—with zero network loopback overhead.

---

## Key Features

- **A2A 1.0.0 Compliant**: Implements the official Agent-to-Agent specification. Serves the canonical `/.well-known/agent-card.json` manifest (with backward compatibility for `/.well-known/agent.json`) and advertises active `supportedInterfaces` (JSON-RPC 2.0).
- **Zero-Hop ASGI Execution**: Inbound agent task invocations bypass external HTTP sockets and are routed directly to FastAPI endpoints through ASGI memory channels.
- **Non-Destructive & Opt-In**: Existing REST/HTTP endpoints, Swagger docs, webhooks, and middlewares remain 100% intact and unchanged.
- **Flexible Skill Discovery**: Expose skills via the passive `@a2a_skill` decorator or native FastAPI `openapi_extra={"a2a": {...}}`. Fully supports root routes, nested `APIRouter`s, and mounted sub-applications.
- **Production Persistence**: Ships with an in-memory task store with TTL eviction for single-instance deployments and a robust `RedisTaskStore` for distributed worker clusters.
- **Full Task Lifecycle & Safety**: Handles asynchronous task queuing, status polling, timeout enforcement, bounded capacity limits, and cooperative task cancellation.
- **Outbound Agent Client**: Built-in async `A2AClient` with automatic remote Agent Card discovery, dynamic interface routing, and SSRF protection.

---

## Installation

> **Supported Python**: `Python >= 3.11` (fully tested across `3.11`, `3.12`, `3.13`)  
> **PyPI Package**: [`fastapi-a2a` v1.0.1](https://pypi.org/project/fastapi-a2a/)

Install using `pip` or `uv`:

```bash
# Core package (FastAPI, Pydantic, HTTPX)
pip install fastapi-a2a

# With Redis task store support for multi-worker deployments
pip install "fastapi-a2a[redis]"
```

Using `uv`:

```bash
uv add fastapi-a2a
# Or with Redis:
uv add "fastapi-a2a[redis]"
```

---

## Quickstart

### 1. Exposing Skills (Agent Node)

Add the `@a2a_skill` decorator to any route and initialize `fastapi-a2a` with `setup_fastapi_a2a`:

```python
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi_a2a import a2a_skill, setup_fastapi_a2a

app = FastAPI(title="NLP Agent")


class SummarizeRequest(BaseModel):
    document: str
    max_bullets: int = 5


class SummarizeResponse(BaseModel):
    summary: list[str]


# Your standard, unchanged FastAPI endpoint
@app.post("/summarize", response_model=SummarizeResponse)
@a2a_skill(
    name="Summarization",
    description="Summarizes text into key bullet points.",
    tags=["nlp", "summarization"],
    examples=["Summarize quarterly earnings report."],
)
async def summarize(payload: SummarizeRequest) -> SummarizeResponse:
    # Your regular business logic
    bullets = [f"Key point from: {payload.document[:30]}..."]
    return SummarizeResponse(summary=bullets)


# Automatically inspects routes, builds Agent Card, and mounts /a2a/rpc
setup_fastapi_a2a(
    app,
    name="Summarizer Node",
    url="https://agent.example.com",
    version="1.0.0",
    description="Agent capable of summarizing text documents.",
)
```

Once mounted, the application automatically serves:
- `GET /.well-known/agent-card.json` (A2A 1.0.0 canonical discovery)
- `GET /.well-known/agent.json` (Backward-compatible discovery)
- `POST /a2a/rpc` (JSON-RPC 2.0 task dispatcher)

#### Alternative: Native `openapi_extra` Declaration

You can also expose skills without importing `@a2a_skill` by declaring metadata directly in `openapi_extra`:

```python
@app.post(
    "/analyze",
    openapi_extra={
        "a2a": {
            "name": "Text Analysis",
            "description": "Performs sentiment and entity extraction.",
            "tags": ["analysis"],
        }
    },
)
async def analyze(data: dict) -> dict:
    return {"sentiment": "positive"}
```

---

### 2. Invoking Remote Agents (Outbound Client)

Use `A2AClient` within your existing route handlers or background jobs to delegate tasks to external A2A agents:

```python
from fastapi_a2a import create_a2a_client


async def run_remote_task() -> dict:
    async with create_a2a_client("https://translation.example.com") as client:
        # 1. Fetch remote agent capabilities (cached automatically)
        card = await client.get_card()
        print(f"Connected to {card['name']} (version {card['version']})")

        # 2. Dispatch task via message/send (returns Task in 'submitted' state)
        task = await client.send_task(
            text="Hello world",
            skill_id="translate",
            data={"target_lang": "fr"},
        )

        # 3. Explicitly poll task status until terminal state
        completed_task = await client.poll_task_status(task["id"])

        # 4. Extract result artifacts
        return completed_task["artifacts"][0]["parts"][0]["data"]
```

---

## Production Deployment

### Multi-Worker / Clustered Setups

When running multiple Uvicorn/Gunicorn workers or orchestrating across Kubernetes pods, in-memory task state cannot be shared between processes. Use `RedisTaskStore` to share task queues and statuses across all nodes:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import redis.asyncio as aioredis
from fastapi_a2a import setup_fastapi_a2a
from fastapi_a2a.stores.redis import RedisTaskStore

redis_client = aioredis.from_url("redis://localhost:6379", decode_responses=False)
store = RedisTaskStore(redis_client, ttl_seconds=86400)

app = FastAPI()

a2a = setup_fastapi_a2a(
    app,
    name="Production Agent",
    url="https://api.example.com",
    store=store,
    max_concurrency=200,
    max_queue_size=1000,
    timeout_seconds=60.0,
)
```

---

## Protocol Specification & Architecture

### Inbound JSON-RPC Methods

`fastapi-a2a` exposes the standard JSON-RPC 2.0 interface at `POST /a2a/rpc`:

| Method | Parameters | Description |
| :--- | :--- | :--- |
| `message/send` | `{"message": Message}` | Submits a new task for asynchronous execution. Returns `Task` in `submitted` state. |
| `tasks/get` | `{"id": str}` | Retrieves the current state, progress, and output artifacts for a task. |
| `tasks/cancel` | `{"id": str}` | Cooperatively cancels an active or queued task. |
| `tasks/list` | `{"limit": int?, "cursor": str?}` | Lists tasks tracked by the store. |

### Task States & Transitions

```
[submitted] ──> [working] ──> [completed]
     │              │
     ├──> [canceled] <──┘ (tasks/cancel)
     │
     └──> [failed] (on exception or timeout)
```

- **Terminal States**: `completed`, `failed`, `canceled`. Once a task enters a terminal state, its status is immutable.
- **Resource Cleanup**: Concurrency permits and task references are cleanly reclaimed upon terminal transitions or cancellation.

---

## Configuration Reference

`setup_fastapi_a2a` and `FastApiA2A` accept the following configuration parameters:

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | *Required* | Agent display name for the Agent Card manifest. |
| `url` | `str` | *Required* | Public base URL of the agent node. |
| `version` | `str` | `"1.0.0"` | Agent version string. |
| `description` | `str?` | `None` | Detailed overview of agent capabilities. |
| `store` | `TaskStore?` | `InMemoryTaskStore` | Task storage backend (`InMemoryTaskStore` or `RedisTaskStore`). |
| `max_concurrency`| `int` | `100` | Maximum simultaneous active task executions. |
| `max_queue_size` | `int?` | `None` | Max backlog capacity before rejecting requests with `-32001 (Server Overloaded)`. |
| `timeout_seconds`| `float` | `120.0` | Maximum execution time before canceling a task with `failed` status. |
| `prefix` | `str` | `"/a2a"` | Base path prefix for RPC endpoints (e.g. `/a2a/rpc`). |

---

## Development & Testing

This project uses [`uv`](https://docs.astral.sh/uv/) for high-speed dependency resolution, linting, and testing:

```bash
# Install dependencies
uv sync --group dev

# Run lint and formatting checks
uv run ruff check .
uv run ruff format --check .

# Run static type checking
uv run pyright

# Run test suite with coverage
uv run pytest --cov=fastapi_a2a --cov-report=term-missing

# Run critical stress & overload tests
uv run pytest tests_critical/test_stress.py -v

# Build wheels & sdist
uv build
```

---

## License

This project is licensed under the terms of the [MIT License](LICENSE).
