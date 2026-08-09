.DEFAULT_GOAL := help
SHELL := /bin/bash

# Alembic runs from backend/ so it picks up alembic.ini and the app package.
ALEMBIC := cd backend && uv run alembic

.PHONY: help
help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install:  ## Sync the uv workspace with dev dependencies
	uv sync --all-packages --dev

.PHONY: up
up:  ## Start Postgres (pgvector)
	docker compose up -d db

.PHONY: down
down:  ## Stop all services
	docker compose down

.PHONY: migrate
migrate:  ## Apply migrations to head
	$(ALEMBIC) upgrade head

.PHONY: revision
revision:  ## Autogenerate a migration: make revision m="add x"
	$(ALEMBIC) revision --autogenerate -m "$(m)"

.PHONY: downgrade
downgrade:  ## Roll back one migration
	$(ALEMBIC) downgrade -1

.PHONY: seed
seed:  ## Load firms.yaml and sources.yaml into the database
	uv run propintel-ingest seed

.PHONY: ingest
ingest:  ## Run a full ingest pass over active sources
	uv run propintel-ingest crawl-firms
	uv run propintel-ingest permits
	uv run propintel-ingest zoning
	uv run propintel-ingest careers
	uv run propintel-ingest enrich-permits

.PHONY: embed
embed:  ## Embed chunks and firm cards
	uv run propintel-ingest embed

.PHONY: detect
detect:  ## Run trend and anomaly detection
	uv run propintel-ingest detect --window 90d

.PHONY: api
api:  ## Run the FastAPI dev server
	cd backend && uv run uvicorn app.main:app --reload

.PHONY: test
test:  ## Run the Python test suite
	uv run pytest

.PHONY: lint
lint:  ## Lint and type-check
	uv run ruff check
	uv run ruff format --check
	uv run mypy

.PHONY: fmt
fmt:  ## Auto-format
	uv run ruff format
	uv run ruff check --fix

.PHONY: check
check: lint test  ## Everything CI runs
