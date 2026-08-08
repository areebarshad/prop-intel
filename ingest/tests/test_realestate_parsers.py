"""Real-estate parsers: team rosters, careers pages, press releases.

Fixtures are hand-written HTML in the shapes real firm sites use. Several cases
encode false positives that actually occurred against live sites — "View
Profile" and "Nusbaum News" were both stored as staff members, and a name got
concatenated with its own job title — so they cannot silently return.
"""

from __future__ import annotations

import pytest
from webscraper_core.fetchers.base import FetchResult
from webscraper_core.parsers.registry import available_tasks, get_parser

import ingest.register  # noqa: F401  (registers the parsers under test)
from ingest.parsers.careers import CareersParser, looks_like_role_title
from ingest.parsers.press import PressReleaseParser, detect_asset_class, parse_money
from ingest.parsers.team import TeamParser, looks_like_job_title, looks_like_person_name


def _result(html: str, url: str = "https://firm.test/team") -> FetchResult:
    return FetchResult(url=url, final_url=url, status=200, html=html)


class TestRegistration:
    def test_the_real_estate_tasks_are_registered(self) -> None:
        """Proves the upstream register_parser() hook is actually load-bearing."""
        for task in ("team", "careers", "press_release"):
            assert task in available_tasks()

    def test_registered_parsers_are_retrievable_by_task(self) -> None:
        assert isinstance(get_parser("team"), TeamParser)
        assert isinstance(get_parser("careers"), CareersParser)
        assert isinstance(get_parser("press_release"), PressReleaseParser)

    def test_the_built_in_tasks_still_work(self) -> None:
        """Registration must add tasks, never displace webscraper-core's own."""
        assert "contact" in available_tasks()


class TestPersonNameHeuristic:
    @pytest.mark.parametrize(
        "name",
        ["Jane Doe", "Robert M. Smith", "Miles Leon", "John M. Profilet, SIOR"],
    )
    def test_accepts_real_names(self, name: str) -> None:
        assert looks_like_person_name(name)

    @pytest.mark.parametrize(
        "label",
        [
            "View Profile",  # a button, stored as staff before the filter existed
            "Nusbaum News",  # a section header, likewise
            "Our Team",
            "Read More",
            "Contact Us",
            "Leadership",
            "Learn More",
        ],
    )
    def test_rejects_interface_and_section_labels(self, label: str) -> None:
        assert not looks_like_person_name(label)

    def test_rejects_a_single_token(self) -> None:
        assert not looks_like_person_name("Leadership")

    def test_rejects_a_sentence(self) -> None:
        assert not looks_like_person_name(
            "We Are A Full Service Commercial Real Estate Firm Serving Virginia"
        )


class TestJobTitleHeuristic:
    @pytest.mark.parametrize(
        "title",
        ["Senior Vice President", "Chief Investment Officer", "Partner", "Asset Manager"],
    )
    def test_accepts_real_titles(self, title: str) -> None:
        assert looks_like_job_title(title)

    def test_rejects_prose(self) -> None:
        assert not looks_like_job_title("Founded in 1906 and headquartered in Norfolk")


TEAM_HTML = """
<html><body>
  <div class="team-member">
    <h3>Jane Doe</h3><p>Senior Vice President</p>
    <a href="mailto:jane@firm.test">Email</a>
    <a href="https://linkedin.com/in/janedoe">LinkedIn</a>
  </div>
  <div class="team-member"><h3>Robert Smith</h3><p>Chief Investment Officer</p></div>
  <div class="team-member"><h3>Maria Vargas</h3><p>Director of Acquisitions</p></div>
  <div class="team-member"><h3>View Profile</h3><p>Managing Director</p></div>
</body></html>
"""


