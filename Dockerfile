# syntax=docker/dockerfile:1.7
# Two-stage build for a small Cloud Run image (~80MB).
# Build stage installs deps with uv; runtime stage copies the virtualenv.

ARG PYTHON_VERSION=3.13-slim-bookworm

FROM python:${PYTHON_VERSION} AS builder
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Production-only deps (no dev group) into a single venv at /app/.venv
RUN uv sync --frozen --no-dev


FROM python:${PYTHON_VERSION} AS runtime
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

# Non-root user
RUN useradd --uid 1000 --create-home --shell /bin/bash app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src ./src
COPY --chown=app:app pyproject.toml ./

USER app

# Cloud Run sends $PORT; default 8080 for local docker run.
ENV PORT=8080
EXPOSE 8080

# HTTP server is the only mode useful inside a container.
# PUBLIC_URL is required (Cloud Run URL) and supplied at deploy time.
ENTRYPOINT ["clockify-mcp", "--http"]
