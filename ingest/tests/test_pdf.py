"""PDF ingestion.

Fixtures are synthesized with PyMuPDF rather than checked in, so the tests run
anywhere and the input that produces each assertion is visible in the test.
"""

from __future__ import annotations

import pymupdf
import pytest

from ingest.documents.pdf import (
    MIN_CHARS_PER_PAGE,
    PdfTable,
    _join_spans,
    convert_pdf_to_markdown,
    extract_pages,
    extract_pdf_text,
    split_by_page_marker,
)


def _build_pdf(pages: list[list[tuple[str, float, float, float, bool]]]) -> bytes:
    """Render pages of (text, x, y, size, bold) into an in-memory PDF."""
    doc = pymupdf.open()
    for spans in pages:
        page = doc.new_page()
        for text, x, y, size, bold in spans:
            page.insert_text(
                (x, y),
                text,
                fontsize=size,
                fontname="hebo" if bold else "helv",
            )
    data: bytes = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def minutes_pdf() -> bytes:
    """Two pages shaped like planning-commission minutes."""
    return _build_pdf(
        [
            [
                ("Henrico County Planning Commission", 72, 90, 18.0, True),
                ("Regular Meeting Minutes", 72, 120, 10.0, False),
                ("Case REZ2026-00014 was approved for 240 units.", 72, 150, 10.0, False),
            ],
            [
                ("Old Business", 72, 90, 15.0, True),
                ("The applicant requested a deferral to the next session.", 72, 120, 10.0, False),
            ],
        ]
    )


@pytest.fixture
def scanned_pdf() -> bytes:
    """Pages with no meaningful text layer, as a scan would produce."""
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_extracts_every_page_with_its_number(minutes_pdf: bytes) -> None:
    document = extract_pages(minutes_pdf)

    assert document.page_count == 2
    assert [page.number for page in document.pages] == [1, 2]


def test_page_numbers_are_one_indexed_to_match_the_printed_page(
    minutes_pdf: bytes,
) -> None:
    """A citation reading "p. 0" would be indefensible to a user."""
    document = extract_pages(minutes_pdf)

    assert document.pages[0].number == 1


def test_content_stays_on_its_own_page(minutes_pdf: bytes) -> None:
    """The property that makes page-level citation possible at all."""
    document = extract_pages(minutes_pdf)

    assert "REZ2026-00014" in document.pages[0].text
    assert "REZ2026-00014" not in document.pages[1].text
    assert "deferral" in document.pages[1].text


def test_larger_bold_type_becomes_a_heading(minutes_pdf: bytes) -> None:
    markdown = extract_pages(minutes_pdf).to_markdown()

    assert "# Henrico County Planning Commission" in markdown


def test_body_text_does_not_become_a_heading(minutes_pdf: bytes) -> None:
    markdown = extract_pages(minutes_pdf).to_markdown()

    body_lines = [line for line in markdown.splitlines() if "was approved for" in line]
    assert body_lines
    assert not any(line.lstrip().startswith("#") for line in body_lines)


def test_uppercase_lines_are_not_treated_as_headings() -> None:
    """The notebook's isupper() rule would turn case numbers into headings.

    Municipal documents are full of uppercase identifiers in body text; the
    font-size rule has to ignore them.
    """
    pdf = _build_pdf(
        [
            [
                ("Meeting Agenda", 72, 90, 16.0, True),
                ("CASE REZ2026-00014 CONTINUED TO MARCH", 72, 130, 10.0, False),
            ]
        ]
    )

    markdown = extract_pages(pdf).to_markdown()

    heading_lines = [ln for ln in markdown.splitlines() if ln.lstrip().startswith("#")]
    assert not any("REZ2026-00014" in line for line in heading_lines)


def _span(text: str, left: float, right: float, size: float = 11.0) -> dict[str, object]:
    return {"text": text, "bbox": (left, 0.0, right, size), "size": size, "flags": 0}


class TestSpanJoining:
    """PDFs encode some spacing as position rather than whitespace.

    Two spans drawn apart with no space character in either concatenate to
    "ComstockHolding", which then gets chunked and embedded — making the firm
    name unsearchable. Tested at the helper because whether a PDF writer emits
    one line with two spans or two lines is not something a fixture controls.
    """

    def test_a_positional_gap_becomes_a_space(self) -> None:
        spans = [_span("Comstock", 72, 120), _span("Holding", 140, 190)]

        assert _join_spans(spans) == "Comstock Holding"

    def test_adjacent_spans_are_not_separated(self) -> None:
        """Kerning between glyphs of one word must not insert a space."""
        spans = [_span("Com", 72, 100), _span("stock", 101, 130)]

        assert _join_spans(spans) == "Comstock"

    def test_an_existing_space_is_not_doubled(self) -> None:
        spans = [_span("Comstock ", 72, 120), _span("Holding", 140, 190)]

        assert _join_spans(spans) == "Comstock Holding"

    def test_leading_space_on_the_next_span_is_respected(self) -> None:
        spans = [_span("Comstock", 72, 120), _span(" Holding", 140, 190)]

        assert _join_spans(spans) == "Comstock Holding"

    def test_empty_spans_are_skipped(self) -> None:
        spans = [_span("Comstock", 72, 120), _span("", 130, 130), _span("Holding", 140, 190)]

        assert _join_spans(spans) == "Comstock Holding"

    def test_no_spans_yields_empty_string(self) -> None:
        assert _join_spans([]) == ""


