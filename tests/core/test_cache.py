import redis

from app.core.cache import Cache, CacheStatus
from tests.utils.utils import random_lower_string


def unreachable_cache() -> Cache:
    # Port 1 is never a Redis server: every call fails fast
    return Cache.from_url("redis://127.0.0.1:1/0", prefix="test", ttl_seconds=60)


def test_cache_without_url_is_bypassed() -> None:
    cache = Cache.from_url(None, prefix="test", ttl_seconds=60)
    lookup = cache.lookup("ns", "key")
    assert lookup.status is CacheStatus.BYPASS
    cache.store(lookup, {"a": 1})  # no-op
    cache.invalidate("ns")  # no-op


def test_unreachable_cache_fails_open() -> None:
    cache = unreachable_cache()
    lookup = cache.lookup("ns", "key")
    assert lookup.status is CacheStatus.BYPASS
    cache.store(lookup, {"a": 1})
    cache.invalidate("ns")


def test_store_then_hit_and_invalidate(redis_client: redis.Redis) -> None:
    prefix = f"test-{random_lower_string()}"
    cache = Cache(redis_client, prefix=prefix, ttl_seconds=60)

    miss = cache.lookup("ns", "key")
    assert miss.status is CacheStatus.MISS
    cache.store(miss, {"a": 1})

    hit = cache.lookup("ns", "key")
    assert hit.status is CacheStatus.HIT
    assert hit.value == {"a": 1}

    cache.invalidate("ns")
    assert cache.lookup("ns", "key").status is CacheStatus.MISS


def test_store_sets_ttl(redis_client: redis.Redis) -> None:
    prefix = f"test-{random_lower_string()}"
    cache = Cache(redis_client, prefix=prefix, ttl_seconds=30)
    miss = cache.lookup("ns", "key")
    cache.store(miss, "value")
    assert miss.key is not None
    assert 0 < int(redis_client.ttl(miss.key)) <= 30
