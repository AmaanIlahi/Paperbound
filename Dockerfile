# Paperbound: demo mode always, live uploads when LIVE_UPLOADS_ENABLED=true.
# Not built or deployed from this repo's CI; see MORNING_REPORT_5.md for the fly commands.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# PyMuPDF ships manylinux wheels with MuPDF bundled; these two shared libraries are the only extras it can ask for on slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-prod.txt .
RUN pip install -r requirements-prod.txt

COPY alembic.ini .
COPY paperbound ./paperbound
COPY web ./web
COPY fixtures/demo ./fixtures/demo

# A local .env is never copied (.dockerignore) and never read: configuration comes from fly.toml and `fly secrets`.
# STORAGE_DIR is where live uploads go; mount a Fly volume at /data if live uploads are on.
ENV PORT=8080 \
    STORAGE_DIR=/data/storage \
    PAPERBOUND_SKIP_DOTENV=1
RUN mkdir -p /data/storage

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn paperbound.api.main:app --host 0.0.0.0 --port ${PORT}"]
