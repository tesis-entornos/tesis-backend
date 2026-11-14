#! /usr/bin/env bash

set -e
set -x

# Run migrations
# If the image carries app/alembic/objetivo (one line: the target revision),
# upgrade to exactly that revision; otherwise upgrade head, as in the baseline.
# Never "heads": it would record the empty revisions of other branches as applied.
OBJETIVO_FILE="app/alembic/objetivo"
if [ -f "$OBJETIVO_FILE" ]; then
    OBJETIVO="$(tr -d '[:space:]' < "$OBJETIVO_FILE")"
    if [ -z "$OBJETIVO" ]; then
        echo "ERROR - $OBJETIVO_FILE existe pero esta vacio; no se aplica ninguna migracion" >&2
        exit 1
    fi
    echo "migracion_objetivo=$OBJETIVO"
    alembic upgrade "$OBJETIVO"
else
    echo "migracion_objetivo=head"
    alembic upgrade head
fi

# Create initial data in DB
python app/initial_data.py
