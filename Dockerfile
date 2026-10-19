# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md alembic.ini ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

# UID/GID 1000 matches the usual host user, so bind-mounted report folders stay writable.
ARG APP_UID=1000
ARG APP_GID=1000
ARG GIT_SHA=unknown
RUN groupadd --gid "${APP_GID}" app && useradd --uid "${APP_UID}" --gid app --home /app --no-create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
COPY --chown=app:app config ./config
ENV PATH="/app/.venv/bin:$PATH" \
    GIT_SHA="${GIT_SHA}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app

CMD ["pendulum", "--help"]
