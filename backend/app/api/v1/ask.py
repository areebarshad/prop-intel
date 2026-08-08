"""RAG endpoint — ask a question, get a grounded answer with citations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas import AskAnswer, AskQuery

router = APIRouter(prefix="/ask", tags=["ask"])


@router.post("", response_model=AskAnswer)
async def ask_question(body: AskQuery, db: AsyncSession = Depends(get_db)) -> dict:
    """Answer a natural-language question using the PropIntel document corpus.

    Returns a grounded answer (sourced from crawled documents) plus citations
    so every claim can be traced back to a source URL and page number.

    When ``firm_id`` is provided, retrieval is scoped to that firm's documents
    only — useful for firm-specific questions like "what did Comstock announce
    this quarter?"
    """
    from app.services.rag import ask

    try:
        result = await ask(
            db,
            question=body.q,
            firm_id=body.firm_id,
            top_k=body.top_k,
            min_similarity=body.min_similarity,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"RAG service error: {exc}") from exc

    return {
        "answer": result.answer,
        "citations": [
            {
                "url": c.url,
                "page_number": c.page_number,
                "excerpt": c.excerpt,
            }
            for c in result.citations
        ],
        "query": body.q,
        "passages_found": result.passages_found,
    }
