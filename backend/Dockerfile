FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Install dependencies before copying source for better layer caching.
# --no-install-project installs deps listed in pyproject.toml without
# building/installing the propintel-backend package itself.
COPY backend/pyproject.toml ./pyproject.toml
RUN uv pip install --system --no-cache --no-install-project .

# Copy application source so `app` is importable from WORKDIR.
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
