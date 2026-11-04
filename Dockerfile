# Base images are pinned by digest (multi-arch index); the tag is kept for readability.
#
# Targets:
#   runtime (default, last stage) - deployable image: no development dependencies,
#                                   runs as an unprivileged user; includes Alembic,
#                                   scripts/prestart.sh and scripts/reset_baseline.py
#   test                          - runtime dependencies + development dependencies
#                                   and tests, for Pytest (docker build --target test)

FROM ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b AS uv

FROM python:3.14@sha256:22caa054c0d309b1eee6f448326a8821a674a75b98653247fe706db90491fc87 AS builder

ENV PYTHONUNBUFFERED=1

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=uv /uv /uvx /bin/

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy

WORKDIR /app/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# Install runtime dependencies only
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY ./scripts /app/scripts

COPY ./pyproject.toml ./uv.lock ./alembic.ini /app/

COPY ./app /app/app

# Sync the project
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# Pytest image: adds the development dependency group and the tests.
FROM builder AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

COPY ./tests /app/tests


# Deployable image: the environment built above, without uv, as an unprivileged user.
FROM python:3.14@sha256:22caa054c0d309b1eee6f448326a8821a674a75b98653247fe706db90491fc87 AS runtime

ENV PYTHONUNBUFFERED=1

WORKDIR /app/

ENV PATH="/app/.venv/bin:$PATH"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin app

# Owned by root and read-only for the application user
COPY --from=builder /app /app

USER 10001:10001

EXPOSE 8000

CMD ["bash", "scripts/start.sh"]
