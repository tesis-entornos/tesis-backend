"""Restore the baseline of one environment: database, cache and bucket.

Runs with the deployable image and the same environment variables as the
application (DATABASE_URL, REDIS_URL, CACHE_KEY_PREFIX, S3_BUCKET, ...):

    RESET_CONFIRM=<database name> python scripts/reset_baseline.py

Steps:

1. Database: ``DROP DATABASE ... WITH (FORCE)`` on the configured database
   (open connections are terminated), ``CREATE DATABASE``, Alembic migrations
   of this version (``upgrade head``) and the initial data (first superuser).
   The statements run on a maintenance database of the same server
   (``RESET_MAINTENANCE_DB``, default ``postgres``) with the same credentials,
   so the configured user needs to own the database and have CREATEDB.
2. Bucket: deletes every object version and delete marker, then every
   remaining object, under ``S3_KEY_PREFIX`` (the whole bucket when the prefix
   is empty, as in every environment of the experiment).
3. Cache: deletes only the keys ``<CACHE_KEY_PREFIX>:*`` with SCAN + UNLINK
   (never FLUSHDB/FLUSHALL: the cluster is shared by several environments).
   It runs last so entries computed from the old data are removed too.
4. Verification: 1 user (the superuser), 0 items, 0 files, 0 cache keys and
   0 objects. A JSON report is printed to stdout.

Exit codes: 0 baseline verified; 1 a step or the verification failed;
2 refused (RESET_CONFIRM does not match the database name); 3 missing
configuration (REDIS_URL, CACHE_KEY_PREFIX and S3_BUCKET are required).
Confirmation and configuration are checked before anything is changed.
"""

import json
import logging
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import redis
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, make_url, text
from sqlalchemy.pool import NullPool
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine, init_db
from app.core.storage import build_s3_client
from app.models import File, Item, User

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger("reset_baseline")

ROOT = Path(__file__).resolve().parents[1]
EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_CONFIG = 3
_DELETE_BATCH = 1000
_SCAN_COUNT = 1000


class ResetError(Exception):
    pass


def database_name() -> str:
    name = make_url(str(settings.DATABASE_URL)).database
    if not name:
        raise ResetError("DATABASE_URL has no database name")
    return name


def check_confirmation(db_name: str, confirm: str | None) -> bool:
    return confirm is not None and confirm == db_name


# --- Database -------------------------------------------------------------


def recreate_database(db_name: str, maintenance_db: str) -> None:
    url = make_url(str(settings.DATABASE_URL)).set(database=maintenance_db)
    admin = create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    quoted = admin.dialect.identifier_preparer.quote_identifier(db_name)
    # Connections of this process must not keep the database open
    engine.dispose()
    try:
        with admin.connect() as conn:
            logger.info("Dropping database %s", db_name)
            conn.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
            logger.info("Creating database %s", db_name)
            conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        admin.dispose()


def migrate_and_seed() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "app" / "alembic"))
    logger.info("Applying migrations (upgrade head)")
    command.upgrade(config, "head")
    # alembic's env.py reconfigures logging (root at WARN) and disables
    # existing loggers: keep this script's progress messages visible
    logger.disabled = False
    logger.setLevel(logging.INFO)
    logger.info("Creating initial data")
    with Session(engine) as session:
        init_db(session)


def database_counts() -> dict[str, Any]:
    with Session(engine) as session:
        users = session.exec(select(func.count()).select_from(User)).one()
        superuser = session.exec(
            select(User).where(User.email == settings.FIRST_SUPERUSER)
        ).first()
        items = session.exec(select(func.count()).select_from(Item)).one()
        files = session.exec(select(func.count()).select_from(File)).one()
    return {
        "users": users,
        "superuser_present": bool(superuser and superuser.is_superuser),
        "items": items,
        "files": files,
    }


# --- Cache ----------------------------------------------------------------


def _escape_glob(value: str) -> str:
    # Redis MATCH uses glob patterns: the prefix must match literally
    return "".join(f"\\{c}" if c in "\\*?[]" else c for c in value)


def cache_pattern(prefix: str) -> str:
    return f"{_escape_glob(prefix)}:*"


def delete_cache_keys(client: redis.Redis, prefix: str) -> int:
    deleted = 0
    batch: list[Any] = []
    for key in client.scan_iter(match=cache_pattern(prefix), count=_SCAN_COUNT):
        batch.append(key)
        if len(batch) >= _DELETE_BATCH:
            deleted += int(client.unlink(*batch))
            batch = []
    if batch:
        deleted += int(client.unlink(*batch))
    return deleted


