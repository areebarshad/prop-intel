"""PDF ingestion.

Ported from the author's `pdf_to_md_fixed.ipynb`, which established the two
things worth keeping: the PyMuPDF extraction loop, and the import guard for the
`fitz` name collision (an unrelated PyPI package also publishes `fitz`, and when
it shadows PyMuPDF the failure is a confusing missing-attribute error rather
than an ImportError).

What changed, and why:

* **Page numbers are retained.** The notebook joined all pages into one string.
  For zoning minutes that is fatal: an answer citing "Henrico Planning
  Commission, p. 14" is the product, and a flat blob cannot produce one.
* **Structure comes from font size and weight**, not from `line.isupper()`.
  The uppercase heuristic was tuned for resumes; municipal minutes are full of
  uppercase case numbers ("REZ2026-00014") and all-caps body text that would
  become spurious headings.
* **Tables are extracted.** Permit schedules and case dispositions are the
  highest-value content in these documents and are pure noise when flattened
  into prose.
* **Text-free PDFs are detected and flagged**, not silently ingested as empty.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

try:  # pragma: no cover - import shape varies by install
    import pymupdf as fitz
except ImportError as exc:  # pragma: no cover
    try:
        import fitz  # type: ignore[no-redef]
    except ImportError as exc2:  # pragma: no cover
        raise ImportError(
            "PyMuPDF is required. Install with `uv pip install --upgrade pymupdf`."
        ) from exc2
    else:
        del exc

if not hasattr(fitz, "open"):  # pragma: no cover
    raise ImportError(
        "The imported module does not expose fitz.open. This usually means a "
        'different package named "fitz" is shadowing PyMuPDF. Run '
        "`uv pip uninstall fitz` and then `uv pip install --upgrade pymupdf`."
    )

# A page yielding less than this many characters has no usable text layer.
# Scanned agendas typically return a handful of stray ligatures, not zero.
MIN_CHARS_PER_PAGE = 40

# If this share of pages has no text layer, treat the document as scanned.
SCANNED_PAGE_RATIO = 0.8

# Headings are rare; a page where a third of lines look like headings is one
# where the font-size signal is meaningless, so structure detection backs off.
MAX_HEADING_RATIO = 0.4

# ...but a ratio computed over a handful of lines is noise. A cover page with a
# title and two lines of body text is legitimately one-third headings, so only
# apply the back-off once there are enough lines for the ratio to mean anything.
MIN_LINES_FOR_HEADING_RATIO = 12


@dataclass(slots=True)
class PdfTable:
    """A table lifted off a page, kept as rows so callers can re-render it."""

    page_number: int
    rows: list[list[str]]

    @property
    def is_meaningful(self) -> bool:
        """Reject 1xN strips, which are almost always layout artifacts."""
        return len(self.rows) >= 2 and len(self.rows[0]) >= 2

    def to_markdown(self) -> str:
        if not self.is_meaningful:
            return ""
        header, *body = self.rows
        width = len(header)

        def render(cells: Sequence[str]) -> str:
            padded = list(cells) + [""] * (width - len(cells))
            cleaned = [c.replace("|", r"\|").replace("\n", " ").strip() for c in padded]
            return "| " + " | ".join(cleaned[:width]) + " |"

        lines = [render(header), "|" + "|".join([" --- "] * width) + "|"]
        lines.extend(render(row) for row in body)
        return "\n".join(lines)


@dataclass(slots=True)
class PdfPage:
    """One page's text, tables, and provenance."""

    number: int  # 1-indexed, matching what a reader sees on the page
    text: str
    markdown: str
    tables: list[PdfTable] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return len(self.text.strip()) >= MIN_CHARS_PER_PAGE

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(slots=True)
class PdfDocument:
    """An extracted PDF."""

    path: str
    pages: list[PdfPage]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    @property
    def is_scanned(self) -> bool:
        """True when the PDF is images with no extractable text layer.

        Callers should record these as skipped rather than ingesting an empty
        document — otherwise a locality that switches to scanned agendas looks
        like a locality that stopped filing anything.
        """
        if not self.pages:
            return True
        empty = sum(1 for page in self.pages if not page.has_text)
        return empty / len(self.pages) >= SCANNED_PAGE_RATIO

    @property
    def tables(self) -> list[PdfTable]:
        return [t for page in self.pages for t in page.tables if t.is_meaningful]

    def to_markdown(self, *, include_page_markers: bool = True) -> str:
        """Render to Markdown.

        Page markers are on by default: the chunker splits on them to attach a
        page number to every chunk, which is what makes citations possible.
        """
        parts: list[str] = []
        for page in self.pages:
            if include_page_markers:
                parts.append(f"<!-- page:{page.number} -->")
            if page.markdown.strip():
                parts.append(page.markdown)
        return "\n\n".join(parts).strip()

    def page(self, number: int) -> PdfPage | None:
        return next((p for p in self.pages if p.number == number), None)


