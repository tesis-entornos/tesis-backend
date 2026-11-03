#! /usr/bin/env sh
# Exports the OpenAPI schema to ./openapi.json from the built image.
# FASTAPI_ENV=development is used so that the /private routes needed by the
# frontend E2E tests are included (same as the upstream template).

set -e

docker compose build prestart
docker compose run --rm --no-deps -T -e FASTAPI_ENV=development prestart \
  python -c "import json, app.main; print(json.dumps(app.main.app.openapi()))" > openapi.json
echo "openapi.json written"