def count_cache_keys(client: redis.Redis, prefix: str) -> int:
    return sum(
        1 for _ in client.scan_iter(match=cache_pattern(prefix), count=_SCAN_COUNT)
    )


# --- Bucket ---------------------------------------------------------------


def _delete_batches(
    client: S3Client, bucket: str, objects: Iterable[dict[str, str]]
) -> int:
    pending = list(objects)
    deleted = 0
    for start in range(0, len(pending), _DELETE_BATCH):
        batch = pending[start : start + _DELETE_BATCH]
        response = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": batch, "Quiet": True},  # type: ignore[typeddict-item]
        )
        errors = response.get("Errors", [])
        if errors:
            raise ResetError(f"Could not delete {len(errors)} objects: {errors[:3]}")
        deleted += len(batch)
    return deleted


def _list_versions(client: S3Client, bucket: str, prefix: str) -> list[dict[str, str]]:
    versions: list[dict[str, str]] = []
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for entry in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]:
            versions.append({"Key": entry["Key"], "VersionId": entry["VersionId"]})
    return versions


def _list_keys(client: S3Client, bucket: str, prefix: str) -> list[dict[str, str]]:
    keys: list[dict[str, str]] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend({"Key": obj["Key"]} for obj in page.get("Contents", []))
    return keys


def empty_bucket(client: S3Client, bucket: str, prefix: str) -> int:
    # Versions and delete markers first (versioned buckets), then any object
    # still listed (unversioned buckets or emulators without version listing)
    deleted = _delete_batches(client, bucket, _list_versions(client, bucket, prefix))
    deleted += _delete_batches(client, bucket, _list_keys(client, bucket, prefix))
    return deleted


def count_objects(client: S3Client, bucket: str, prefix: str) -> int:
    return len(_list_versions(client, bucket, prefix)) or len(
        _list_keys(client, bucket, prefix)
    )


# --- Main -----------------------------------------------------------------


def missing_configuration() -> list[str]:
    """Settings the reset cannot run without (checked before any change)."""
    missing = []
    if not settings.REDIS_URL:
        missing.append("REDIS_URL")
    if not settings.CACHE_KEY_PREFIX:
        missing.append("CACHE_KEY_PREFIX")
    if not settings.S3_BUCKET:
        missing.append("S3_BUCKET")
    return missing


def open_s3() -> S3Client:
    return build_s3_client(
        region=settings.AWS_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        force_path_style=settings.S3_FORCE_PATH_STYLE,
    )


def open_cache() -> redis.Redis:
    return redis.Redis.from_url(str(settings.REDIS_URL))


EXPECTED_BASELINE: dict[str, Any] = {
    "users": 1,
    "superuser_present": True,
    "items": 0,
    "files": 0,
    "cache_keys": 0,
    "s3_objects": 0,
}


def run() -> tuple[int, dict[str, Any]]:
    db_name = database_name()
    report: dict[str, Any] = {"database": db_name, "baseline_ok": False}
    if not check_confirmation(db_name, os.environ.get("RESET_CONFIRM")):
        report["error"] = (
            f"refused: RESET_CONFIRM must be exactly the database name ({db_name!r})"
        )
        return EXIT_REFUSED, report
    missing = missing_configuration()
    if missing:
        report["error"] = f"missing configuration: {', '.join(missing)}"
        return EXIT_CONFIG, report
    bucket = settings.S3_BUCKET or ""
    prefix = settings.CACHE_KEY_PREFIX or ""

    recreate_database(db_name, os.environ.get("RESET_MAINTENANCE_DB", "postgres"))
    migrate_and_seed()

    s3 = open_s3()
    logger.info("Emptying bucket %s (prefix %r)", bucket, settings.S3_KEY_PREFIX)
    report["s3_deleted"] = empty_bucket(s3, bucket, settings.S3_KEY_PREFIX)

    cache = open_cache()
    logger.info("Deleting cache keys %s", cache_pattern(prefix))
    report["cache_deleted"] = delete_cache_keys(cache, prefix)

    counts = database_counts()
    counts["cache_keys"] = count_cache_keys(cache, prefix)
    counts["s3_objects"] = count_objects(s3, bucket, settings.S3_KEY_PREFIX)
    report["counts"] = counts

    mismatches = {
        k: {"expected": v, "actual": counts.get(k)}
        for k, v in EXPECTED_BASELINE.items()
        if counts.get(k) != v
    }
    report["baseline_ok"] = not mismatches
    if mismatches:
        report["mismatches"] = mismatches
        return EXIT_FAILED, report
    return 0, report


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        code, report = run()
    except Exception as e:  # report every failure with a non-zero exit code
        logger.exception("Baseline reset failed")
        code = EXIT_FAILED
        report = {"baseline_ok": False, "error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
