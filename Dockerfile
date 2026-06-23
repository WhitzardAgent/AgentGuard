# AgentGuard server/runtime image. The server image only carries server + shared
# source; client code is not required for backend imports.
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AGENTGUARD_HOST=0.0.0.0 \
    AGENTGUARD_PORT=38080 \
    PYTHONPATH="/opt/agentguard/src:/opt/agentguard/src/server:/opt/agentguard"

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/agentguard

# Dependencies first for better layer caching.
COPY pyproject.toml README.md ./
RUN pip install "pydantic>=2.5,<3.0" "fastapi>=0.110" "uvicorn>=0.27"

# Server source + shared source (PYTHONPATH layout, no editable install needed).
COPY src/server ./src/server
COPY src/shared ./src/shared
COPY config ./config
COPY rules ./rules
COPY config ./config
COPY plugins ./plugins
COPY scripts ./scripts

RUN chmod +x scripts/*.sh 2>/dev/null || true

EXPOSE 38080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${AGENTGUARD_PORT}/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/agentguard/scripts/entrypoint.sh"]
CMD ["serve"]
