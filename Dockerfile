# MASSHINE — FastAPI backend + static v4 frontend, single container.
# Build context is the repo root: engine/, web/, and packs/ are siblings there and
# masshine/config.py + masshine/packs.py locate each other by that relative layout.
FROM python:3.12-slim

WORKDIR /app

# Install deps first (separate layer — only rebuilds when requirements.txt changes).
COPY engine/requirements.txt engine/requirements.txt
RUN pip install --no-cache-dir -r engine/requirements.txt

COPY engine/ engine/
COPY web/ web/
COPY packs/ packs/

# Fallback mount point for project data. In production set MASSHINE_DATA_DIR to a path backed
# by a persistent volume (e.g. Coolify storage mounted at /data) — otherwise coded projects are
# lost on every redeploy.
RUN mkdir -p /data
ENV PYTHONUNBUFFERED=1 \
    MASSHINE_DATA_DIR=/data \
    PORT=8760

EXPOSE 8760

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8760\")}/health', timeout=3)" || exit 1

CMD ["sh", "-c", "uvicorn app:app --app-dir engine --host 0.0.0.0 --port ${PORT:-8760}"]
