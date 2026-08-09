"""Careers page parser.

First-party pages only. LinkedIn and Indeed prohibit scraping in their terms
and block it in practice; for 20 regional firms the company's own page is also
the authoritative, unaggregated source.

Open-posting count over time is the hiring-surge anomaly input, so what matters
most is that the *set* of roles is captured consistently between crawls — a
posting that vanishes is how a role gets closed.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser, Node
from webscraper_core.fetchers.base import FetchResult
from webscraper_core.llm.anthropic_client import LLMExtractor
from webscraper_core.parsers.base import BaseParser
from webscraper_core.utils.htmlclean import jsonld_objects, strip_noise

from ingest.schemas.realestate import CareersPage, JobListing

# Real-estate role words. A link is only treated as a job posting if its text
# contains one — otherwise every nav item on the page becomes a "job".
ROLE_MARKERS = (
    "manager",
    "director",
    "analyst",
    "associate",
    "coordinator",
    "engineer",
    "superintendent",
    "estimator",
    "accountant",
    "controller",
    "specialist",
    "assistant",
    "administrator",
    "developer",
    "broker",
    "agent",
    "leasing",
    "maintenance",
    "technician",
    "supervisor",
    "president",
    "counsel",
    "intern",
    "project",
    "construction",
    "acquisitions",
    "asset",
    "property",
    "marketing",
    "recruiter",
    "foreman",
    "carpenter",
    "concierge",
)

# Link targets typical of applicant tracking systems.
# Bug 35: split into netloc-based (actual ATS domains) and path-based patterns.
# Checking bare strings like "careers"/"jobs" against the full URL causes false
# positives when the base URL itself contains /careers/.
ATS_NETLOC_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "workday",
    "myworkdayjobs",
    "icims",
    "jobvite",
    "smartrecruiters",
    "bamboohr",
    "paylocity",
    "adp.com",
    "ultipro",
    "applytojob",
    "breezy.hr",
    "recruiting",
    "careers",
    "jobs",
)

ATS_PATH_PARTS = ("careers", "jobs", "recruiting", "openings", "apply")

LOCATION_RE = re.compile(r"\b([A-Z][A-Za-z.\- ]+),\s*(VA|MD|DC|Virginia|Maryland)\b")

MAX_LISTINGS = 300


def looks_like_role_title(text: str) -> bool:
    cleaned = " ".join(text.split())
    if not 3 <= len(cleaned) <= 120:
        return False
    lowered = cleaned.lower()
    if lowered in {"careers", "jobs", "open positions", "apply now", "join our team"}:
        return False
    return any(marker in lowered for marker in ROLE_MARKERS)


class CareersParser(BaseParser):
    task = "careers"
    engine = "selectolax"

    def parse(self, res: FetchResult) -> CareersPage | None:
        # See the note in the team parser: strip_noise drops <script>, so
        # JSON-LD must be read from the uncleaned tree.
        raw_tree = HTMLParser(res.html)
        tree = strip_noise(HTMLParser(res.html))

        # Bug 37: merge both passes so a single JSON-LD JobPosting (e.g. a
        # "featured role" embed) doesn't suppress the full structural listing.
        jsonld_listings = self._from_jsonld(raw_tree, page_url=res.final_url)
        link_listings = self._from_links(tree, res.final_url)
        seen_titles = {j.job_title.lower() for j in jsonld_listings}
        listings = list(jsonld_listings)
        for listing in link_listings:
            if listing.job_title.lower() not in seen_titles:
                listings.append(listing)
        if not listings:
            return None

        return CareersPage(
            source_url=res.final_url,
            firm_name=self._firm_name(tree),
            listings=listings[:MAX_LISTINGS],
        )

    async def llm_fallback(self, res: FetchResult, extractor: LLMExtractor) -> CareersPage | None:
        if not extractor.enabled:
            return None
        return await extractor.extract(
            res,
            CareersPage,
            "Extract all job listings from this careers page. "
            "For each listing include job title, location, and employment type.",
        )

    @staticmethod
    def _from_jsonld(tree: HTMLParser, *, page_url: str = "") -> list[JobListing]:
        """schema.org JobPosting, which most ATS platforms emit."""
        listings: list[JobListing] = []
        for obj in jsonld_objects(tree):
            types = obj.get("@type")
            kinds = types if isinstance(types, list) else [types]
            if "JobPosting" not in kinds:
                continue
            title = obj.get("title")
            if not isinstance(title, str) or not title.strip():
                continue

            location = None
            job_location = obj.get("jobLocation")
            if isinstance(job_location, dict):
                address = job_location.get("address")
                if isinstance(address, dict):
                    city = address.get("addressLocality")
                    region = address.get("addressRegion")
                    location = ", ".join(p for p in (city, region) if p) or None

            posting_url = _as_str(obj.get("url")) or page_url
            try:
                listings.append(
                    JobListing(
                        source_url=posting_url,
                        job_title=title,
                        location=location,
                        employment_type=_as_str(obj.get("employmentType")),
                    )
                )
            except ValueError:
                continue
        return listings

    def _from_links(self, tree: HTMLParser, base_url: str) -> list[JobListing]:
        """Anchor text that reads like a role, pointing at an ATS or job path."""
        listings: list[JobListing] = []
        seen: set[str] = set()

        for anchor in tree.css("a[href]"):
            text = " ".join(anchor.text(strip=True).split())
            if not looks_like_role_title(text):
                continue

            href = anchor.attributes.get("href") or ""
            absolute = urljoin(base_url, href)
            # Bug 35: check ATS domains against netloc only; check path patterns
            # against the raw href so the base URL path cannot cause false matches.
            parsed = urlparse(absolute)
            netloc = parsed.netloc.lower()
            href_lower = href.lower()
            if not (
                any(h in netloc for h in ATS_NETLOC_HOSTS)
                or any(p in href_lower for p in ATS_PATH_PARTS)
            ):
                continue

            key = text.lower()
            if key in seen:
                continue
            seen.add(key)

            try:
                listings.append(
                    JobListing(
                        source_url=absolute,
                        job_title=text,
                        location=self._location_near(anchor),
                        url=absolute,
                    )
                )
            except ValueError:
                continue
        return listings

    @staticmethod
    def _location_near(anchor: Node) -> str | None:
        parent = anchor.parent
        if parent is None:
            return None
        match = LOCATION_RE.search(parent.text(strip=True))
        return match.group(0) if match else None

    @staticmethod
    def _firm_name(tree: HTMLParser) -> str | None:
        meta = tree.css_first("meta[property='og:site_name']")
        if meta and meta.attributes.get("content"):
            return str(meta.attributes["content"]).strip()
        return None


def _as_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list) and value:
        first = value[0]
        return first.strip() if isinstance(first, str) else None
    return None
