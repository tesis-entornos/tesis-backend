#! /usr/bin/env sh
# Exports the OpenAPI schema to ./openapi.json from the deployable (runtime) image,
# in production mode (no FASTAPI_ENV), so the /private routes are NOT included.
# The required settings get placeholder values: importing the app opens no
# connection to the database, Redis or S3.

set -e

IMAGE="${OPENAPI_IMAGE:-tesis-backend:openapi}"

docker build -q -t "$IMAGE" . > /dev/null
docker run --rm \
  -e PROJECT_NAME="Full Stack FastAPI Project" \
  -e SECRET_KEY=openapi-export-placeholder \
  -e FIRST_SUPERUSER=admin@example.com \
  -e FIRST_SUPERUSER_PASSWORD=openapi-export-placeholder \
  -e DATABASE_URL=postgresql://openapi:openapi-export-placeholder@localhost:5432/openapi \
  "$IMAGE" \
  python -c "import json, app.main; print(json.dumps(app.main.app.openapi()))" > openapi.json
echo "openapi.json written"
