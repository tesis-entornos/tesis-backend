# Changes from the template

Base: `fastapi/full-stack-fastapi-template` @ `aabb90126964a164e02aff39e7c385bb0598236d` (see `NOTICE.md`).
Paths on the left are upstream paths; on the right, paths in this repository.

## Repository split (stage 1)

| # | Change | Reason |
|---|--------|--------|
| B1 | `backend/*` → repository root. `LICENSE`, `.gitattributes`, `.python-version` copied unchanged from the upstream root. | Backend and frontend are separate repositories, each producing its own image. |
| B2 | `uv.lock` moved from the upstream root and re-locked with uv 0.9.26 as a standalone project (no uv workspace). Only `prek`, `smokeshow` and `zizmor` were removed (dev tools of the upstream root project); every other package keeps the exact same version. | The upstream root `pyproject.toml` (workspace + tooling for upstream maintenance) is not part of this repo. |
| B3 | `app/main.py`: removed `app.frontend("/", directory=FRONTEND_DIR)` and `FRONTEND_DIR`. | Upstream serves the built frontend from the backend (single image). Here the frontend is its own image; `/` vs `/api/*` routing is done by the edge (CloudFront in AWS, an nginx container locally). |
| B4 | `app/core/config.py`: `env_file="../.env"` → `".env"`. | The `.env` file now lives at this repository's root. |
| B5 | `Dockerfile`: removed the `oven/bun` frontend build stage and the copy of the built frontend; paths adapted to the repository root (`WORKDIR /app` instead of `/app/backend`); `uv sync` without `--package app`. Base image, uv version and the rest of the build are unchanged. | Consequence of B1 and B3. |
| B6 | `compose.yml` rewritten for local development: removed Traefik (`proxy`) and its labels; Mailpit moved here from the override and pinned to `v1.27`; added a one-shot `prestart` service (migrations + initial data) that `backend` waits for; ports published here. `compose.override.yml` only keeps `fastapi dev`, `develop.watch` (paths adapted) and mounts `./tests` and `./htmlcov`. | Traefik was only used for upstream's own deployment. `prestart` replaces the manual `docker compose run backend bash scripts/prestart.sh`. Pinned tags improve reproducibility. |
| B7 | Tests are not copied into the image; they are mounted as a volume when Pytest runs (same as upstream, where the image had no `tests/`). | Keep the image content identical to upstream's backend part. |
| B8 | `scripts/run-tests.sh` (adapted from upstream root `scripts/test.sh`, now using `up --wait`). | Same flow, adapted to the new compose file. |
| B9 | `scripts/export-openapi.sh` (first half of upstream root `scripts/generate-client.sh`) and `openapi.json` versioned. Exported with `FASTAPI_ENV=development`, as upstream, so the `private` routes used by E2E tests are included. | The frontend repository generates its client from this file; versioning it makes the API contract of every release traceable. |
| B10 | Committed `.env` → `.env.example` (same values, plus explicit `FRONTEND_HOST`); `.env` added to `.gitignore` and `.dockerignore`; `.git` and `openapi.json` added to `.dockerignore`. | Never commit environment files; keep the image independent from local files. |
| B11 | `README.md` replaced. | Instructions for this repository only. |

## Module: Redis cache (stage 3)

Declared change of the reference application: a cache-aside Redis cache for item reads.

| # | Change | Reason |
|---|--------|--------|
| C1 | Dependency `redis>=8.1.0,<9.0.0` (redis-py 8.1.0 locked, no transitive dependencies). | Redis client (works with ElastiCache, `rediss://` for TLS). |
| C2 | Settings `REDIS_URL` (optional; without it the cache is disabled), `CACHE_TTL_SECONDS` (60) and `CACHE_KEY_PREFIX` (`tesis`). | Same image for every environment; the prefix lets several environments share a cluster without key collisions. |
| C3 | `app/core/cache.py`: JSON cache with one *epoch* counter per namespace inside every key; invalidation = `INCR` of the epoch. Fail-open: any Redis error is logged and the request is served from the database. 0.5 s socket timeouts. | Cache-aside without pattern deletes; safe against concurrent readers storing values computed before a write. Fail-open keeps the cache from being a single point of failure. |
| C4 | `app/core/cache_invalidation.py`, registered in `app/main.py`: SQLAlchemy session listeners mark the `items` namespace when a flush or a bulk statement touches `Item` (or deletes a `User`) and invalidate it **after commit** (discarded on rollback). | Covers every write path, including `crud` helpers, ORM cascades and the bulk `DELETE` used when deleting a user, without touching the write routes. |
| C5 | `app/api/routes/items.py`: `GET /items/` and `GET /items/{id}` read through the cache (`CacheDep` in `app/api/deps.py`) and return the header `X-Cache: HIT \| MISS \| BYPASS`. Permission checks run on every request, also for cached items; 404s are not cached. Write routes are unchanged. Response bodies are unchanged. | Makes the cache behavior observable (tests and the experiment) without reading Redis internals. |
| C6 | `compose.yml`: `redis:7.2-alpine` service with healthcheck; backend waits for it. `.env.example` documents the new settings. | Local emulation, closest to ElastiCache Redis OSS 7.x. |
| C7 | Tests: `tests/core/test_cache.py` (disabled, unreachable/fail-open, hit + invalidation, TTL) and `tests/api/routes/test_items_cache.py` (MISS→HIT, invalidation on update, delete, create and owner deletion, permissions on cached items, BYPASS when Redis is unavailable). `tests/conftest.py` gains a `redis_client` fixture. Tests use random ids and their own key prefixes; no `FLUSHALL`. | Coverage of the new module against a real Redis, without dependence on residual data. |

