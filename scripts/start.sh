#! /usr/bin/env bash
# Starts the API server. WEB_CONCURRENCY sets the number of worker processes
# (default 2); every worker has its own database pool (DB_POOL_SIZE + DB_MAX_OVERFLOW).

set -e

exec fastapi run --workers "${WEB_CONCURRENCY:-2}"
