# tesis-backend

Backend of the reference application: FastAPI, SQLModel, Alembic migrations and Pytest tests.
Derived from [Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template) (MIT) — see [`NOTICE.md`](NOTICE.md) and [`CHANGES_FROM_TEMPLATE.md`](CHANGES_FROM_TEMPLATE.md).

The frontend lives in [`tesis-frontend`](https://github.com/tesis-entornos/tesis-frontend).

## Requirements

Docker with Compose v2. Nothing else is required locally.

## Run locally

```bash
cp .env.example .env
docker compose up -d --wait backend    # db + mailpit + migrations + API
```

- API: <http://localhost:8000/api/v1> — docs: <http://localhost:8000/docs>
- Redis (cache): `localhost:6379` — responses of `GET /items` include `X-App-Cache: HIT | MISS | BYPASS`
- S3 emulator (moto): <http://localhost:5050>, bucket `tesis-files`, created by `s3-init`
- Mailpit: <http://localhost:8025> · Adminer (optional): `docker compose up -d adminer` → <http://localhost:8080>

With `docker compose watch` the code in `app/` is synced into the container and reloaded.

## Tests

```bash
./scripts/run-tests.sh     # builds the image, runs Pytest in a clean stack, removes it
```

The coverage report is written to `htmlcov/`.

## OpenAPI contract

```bash
./scripts/export-openapi.sh   # writes openapi.json
```

`openapi.json` is versioned. After changing the API, export it, commit it, and regenerate the client in `tesis-frontend`.

## Images

The `Dockerfile` has two targets:

- `runtime` (default): deployable image, without development dependencies, running as an
  unprivileged user (uid 10001). `CMD` is `scripts/start.sh` (`WEB_CONCURRENCY` workers, default 2).
  Migrations and initial data: `bash scripts/prestart.sh` (one-off task before the service update).

  ```bash
  docker build -t tesis-backend:local .   # used by the frontend E2E tests (BACKEND_IMAGE)
  ```

- `test`: runtime dependencies plus development dependencies and `tests/`, for Pytest.
  The local compose stack (`docker compose up`, `scripts/run-tests.sh`) runs this target
  (`BACKEND_TEST_IMAGE`, default `tesis-backend:test`).

Base images (and the images in `compose.yml`) are pinned by digest.
