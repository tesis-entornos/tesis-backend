#! /usr/bin/env sh
# Builds the image, starts a clean stack, runs Pytest inside the backend container
# and removes the stack (including volumes) afterwards.

set -e
set -x

docker compose build
docker compose down -v --remove-orphans # Remove possibly previous broken stacks left hanging after an error
docker compose up -d --wait backend
docker compose exec -T backend bash scripts/tests-start.sh "$@"
docker compose down -v --remove-orphans
