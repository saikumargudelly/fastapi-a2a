# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2024-03-08

### Added
- `FastApiA2A` plugin — attach A2A protocol to any existing FastAPI app in 3 lines
- `@a2a_skill()` decorator — mark existing routes as A2A skills (opt-in, zero behaviour change)
- `RouteScanner` — auto-discovers `@a2a_skill`-decorated routes and builds `AgentSkill` list
- `AgentCardBuilder` — produces and caches the `/.well-known/agent.json` discovery endpoint
- `RequestExecutor` — routes incoming A2A tasks to existing FastAPI endpoints via ASGI (zero network hop)
- `TaskManager` — full JSON-RPC dispatch: `message/send`, `tasks/get`, `tasks/cancel`, `message/stream`, `tasks/resubscribe`
- `InMemoryTaskStore` — default task store with TTL eviction and per-task asyncio locks
- `RedisTaskStore` — distributed task store (optional `[redis]` extra)
- `A2AClient` — outbound async client for calling any A2A-compliant agent; supports `send_task()`, `stream_task()`, `get_card()`, `get_task()`, `cancel_task()`
- `RequestContext` — read-only context object injected into route calls for A2A metadata access
- `get_a2a_context` / `get_task_store` FastAPI dependency helpers
- Full A2A v0.3.0 schema: `Task`, `Message`, `Artifact`, `Part` (Text/File/Data), `AgentCard`, JSON-RPC envelope
- SSE streaming support for `message/stream` and `tasks/resubscribe`
- A2A-Version header validation middleware

[Unreleased]: https://github.com/yourorg/fastapi-a2a/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yourorg/fastapi-a2a/releases/tag/v0.1.0