def _normalize_whitespace(text: str) -> str:
    # PDF extraction leaves ragged spacing and soft hyphens from justified text.
    text = text.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _dehyphenate(text: str) -> str:
    """Rejoin words split across a line break by justification.

    "develop-\nment" is one word; leaving it broken means neither "development"
    nor a search for it will match.
    """
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


def _iter_spans(page_dict: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:  # 0 = text; 1 = image
            continue
        for line in block.get("lines", []):
            yield from line.get("spans", [])


def _body_font_size(page_dicts: Sequence[dict[str, Any]]) -> float:
    """Median span size across the document — the body-text baseline.

    Computed document-wide rather than per page so that a title page, which is
    mostly large type, doesn't shift its own baseline and end up with no
    headings detected at all.
    """
    sizes = [
        round(float(span.get("size", 0)), 1)
        for page_dict in page_dicts
        for span in _iter_spans(page_dict)
        if span.get("text", "").strip()
    ]
    return statistics.median(sizes) if sizes else 0.0


def _heading_level(size: float, body_size: float, is_bold: bool) -> int | None:
    """Map a span's size and weight to a Markdown heading level, or None.

    Thresholds are ratios rather than absolute points because municipal
    documents vary wildly in base font size.
    """
    if body_size <= 0:
        return None
    ratio = size / body_size
    if ratio >= 1.5:
        return 2
    if ratio >= 1.25:
        return 3
    if ratio >= 1.1 or (is_bold and ratio >= 1.02):
        return 4
    return None


def _is_bold(span: dict[str, Any]) -> bool:
    # Bit 4 of the span flags is the synthetic/real bold marker; the font name
    # check catches fonts that encode weight in the name instead.
    if int(span.get("flags", 0)) & 2**4:
        return True
    return "bold" in str(span.get("font", "")).lower()


def _join_spans(spans: Sequence[dict[str, Any]]) -> str:
    """Concatenate a line's spans, restoring spaces the PDF encoded as position.

    A PDF has no space characters between spans that are merely drawn apart —
    "Areeb" and "Arshad" can be two spans with a positional gap and no
    whitespace in either. Naive concatenation yields "AreebArshad", which then
    gets chunked and embedded, so the name is unsearchable. Bridge the gap when
    the horizontal distance is a meaningful fraction of the font size.
    """
    out = ""
    previous_right: float | None = None

    for span in spans:
        text = span.get("text", "")
        if not text:
            continue
        bbox = span.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        left, right = float(bbox[0]), float(bbox[2])
        size = float(span.get("size", 0)) or 10.0

        if (
            previous_right is not None
            and out
            and not out[-1].isspace()
            and not text[0].isspace()
            # A quarter of the font size is comfortably wider than kerning and
            # narrower than a real space in every font seen in these documents.
            and (left - previous_right) > size * 0.25
        ):
            out += " "

        out += text
        previous_right = right

    return out


def _page_markdown(page_dict: dict[str, Any], body_size: float) -> str:
    """Rebuild a page as Markdown using font size and weight for structure."""
    lines: list[str] = []
    heading_count = 0
    total_lines = 0

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            total_lines += 1
            text = _normalize_whitespace(_join_spans(spans))
            if not text:
                continue

            lead = spans[0]
            level = _heading_level(float(lead.get("size", 0)), body_size, _is_bold(lead))
            # A "heading" that runs to sentence length is body text in bold.
            if level is not None and len(text) <= 120:
                heading_count += 1
                lines.append(f"{'#' * level} {text}")
            else:
                lines.append(text)
        lines.append("")

    # If nearly everything looked like a heading the font signal is unreliable
    # (single-font documents, or OCR output with uniform sizing). Fall back to
    # plain text rather than emitting a document that is all headings.
    if (
        total_lines >= MIN_LINES_FOR_HEADING_RATIO
        and heading_count / total_lines > MAX_HEADING_RATIO
    ):
        lines = [re.sub(r"^#+ ", "", line) for line in lines]

    return _dehyphenate(_normalize_whitespace("\n".join(lines)))


def _extract_tables(page: Any, page_number: int) -> list[PdfTable]:
    """Pull tables off a page. Never raises — a table is a bonus, not the point."""
    try:
        finder = page.find_tables()
    except Exception as exc:  # noqa: BLE001
        log.debug("table detection failed on page %d: %s", page_number, exc)
        return []

    tables: list[PdfTable] = []
    for table in getattr(finder, "tables", []):
        try:
            raw = table.extract()
        except Exception as exc:  # noqa: BLE001
            log.debug("table extract failed on page %d: %s", page_number, exc)
            continue
        rows = [
            [(cell or "").strip() for cell in row]
            for row in raw
            if any((cell or "").strip() for cell in row)
        ]
        if rows:
            tables.append(PdfTable(page_number=page_number, rows=rows))
    return tables


def extract_pages(
    source: str | Path | bytes,
    *,
    extract_tables: bool = True,
    max_pages: int | None = None,
) -> PdfDocument:
    """Extract a PDF into per-page text, Markdown, and tables.

    ``source`` may be a path or raw bytes, so a PDF fetched over HTTP never has
    to touch disk.
    """
    is_bytes = isinstance(source, bytes)
    label = "<bytes>" if is_bytes else str(source)
    open_kwargs: dict[str, Any] = (
        {"stream": source, "filetype": "pdf"} if is_bytes else {"filename": str(source)}
    )

    pages: list[PdfPage] = []
    metadata: dict[str, Any] = {}

    with fitz.open(**open_kwargs) as doc:  # type: ignore[no-untyped-call]
        metadata = dict(doc.metadata or {})
        limit = doc.page_count if max_pages is None else min(max_pages, doc.page_count)

        # Two passes: the first establishes the document-wide body font size,
        # which page-level structure detection needs before it can run.
        page_dicts = [doc[i].get_text("dict") for i in range(limit)]
        body_size = _body_font_size(page_dicts)

        for index in range(limit):
            page = doc[index]
            number = index + 1
            raw_text = _dehyphenate(_normalize_whitespace(page.get_text("text")))
            markdown = _page_markdown(page_dicts[index], body_size)

            tables = _extract_tables(page, number) if extract_tables else []
            if tables:
                rendered = [t.to_markdown() for t in tables if t.is_meaningful]
                if rendered:
                    markdown = f"{markdown}\n\n" + "\n\n".join(rendered)

            pages.append(
                PdfPage(
                    number=number,
                    text=raw_text,
                    markdown=markdown.strip(),
                    tables=tables,
                )
            )

    document = PdfDocument(path=label, pages=pages, metadata=metadata)
    if document.is_scanned:
        log.warning(
            "%s has no usable text layer across %d pages; needs OCR",
            label,
            document.page_count,
        )
    return document


def extract_pdf_text(pdf_path: str | Path) -> str:
    """Flat text for the whole document.

    Kept with the notebook's original name and signature so existing callers
    keep working. Prefer `extract_pages` for anything that will be cited.
    """
    return extract_pages(pdf_path, extract_tables=False).text


def convert_pdf_to_markdown(
    source: str | Path | bytes, *, include_page_markers: bool = True
) -> str:
    """One-call PDF to Markdown."""
    return extract_pages(source).to_markdown(include_page_markers=include_page_markers)


PAGE_MARKER = re.compile(r"<!--\s*page:(\d+)\s*-->")


def split_by_page_marker(markdown: str) -> list[tuple[int, str]]:
    """Recover (page_number, text) pairs from rendered Markdown.

    The chunker uses this to carry a page number onto every chunk, which is what
    lets a RAG answer cite a page rather than just a document.
    """
    matches = list(PAGE_MARKER.finditer(markdown))
    if not matches:
        return [(1, markdown.strip())] if markdown.strip() else []

    sections: list[tuple[int, str]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if body:
            sections.append((int(match.group(1)), body))
    return sections
