"""Semantic and keyword firm search."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.firm import Firm
from app.schemas import SearchQuery, SearchResult

router = APIRouter(prefix="/search", tags=["search"])

# Module-level cache so the embeddings count is not re-queried on every request.
# Refreshed on the next request after the TTL, not on a timer.
_embeddings_cached: bool | None = None
_embeddings_cache_time: float = 0.0
_EMBEDDINGS_CACHE_TTL = 300.0  # 5 minutes


async def _has_firm_embeddings(db: AsyncSession) -> bool:
    import time

    global _embeddings_cached, _embeddings_cache_time
    now = time.monotonic()
    if _embeddings_cached is None or (now - _embeddings_cache_time) > _EMBEDDINGS_CACHE_TTL:
        _embeddings_cached = bool(
            await db.scalar(
                select(func.count()).select_from(Firm).where(Firm.embedding.is_not(None))
            )
        )
        _embeddings_cache_time = now
    return _embeddings_cached


@router.post("", response_model=SearchResult)
async def search_firms(body: SearchQuery, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Search firms by keyword or semantic similarity.

    If embeddings are populated (``firms.embedding IS NOT NULL``), runs a
    vector cosine-similarity search and re-ranks by score. Falls back to a
    trigram ILIKE keyword search when no embeddings exist.
    """
    has_embeddings = await _has_firm_embeddings(db)

    base_filter: list[ColumnElement[bool]] = [Firm.is_active.is_(True)]
    if body.asset_classes:
        from sqlalchemy import or_

        base_filter.append(or_(*[Firm.asset_classes.any(ac) for ac in body.asset_classes]))  # type: ignore[arg-type]
    if body.localities:
        from sqlalchemy import or_

        base_filter.append(or_(*[Firm.localities.any(loc) for loc in body.localities]))  # type: ignore[arg-type]

    if has_embeddings:
        from app.services.embedding import async_embed

        try:
            vector = (await async_embed([body.q]))[0]
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"embedding unavailable: {exc}") from exc

        # pgvector cosine distance (<=>); lower = more similar (0=identical, 2=opposite)
        # Drop results with distance > 0.5 (cosine similarity < 0.5) as irrelevant.
        _SIMILARITY_DISTANCE_THRESHOLD = 0.5
        dist_expr = Firm.embedding.cosine_distance(vector)
        query = (
            select(Firm)
            .where(*base_filter, dist_expr <= _SIMILARITY_DISTANCE_THRESHOLD)
            .order_by(dist_expr)
            .limit(body.limit)
        )
        firms = list((await db.execute(query)).scalars())
        return {"firms": firms, "query": body.q, "semantic": True}

    # Fallback: PostgreSQL trigram similarity — uses ix_firms_canonical_name_trgm
    # index rather than a sequential ILIKE scan.
    similarity_fn = func.similarity(Firm.name, body.q)
    query = (
        select(Firm)
        .where(
            *base_filter,
            similarity_fn > 0.1,
        )
        .order_by(similarity_fn.desc())
        .limit(body.limit)
    )
    firms = list((await db.execute(query)).scalars())
    return {"firms": firms, "query": body.q, "semantic": False}
