"""Which indexes Alembic autogenerate is allowed to manage.

Some indexes cannot be expressed in SQLAlchemy metadata — a pgvector HNSW index
carries an operator class and build parameters, a pg_trgm index carries an
operator class — so they live only in hand-written migrations. Autogenerate sees
them in the database, finds no matching metadata, and proposes DROP INDEX.

That failure is silent and expensive: the next routine `alembic revision
--autogenerate` would quietly delete the indexes semantic search and entity
resolution depend on, and the only symptom would be queries getting slow.

Lives here rather than in `alembic/env.py` so tests can import it — env.py is a
script Alembic executes, not an importable module.
"""

from __future__ import annotations

HAND_WRITTEN_INDEX_SUFFIXES: tuple[str, ...] = ("_hnsw", "_trgm")


def is_hand_written_index(name: str | None) -> bool:
    return bool(name) and name.endswith(HAND_WRITTEN_INDEX_SUFFIXES)  # type: ignore[union-attr]


def include_object(obj: object, name: str | None, type_: str, *_: object) -> bool:
    """Alembic `include_object` hook: skip indexes migrations own by hand."""
    if type_ == "index" and is_hand_written_index(name):
        return False
    return True
