# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# System deps for healthchecks / SSL
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md AGENTS.md CLAUDE.md DOCKER.md ./
COPY schemas ./schemas
COPY crm ./crm

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

ARG CRM_VERSION=1.0.0

ENV CRM_HOST=0.0.0.0 \
    CRM_PORT=8765 \
    CRM_DATA_DIR=/app/data \
    CRM_INBOX_DIR=/app/inbox \
    CRM_BACKUP_DIR=/app/backups \
    CRM_VERSION=${CRM_VERSION} \
    PYTHONUNBUFFERED=1

EXPOSE 8765

VOLUME ["/app/data", "/app/inbox", "/app/backups"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${CRM_PORT}/" >/dev/null || exit 1

CMD ["python", "-m", "crm", "serve", "--port", "8765"]
