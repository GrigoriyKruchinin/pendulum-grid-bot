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

RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
COPY --chown=app:app config ./config
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app

CMD ["pendulum", "--help"]
