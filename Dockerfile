# OrionHarness API. Build:  docker build -t orion-harness .
# Run with docker compose (see docker-compose.yml); data, KB, outputs and config are volumes, not baked in.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ORION_STATE_DB=/app/outputs/state/orion_state.db \
    ORION_AUTH_DB=/app/outputs/state/orion_auth.db \
    ORION_KB_DIR=/app/outputs/kb

WORKDIR /app

# Dependencies first so source edits do not invalidate the pip layer.
COPY pyproject.toml README.md ./
RUN mkdir -p harness backend workers sandbox repair lab kb retrieval packs \
    && for d in harness backend workers sandbox repair lab kb retrieval packs; do touch "$d/__init__.py"; done \
    && pip install -e ".[api,search,oidc]"

COPY harness harness
COPY backend backend
COPY workers workers
COPY sandbox sandbox
COPY repair repair
COPY lab lab
COPY kb kb
COPY retrieval retrieval
COPY packs packs
COPY config config

# Writable state lives on volumes owned by the unprivileged user.
RUN useradd --create-home --uid 10001 orion \
    && mkdir -p outputs/state outputs/kb lab/generated chroma_db data \
    && chown -R orion:orion /app/outputs /app/lab/generated /app/chroma_db
USER orion

EXPOSE 8000
VOLUME ["/app/outputs", "/app/lab/generated", "/app/chroma_db"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# `orion serve` refuses to start in production without ORION_ADMIN_PASSWORD (set it in .env).
CMD ["orion", "serve", "--host", "0.0.0.0", "--port", "8000"]