def test_markdown_loses_no_words_from_the_raw_text(minutes_pdf: bytes) -> None:
    import re

    document = extract_pages(minutes_pdf)
    words = lambda text: set(re.findall(r"[A-Za-z0-9]+", text.lower()))  # noqa: E731

    assert not words(document.text) - words(document.to_markdown())


def test_scanned_pdf_is_flagged_rather_than_ingested_empty(scanned_pdf: bytes) -> None:
    """A locality that switches to scanned agendas must not look inactive."""
    document = extract_pages(scanned_pdf)

    assert document.is_scanned
    assert not any(page.has_text for page in document.pages)


def test_a_text_pdf_is_not_flagged_as_scanned(minutes_pdf: bytes) -> None:
    assert not extract_pages(minutes_pdf).is_scanned


def test_page_with_trivial_text_counts_as_having_none() -> None:
    pdf = _build_pdf([[("ok", 72, 100, 10.0, False)]])

    page = extract_pages(pdf).pages[0]

    assert page.char_count < MIN_CHARS_PER_PAGE
    assert not page.has_text


def test_page_markers_round_trip_to_page_numbers(minutes_pdf: bytes) -> None:
    """The chunker relies on this to attach a page number to every chunk."""
    markdown = extract_pages(minutes_pdf).to_markdown()

    sections = split_by_page_marker(markdown)

    assert [number for number, _ in sections] == [1, 2]
    assert "REZ2026-00014" in dict(sections)[1]


def test_split_by_page_marker_handles_markdown_without_markers() -> None:
    assert split_by_page_marker("plain text") == [(1, "plain text")]


def test_split_by_page_marker_handles_empty_input() -> None:
    assert split_by_page_marker("   ") == []


def test_markers_can_be_suppressed(minutes_pdf: bytes) -> None:
    markdown = convert_pdf_to_markdown(minutes_pdf, include_page_markers=False)

    assert "<!-- page:" not in markdown


def test_max_pages_bounds_the_work(minutes_pdf: bytes) -> None:
    assert extract_pages(minutes_pdf, max_pages=1).page_count == 1


def test_accepts_bytes_so_fetched_pdfs_need_no_temp_file(minutes_pdf: bytes) -> None:
    assert extract_pages(minutes_pdf).page_count == 2


def test_accepts_a_path(minutes_pdf: bytes, tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "minutes.pdf"
    path.write_bytes(minutes_pdf)

    assert extract_pages(path).page_count == 2


def test_extract_pdf_text_keeps_the_notebooks_flat_output(minutes_pdf: bytes, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The original notebook entry point still works for existing callers."""
    path = tmp_path / "minutes.pdf"
    path.write_bytes(minutes_pdf)

    text = extract_pdf_text(path)

    assert "REZ2026-00014" in text
    assert "deferral" in text


class TestPdfTable:
    def test_renders_a_pipe_table(self) -> None:
        table = PdfTable(
            page_number=3,
            rows=[["Permit", "Units"], ["B-2026-001", "240"]],
        )

        markdown = table.to_markdown()

        assert markdown.splitlines()[0] == "| Permit | Units |"
        assert markdown.splitlines()[2] == "| B-2026-001 | 240 |"

    def test_escapes_pipes_so_a_cell_cannot_break_the_table(self) -> None:
        table = PdfTable(page_number=1, rows=[["a|b", "c"], ["d", "e"]])

        assert r"a\|b" in table.to_markdown()

    def test_pads_short_rows_to_the_header_width(self) -> None:
        table = PdfTable(page_number=1, rows=[["a", "b", "c"], ["d"]])

        assert table.to_markdown().splitlines()[2] == "| d |  |  |"

    def test_single_column_strips_are_rejected_as_layout_artifacts(self) -> None:
        table = PdfTable(page_number=1, rows=[["only"], ["one"]])

        assert not table.is_meaningful
        assert table.to_markdown() == ""

    def test_single_row_is_rejected(self) -> None:
        assert not PdfTable(page_number=1, rows=[["a", "b"]]).is_meaningful
