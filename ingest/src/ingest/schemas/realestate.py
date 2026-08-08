"""ScrapeRecord subclasses for the real-estate tasks.

These satisfy webscraper-core's exporter contract (`note_kind`, `title`,
`frontmatter`, `body`) so a record can still be written to an Obsidian vault,
but PropIntel reads the typed fields directly and writes them to Postgres.

Every record follows the same rule as the rest of the pipeline: a parser
returns `None` rather than a half-filled record, so the persistence layer never
has to guess whether a blank field means "absent" or "not extracted".
"""

from __future__ import annotations

from datetime import date

from pydantic import Field, field_validator
from webscraper_core.schemas.base import ScrapeRecord


class TeamMember(ScrapeRecord):
    """One person on a firm's team or leadership page."""

    note_kind: str = "team_member"

    name: str
    job_title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    bio: str | None = None
    firm_name: str | None = None

    @field_validator("name")
    @classmethod
    def _name_looks_like_a_person(cls, v: str) -> str:
        cleaned = " ".join(v.split())
        if len(cleaned) < 4 or len(cleaned) > 80:
            raise ValueError("implausible person name")
        # A name with no space is a nav label ("Team", "Leadership"), not a person.
        if " " not in cleaned:
            raise ValueError("single-token name is probably a heading")
        return cleaned

    def title(self) -> str:  # pragma: no cover - vault export only
        return self.name

    def frontmatter(self) -> dict[str, object]:  # pragma: no cover
        return {"kind": self.note_kind, "name": self.name, "title": self.job_title}

    def body(self) -> str:  # pragma: no cover
        return self.bio or ""


class TeamRoster(ScrapeRecord):
    """Every person found on one team page.

    A roster rather than N separate records because the diff between crawls is
    what produces hire and departure signals — that comparison needs the whole
    page, not one person at a time.
    """

    note_kind: str = "team_roster"

    firm_name: str | None = None
    members: list[TeamMember] = Field(default_factory=list)

    def title(self) -> str:  # pragma: no cover
        return f"{self.firm_name or 'Firm'} — team"

    def frontmatter(self) -> dict[str, object]:  # pragma: no cover
        return {"kind": self.note_kind, "count": len(self.members)}

    def body(self) -> str:  # pragma: no cover
        return "\n".join(f"- {m.name} — {m.job_title or ''}" for m in self.members)


class JobListing(ScrapeRecord):
    """One open role on a firm's own careers page."""

    note_kind: str = "job_listing"

    job_title: str
    location: str | None = None
    department: str | None = None
    employment_type: str | None = None
    url: str | None = None

    @field_validator("job_title")
    @classmethod
    def _title_is_plausible(cls, v: str) -> str:
        cleaned = " ".join(v.split())
        if len(cleaned) < 3 or len(cleaned) > 200:
            raise ValueError("implausible job title")
        return cleaned

    def frontmatter(self) -> dict[str, object]:  # pragma: no cover
        return {"kind": self.note_kind, "title": self.job_title}

    def title(self) -> str:  # pragma: no cover
        return self.job_title

    def body(self) -> str:  # pragma: no cover
        return self.location or ""


class CareersPage(ScrapeRecord):
    """All open roles on one careers page.

    Whole-page, for the same reason as TeamRoster: a posting that disappears
    between crawls is how a role gets closed, and that is only visible by
    comparing complete sets.
    """

    note_kind: str = "careers_page"

    firm_name: str | None = None
    listings: list[JobListing] = Field(default_factory=list)

    def title(self) -> str:  # pragma: no cover
        return f"{self.firm_name or 'Firm'} — careers"

    def frontmatter(self) -> dict[str, object]:  # pragma: no cover
        return {"kind": self.note_kind, "count": len(self.listings)}

    def body(self) -> str:  # pragma: no cover
        return "\n".join(f"- {job.job_title}" for job in self.listings)


class ProjectAnnouncement(ScrapeRecord):
    """A development announced in a press release or news article.

    Deliberately conservative: news is a *report* of a filing, not the filing
    itself, so anything extracted here is lower-confidence than a permit and is
    stored with the article as its provenance.
    """

    note_kind: str = "project_announcement"

    headline: str
    project_name: str | None = None
    firm_names: list[str] = Field(default_factory=list)
    project_type: str | None = None
    locality: str | None = None
    address: str | None = None
    unit_count: int | None = None
    square_feet: int | None = None
    acreage: float | None = None
    est_value_usd: float | None = None
    announced_date: date | None = None
    summary: str | None = None

    def title(self) -> str:  # pragma: no cover
        return self.project_name or self.headline

    def frontmatter(self) -> dict[str, object]:  # pragma: no cover
        return {"kind": self.note_kind, "headline": self.headline}

    def body(self) -> str:  # pragma: no cover
        return self.summary or self.headline
