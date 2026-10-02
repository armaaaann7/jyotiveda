# syntax=docker/dockerfile:1.7
# One image, many roles (JYOTIVEDA_ROLE=gateway|twin|forecast|reliability|dispatch|copilot|control-loop|all)
# Build the AI-heavy variant with:  docker build --build-arg EXTRAS=ml -t jyotiveda:ml .

ARG PYTHON_VERSION=3.12
FROM ghcr.io/astral-sh/uv:0.5 AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS build
ARG EXTRAS=""
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libgomp1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install ".${EXTRAS:+[$EXTRAS]}"

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
LABEL org.opencontainers.image.title="jyotiveda" \
      org.opencontainers.image.description="Transformer-scale renewable reliability OS" \
      org.opencontainers.image.source="https://github.com/jyotiveda/jyotiveda" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 tini curl && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 10001 jyoti && useradd -u 10001 -g jyoti -s /usr/sbin/nologin -M jyoti
COPY --from=build /opt/venv /opt/venv
COPY migrations /app/migrations
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    JYOTIVEDA_LOG_JSON=true JYOTIVEDA_DATABASE_URL=sqlite+aiosqlite:////tmp/jyotiveda.db
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["jyotiveda", "serve", "--host", "0.0.0.0", "--port", "8000"]
