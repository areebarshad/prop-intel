"""Tests for the embedding pipeline (Phase 2, Step 3).

No live model or database needed — tests cover chunking logic only.
sentence-transformers is not imported unless explicitly tested.
"""

from __future__ import annotations

from app.services.embedding import _rough_token_count, chunk_document, chunk_text


def test_rough_token_count_basic() -> None:
    text = "hello world foo bar baz"
    count = _rough_token_count(text)
    assert count > 0


def test_chunk_text_short_doc_single_chunk() -> None:
    text = " ".join(["word"] * 50)
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0]["text"] == text


def test_chunk_text_long_doc_multiple_chunks() -> None:
    # 2000 words should produce multiple chunks at default 450-token target
    text = " ".join([f"word{i}" for i in range(2000)])
    chunks = chunk_text(text)
    assert len(chunks) > 1


def test_chunk_text_overlap() -> None:
    text = " ".join([f"w{i}" for i in range(1000)])
    chunks = chunk_text(text)
    # Consecutive chunks should share words (overlap)
    if len(chunks) >= 2:
        words_a = set(str(chunks[0]["text"]).split())
        words_b = set(str(chunks[1]["text"]).split())
        assert words_a & words_b, "consecutive chunks should overlap"


def test_chunk_text_page_number_propagated() -> None:
    text = "Some page text here"
    chunks = chunk_text(text, page_number=3)
    assert all(c["page_number"] == 3 for c in chunks)


def test_chunk_text_no_page_number() -> None:
    text = "Some text"
    chunks = chunk_text(text)
    assert all(c["page_number"] is None for c in chunks)


def test_chunk_document_plain_html() -> None:
    from unittest.mock import MagicMock

    doc = MagicMock()
    doc.cleaned_text = "Plain HTML content without page markers. " * 10
    doc.page_count = None

    chunks = chunk_document(doc)
    assert len(chunks) >= 1
    assert all(c["page_number"] is None for c in chunks)


def test_chunk_document_pdf_with_page_markers() -> None:
    from unittest.mock import MagicMock

    doc = MagicMock()
    doc.cleaned_text = (
        "First page content here.\n\n"
        "--- Page 2 ---\n\n"
        "Second page content here.\n\n"
        "--- Page 3 ---\n\n"
        "Third page content here."
    )
    doc.page_count = 3

    chunks = chunk_document(doc)
    assert len(chunks) >= 3
    # Each chunk should have a page number
    page_numbers = {c.get("page_number") for c in chunks}
    assert page_numbers - {None}  # at least some are non-None


def test_chunk_document_empty() -> None:
    from unittest.mock import MagicMock

    doc = MagicMock()
    doc.cleaned_text = ""
    assert chunk_document(doc) == []


def test_chunk_document_whitespace_only() -> None:
    from unittest.mock import MagicMock

    doc = MagicMock()
    doc.cleaned_text = "   \n\n\t  "
    assert chunk_document(doc) == []
