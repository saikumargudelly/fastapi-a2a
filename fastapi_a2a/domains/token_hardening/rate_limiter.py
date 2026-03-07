"""
Redis LUA Rate Limiter — Two-tier hot-path implementation (§19.6).

Tier 1: Redis INCR with EXPIRE — sub-millisecond O(1) per request
Tier 2: PostgreSQL fallback when Redis is unavailable

The LUA script is atomic on the Redis server:
  1. INCR the counter key
  2. If new key: SET EXPIRE = window_seconds
  3. Return (count, window_remaining_ms)

Shard-aware: request count spread across N shards (§17.3.3) to avoid
Redis hot-key on high-traffic agents.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import NamedTuple

logger = logging.getLogger("fastapi_a2a.rate_limiter")

# LUA script for atomic INCR + conditional EXPIRE
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
end
local ttl = redis.call('TTL', key)
return {current, ttl, limit}
"""


class RateLimitResult(NamedTuple):
    allowed: bool
    current_count: int
    limit: int
    window_seconds: int
    remaining: int
    retry_after_seconds: int


class RedisRateLimiter:
    """
    Sliding-window rate limiter using Redis INCR + LUA.
    Falls back to in-memory counter when Redis is unavailable.

    Usage:
        limiter = RedisRateLimiter(redis_url="redis://localhost:6379", shards=8)
        result = await limiter.check(key="agent_id:caller_id", limit=100, window=60)
        if not result.allowed:
            raise HTTPException(429, headers={"Retry-After": str(result.retry_after_seconds)})
    """

    def __init__(
        self,
        redis_url: str | None = None,
        shards: int = 8,
        key_prefix: str = "a2a:rl",
    ):
        self._redis_url = redis_url
        self._shards = shards
        self._key_prefix = key_prefix
        self._redis = None
        self._lua_sha: str | None = None
        self._fallback: dict[str, list[float]] = {}  # In-memory fallback

    async def _get_redis(self):
        """Lazily initialize Redis connection."""
        if self._redis is not None:
            return self._redis
        if not self._redis_url:
            return None
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            # Load LUA script
            self._lua_sha = await self._redis.script_load(_RATE_LIMIT_LUA)
            logger.debug("Redis rate limiter connected, LUA SHA=%s", self._lua_sha)
            return self._redis
        except ImportError:
            logger.warning("redis[hiredis] not installed — using in-memory fallback")
            return None
        except Exception as exc:
            logger.warning("Redis connection failed: %s — using fallback", exc)
            return None

    def _shard_key(self, key: str, window: int) -> str:
        """Select a shard key using consistent hashing."""
        shard_idx = int(hashlib.md5(key.encode()).hexdigest(), 16) % self._shards  # noqa: S324
        window_bucket = int(time.time()) // window
        return f"{self._key_prefix}:{key}:{window_bucket}:s{shard_idx}"

    async def check(
        self,
        key: str,
        limit: int,
        window: int = 60,
    ) -> RateLimitResult:
        """
        Check rate limit. Returns RateLimitResult.
        key: unique identifier (e.g. "agent_id:caller_identity")
        limit: max requests per window
        window: window size in seconds
        """
        # Adjust limit per shard
        shard_limit = max(1, limit // self._shards)
        redis = await self._get_redis()
        redis_key = self._shard_key(key, window)

        if redis:
            try:
                if self._lua_sha is None:
                    raise RuntimeError("LUA script not loaded")
                result = await redis.evalsha(  # type: ignore[misc]
                    self._lua_sha, 1, redis_key, window, shard_limit
                )
                count, ttl, lim = int(result[0]), int(result[1]), int(result[2])
                allowed = count <= lim
                remaining = max(0, lim - count)
                return RateLimitResult(
                    allowed=allowed,
                    current_count=count,
                    limit=lim,
                    window_seconds=window,
                    remaining=remaining,
                    retry_after_seconds=max(0, ttl) if not allowed else 0,
                )
            except Exception as exc:
                logger.warning("Redis rate limit check failed: %s — falling back", exc)

        # In-memory sliding-window fallback
        return self._fallback_check(key, limit, window)

    def _fallback_check(self, key: str, limit: int, window: int) -> RateLimitResult:
        """Simple sliding-window fallback using local memory."""
        now = time.time()
        cutoff = now - window
        hits = self._fallback.get(key, [])
        hits = [t for t in hits if t > cutoff]
        hits.append(now)
        self._fallback[key] = hits[-limit * 2:]  # Cap list size
        count = len(hits)
        allowed = count <= limit
        return RateLimitResult(
            allowed=allowed,
            current_count=count,
            limit=limit,
            window_seconds=window,
            remaining=max(0, limit - count),
            retry_after_seconds=window if not allowed else 0,
        )

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None


# Module-level singleton — initialized by FastApiA2A.startup()
_default_limiter: RedisRateLimiter | None = None


def get_rate_limiter() -> RedisRateLimiter:
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RedisRateLimiter()
    return _default_limiter


def configure_rate_limiter(redis_url: str | None, shards: int = 8) -> RedisRateLimiter:
    global _default_limiter
    _default_limiter = RedisRateLimiter(redis_url=redis_url, shards=shards)
    return _default_limiter
