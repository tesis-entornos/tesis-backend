"""Fail-open JSON cache on Redis (cache-aside).

Keys are grouped in namespaces. Every namespace has an *epoch* counter that is
part of each key: ``{prefix}:{namespace}:e{epoch}:{parts...}``. Invalidating a
namespace increments its epoch, so all its previous keys become unreachable at
once and expire by TTL. This avoids pattern deletes and is safe against
concurrent readers that store values computed before a write.

Any Redis error degrades to ``CacheStatus.BYPASS``: the request is served from
the database and the error is logged.
"""

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

ITEMS_NAMESPACE = "items"

# Short timeouts: an unavailable cache must not slow requests down noticeably
_SOCKET_TIMEOUT_SECONDS = 0.5


class CacheStatus(StrEnum):
    HIT = "HIT"
    MISS = "MISS"
    BYPASS = "BYPASS"


@dataclass(frozen=True)
class CacheLookup:
    status: CacheStatus
    value: Any = None
    # Key where the value must be stored after a MISS
    key: str | None = None


class Cache:
    def __init__(
        self, client: redis.Redis | None, *, prefix: str, ttl_seconds: int
    ) -> None:
        self._client = client
        self._prefix = prefix
        self._ttl_seconds = ttl_seconds

    @classmethod
    def from_url(cls, url: str | None, *, prefix: str, ttl_seconds: int) -> Cache:
        client = (
            redis.Redis.from_url(
                url,
                socket_timeout=_SOCKET_TIMEOUT_SECONDS,
                socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
            )
            if url
            else None
        )
        return cls(client, prefix=prefix, ttl_seconds=ttl_seconds)

    def _epoch_key(self, namespace: str) -> str:
        return f"{self._prefix}:{namespace}:epoch"

    def lookup(self, namespace: str, *parts: str) -> CacheLookup:
        if self._client is None:
            return CacheLookup(CacheStatus.BYPASS)
        try:
            epoch = int(self._client.get(self._epoch_key(namespace)) or 0)
            key = ":".join([self._prefix, namespace, f"e{epoch}", *parts])
            raw = self._client.get(key)
        except redis.RedisError:
            logger.warning("Cache unavailable, bypassing", exc_info=True)
            return CacheLookup(CacheStatus.BYPASS)
        if raw is None:
            return CacheLookup(CacheStatus.MISS, key=key)
        return CacheLookup(CacheStatus.HIT, value=json.loads(raw))

    def store(self, lookup: CacheLookup, value: Any) -> None:
        if self._client is None or lookup.status is not CacheStatus.MISS:
            return
        assert lookup.key is not None
        try:
            self._client.set(lookup.key, json.dumps(value), ex=self._ttl_seconds)
        except redis.RedisError:
            logger.warning("Cache unavailable, value not stored", exc_info=True)

    def invalidate(self, namespace: str) -> None:
        if self._client is None:
            return
        try:
            self._client.incr(self._epoch_key(namespace))
        except redis.RedisError:
            # Stale entries remain readable until their TTL expires
            logger.error("Cache invalidation failed for %s", namespace, exc_info=True)


cache = Cache.from_url(
    str(settings.REDIS_URL) if settings.REDIS_URL else None,
    # Only None when REDIS_URL is not set (enforced by Settings)
    prefix=settings.CACHE_KEY_PREFIX or "",
    ttl_seconds=settings.CACHE_TTL_SECONDS,
)
