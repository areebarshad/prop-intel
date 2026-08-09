"""Team and leadership page parser.

Diffing this page across crawls is what produces "key hire" and "departure"
signals — a developer hiring a head of industrial acquisitions is telling you
where they are going months before a permit is filed.

Team pages have no standard markup, so this works structurally: find the
repeated container that holds a person-shaped name next to a job title, and
require both. Requiring the title is what keeps board-member lists, testimonial
carousels, and article bylines out of the roster.
"""

from __future__ import annotations

import re
from typing import Any

from selectolax.parser import HTMLParser, Node
from webscraper_core.fetchers.base import FetchResult
from webscraper_core.llm.anthropic_client import LLMExtractor
from webscraper_core.parsers.base import BaseParser
from webscraper_core.utils.htmlclean import jsonld_objects, strip_noise

from ingest.schemas.realestate import TeamMember, TeamRoster

# Words that mark a string as a real estate job title. A "name" with none of
# these next to it is far more likely a heading or a project than a person.
TITLE_MARKERS = (
    "president",
    "ceo",
    "cfo",
    "coo",
    "cto",
    "chief",
    "founder",
    "partner",
    "principal",
    "director",
    "manager",
    "vice president",
    "vp",
    "svp",
    "evp",
    "broker",
    "associate",
    "analyst",
    "developer",
    "development",
    "acquisitions",
    "leasing",
    "counsel",
    "controller",
    "superintendent",
    "estimator",
    "project executive",
    "asset management",
    "portfolio",
    "chairman",
    "owner",
    "head of",
)

# Containers that commonly wrap one team member.
MEMBER_SELECTORS = (
    "[class*='team-member']",
    "[class*='teammember']",
    "[class*='staff-member']",
    "[class*='person']",
    "[class*='bio-card']",
    "[class*='leader']",
    "[class*='profile-card']",
    "article",
    "li",
)

# A person's name: 2-4 capitalized tokens, allowing initials, hyphens, and
# particles like "van" or "de".
NAME_RE = re.compile(
    r"^(?:[A-Z][A-Za-z'’.-]+|[A-Z]\.)"
    r"(?:\s+(?:de|van|von|del|della|di|la|le|Mc|Mac)?\s*"
    r"(?:[A-Z][A-Za-z'’.-]+|[A-Z]\.)){1,3}"
    r"(?:,?\s+(?:Jr|Sr|II|III|IV|PhD|MBA|CCIM|SIOR|LEED|AIA|PE|CPA|CFA)\.?)?$"
)

MAX_MEMBERS = 400

# Interface and section labels that satisfy the name pattern but are not people.
# Observed live: "View Profile" (a button) and "Nusbaum News" (a section header)
# were both extracted as staff before this filter existed. A person's name is
# never one of these, so a token match is enough to reject.
NON_PERSON_TOKENS = frozenset(
    {
        "view",
        "read",
        "learn",
        "more",
        "profile",
        "profiles",
        "bio",
        "team",
        "our",
        "news",
        "press",
        "media",
        "contact",
        "about",
        "home",
        "careers",
        "jobs",
        "services",
        "portfolio",
        "properties",
        "leadership",
        "management",
        "board",
        "directors",
        "click",
        "here",
        "email",
        "phone",
        "linkedin",
        "download",
        "search",
        "menu",
        "close",
        "next",
        "previous",
        "all",
        "see",
        "meet",
        "full",
        "details",
        "info",
        "back",
        "top",
    }
)


def looks_like_person_name(text: str) -> bool:
    cleaned = " ".join(text.split())
    if not 4 <= len(cleaned) <= 80 or "\n" in text.strip():
        return False
    if not NAME_RE.match(cleaned):
        return False
    # "View Profile" and "Nusbaum News" both match the capitalization pattern.
    tokens = {token.lower().strip(".,") for token in cleaned.split()}
    return not (tokens & NON_PERSON_TOKENS)


def looks_like_job_title(text: str) -> bool:
    cleaned = " ".join(text.split()).lower()
    if not 2 <= len(cleaned) <= 120:
        return False
    return any(marker in cleaned for marker in TITLE_MARKERS)


def _node_text(node: Node | None) -> str:
    """Visible text of a node, with child elements kept apart.

    `text(strip=True)` concatenates children with no separator, so
    `<h3>Tim Johnson</h3><p>Chief Investment Officer</p>` collapses to
    "Tim JohnsonChief Investment Officer" — which then gets stored as somebody's
    name. The separator keeps the tokens distinct so the name pattern rejects
    the merged string and the real leaf node wins instead.
    """
    if node is None:
        return ""
    return " ".join(node.text(separator=" ", strip=True).split())


