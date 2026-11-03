import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlmodel import Session, select

from app.api import deps
from app.api.deps import get_storage
from app.core.config import settings
from app.core.storage import FileStorage, ObjectNotFound, storage
from app.main import app
from app.models import File
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

FILES_URL = f"{settings.API_V1_STR}/files"


def upload(
    client: TestClient,
    headers: dict[str, str],
    *,
    filename: str | None = None,
    content: bytes = b"hello",
    content_type: str = "text/plain",
) -> Response:
    name = filename or f"{random_lower_string()}.txt"
    response: Response = client.post(
        f"{FILES_URL}/",
        headers=headers,
        files={"file": (name, content, content_type)},
    )
    return response


def s3_key_of(db: Session, file_id: str) -> str:
    db.expire_all()
    key = db.exec(select(File.s3_key).where(File.id == uuid.UUID(file_id))).first()
    assert key is not None
    return key


def test_upload_list_download_delete(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    assert storage is not None
    content = random_lower_string().encode() * 100
    filename = f"informe año {random_lower_string()}.txt"

    uploaded = upload(
        client, normal_user_token_headers, filename=filename, content=content
    )
    assert uploaded.status_code == 200
    file = uploaded.json()
    assert file["filename"] == filename
    assert file["size"] == len(content)
    assert file["content_type"] == "text/plain"
    key = s3_key_of(db, file["id"])
    assert key.endswith(f"files/{file['owner_id']}/{file['id']}")

    listing = client.get(f"{FILES_URL}/", headers=normal_user_token_headers)
    assert listing.status_code == 200
    assert file["id"] in [f["id"] for f in listing.json()["data"]]

    downloaded = client.get(
        f"{FILES_URL}/{file['id']}", headers=normal_user_token_headers
    )
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["content-type"].startswith("text/plain")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert (
        "attachment; filename*=UTF-8''informe%20a%C3%B1o"
        in (downloaded.headers["content-disposition"])
    )

    deleted = client.delete(
        f"{FILES_URL}/{file['id']}", headers=normal_user_token_headers
    )
    assert deleted.status_code == 200
    assert (
        client.get(f"{FILES_URL}/{file['id']}", headers=normal_user_token_headers)
    ).status_code == 404
    with pytest.raises(ObjectNotFound):
        storage.get(key)


def test_upload_too_large(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 10)
    response = upload(client, normal_user_token_headers, content=b"x" * 11)
    assert response.status_code == 413


def test_upload_at_size_limit(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 10)
    response = upload(client, normal_user_token_headers, content=b"x" * 10)
    assert response.status_code == 200
    assert response.json()["size"] == 10


def test_users_only_list_their_files_and_superusers_list_all(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
) -> None:
    own = upload(client, normal_user_token_headers).json()
    other = upload(client, superuser_token_headers).json()

    user_ids = [
        f["id"]
        for f in client.get(
            f"{FILES_URL}/", headers=normal_user_token_headers, params={"limit": 1000}
        ).json()["data"]
    ]
    assert own["id"] in user_ids
    assert other["id"] not in user_ids

    superuser_ids = [
        f["id"]
        for f in client.get(
            f"{FILES_URL}/", headers=superuser_token_headers, params={"limit": 1000}
        ).json()["data"]
    ]
    assert {own["id"], other["id"]} <= set(superuser_ids)


def test_other_users_file_is_forbidden(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    normal_user_token_headers: dict[str, str],
) -> None:
    file = upload(client, superuser_token_headers).json()

    download = client.get(
        f"{FILES_URL}/{file['id']}", headers=normal_user_token_headers
    )
    delete = client.delete(
        f"{FILES_URL}/{file['id']}", headers=normal_user_token_headers
    )
    assert download.status_code == 403
    assert delete.status_code == 403
    still_there = client.get(
        f"{FILES_URL}/{file['id']}", headers=superuser_token_headers
    )
    assert still_there.status_code == 200


def test_missing_file(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    missing = uuid.uuid4()
    assert (
        client.get(f"{FILES_URL}/{missing}", headers=normal_user_token_headers)
    ).status_code == 404
    assert (
        client.delete(f"{FILES_URL}/{missing}", headers=normal_user_token_headers)
    ).status_code == 404


def test_deleting_user_deletes_their_objects(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    assert storage is not None
    email, password = random_email(), random_lower_string()
    created = client.post(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
        json={"email": email, "password": password},
    )
    assert created.status_code == 200
    headers = user_authentication_headers(client=client, email=email, password=password)
    file = upload(client, headers).json()
    key = s3_key_of(db, file["id"])

    response = client.delete(
        f"{settings.API_V1_STR}/users/{created.json()['id']}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    with pytest.raises(ObjectNotFound):
        storage.get(key)


def test_deleting_own_user_deletes_their_objects(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    assert storage is not None
    email, password = random_email(), random_lower_string()
    client.post(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
        json={"email": email, "password": password},
    )
    headers = user_authentication_headers(client=client, email=email, password=password)
    file = upload(client, headers).json()
    key = s3_key_of(db, file["id"])

    response = client.delete(f"{settings.API_V1_STR}/users/me", headers=headers)
    assert response.status_code == 200
    with pytest.raises(ObjectNotFound):
        storage.get(key)


@pytest.fixture
def unavailable_storage() -> Generator[None]:
    unreachable = FileStorage.from_settings(
        bucket="unreachable",
        key_prefix="",
        region="us-east-1",
        endpoint_url="http://127.0.0.1:1",
        force_path_style=True,
        max_attempts=1,
    )
    app.dependency_overrides[get_storage] = lambda: unreachable
    yield
    app.dependency_overrides.pop(get_storage)


@pytest.mark.usefixtures("unavailable_storage")
def test_upload_when_storage_is_unavailable(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    filename = f"{random_lower_string()}.txt"
    response = upload(client, normal_user_token_headers, filename=filename)
    assert response.status_code == 502
    # No metadata is stored for a failed upload
    db.expire_all()
    assert db.exec(select(File).where(File.filename == filename)).first() is None


def test_storage_not_configured(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "storage", None)
    response = client.get(
        f"{FILES_URL}/{uuid.uuid4()}", headers=normal_user_token_headers
    )
    assert response.status_code == 503
