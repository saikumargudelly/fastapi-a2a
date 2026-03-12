# fastapi-a2a

A seamless, zero-friction plugin that transforms any existing FastAPI application into a fully compliant Agent-to-Agent (A2A) node.

Instead of writing custom bridging code or setting up new infrastructure to let AI agents talk to each other, `fastapi-a2a` hooks directly into your application's existing routing. It exposes your regular HTTP endpoints as discoverable "skills" and allows your internal systems to natively communicate with external A2A nodes.

[![PyPI](https://img.shields.io/pypi/v/fastapi-a2a)](#)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-a2a)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why use this?

When you're building systems that involve multiple AI agents, you eventually run into a communication problem. How does Agent A (say, an NLP classifier) ask Agent B (a database RAG system) for context? 

The A2A protocol solves this by standardising discovery, capability negotiation, and task execution. This library implements that standard natively for FastAPI. It lets you do two things:

1. **Become an A2A Agent**: By adding a single decorator to your existing FastAPI routes, they are automatically published to an `/.well-known/agent.json` card. Other agents on the network can discover your application and invoke those routes via a standard JSON-RPC interface.
2. **Consume other Agents**: You can use the built-in `A2AClient` within your existing route handlers to pause your local execution, delegate a sub-task to a remote agent, wait for its completion, and resume seamlessly.

It requires zero changes to your actual business logic. Your existing frontend clients and webhooks will continue hitting the standard REST endpoints exactly as they did before.

## Installation

You can install the core library via pip. It depends strictly on `fastapi`, `pydantic`, and `httpx`.

```bash
pip install fastapi-a2a
```

If you plan to run multiple uvicorn workers and need tasks to persist across process boundaries, we include an optional Redis-backed store:

```bash
pip install "fastapi-a2a[redis]"
```

## Quick Start

### 1. Expose your application

Imagine you have a straightforward summarisation endpoint. To make it discoverable on the A2A network, just add the `@a2a_skill` decorator and mount the plugin at the bottom of your file.

```python
from fastapi import FastAPI
from fastapi_a2a import setup_fastapi_a2a, a2a_skill

app = FastAPI()

# Your existing, unchanged business logic
@app.post("/summarise")
@a2a_skill(description="Summarises long-form text into key bullet points.", tags=["nlp"])
async def summarise(req: dict) -> dict:
    return {"summary": "This is a summary of the text..."}

# The integration
a2a = setup_fastapi_a2a(app, name="My NLP Tools", url="https://nlp.example.com")
```

By calling `a2a.mount()`, the library automatically inspects your app, finds the decorated route, generates the standardised `AgentCard`, and opens up a `/a2a/rpc` endpoint to handle incoming agent requests.

### 2. Communicate with other agents

If your application needs to delegate work to an external system, you don't need to manually poll HTTP endpoints. The `A2AClient` handles the lifecycle for you.

```python
from fastapi_a2a import create_a2a_client

@app.post("/pipeline")
async def translation_pipeline(req: dict) -> dict:
    # 1. Do some local processing first
    local_summary = await do_local_work(req["document"])

    # 2. Delegate the translation step to an external specialised agent
    async with create_a2a_client("https://translation-agent.example.com") as client:
        # This will dispatch the payload and return the Task ID immediately
        task = await client.send_task(
            text=local_summary,
            skill_id="translate",
            data={"target_lang": "es"}
        )
        
        # 3. Explicitly poll the remote store until completion
        task = await client.poll_task_status(task["id"])
        
        # Extract the final output from the remote agent's artifacts
        translation = task["artifacts"][0]["parts"][0]["data"]["text"]

    return {"final_translation": translation}
```

## Production Deployment

If you are deploying `fastapi-a2a` via Gunicorn, Uvicorn workers, or Kubernetes Pods, you **must not** use the default `InMemoryTaskStore`. Memory stores do not sync state across processes—a task started on Worker A cannot be polled or completed by Worker B.

For production, install the Redis extension and pass it during mounting:

```bash
pip install "fastapi-a2a[redis]"
```

```python
import redis.asyncio as redis
from fastapi_a2a.stores.redis import RedisTaskStore
from fastapi_a2a import setup_fastapi_a2a

redis_client = redis.from_url("redis://localhost:6379")
store = RedisTaskStore(redis_client)

a2a = setup_fastapi_a2a(app, name="Agent", url="...", store=store)
```

## Architecture

The plugin is designed to be highly modular and defensive:

- **Type Safety**: The entire A2A schema (version 0.3.0) is modelled using strict, zero-overhead `TypedDict` implementations validated by Pydantic's `TypeAdapter`.
- **Stateless by Default**: Out of the box, it uses an `InMemoryTaskStore` with built-in TTL eviction, making it perfect for single-instance deployments or quick local development.
- **Pluggable Persistence**: For production workloads, you can easily swap the storage backend by implementing the `TaskStore` abstract base class (e.g., using PostgreSQL or the provided Redis adapter).
- **In-process Bridging**: When an external agent invokes your skill via the RPC endpoint, the request is bridged to your FastAPI route directly through the ASGI protocol. It does not make a network loopback call.

## Development

If you'd like to contribute, we use `uv` for dependency management.

```bash
uv sync --group dev
uv run pytest tests/ -v
uv run ruff check fastapi_a2a
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.
