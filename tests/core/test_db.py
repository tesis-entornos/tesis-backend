from sqlalchemy import text

from app.core.config import settings
from app.core.db import build_engine, engine


def test_pool_uses_configured_sizes() -> None:
    assert engine.pool.size() == settings.DB_POOL_SIZE  # type: ignore[attr-defined]
    assert engine.pool._max_overflow == settings.DB_MAX_OVERFLOW  # type: ignore[attr-defined]


def test_pool_recovers_after_connection_is_terminated() -> None:
    # Simulates the database dropping a pooled connection (restart or
    # recreation). A dedicated engine with the application's settings keeps
    # the test deterministic: its only idle connection is the terminated one.
    isolated = build_engine(str(settings.DATABASE_URL))
    try:
        with isolated.connect() as conn:
            old_pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with engine.connect() as other:
            other.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": old_pid})
        with isolated.connect() as conn:
            new_pid = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
        assert new_pid != old_pid
    finally:
        isolated.dispose()
