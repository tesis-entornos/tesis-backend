"""Tests for scripts/reset_baseline.py that do not drop the test database.

The full reset (drop, recreate, migrate, seed, verify) is exercised against a
running stack, not from Pytest, because it would destroy this run's data.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
import redis

from app.core.config import settings
from app.core.storage import build_s3_client
from tests.utils.utils import random_lower_string

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reset_baseline.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("reset_baseline", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("confirm", [None, "", "wrong-database"])
def test_refuses_without_matching_confirmation(
    monkeypatch: pytest.MonkeyPatch, confirm: str | None
) -> None:
    script = load_script()

    def must_not_run(*_args: object) -> None:
        raise AssertionError("the database must not be touched")

    monkeypatch.setattr(script, "recreate_database", must_not_run)
    if confirm is None:
        monkeypatch.delenv("RESET_CONFIRM", raising=False)
    else:
        monkeypatch.setenv("RESET_CONFIRM", confirm)

    code, report = script.run()

    assert code == script.EXIT_REFUSED
    assert "refused" in report["error"]


def test_deletes_only_keys_with_the_prefix(redis_client: redis.Redis) -> None:
    script = load_script()
    base = f"rb-{random_lower_string()}"
    # Glob characters in the prefix must match literally
    prefix = f"{base}*"
    decoy = f"{base}-other:key"  # matched by an unescaped "{base}*:*"
    redis_client.set(f"{prefix}:a", "1")
    redis_client.set(f"{prefix}:b", "2")
    redis_client.set(decoy, "3")
    try:
        assert script.delete_cache_keys(redis_client, prefix) == 2
        assert script.count_cache_keys(redis_client, prefix) == 0
        assert redis_client.get(decoy) == b"3"
    finally:
        redis_client.delete(decoy)


def test_empties_only_objects_under_the_prefix() -> None:
    script = load_script()
    assert settings.S3_BUCKET
    bucket = settings.S3_BUCKET
    client = build_s3_client(
        region=settings.AWS_REGION,
        endpoint_url=settings.S3_ENDPOINT_URL,
        force_path_style=settings.S3_FORCE_PATH_STYLE,
    )
    prefix = f"reset-test-{random_lower_string()}/"
    other = f"reset-other-{random_lower_string()}/kept"
    for i in range(3):
        client.put_object(Bucket=bucket, Key=f"{prefix}{i}", Body=b"x")
    client.put_object(Bucket=bucket, Key=other, Body=b"y")
    try:
        assert script.empty_bucket(client, bucket, prefix) >= 3
        assert script.count_objects(client, bucket, prefix) == 0
        assert client.get_object(Bucket=bucket, Key=other)["Body"].read() == b"y"
    finally:
        client.delete_object(Bucket=bucket, Key=other)


def _stub_steps(
    monkeypatch: pytest.MonkeyPatch,
    script: ModuleType,
    *,
    counts: dict[str, object],
    cache_keys: int = 0,
    s3_objects: int = 0,
) -> None:
    """Replace every step that touches a service; only run()'s logic is tested."""
    monkeypatch.setenv("RESET_CONFIRM", script.database_name())
    monkeypatch.setattr(script, "recreate_database", lambda *_: None)
    monkeypatch.setattr(script, "migrate_and_seed", lambda: None)
    monkeypatch.setattr(script, "open_s3", lambda: object())
    monkeypatch.setattr(script, "open_cache", lambda: object())
    monkeypatch.setattr(script, "empty_bucket", lambda *_: 0)
    monkeypatch.setattr(script, "delete_cache_keys", lambda *_: 0)
    monkeypatch.setattr(script, "database_counts", lambda: dict(counts))
    monkeypatch.setattr(script, "count_cache_keys", lambda *_: cache_keys)
    monkeypatch.setattr(script, "count_objects", lambda *_: s3_objects)


BASELINE_COUNTS = {"users": 1, "superuser_present": True, "items": 0, "files": 0}


@pytest.mark.parametrize("setting", ["REDIS_URL", "CACHE_KEY_PREFIX", "S3_BUCKET"])
def test_fails_without_required_configuration(
    monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    script = load_script()
    _stub_steps(monkeypatch, script, counts=BASELINE_COUNTS)

    def must_not_run(*_args: object) -> None:
        raise AssertionError("nothing must change without full configuration")

    monkeypatch.setattr(script, "recreate_database", must_not_run)
    monkeypatch.setattr(script.settings, setting, None)

    code, report = script.run()

    assert code == script.EXIT_CONFIG
    assert setting in report["error"]
    assert report["baseline_ok"] is False


def test_succeeds_only_when_every_count_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load_script()
    _stub_steps(monkeypatch, script, counts=BASELINE_COUNTS)

    code, report = script.run()

    assert code == 0
    assert report["baseline_ok"] is True
    assert report["counts"] == {**BASELINE_COUNTS, "cache_keys": 0, "s3_objects": 0}


def test_fails_when_verification_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load_script()
    _stub_steps(
        monkeypatch,
        script,
        counts={**BASELINE_COUNTS, "users": 2, "superuser_present": False},
        cache_keys=1,
    )

    code, report = script.run()

    assert code == script.EXIT_FAILED
    assert report["baseline_ok"] is False
    assert set(report["mismatches"]) == {"users", "superuser_present", "cache_keys"}


def test_main_exits_non_zero_when_a_step_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = load_script()
    _stub_steps(monkeypatch, script, counts=BASELINE_COUNTS)

    def bucket_unreachable(*_args: object) -> int:
        raise RuntimeError("bucket unreachable")

    monkeypatch.setattr(script, "empty_bucket", bucket_unreachable)

    assert script.main() == script.EXIT_FAILED
    report = json.loads(capsys.readouterr().out)
    assert report["baseline_ok"] is False
    assert "bucket unreachable" in report["error"]
