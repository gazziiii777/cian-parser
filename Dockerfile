FROM python:3.12-slim-bookworm AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libzstd-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
RUN uv run --no-sync camoufox fetch

COPY cian_watch ./cian_watch
COPY alembic ./alembic
COPY alembic.ini ./
COPY config ./config
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    CIAN_HEADLESS=virtual

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        xvfb \
        libgtk-3-0 \
        libdbus-glib-1-2 \
        libasound2 \
        libx11-xcb1 \
        libxt6 \
        libxtst6 \
        libpci3 \
        libegl1 \
        libgl1 \
        fonts-liberation \
        fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/cian_watch /app/cian_watch
COPY --from=builder /app/config /app/config
COPY --from=builder /app/alembic /app/alembic
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY --from=builder /root/.cache/camoufox /root/.cache/camoufox

CMD ["cian-watch"]
