# propintel-backend

FastAPI application, SQLAlchemy models, and the intelligence layer (embeddings,
RAG, entity resolution, trend and anomaly detection) for PropIntel.

This package owns the database schema. `propintel-ingest` depends on it so both
sides share one set of models, while the scraper's heavier dependencies
(Playwright, PyMuPDF) stay out of the API container image.

```bash
uv run alembic upgrade head          # from this directory
uv run uvicorn app.main:app --reload
```
