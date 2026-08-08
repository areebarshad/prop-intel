"""Semantic and keyword firm search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.firm import Firm
from app.schemas import SearchQuery, SearchResult

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResult)
async def search_firms(body: SearchQuery, db: AsyncSession = Depends(get_db)) -> dict:
    """Search firms by keyword or semantic similarity.

    If embeddings are populated (``firms.embedding IS NOT NULL``), runs a
    vector cosine-similarity search and re-ranks by score. Falls back to a
    trigram ILIKE keyword search when no embeddings exist.
    """
    # Check if any firm embeddings exist
    has_embeddings = bool(
        await db.scalar(
            select(func.count()).select_from(Firm).where(Firm.embedding.is_not(None))
        )
    )

    base_filter = [Firm.is_active.is_(True)]
    if body.asset_classes:
        base_filter.append(Firm.asset_classes.any_([body.asset_classes]))  # type: ignore[attr-defined]
    if body.localities:
        base_filter.append(Firm.localities.any_([body.localities]))  # type: ignore[attr-defined]

    if has_embeddings:
        from app.services.embedding import _embed

        try:
            vector = _embed([body.q])[0]
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"embedding unavailable: {exc}") from exc

        # pgvector cosine distance (<=>); lower = more similar
        query = (
            select(Firm)
            .where(*base_filter)
            .order_by(Firm.embedding.cosine_distance(vector))  # type: ignore[attr-defined]
            .limit(body.limit)
        )
        firms = list((await db.execute(query)).scalars())
        return {"firms": firms, "query": body.q, "semantic": True}

    # Fallback: trigram keyword search
    keyword = f"%{body.q}%"
    query = (
        select(Firm)
        .where(
            Firm.is_active.is_(True),
            Firm.name.ilike(keyword),
        )
        .order_by(Firm.name)
        .limit(body.limit)
    )
    firms = list((await db.execute(query)).scalars())
    return {"firms": firms, "query": body.q, "semantic": False}
