from collections.abc import Generator

import pytest
import redis
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine, init_db
from app.core.storage import storage
from app.main import app
from tests.utils.cleanup import delete_created_records, track_created_records
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session]:
    # Only the records created during this run are deleted at the end, so the
    # tests can run against a database that already holds other data.
    with track_created_records() as created, Session(engine) as session:
        init_db(session)
        yield session
        delete_created_records(session, created, storage)


@pytest.fixture(scope="module")
def client() -> Generator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )


@pytest.fixture(scope="session")
def redis_client() -> Generator[redis.Redis]:
    assert settings.REDIS_URL, "REDIS_URL must be configured to run the tests"
    client = redis.Redis.from_url(str(settings.REDIS_URL))
    yield client
    client.close()
