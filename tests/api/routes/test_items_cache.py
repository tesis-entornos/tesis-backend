from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_cache
from app.core.cache import Cache
from app.core.config import settings
from app.main import app
from tests.utils.item import create_random_item

ITEMS_URL = f"{settings.API_V1_STR}/items"


def test_read_item_is_cached(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    item = create_random_item(db)

    first = client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)
    second = client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)

    assert first.status_code == 200
    assert first.headers["X-Cache"] == "MISS"
    assert second.status_code == 200
    assert second.headers["X-Cache"] == "HIT"
    assert second.json() == first.json()


def test_update_item_invalidates_cache(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    item = create_random_item(db)
    client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)

    response = client.put(
        f"{ITEMS_URL}/{item.id}",
        headers=superuser_token_headers,
        json={"title": "Updated title"},
    )
    assert response.status_code == 200

    after = client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)
    assert after.headers["X-Cache"] == "MISS"
    assert after.json()["title"] == "Updated title"


def test_delete_item_invalidates_cache(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    item = create_random_item(db)
    client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)

    response = client.delete(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)
    assert response.status_code == 200

    after = client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)
    assert after.status_code == 404


def test_create_item_invalidates_list(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    client.get(f"{ITEMS_URL}/", headers=superuser_token_headers)
    cached = client.get(f"{ITEMS_URL}/", headers=superuser_token_headers)
    assert cached.headers["X-Cache"] == "HIT"

    created = client.post(
        f"{ITEMS_URL}/",
        headers=superuser_token_headers,
        json={"title": "New item"},
    )
    assert created.status_code == 200

    after = client.get(f"{ITEMS_URL}/", headers=superuser_token_headers)
    assert after.headers["X-Cache"] == "MISS"
    assert created.json()["id"] in [item["id"] for item in after.json()["data"]]


def test_cached_item_still_checks_permissions(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    # Owned by a random user, not by the normal test user
    item = create_random_item(db)
    client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)
    cached = client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)
    assert cached.headers["X-Cache"] == "HIT"

    response = client.get(f"{ITEMS_URL}/{item.id}", headers=normal_user_token_headers)
    assert response.status_code == 403


def test_deleting_owner_invalidates_cached_items(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    item = create_random_item(db)
    client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)

    response = client.delete(
        f"{settings.API_V1_STR}/users/{item.owner_id}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200

    after = client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)
    assert after.status_code == 404


@pytest.fixture
def unavailable_cache() -> Generator[None]:
    unreachable = Cache.from_url("redis://127.0.0.1:1/0", prefix="test", ttl_seconds=60)
    app.dependency_overrides[get_cache] = lambda: unreachable
    yield
    app.dependency_overrides.pop(get_cache)


@pytest.mark.usefixtures("unavailable_cache")
def test_items_are_served_when_cache_is_unavailable(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    item = create_random_item(db)

    response = client.get(f"{ITEMS_URL}/{item.id}", headers=superuser_token_headers)
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "BYPASS"
    assert response.json()["id"] == str(item.id)

    listing = client.get(f"{ITEMS_URL}/", headers=superuser_token_headers)
    assert listing.status_code == 200
    assert listing.headers["X-Cache"] == "BYPASS"