class TeamParser(BaseParser):
    task = "team"
    engine = "selectolax"

    def parse(self, res: FetchResult) -> TeamRoster | None:
        # JSON-LD lives inside <script>, which strip_noise removes, so the
        # structured-data pass has to run before the tree is cleaned.
        raw_tree = HTMLParser(res.html)
        tree = strip_noise(HTMLParser(res.html))

        # Bug 37: filter JSON-LD results to those with a job title so that bare
        # Person entries (article authors, bylines) don't suppress the structural
        # team pass, which is the authoritative roster source for most sites.
        jsonld_members = self._from_jsonld(raw_tree)
        valid_jsonld = [m for m in jsonld_members if m.job_title]
        members = valid_jsonld or self._from_structure(tree)
        if not members:
            return None

        return TeamRoster(
            source_url=res.final_url,
            firm_name=self._firm_name(tree),
            members=members[:MAX_MEMBERS],
        )

    async def llm_fallback(self, res: FetchResult, extractor: LLMExtractor) -> TeamRoster | None:
        """Only reached when the structural pass finds nothing.

        Left as a no-op when the extractor is disabled (no API key), which is
        the common case in local development — the rule pass carries the load.
        """
        if not extractor.enabled:
            return None
        return await extractor.extract(
            res,
            TeamRoster,
            "Extract all team members and leadership from this company page. "
            "For each person include name, job title, email, and LinkedIn URL.",
        )

    @staticmethod
    def _from_jsonld(tree: HTMLParser) -> list[TeamMember]:
        """schema.org Person entries, when a site publishes them.

        Cheapest and most reliable path — no heuristics involved.
        """
        members: list[TeamMember] = []
        for obj in jsonld_objects(tree):
            types = obj.get("@type")
            kinds = types if isinstance(types, list) else [types]
            if "Person" not in kinds:
                continue
            name = obj.get("name")
            if not isinstance(name, str):
                continue
            try:
                members.append(
                    TeamMember(
                        source_url="",
                        name=name,
                        job_title=_first_str(obj.get("jobTitle")),
                        email=_first_str(obj.get("email")),
                        phone=_first_str(obj.get("telephone")),
                    )
                )
            except ValueError:
                continue
        return members

    def _from_structure(self, tree: HTMLParser) -> list[TeamMember]:
        """Find repeated name+title pairs in the page structure."""
        seen: set[str] = set()
        members: list[TeamMember] = []

        for selector in MEMBER_SELECTORS:
            for node in tree.css(selector):
                member = self._member_from_node(node)
                if member is None:
                    continue
                key = member.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                members.append(member)
            # Stop at the first selector that produced a plausible roster —
            # continuing would re-harvest the same people out of ancestor
            # containers under a looser selector.
            if len(members) >= 3:
                break

        return members

    def _member_from_node(self, node: Node) -> TeamMember | None:
        # Look at short leaf-ish texts: headings, then anything else.
        candidates = [
            _node_text(child)
            for child in node.css("h1, h2, h3, h4, h5, h6, strong, b, p, span, div, a")
        ]
        candidates = [c for c in candidates if c]
        if len(candidates) < 2:
            return None

        name = next(
            (c for c in candidates if looks_like_person_name(c) and not looks_like_job_title(c)),
            None,
        )
        if name is None:
            return None

        title = next((c for c in candidates if c != name and looks_like_job_title(c)), None)
        if title is None:
            # Without a title this is probably a heading or an article byline.
            return None

        email = None
        for a in node.css("a[href^='mailto:']"):
            email = (a.attributes.get("href") or "").removeprefix("mailto:").split("?")[0]
            break

        linkedin = None
        for a in node.css("a[href]"):
            href = a.attributes.get("href") or ""
            if "linkedin.com" in href:
                linkedin = href
                break

        try:
            return TeamMember(
                source_url="",
                name=name,
                job_title=title,
                email=email or None,
                linkedin_url=linkedin,
            )
        except ValueError:
            return None

    @staticmethod
    def _firm_name(tree: HTMLParser) -> str | None:
        meta = tree.css_first("meta[property='og:site_name']")
        if meta and meta.attributes.get("content"):
            return str(meta.attributes["content"]).strip()
        return None


def _first_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None
