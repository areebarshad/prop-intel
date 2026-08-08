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
from urllib.parse import urljoin

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
ATS_HOSTS = (
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

        listings = self._from_jsonld(raw_tree) or self._from_links(tree, res.final_url)
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
        return None

    @staticmethod
    def _from_jsonld(tree: HTMLParser) -> list[JobListing]:
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

            try:
                listings.append(
                    JobListing(
                        source_url="",
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
            # A role-shaped link that goes nowhere job-related is usually a
            # service page ("Property Management"), not a posting.
            if not any(host in absolute.lower() for host in ATS_HOSTS):
                continue

            key = text.lower()
            if key in seen:
                continue
            seen.add(key)

            try:
                listings.append(
                    JobListing(
                        source_url="",
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
