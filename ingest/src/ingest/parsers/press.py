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

# Bug 38: patterns for extracting firm names and project names from article text.
FIRM_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&\s]{1,40}"
    r"(?:Development|Realty|Properties|Property Group|Capital|Partners|REIT|"
    r"Investments?|Management|Construction|Builders?|Associates|Real Estate|"
    r"Residential|Commercial))"
    r"(?=[\s,\.\)])",
)
PROJECT_NAME_RE = re.compile(
    r'["“‘]([A-Z][^"”’\n]{4,79})["”’]'
    r"|\bthe\s+([A-Z][A-Za-z\s]{3,39})\s+"
    r"(?:development|project|tower|center|district|community|phase)\b",
    re.I,
)

# Bug 41: context words used to score numeric match relevance.
_NUMERIC_CONTEXT_WORDS = (
    "development", "project", "building", "tower", "community",
    "acquired", "develop", "deliver", "construct", "announce",
    "residential", "commercial", "mixed-use", "multifamily",
)

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
    # Bug 40: return the locality that appears earliest in the text rather than
    # the one listed first in VA_LOCALITIES (declaration order is arbitrary).
    best: tuple[int, str] | None = None
    for locality in VA_LOCALITIES:
        m = re.search(rf"\b{re.escape(locality)}\b", text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), locality)
    return best[1] if best else None


def _best_numeric_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """Bug 41: return match with the most development-related context words nearby.

    Index-0 grabs are unreliable when boilerplate (e.g. sidebar stats, ad copy)
    appears before the article body. Scoring by surrounding context words prefers
    the match that sits inside a development sentence.
    """
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    def _score(m: re.Match[str]) -> int:
        start = max(0, m.start() - 200)
        end = min(len(text), m.end() + 200)
        snippet = text[start:end].lower()
        return sum(1 for w in _NUMERIC_CONTEXT_WORDS if w in snippet)

    # Highest context score wins; ties broken by latest position (article body
    # follows sidebars/boilerplate in HTML reading order).
    return max(matches, key=lambda m: (_score(m), m.start()))


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

        # Bug 41: use context-scored match selection instead of index-0.
        units = _best_numeric_match(UNITS_RE, text)
        sqft = _best_numeric_match(SQFT_RE, text)
        acres = _best_numeric_match(ACRES_RE, text)

        return ProjectAnnouncement(
            source_url=res.final_url,
            headline=headline,
            # Bug 38: populate project_name and firm_names from article text.
            project_name=self._extract_project_name(headline, text),
            firm_names=self._extract_firm_names(text),
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
        return await extractor.extract(
            res,
            ProjectAnnouncement,
            "Extract real estate project announcement details from this press release "
            "or news article. Include headline, project name, firm names, project type, "
            "locality, unit count, square footage, acreage, and estimated value in USD.",
        )

    @staticmethod
    def _extract_firm_names(text: str) -> list[str]:
        """Bug 38: firm names ending with a real-estate company suffix."""
        seen: set[str] = set()
        firms: list[str] = []
        for m in FIRM_RE.finditer(text):
            name = " ".join(m.group(1).split())
            key = name.lower()
            if key not in seen:
                seen.add(key)
                firms.append(name)
        return firms[:5]

    @staticmethod
    def _extract_project_name(headline: str, text: str) -> str | None:
        """Bug 38: quoted project name or '[Name] development/project/...' pattern."""
        for source in (headline, text[:1200]):
            for m in PROJECT_NAME_RE.finditer(source):
                name = (m.group(1) or m.group(2) or "").strip()
                if 4 < len(name) < 80:
                    return " ".join(name.split())
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