class TestTeamParser:
    @pytest.fixture
    def roster(self) -> object:
        return TeamParser().parse(_result(TEAM_HTML))

    def test_extracts_the_people(self, roster: object) -> None:
        assert roster is not None
        assert {m.name for m in roster.members} == {  # type: ignore[attr-defined]
            "Jane Doe",
            "Robert Smith",
            "Maria Vargas",
        }

    def test_a_button_label_is_not_a_person(self, roster: object) -> None:
        """Regression: "View Profile" was stored as staff against a live site."""
        assert "View Profile" not in {m.name for m in roster.members}  # type: ignore[attr-defined]

    def test_captures_titles_and_contacts(self, roster: object) -> None:
        jane = next(m for m in roster.members if m.name == "Jane Doe")  # type: ignore[attr-defined]
        assert jane.job_title == "Senior Vice President"
        assert jane.email == "jane@firm.test"
        assert "linkedin.com" in (jane.linkedin_url or "")

    def test_a_name_is_never_concatenated_with_its_title(self) -> None:
        """Regression: `text(strip=True)` merged sibling nodes with no space,
        storing "Tim JohnsonChief Investment Officer" as somebody's name."""
        html = """
        <html><body>
          <div class="person"><h3>Tim Johnson</h3><span>Chief Investment Officer</span></div>
          <div class="person"><h3>Ann Lee</h3><span>Managing Director</span></div>
          <div class="person"><h3>Paul Ray</h3><span>Vice President</span></div>
        </body></html>
        """
        roster = TeamParser().parse(_result(html))

        assert roster is not None
        for member in roster.members:
            assert "JohnsonChief" not in member.name
            # No lowercase letter immediately followed by an uppercase one,
            # except in genuine internal capitals which contain no space.
            assert member.name.count(" ") <= 3

    def test_a_job_title_is_not_stored_as_a_persons_name(self) -> None:
        """Regression: when a card's real name was rejected the parser fell through
        to using the title ('Managing Director') as the name instead."""
        html = """
        <html><body>
          <div class="team-member"><h3>View Profile</h3><p>Managing Director</p></div>
          <div class="team-member"><h3>Jane Doe</h3><p>Senior Vice President</p></div>
          <div class="team-member"><h3>Robert Smith</h3><p>Chief Investment Officer</p></div>
          <div class="team-member"><h3>Maria Vargas</h3><p>Director of Acquisitions</p></div>
        </body></html>
        """
        roster = TeamParser().parse(_result(html))

        assert roster is not None
        names = {m.name for m in roster.members}
        assert "Managing Director" not in names
        assert "View Profile" not in names

    def test_a_page_with_no_people_returns_none(self) -> None:
        """None rather than an empty roster: an empty roster downstream would
        mark every existing employee as departed."""
        html = "<html><body><h1>About Us</h1><p>Founded in 1906.</p></body></html>"

        assert TeamParser().parse(_result(html)) is None

    def test_a_name_without_a_title_is_skipped(self) -> None:
        """Article bylines and testimonial names are not staff."""
        html = """
        <html><body>
          <article><h3>Jane Doe</h3><p>Published March 2026 about the market.</p></article>
        </body></html>
        """

        assert TeamParser().parse(_result(html)) is None

    def test_reads_schema_org_person_entries(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Person","name":"Alice Chen","jobTitle":"Managing Partner",
         "email":"alice@firm.test"}
        </script></head><body></body></html>
        """
        roster = TeamParser().parse(_result(html))

        assert roster is not None
        assert roster.members[0].name == "Alice Chen"
        assert roster.members[0].job_title == "Managing Partner"


class TestCareersParser:
    def test_reads_schema_org_job_postings(self) -> None:
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"JobPosting","title":"Development Manager",
         "jobLocation":{"address":{"addressLocality":"Reston","addressRegion":"VA"}}}
        </script></head><body></body></html>
        """
        page = CareersParser().parse(_result(html, "https://firm.test/careers"))

        assert page is not None
        assert page.listings[0].job_title == "Development Manager"
        assert page.listings[0].location == "Reston, VA"

    def test_reads_ats_links(self) -> None:
        html = """
        <html><body>
          <a href="https://boards.greenhouse.io/firm/jobs/1">Acquisitions Analyst</a>
          <a href="https://firm.test/about">About Us</a>
        </body></html>
        """
        page = CareersParser().parse(_result(html, "https://firm.test/careers"))

        assert page is not None
        assert [job.job_title for job in page.listings] == ["Acquisitions Analyst"]

    def test_a_service_page_link_is_not_a_job(self) -> None:
        """ "Property Management" is a service, not an opening."""
        html = """
        <html><body><a href="https://firm.test/services">Property Management</a></body></html>
        """

        assert CareersParser().parse(_result(html, "https://firm.test/careers")) is None

    @pytest.mark.parametrize("label", ["Careers", "Apply Now", "Join Our Team"])
    def test_generic_calls_to_action_are_not_roles(self, label: str) -> None:
        assert not looks_like_role_title(label)


