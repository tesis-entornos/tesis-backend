from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.api.deps import get_engine, get_optional_storage
from app.core.config import settings
from app.core.storage import FileStorage
from app.main import app

UTILS_URL = f"{settings.API_V1_STR}/utils"


def test_health_check(client: TestClient) -> None:
    r = client.get(f"{UTILS_URL}/health-check/")
    assert r.status_code == 200
    assert r.json() is True


def test_ready_when_dependencies_are_available(client: TestClient) -> None:
    r = client.get(f"{UTILS_URL}/ready")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ready",
        "checks": {"database": "ok", "storage": "ok"},
    }


@pytest.fixture
def unreachable_database() -> Generator[None]:
    # Port 1 is never a Postgres server: the connection fails fast
    unreachable = create_engine("postgresql+psycopg://user:pass@127.0.0.1:1/none")
    app.dependency_overrides[get_engine] = lambda: unreachable
    yield
    app.dependency_overrides.pop(get_engine)
    unreachable.dispose()


@pytest.mark.usefixtures("unreachable_database")
def test_not_ready_when_database_is_unavailable(client: TestClient) -> None:
    r = client.get(f"{UTILS_URL}/ready")
    assert r.status_code == 503
    assert r.json() == {
        "status": "not_ready",
        "checks": {"database": "error", "storage": "ok"},
    }


@pytest.fixture
def unreachable_storage() -> Generator[None]:
    unreachable = FileStorage.from_settings(
        bucket="unreachable",
        key_prefix="",
        region="us-east-1",
        endpoint_url="http://127.0.0.1:1",
        force_path_style=True,
        max_attempts=1,
    )
    app.dependency_overrides[get_optional_storage] = lambda: unreachable
    yield
    app.dependency_overrides.pop(get_optional_storage)


@pytest.mark.usefixtures("unreachable_storage")
def test_not_ready_when_storage_is_unavailable(client: TestClient) -> None:
    r = client.get(f"{UTILS_URL}/ready")
    assert r.status_code == 503
    assert r.json() == {
        "status": "not_ready",
        "checks": {"database": "ok", "storage": "error"},
    }


def test_not_ready_when_bucket_does_not_exist(client: TestClient) -> None:
    assert settings.S3_BUCKET
    missing = FileStorage.from_settings(
        bucket=f"{settings.S3_BUCKET}-missing",
        key_prefix="",
        region=settings.AWS_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        force_path_style=settings.S3_FORCE_PATH_STYLE,
    )
    app.dependency_overrides[get_optional_storage] = lambda: missing
    try:
        r = client.get(f"{UTILS_URL}/ready")
    finally:
        app.dependency_overrides.pop(get_optional_storage)
    assert r.status_code == 503
    assert r.json()["checks"] == {"database": "ok", "storage": "error"}


def test_not_ready_when_storage_is_not_configured(client: TestClient) -> None:
    app.dependency_overrides[get_optional_storage] = lambda: None
    try:
        r = client.get(f"{UTILS_URL}/ready")
    finally:
        app.dependency_overrides.pop(get_optional_storage)
    assert r.status_code == 503
    assert r.json()["checks"] == {"database": "ok", "storage": "not_configured"}
