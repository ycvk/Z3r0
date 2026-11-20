# syntax=docker/dockerfile:1

ARG NODE_VERSION=24.18.0
ARG PYTHON_VERSION=3.13.5
ARG UV_VERSION=0.13.7

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin

FROM node:${NODE_VERSION}-alpine AS web-builder

WORKDIR /app/web

COPY web/package*.json ./
RUN npm ci

COPY web/ ./
RUN npm run build


FROM python:${PYTHON_VERSION}-slim AS backend

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PYTHON_DOWNLOADS=never
ENV PATH="/app/.venv/bin:$PATH"
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken

WORKDIR /app

COPY --from=uv-bin /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-cache
# LightRAG and context compaction count tokens with tiktoken, which downloads an encoding the first
# time it is used. Fetching every encoding here lets the application start without that download.
RUN python -c "import tiktoken; [tiktoken.get_encoding(name) for name in tiktoken.list_encoding_names()]"

COPY app.py config.py database.py logger.py main.py ./
COPY core ./core
COPY handler ./handler
COPY middleware ./middleware
COPY migrations ./migrations
COPY model ./model
COPY router ./router
COPY schema ./schema
COPY service ./service
COPY tools ./tools
COPY utils ./utils
COPY --from=web-builder /app/web/dist-app ./web/dist-app

EXPOSE 8000

CMD ["python", "main.py"]
