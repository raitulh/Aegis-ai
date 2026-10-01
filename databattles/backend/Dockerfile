# API and worker image (same image, different command).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app

WORKDIR /srv/api
COPY requirements.lock requirements.txt ./
RUN pip install -r requirements.lock
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN mkdir -p /data/storage && chown -R app:app /data /srv/api
USER app

ENV STORAGE_LOCAL_ROOT=/data/storage
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# Migrations run explicitly (see docker-compose `migrate` service); the API never auto-migrates.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
