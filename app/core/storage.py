"""File storage on S3 (or an S3-compatible emulator in local development).

The application never creates buckets: in AWS the bucket is provisioned by
infrastructure code, locally by the ``s3-init`` compose service.
"""

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024
# delete_objects accepts at most 1000 keys per request
_DELETE_BATCH = 1000


class StorageError(Exception):
    """The storage backend failed or is unavailable."""


class ObjectNotFound(StorageError):
    """The requested object does not exist in the bucket."""


@dataclass(frozen=True)
class StoredObject:
    chunks: Iterator[bytes]
    content_length: int


def build_s3_client(
    *,
    region: str,
    endpoint_url: str | None,
    force_path_style: bool,
    max_attempts: int = 3,
) -> S3Client:
    # Credentials come from the default AWS chain (ECS task role in AWS)
    config = Config(
        region_name=region,
        connect_timeout=2,
        read_timeout=10,
        retries={"max_attempts": max_attempts, "mode": "standard"},
        s3={"addressing_style": "path" if force_path_style else "auto"},
    )
    return boto3.client("s3", endpoint_url=endpoint_url or None, config=config)


class FileStorage:
    def __init__(self, client: S3Client, *, bucket: str, key_prefix: str) -> None:
        self._client = client
        self._bucket = bucket
        self._key_prefix = key_prefix

    @classmethod
    def from_settings(
        cls,
        *,
        bucket: str,
        key_prefix: str,
        region: str,
        endpoint_url: str | None,
        force_path_style: bool,
        max_attempts: int = 3,
    ) -> FileStorage:
        client = build_s3_client(
            region=region,
            endpoint_url=endpoint_url,
            force_path_style=force_path_style,
            max_attempts=max_attempts,
        )
        return cls(client, bucket=bucket, key_prefix=key_prefix)

    def key_for(self, *, owner_id: str, file_id: str) -> str:
        # User supplied file names are never part of the key
        return f"{self._key_prefix}files/{owner_id}/{file_id}"

    def check_bucket(self) -> None:
        """Raise StorageError unless the bucket exists and is reachable."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except (BotoCoreError, ClientError) as e:
            raise StorageError(f"Bucket {self._bucket} is not reachable") from e

    def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        except (BotoCoreError, ClientError) as e:
            raise StorageError(f"Could not store {key}") from e

    def get(self, key: str) -> StoredObject:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ObjectNotFound(key) from e
            raise StorageError(f"Could not read {key}") from e
        except BotoCoreError as e:
            raise StorageError(f"Could not read {key}") from e
        body = response["Body"]
        return StoredObject(
            chunks=body.iter_chunks(_CHUNK_SIZE),
            content_length=response["ContentLength"],
        )

    def delete(self, key: str) -> None:
        # Deleting a missing key succeeds in S3 (idempotent)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError) as e:
            raise StorageError(f"Could not delete {key}") from e

    def delete_many(self, keys: Iterable[str]) -> None:
        """Best effort: failures are logged, never raised."""
        pending = list(keys)
        for start in range(0, len(pending), _DELETE_BATCH):
            batch = pending[start : start + _DELETE_BATCH]
            try:
                response = self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
            except BotoCoreError, ClientError:
                logger.error("Could not delete %d objects", len(batch), exc_info=True)
                continue
            for error in response.get("Errors", []):
                logger.error("Could not delete %s: %s", error.get("Key"), error)


def build_storage() -> FileStorage | None:
    if not settings.S3_BUCKET:
        return None
    return FileStorage.from_settings(
        bucket=settings.S3_BUCKET,
        key_prefix=settings.S3_KEY_PREFIX,
        region=settings.AWS_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        force_path_style=settings.S3_FORCE_PATH_STYLE,
    )


storage = build_storage()
