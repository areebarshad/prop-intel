"""Tests for the RAG service — no live DB or LLM needed."""

from __future__ import annotations

from app.services.rag import (
    DEFAULT_MIN_SIMILARITY,
    DEFAULT_TOP_K,
    MAX_CONTEXT_CHARS,
    Citation,
    RagAnswer,
)


def test_citation_fields() -> None:
    c = Citation(url="https://example.com", page_number=3, excerpt="test")
    assert c.url == "https://example.com"
    assert c.page_number == 3
    assert c.excerpt == "test"


def test_citation_nullable_fields() -> None:
    c = Citation(url=None, page_number=None, excerpt="x")
    assert c.url is None
    assert c.page_number is None


def test_rag_answer_defaults() -> None:
    a = RagAnswer(answer="hello")
    assert a.citations == []
    assert a.passages_found == 0


def test_rag_answer_with_citations() -> None:
    citations = [Citation(url=None, page_number=1, excerpt="excerpt")]
    a = RagAnswer(answer="answer", citations=citations, passages_found=1)
    assert len(a.citations) == 1
    assert a.passages_found == 1


def test_constants() -> None:
    assert DEFAULT_MIN_SIMILARITY == 0.35
    assert DEFAULT_TOP_K == 8
    assert MAX_CONTEXT_CHARS == 12_000


def test_distance_floor() -> None:
    # 1 - 0.35 = 0.65; ensure formula is correct
    max_dist = 1.0 - DEFAULT_MIN_SIMILARITY
    assert abs(max_dist - 0.65) < 1e-9
