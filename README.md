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

## Image

`docker build -t tesis-backend:local .` — the same image is used by the frontend E2E tests (`BACKEND_IMAGE`).
