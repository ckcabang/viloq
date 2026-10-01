# syntax=docker/dockerfile:1

# --- Frontend -----------------------------------------------------------------
# The frontend is no-build ES modules, so "building" it means checking that every
# module parses and assembling the files the server will serve. A syntax error
# fails the image build instead of the user's browser.
FROM node:24-alpine AS frontend

WORKDIR /src
COPY frontend/ ./

RUN find js -name '*.js' -print0 | xargs -0 -n1 node --check \
 && mkdir -p /dist \
 && cp -r index.html styles.css js /dist/

# --- Backend ------------------------------------------------------------------
FROM python:3.14-slim AS app

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so editing the code does not reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY backend/ ./backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

COPY --from=frontend /dist ./frontend/

RUN useradd --system viloq

# The database is Postgres, named by VILOQ_DATABASE_URL at run time (see
# compose.yaml); the image holds no data of its own.
ENV PATH="/app/.venv/bin:$PATH" \
    VILOQ_FRONTEND_DIR=/app/frontend \
    VILOQ_CORS_ORIGINS=""

USER viloq
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