class TestPressReleaseParser:
    ARTICLE = """
    <html><head><meta property="article:published_time" content="2026-03-14T00:00:00Z">
    </head><body>
      <h1>Comstock breaks ground on 240-unit multifamily project in Reston</h1>
      <p>Comstock Holding Companies has broken ground on a new multifamily
      development in Reston, Virginia. The mixed-use construction project spans
      12.5 acres and 310,000 square feet, with an estimated cost of $120 million.
      The developer acquired the land last year and expects delivery in 2028.</p>
    </body></html>
    """

    @pytest.fixture
    def announcement(self) -> object:
        return PressReleaseParser().parse(_result(self.ARTICLE, "https://news.test/comstock"))

    def test_extracts_the_headline(self, announcement: object) -> None:
        assert announcement is not None
        assert "Comstock" in announcement.headline  # type: ignore[attr-defined]

    def test_extracts_magnitudes_with_the_right_units(self, announcement: object) -> None:
        """Confusing 240 units with 310,000 sq ft would put a wrong figure in a
        digest a broker acts on, so each unit is matched explicitly."""
        assert announcement.unit_count == 240  # type: ignore[attr-defined]
        assert announcement.square_feet == 310_000  # type: ignore[attr-defined]
        assert announcement.acreage == 12.5  # type: ignore[attr-defined]

    def test_normalizes_a_written_dollar_amount(self, announcement: object) -> None:
        assert announcement.est_value_usd == 120_000_000  # type: ignore[attr-defined]

    def test_detects_locality_and_asset_class(self, announcement: object) -> None:
        assert announcement.locality == "Reston"  # type: ignore[attr-defined]
        assert announcement.project_type == "multifamily"  # type: ignore[attr-defined]

    def test_reads_the_published_date(self, announcement: object) -> None:
        assert announcement.announced_date is not None  # type: ignore[attr-defined]
        assert announcement.announced_date.year == 2026  # type: ignore[attr-defined]

    def test_a_non_development_article_is_ignored(self) -> None:
        """Otherwise unrelated business news lands in the project table."""
        html = """
        <html><body><h1>Firm names new chief marketing officer</h1>
        <p>The company announced a new marketing hire this week. She previously
        worked in brand strategy and will report to the chief executive. The
        appointment takes effect next month.</p></body></html>
        """

        assert PressReleaseParser().parse(_result(html, "https://news.test/x")) is None

    def test_a_stub_page_is_ignored(self) -> None:
        assert PressReleaseParser().parse(_result("<html><body>Hi</body></html>")) is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("$120 million", 120_000_000),
            ("$1.5 billion", 1_500_000_000),
            ("$4,500,000", 4_500_000),
            ("$750", 750),
        ],
    )
    def test_money_parsing(self, text: str, expected: float) -> None:
        assert parse_money(text) == expected

    def test_money_parsing_returns_none_without_an_amount(self) -> None:
        assert parse_money("no figures disclosed") is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a new data center campus", "data_center"),
            ("300,000 sq ft warehouse and distribution center", "industrial"),
            ("a 240-unit apartment community", "multifamily"),
            ("grocery-anchored retail", "retail"),
        ],
    )
    def test_asset_class_detection(self, text: str, expected: str) -> None:
        assert detect_asset_class(text) == expected
