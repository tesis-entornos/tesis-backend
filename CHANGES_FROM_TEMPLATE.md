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

## Removed from upstream (not copied)

| Path | Reason |
|------|--------|
| `.github/` (all workflows, `dependabot.yml`, etc.) | CI/CD will be CodeBuild only; Dependabot would change dependencies over time and hurt reproducibility. |
| `compose.deploy.yml`, `deployment.md`, `deployment-docker-compose.md` | Deployment targets ECS/Fargate (out of scope here), not Docker Compose + Traefik. |
| `packages/react-email/` | Development tool that exports the email HTML; the exported templates are already versioned in `app/email-templates/` and are kept unchanged. |
| Root `pyproject.toml`, `package.json`, `bun.lock` (backend side), `.pre-commit-config.yaml` | Tooling of the upstream monorepo. Lint is available through `scripts/lint.sh` / `scripts/format.sh`. |
| `.claude/`, `.agents/`, `.vscode/`, `hooks/`, `.fastapicloudignore`, `img/`, `release-notes.md`, `CONTRIBUTING.md`, `development.md`, `scripts/prepare_release.py`, `scripts/add_latest_release_date.py`, `scripts/test-local.sh` | Upstream maintenance, editor or hosting specific; not part of the application. |
