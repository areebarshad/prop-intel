"""The migration chain must reproduce the model metadata exactly.

Runs entirely offline against the Postgres dialect — no database required, so it
runs in CI on every push. This catches the failure that otherwise shows up in
production: a model gains a column, nobody generates a migration, and the app
queries a column the deployed database doesn't have.
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_mock_engine

from app.models import Base

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Indexes the migrations add by hand because SQLAlchemy metadata cannot express
# them: pgvector HNSW (operator class + build parameters) and pg_trgm GIN.
HAND_WRITTEN_INDEX_MARKERS = ("hnsw", "gin_trgm_ops")


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas that aren't inside parentheses.

    Naive splitting breaks on types like NUMERIC(14, 2) and constraints like
    CHECK (lat BETWEEN -90 AND 90).
    """
    parts: list[str] = []
    depth = 0
    current = ""
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return [p for p in parts if p]


def _parse(sql: str) -> tuple[dict[str, list[str]], set[str]]:
    """Reduce raw DDL to {table: sorted clauses} and a set of index statements.

    Clauses are sorted because SQLAlchemy and Alembic emit constraints in
    different orders for the same schema; ordering is not semantic.
    """
    tables: dict[str, list[str]] = {}
    indexes: set[str] = set()

    for raw in sql.split(";"):
        statement = re.sub(r"\s+", " ", raw.strip())
        if not statement:
            continue

        table_match = re.match(r"CREATE TABLE (\w+) \((.*)\)$", statement)
        if table_match:
            name = table_match.group(1)
            if name == "alembic_version":  # Alembic's own bookkeeping
                continue
            tables[name] = sorted(_split_top_level(table_match.group(2)))
            continue

        if re.match(r"CREATE (?:UNIQUE )?INDEX ", statement):
            indexes.add(statement)

    return tables, indexes


@pytest.fixture(scope="module")
def model_ddl() -> tuple[dict[str, list[str]], set[str]]:
    """DDL SQLAlchemy would emit straight from the models."""
    statements: list[str] = []

    def capture(sql, *args, **kwargs):  # type: ignore[no-untyped-def]
        statements.append(str(sql.compile(dialect=engine.dialect)))

    engine = create_mock_engine("postgresql://", capture)
    Base.metadata.create_all(engine, checkfirst=False)
    return _parse(";\n".join(statements))


def _render_migration_sql() -> str:
    """Run the migration chain in offline mode and capture the emitted SQL.

    Alembic writes offline output to ``Config.output_buffer``; assigning to
    ``stdout`` silently produces nothing.
    """
    buffer = StringIO()
    config = Config(str(BACKEND_ROOT / "alembic.ini"), stdout=buffer)
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.output_buffer = buffer
    command.upgrade(config, "head", sql=True)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def migration_sql() -> str:
    return _render_migration_sql()


@pytest.fixture(scope="module")
def migration_ddl(migration_sql: str) -> tuple[dict[str, list[str]], set[str]]:
    """DDL the migration chain emits, rendered in Alembic's offline mode."""
    return _parse(migration_sql)


def test_same_tables(
    model_ddl: tuple[dict[str, list[str]], set[str]],
    migration_ddl: tuple[dict[str, list[str]], set[str]],
) -> None:
    assert set(model_ddl[0]) == set(migration_ddl[0])


@pytest.mark.parametrize("table", sorted(Base.metadata.tables))
def test_table_definitions_match(
    table: str,
    model_ddl: tuple[dict[str, list[str]], set[str]],
    migration_ddl: tuple[dict[str, list[str]], set[str]],
) -> None:
    """Every column, type, nullability, and constraint agrees."""
    assert model_ddl[0][table] == migration_ddl[0][table]


def test_no_model_index_is_missing_from_the_migration(
    model_ddl: tuple[dict[str, list[str]], set[str]],
    migration_ddl: tuple[dict[str, list[str]], set[str]],
) -> None:
    assert not model_ddl[1] - migration_ddl[1]


def test_extra_migration_indexes_are_only_the_hand_written_ones(
    model_ddl: tuple[dict[str, list[str]], set[str]],
    migration_ddl: tuple[dict[str, list[str]], set[str]],
) -> None:
    extra = migration_ddl[1] - model_ddl[1]
    unexplained = [
        index
        for index in extra
        if not any(marker in index for marker in HAND_WRITTEN_INDEX_MARKERS)
    ]
    assert not unexplained


def test_every_hand_written_index_is_excluded_from_autogenerate(
    model_ddl: tuple[dict[str, list[str]], set[str]],
    migration_ddl: tuple[dict[str, list[str]], set[str]],
) -> None:
    """Indexes not in the metadata must be filtered out of autogenerate.

    Otherwise `alembic revision --autogenerate` emits DROP INDEX for them —
    quietly deleting the pgvector and pg_trgm indexes that semantic search and
    entity resolution depend on, with no error at any point.
    """
    from app.db.index_policy import HAND_WRITTEN_INDEX_SUFFIXES, include_object

    for statement in migration_ddl[1] - model_ddl[1]:
        name = statement.split()[2]
        assert name.endswith(HAND_WRITTEN_INDEX_SUFFIXES), (
            f"{name} is created by a migration but not covered by "
            f"env.py's HAND_WRITTEN_INDEX_SUFFIXES {HAND_WRITTEN_INDEX_SUFFIXES}"
        )
        assert not include_object(None, name, "index", True, None), (
            f"{name} would be proposed for deletion by autogenerate"
        )


def test_vector_columns_have_an_hnsw_index(
    migration_ddl: tuple[dict[str, list[str]], set[str]],
) -> None:
    """An embedding column without an index means sequential scans at query time.

    The symptom is a search endpoint that works fine on seed data and times out
    once the corpus is real, so assert it at build time instead.
    """
    vector_columns = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if type(column.type).__name__.upper().startswith("VECTOR")
    }
    assert vector_columns, "expected at least one VECTOR column"

    for table_name, column_name in vector_columns:
        assert any(
            "hnsw" in index and f"ON {table_name}" in index and column_name in index
            for index in migration_ddl[1]
        ), f"{table_name}.{column_name} has no HNSW index"


def test_pgvector_extension_precedes_vector_columns(migration_sql: str) -> None:
    """CREATE EXTENSION vector must come before the first VECTOR column.

    Postgres rejects the column type outright if the extension is not installed,
    so this ordering is what makes a migration on a fresh database succeed.
    """
    sql = migration_sql.lower()

    extension_at = sql.find("create extension if not exists vector")
    first_vector_at = sql.find("vector(")
    assert extension_at != -1, "migration never creates the vector extension"
    assert first_vector_at != -1, "migration has no VECTOR column"
    assert extension_at < first_vector_at
