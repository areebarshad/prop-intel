FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Install Python dependencies (cached layer — only rebuilds when pyproject.toml changes).
# --no-install-project skips building the package itself; source is mounted below.
COPY backend/pyproject.toml ./pyproject.toml
RUN uv pip install --system --no-install-project .

# Pre-download the fastembed ONNX model during build so containers start without
# needing outbound network access and with no cold-start latency on first embed.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')"

# Copy application source so `app` is importable from WORKDIR.
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini

EXPOSE 8000

# Render sets PORT; default to 8000 for local docker run.
# Run alembic migrations before starting so the schema is always current.
CMD sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"
