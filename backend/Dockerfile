FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Copy full backend source. uv pip install --system . installs the package
# and all its dependencies in one step.
COPY backend/ .

RUN uv pip install --system .

# Pre-download the fastembed ONNX model during build so containers start without
# needing outbound network access and with no cold-start latency on first embed.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2')"

EXPOSE 8000

# Render sets PORT; defaults to 8000 for local docker run.
# Migrations run before uvicorn so the schema is always current on deploy.
CMD sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"
