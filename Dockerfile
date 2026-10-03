# syntax=docker/dockerfile:1.7
# MeoBot - single image, four entry points (api, bot, worker, beat).
#
# Build stages:
#   builder - resolves and installs dependencies with uv into /app/.venv
#   runtime - slim image containing only the venv, the source and tzdata
#
# Pinned by minor version rather than digest so security patches arrive with a
# rebuild; see README "Versions" for the exact resolved dependency versions.

FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml/uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Then the project itself.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

# tzdata is required: settings validate APP_TIMEZONE through zoneinfo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 meobot \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin meobot

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app

COPY --from=builder --chown=meobot:meobot /app/.venv /app/.venv
COPY --chown=meobot:meobot src/ ./src/
COPY --chown=meobot:meobot alembic/ ./alembic/
COPY --chown=meobot:meobot alembic.ini pyproject.toml README.md ./

# Celery Beat needs a writable directory for its schedule database. Creating it
# here with the right owner means the named volume inherits that ownership.
RUN mkdir -p /var/lib/meobot-beat && chown meobot:meobot /var/lib/meobot-beat

USER meobot

EXPOSE 8000

# Default entry point; compose overrides `command` for bot/worker/beat.
CMD ["uvicorn", "meobot.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
