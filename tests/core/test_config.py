import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("prefix", [None, ""])
def test_redis_url_requires_cache_key_prefix(prefix: str | None) -> None:
    with pytest.raises(ValidationError, match="CACHE_KEY_PREFIX is required"):
        Settings(REDIS_URL="redis://redis:6379/0", CACHE_KEY_PREFIX=prefix)  # type: ignore[arg-type]


def test_cache_key_prefix_with_redis_url() -> None:
    settings = Settings(REDIS_URL="redis://redis:6379/0", CACHE_KEY_PREFIX="env-a")  # type: ignore[arg-type]
    assert settings.CACHE_KEY_PREFIX == "env-a"


def test_cache_key_prefix_not_required_without_redis() -> None:
    settings = Settings(REDIS_URL=None, CACHE_KEY_PREFIX=None)
    assert settings.REDIS_URL is None
