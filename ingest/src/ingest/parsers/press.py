"""Press release and trade-news parser.

News is a *report* of a filing, never the filing itself, so everything this
produces is lower-confidence than a permit and always carries the article as
provenance. It exists because announcements lead permits by months: a developer
says "we've acquired 20 acres in Richmond" long before a site plan appears.

Numbers are extracted with explicit patterns rather than an LLM because the
units are the whole point — confusing 240 units with 240,000 square feet would
put a wrong figure in a digest a broker acts on.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from selectolax.parser import HTMLParser
from webscraper_core.fetchers.base import FetchResult
from webscraper_core.llm.anthropic_client import LLMExtractor
from webscraper_core.parsers.base import BaseParser
from webscraper_core.utils.htmlclean import clean_text, find_jsonld, strip_noise

from ingest.schemas.realestate import ProjectAnnouncement

# Words that make an article about a development rather than, say, an
# executive-comp story. At least two must appear.
DEVELOPMENT_MARKERS = (
    "acquired",
    "acquisition",
    "broke ground",
    "groundbreaking",
    "development",
    "developer",
    "construction",
    "rezoning",
    "site plan",
    "mixed-use",
    "multifamily",
    "industrial",
    "warehouse",
    "data center",
    "square feet",
    "square-foot",
    "units",
    "acres",
    "delivered",
    "leased",
    "joint venture",
)

ASSET_CLASS_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data_center", ("data center", "data centre", "hyperscale")),
    ("industrial", ("industrial", "warehouse", "distribution center", "logistics")),
    ("multifamily", ("multifamily", "multi-family", "apartment", "residential units")),
    ("office", ("office building", "office tower", "office space")),
    ("retail", ("retail", "shopping center", "grocery-anchored")),
    ("mixed_use", ("mixed-use", "mixed use")),
    ("hospitality", ("hotel", "hospitality")),
    ("land", ("land acquisition", "acres of land", "raw land")),
)

VA_LOCALITIES = (
    "Fairfax",
    "Loudoun",
    "Arlington",
    "Prince William",
    "Alexandria",
    "Richmond",
    "Henrico",
    "Chesterfield",
    "Hanover",
    "Virginia Beach",
    "Norfolk",
    "Chesapeake",
    "Reston",
    "Tysons",
    "Herndon",
    "Ashburn",
    "Sterling",
    "Manassas",
)

UNITS_RE = re.compile(r"\b([\d,]{1,7})\s*(?:-|\s)?(?:apartment\s+)?units?\b", re.I)
SQFT_RE = re.compile(
    r"\b([\d,]{2,12})\s*(?:-|\s)?(?:square[\s-]?feet|square[\s-]?foot|sq\.?\s?ft\.?|sf)\b",
    re.I,
)
ACRES_RE = re.compile(r"\b([\d,]+(?:\.\d+)?)\s*(?:-|\s)?acres?\b", re.I)
MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion|M|B)?\b", re.I)

MIN_ARTICLE_CHARS = 250


def _to_int(raw: str) -> int | None:
    try:
        return int(raw.replace(",", ""))
    except ValueError:
        return None


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def detect_asset_class(text: str) -> str | None:
    lowered = text.lower()
    for asset_class, markers in ASSET_CLASS_PATTERNS:
        if any(marker in lowered for marker in markers):
            return asset_class
    return None


def detect_locality(text: str) -> str | None:
    for locality in VA_LOCALITIES:
        if re.search(rf"\b{re.escape(locality)}\b", text):
            return locality
    return None


def parse_money(text: str) -> float | None:
    """First dollar amount in the text, normalized to whole dollars.

    "$120 million" and "$120,000,000" must produce the same number, or a
    valuation comparison across articles is meaningless.
    """
    match = MONEY_RE.search(text)
    if not match:
        return None
    amount = _to_float(match.group(1))
    if amount is None:
        return None
    scale = (match.group(2) or "").lower()
    if scale in {"million", "m"}:
        amount *= 1_000_000
    elif scale in {"billion", "b"}:
        amount *= 1_000_000_000
    return amount


class PressReleaseParser(BaseParser):
    task = "press_release"
    engine = "selectolax"

    def parse(self, res: FetchResult) -> ProjectAnnouncement | None:
        # strip_noise removes <script>, where JSON-LD lives, so keep an
        # uncleaned tree for the publication date.
        raw_tree = HTMLParser(res.html)
        tree = strip_noise(HTMLParser(res.html))
        text = clean_text(res.html)

        if len(text) < MIN_ARTICLE_CHARS:
            return None

        lowered = text.lower()
        hits = sum(1 for marker in DEVELOPMENT_MARKERS if marker in lowered)
        if hits < 2:
            # Not a development story. Returning None rather than a sparse
            # record keeps unrelated business news out of the project table.
            return None

        headline = self._headline(tree)
        if not headline:
            return None

        units = UNITS_RE.search(text)
        sqft = SQFT_RE.search(text)
        acres = ACRES_RE.search(text)

        return ProjectAnnouncement(
            source_url=res.final_url,
            headline=headline,
            project_type=detect_asset_class(text),
            locality=detect_locality(text),
            unit_count=_to_int(units.group(1)) if units else None,
            square_feet=_to_int(sqft.group(1)) if sqft else None,
            acreage=_to_float(acres.group(1)) if acres else None,
            est_value_usd=parse_money(text),
            announced_date=self._published_date(raw_tree),
            summary=self._summary(text),
        )

    async def llm_fallback(
        self, res: FetchResult, extractor: LLMExtractor
    ) -> ProjectAnnouncement | None:
        if not extractor.enabled:
            return None
        return None

    @staticmethod
    def _headline(tree: HTMLParser) -> str | None:
        for selector in ("h1", "meta[property='og:title']", "title"):
            node = tree.css_first(selector)
            if node is None:
                continue
            value = (
                node.attributes.get("content")
                if selector.startswith("meta")
                else node.text(strip=True)
            )
            if value and value.strip():
                return " ".join(value.split())[:400]
        return None

    @staticmethod
    def _published_date(tree: HTMLParser) -> date | None:
        article = find_jsonld(tree, "NewsArticle") or find_jsonld(tree, "Article") or {}
        raw = article.get("datePublished")
        if not isinstance(raw, str):
            meta = tree.css_first("meta[property='article:published_time']")
            raw = meta.attributes.get("content") if meta else None
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return None

    @staticmethod
    def _summary(text: str) -> str:
        paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 80]
        return paragraphs[0][:600] if paragraphs else text[:600]
