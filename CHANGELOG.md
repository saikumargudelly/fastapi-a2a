# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] — 2026-10-29

### Fixed
- Fixed route discovery in `FastApiAdapter.scan()` to unpack `original_route` from `iter_route_contexts` correctly, traverse mounted sub-applications recursively, and discover `openapi_extra` declarations.
- Fixed task store lifecycle management (`start()` / `stop()`) by mounting endpoints with Starlette router lifespans and guarded fallback for legacy custom adapters.
- Fixed `RedisTaskStore` by implementing the missing `create()`, `start()`, `stop()`, and `aclose()` methods required by the `TaskStore` abstract interface.
- Fixed task queue capacity and overload handling in `TaskManager` by replacing the dynamic semaphore permit threshold with a stable `max_queue_size` capacity bound.
- Fixed task cancellation propagation in `TaskManager` to cancel underlying `asyncio.Task` coroutines, release concurrency resources upon termination, and prevent completed/failed status transitions from overwriting terminal `canceled` states.

### Added
- Aligned Agent Card discovery with official A2A 1.0.0 specification:
  - Added `supportedInterfaces` specifying protocol bindings (`JSONRPC`), protocol versions (`1.0`), and interface URLs.
  - Added canonical discovery endpoint `/.well-known/agent-card.json` while maintaining backward-compatible `/.well-known/agent.json`.
  - Added strict JSON Schema validation test suite against official A2A 1.0.0 AgentCard schema.
- Top-level public exports for `FastApiA2A`, `A2AClient`, `TaskStore`, and `InMemoryTaskStore` in `fastapi_a2a`.
- Regression test suites for cancellation, concurrency, lifecycle, quickstart, Redis store, route discovery, and critical stress scenarios.

### Changed
- Explicitly documented and guarded unsupported capabilities (`streaming=True` and `push_notifications=True` raise `NotImplementedError`).
- Updated repository links to `https://github.com/saikumargudelly/fastapi-a2a`.

## [1.0.0] — 2026-03-08

### Added
- `FastApiA2A` plugin — attach A2A protocol to any existing FastAPI app in 3 lines
- `@a2a_skill()` decorator — mark existing routes as A2A skills (opt-in, zero behaviour change)
- `RouteScanner` — auto-discovers `@a2a_skill`-decorated routes and builds `AgentSkill` list
- `AgentCardBuilder` — produces and caches the `/.well-known/agent.json` discovery endpoint
- `RequestExecutor` — routes incoming A2A tasks to existing FastAPI endpoints via ASGI (zero network hop)
- `TaskManager` — full JSON-RPC dispatch: `message/send`, `tasks/get`, `tasks/cancel`, `tasks/list`
- `InMemoryTaskStore` — default task store with TTL eviction and per-task asyncio locks
- `RedisTaskStore` — distributed task store (optional `[redis]` extra)
- `A2AClient` — outbound async client for calling any A2A-compliant agent; supports `send_task()`, `get_card()`, `get_task()`, `cancel_task()`
- `RequestContext` — read-only context object injected into route calls for A2A metadata access
- `get_a2a_context` / `get_task_store` FastAPI dependency helpers
- Full A2A v0.3.0 schema: `Task`, `Message`, `Artifact`, `Part` (Text/File/Data), `AgentCard`, JSON-RPC envelope
- A2A-Version header validation middleware

## [0.1.0] — 2024-03-08

### Added
- Initial proof of concept release.

[Unreleased]: https://github.com/saikumargudelly/fastapi-a2a/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/saikumargudelly/fastapi-a2a/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/saikumargudelly/fastapi-a2a/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/saikumargudelly/fastapi-a2a/releases/tag/v0.1.0
