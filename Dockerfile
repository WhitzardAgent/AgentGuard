# AgentGuard runtime image — multi-stage, single binary surface.

# ─── Stage 1: build the wheel & dependencies into a venv ───
FROM python:3.11 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/agentguard

COPY pyproject.toml README.md README_CN.md ./
COPY agentguard ./agentguard

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install ".[server,redis,postgres,dynamic]"


# ─── Stage 2: lean runtime ───
FROM python:3.11 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    AGENTGUARD_HOST=0.0.0.0 \
    AGENTGUARD_PORT=38080

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system agentguard \
 && useradd --system --gid agentguard --home /home/agentguard --create-home agentguard

WORKDIR /opt/agentguard

COPY --from=builder /opt/venv /opt/venv
COPY agentguard ./agentguard
COPY rules ./rules
COPY frontend ./frontend
COPY scripts ./scripts

RUN chown -R agentguard:agentguard /opt/agentguard

USER agentguard

EXPOSE 38080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${AGENTGUARD_PORT}/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/agentguard/scripts/entrypoint.sh"]
CMD ["serve"]