## Module: file storage on S3 (stage 3)

Declared change of the reference application: endpoints to upload, list, download and delete files stored in S3.

| # | Change | Reason |
|---|--------|--------|
| S1 | Dependencies `boto3>=1.43.123,<2.0.0` and, for type checking only, `boto3-stubs[s3]` (dev). | Official AWS SDK; stubs keep `mypy --strict` passing. |
| S2 | Settings `S3_BUCKET` (optional; without it `/files` answers 503), `S3_KEY_PREFIX`, `AWS_REGION`, `S3_ENDPOINT_URL` and `S3_FORCE_PATH_STYLE` (local emulator only), `MAX_UPLOAD_BYTES` (10 MiB). Credentials always come from the default AWS chain (ECS task role in AWS); they are never read from `.env`. | Same image for every environment; the key prefix lets several environments share a bucket. |
| S3 | Model `File` (`id`, `filename`, `content_type`, `size`, unique `s3_key`, `created_at`, indexed `owner_id` with `ON DELETE CASCADE`), `FilePublic`, `FilesPublic`; relationship `User.files` with cascade, like `User.items`. Migration `e3acbde9bd2f_add_file_table` (autogenerated; `created_at` typed `sa.DateTime(timezone=True)` like upstream migrations). | Metadata in Postgres allows per-user listing and pagination with the same permission rules as items, without listing S3. |
| S4 | `app/core/storage.py`: `FileStorage` (put, streamed get, delete, best-effort batch delete), botocore timeouts and standard retries; keys `{S3_KEY_PREFIX}files/{owner_id}/{file_id}` (user file names are never part of a key). The application never creates buckets. | Isolates S3 access; in AWS the bucket is created by infrastructure code. |
| S5 | `app/api/routes/files.py` (`/api/v1/files`): `POST /` (multipart, 413 above the limit), `GET /` (own files; superusers see all), `GET /{id}` (streamed through the backend, `Content-Disposition: attachment` with RFC 5987 file name, `X-Content-Type-Options: nosniff`), `DELETE /{id}`. 404/403 like items; storage errors → 502. Order of operations: upload = object then row (the object is removed if the row cannot be stored); delete = object then row. | Metadata never points to a missing object; `nosniff` + attachment prevent rendering user supplied content in the app origin. |
| S6 | `app/api/routes/users.py`: `DELETE /users/{id}` and `DELETE /users/me` delete the user's objects **after** the commit (best effort, failures logged). Uses an optional storage dependency so deleting users keeps working without S3. | Avoid orphan objects when a user is deleted (their file rows are removed by the cascade). |
| S7 | `compose.yml`: `motoserver/moto:5.2.5` (S3 emulator, host port 5050 because 5000 is often taken on macOS) and a one-shot `s3-init` (`amazon/aws-cli:2.39.3`) that creates the bucket; the backend waits for it and gets fake credentials (`test`/`test`) for the emulator. | Local emulation without AWS resources or credentials. |
| S8 | Tests `tests/api/routes/test_files.py`: round trip with byte comparison and a non-ASCII name, size limit (at and above), listing visibility, 403/404, user deletion (admin and self) removes objects, 502 with no metadata when storage is unreachable, 503 when not configured. `tests/conftest.py` deletes the objects of remaining files in its final cleanup. | Coverage of the new module against the emulator, without residual data in the bucket. |

## Removed from upstream (not copied)

| Path | Reason |
|------|--------|
| `.github/` (all workflows, `dependabot.yml`, etc.) | CI/CD will be CodeBuild only; Dependabot would change dependencies over time and hurt reproducibility. |
| `compose.deploy.yml`, `deployment.md`, `deployment-docker-compose.md` | Deployment targets ECS/Fargate (out of scope here), not Docker Compose + Traefik. |
| `packages/react-email/` | Development tool that exports the email HTML; the exported templates are already versioned in `app/email-templates/` and are kept unchanged. |
| Root `pyproject.toml`, `package.json`, `bun.lock` (backend side), `.pre-commit-config.yaml` | Tooling of the upstream monorepo. Lint is available through `scripts/lint.sh` / `scripts/format.sh`. |
| `.claude/`, `.agents/`, `.vscode/`, `hooks/`, `.fastapicloudignore`, `img/`, `release-notes.md`, `CONTRIBUTING.md`, `development.md`, `scripts/prepare_release.py`, `scripts/add_latest_release_date.py`, `scripts/test-local.sh` | Upstream maintenance, editor or hosting specific; not part of the application. |
