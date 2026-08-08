"""Entity resolution.

The accuracy test at the bottom runs a labeled set of applicant strings in the
shapes Virginia permit portals actually produce. It is the gate the plan sets
for Phase 1: at least 90% correct attribution, and — more important — zero
wrong attributions, because a wrong link silently credits one developer's land
acquisition to a competitor.
"""

from __future__ import annotations

import pytest

from app.models.enums import ResolutionMethod, ResolutionStatus
from app.services.entity_resolution import (
    Candidate,
    EntityResolver,
    FirmRecord,
    Mention,
)
from app.services.naming import canonicalize

COMSTOCK = FirmRecord(
    id="comstock",
    name="Comstock Holding Companies, Inc.",
    canonical_name=canonicalize("Comstock Holding Companies, Inc."),
    aliases=("Comstock Partners", "CHCI"),
    address="1900 Reston Metro Plaza, Reston, VA",
    phone="(703) 230-1985",
)
JBG = FirmRecord(
    id="jbg",
    name="JBG SMITH Properties",
    canonical_name=canonicalize("JBG SMITH Properties"),
    address="4747 Bethesda Ave, Bethesda, MD",
    phone="(240) 333-3600",
)
STANLEY_MARTIN = FirmRecord(
    id="stanley_martin",
    name="Stanley Martin Companies, LLC",
    canonical_name=canonicalize("Stanley Martin Companies, LLC"),
    address="11710 Plaza America Dr, Reston, VA",
)
RAPPAHANNOCK_CAPITAL = FirmRecord(
    id="rapp_capital",
    name="Rappahannock Capital",
    canonical_name=canonicalize("Rappahannock Capital"),
)
RAPPAHANNOCK_PARTNERS = FirmRecord(
    id="rapp_partners",
    name="Rappahannock Partners",
    canonical_name=canonicalize("Rappahannock Partners"),
)

FIRMS = [COMSTOCK, JBG, STANLEY_MARTIN, RAPPAHANNOCK_CAPITAL, RAPPAHANNOCK_PARTNERS]


@pytest.fixture
def resolver() -> EntityResolver:
    return EntityResolver(FIRMS)


class _FakeAdjudicator:
    """Deterministic stand-in for the Haiku-tier adjudicator.

    Picks the top candidate when its lead is decisive, otherwise declines — the
    same shape of judgment the model is asked for, without a network call.
    """

    def __init__(self, *, decisive_margin: float = 3.0) -> None:
        self.decisive_margin = decisive_margin
        self.calls = 0

    def choose(
        self, mention: Mention, candidates: list[Candidate]
    ) -> tuple[str | None, float, str]:
        self.calls += 1
        if not candidates:
            return None, 0.0, "no candidates"
        best = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        if (
            runner_up is None
            or best.effective_score - runner_up.effective_score >= self.decisive_margin
        ):
            return best.firm.id, 0.9, "clear leader among candidates"
        return None, 0.0, "candidates too close"


class TestExactAndAlias:
    def test_exact_canonical_match(self, resolver: EntityResolver) -> None:
        result = resolver.resolve(Mention("Comstock Holding Companies, Inc."))

        assert result.firm_id == "comstock"
        assert result.method is ResolutionMethod.EXACT
        assert result.confidence == 1.0

    def test_a_different_rendering_still_hits_exactly(self, resolver: EntityResolver) -> None:
        assert resolver.resolve(Mention("COMSTOCK HOLDING COS INC")).method is (
            ResolutionMethod.EXACT
        )

    def test_known_alias_match(self, resolver: EntityResolver) -> None:
        result = resolver.resolve(Mention("CHCI"))

        assert result.firm_id == "comstock"
        assert result.method is ResolutionMethod.ALIAS

    def test_exact_and_alias_matches_are_not_re_memoized(self, resolver: EntityResolver) -> None:
        """There is nothing to learn from a hit that was already exact."""
        assert not resolver.resolve(Mention("CHCI")).should_write_alias


class TestFuzzy:
    def test_a_close_variant_resolves(self, resolver: EntityResolver) -> None:
        result = resolver.resolve(Mention("Stanley Martin Comapnies LLC"))  # typo

        assert result.firm_id == "stanley_martin"
        assert result.method is ResolutionMethod.FUZZY

    def test_an_accepted_fuzzy_match_is_memoized(self, resolver: EntityResolver) -> None:
        """The next thousand filings under this name become exact lookups."""
        assert resolver.resolve(Mention("Stanley Martin Comapnies LLC")).should_write_alias

    def test_an_unrelated_name_does_not_resolve(self, resolver: EntityResolver) -> None:
        result = resolver.resolve(Mention("Blue Ridge Excavating Services"))

        assert result.status is ResolutionStatus.UNMATCHED
        assert result.firm_id is None

    def test_a_shared_token_is_required(self, resolver: EntityResolver) -> None:
        """Character similarity alone is not evidence of identity."""
        result = resolver.resolve(Mention("Properties JBG Holdings of Virginia"))

        assert result.firm_id in {None, "jbg"}

    def test_a_subcontractor_sharing_a_firms_name_is_not_attributed_to_it(
        self, resolver: EntityResolver
    ) -> None:
        """token_set_ratio alone scores this a perfect 100 against the developer.

        The firm's tokens are a subset of the mention's, so without the
        dilution guard an unrelated excavating contractor would be credited
        with a tracked developer's filings.
        """
        result = resolver.resolve(
            Mention("Stanley Martin Excavating Subcontractors of Virginia LLC")
        )

        assert result.firm_id is None

    def test_a_reordered_name_buried_in_another_business_is_rejected(
        self, resolver: EntityResolver
    ) -> None:
        result = resolver.resolve(Mention("Martin Stanley Plumbing and Heating"))

        assert result.firm_id is None

    def test_a_genuine_affiliate_still_resolves(self, resolver: EntityResolver) -> None:
        """The guard must not be so strict that real subsidiaries fall out."""
        assert resolver.resolve(Mention("Stanley Martin Homes")).firm_id == ("stanley_martin")


class TestAmbiguity:
    def test_two_equally_good_candidates_are_never_guessed(self) -> None:
        """Picking either would be a coin flip on whose activity this is."""
        resolver = EntityResolver([RAPPAHANNOCK_CAPITAL, RAPPAHANNOCK_PARTNERS])

        result = resolver.resolve(Mention("Rappahannock Holdings LLC"))

        assert result.status is not ResolutionStatus.RESOLVED

    def test_ambiguous_results_carry_their_candidates_for_review(self) -> None:
        resolver = EntityResolver([RAPPAHANNOCK_CAPITAL, RAPPAHANNOCK_PARTNERS])

        result = resolver.resolve(Mention("Rappahannock Holdings LLC"))

        if result.status is ResolutionStatus.AMBIGUOUS:
            assert result.candidates
            assert result.reason


class TestCorroboration:
    def test_a_project_llc_resolves_via_a_shared_phone(self) -> None:
        """The signal that rescues names sharing nothing with the parent.

        Single-purpose LLCs are named after the project, not the developer, but
        they file from the parent's phone and address.
        """
        resolver = EntityResolver(FIRMS)
        weak = Mention("Comstock Reston Station Phase III LLC")
        strong = Mention("Comstock Reston Station Phase III LLC", phone="703-230-1985")

        assert resolver.resolve(strong).confidence >= resolver.resolve(weak).confidence

    def test_corroboration_is_recorded_for_audit(self) -> None:
        resolver = EntityResolver(FIRMS)

        result = resolver.resolve(Mention("Comstock Holding Cmpanies", phone="(703) 230-1985"))

        if result.method is ResolutionMethod.CORROBORATED:
            assert "phone" in result.reason

    def test_a_mismatched_phone_adds_nothing(self) -> None:
        resolver = EntityResolver(FIRMS)

        result = resolver.resolve(Mention("Comstock Holding Cmpanies", phone="(202) 000-0000"))

        assert result.method is not ResolutionMethod.CORROBORATED


class TestAdjudicator:
    # Scores ~87 against Stanley Martin: above the review floor, below
    # auto-accept. That is the band the adjudicator exists to settle. An exact
    # match short-circuits the whole ladder regardless of thresholds, which is
    # the point of ordering it first.
    FUZZY_MENTION = "Stanleigh Martin Co"

    def test_an_exact_match_never_reaches_the_adjudicator(self) -> None:
        adjudicator = _FakeAdjudicator()
        resolver = EntityResolver(FIRMS, adjudicator=adjudicator, auto_accept_score=99.5)

        result = resolver.resolve(Mention("Stanley Martin Companies, LLC"))

        assert result.method is ResolutionMethod.EXACT
        assert adjudicator.calls == 0

    def test_an_adjudicated_match_is_labeled_as_such(self) -> None:
        adjudicator = _FakeAdjudicator()
        resolver = EntityResolver(FIRMS, adjudicator=adjudicator, auto_accept_score=99.5)

        result = resolver.resolve(Mention(self.FUZZY_MENTION))

        assert adjudicator.calls == 1
        assert result.method is ResolutionMethod.LLM
        assert result.should_write_alias

    def test_a_declining_adjudicator_yields_review_not_a_guess(self) -> None:
        class _AlwaysDeclines:
            def choose(
                self, mention: Mention, candidates: list[Candidate]
            ) -> tuple[str | None, float, str]:
                return None, 0.0, "not confident"

        resolver = EntityResolver(FIRMS, adjudicator=_AlwaysDeclines(), auto_accept_score=99.5)

        result = resolver.resolve(Mention(self.FUZZY_MENTION))

        assert result.status is ResolutionStatus.AMBIGUOUS
        assert result.firm_id is None

    def test_without_an_adjudicator_the_band_goes_to_review(self) -> None:
        resolver = EntityResolver(FIRMS, auto_accept_score=99.5)

        result = resolver.resolve(Mention(self.FUZZY_MENTION))

        assert result.status is ResolutionStatus.AMBIGUOUS


class TestEdgeCases:
    def test_empty_mention(self, resolver: EntityResolver) -> None:
        assert resolver.resolve(Mention("")).status is ResolutionStatus.UNMATCHED

    def test_punctuation_only_mention(self, resolver: EntityResolver) -> None:
        assert resolver.resolve(Mention("---")).status is ResolutionStatus.UNMATCHED

    def test_no_firms_configured(self) -> None:
        assert EntityResolver([]).resolve(Mention("Anything")).status is (
            ResolutionStatus.UNMATCHED
        )


# (applicant string as filed, expected firm id or None)
LABELED_APPLICANTS: list[tuple[str, str | None]] = [
    ("Comstock Holding Companies, Inc.", "comstock"),
    ("COMSTOCK HOLDING COMPANIES INC", "comstock"),
    ("Comstock Holding Cos., Inc.", "comstock"),
    ("Comstock Partners", "comstock"),
    ("CHCI", "comstock"),
    ("Comstock Holding Companies", "comstock"),
    ("comstock holding companies inc.", "comstock"),
    ("JBG SMITH Properties", "jbg"),
    ("JBG SMITH PROPERTIES LP", "jbg"),
    ("JBG Smith Properties, L.P.", "jbg"),
    ("JBG SMITH", "jbg"),
    ("Stanley Martin Companies, LLC", "stanley_martin"),
    ("STANLEY MARTIN COMPANIES LLC", "stanley_martin"),
    ("Stanley Martin Homes", "stanley_martin"),
    ("Stanley Martin Companies", "stanley_martin"),
    ("Rappahannock Capital", "rapp_capital"),
    ("RAPPAHANNOCK CAPITAL LLC", "rapp_capital"),
    ("Rappahannock Partners", "rapp_partners"),
    ("RAPPAHANNOCK PARTNERS, LP", "rapp_partners"),
    # Genuinely unrelated filers — subcontractors and homeowners dominate real
    # permit data and must not be forced onto a tracked firm.
    ("Blue Ridge Excavating Services", None),
    ("Dominion Energy Virginia", None),
    ("Smith Residence", None),
    ("ABC Plumbing & Heating LLC", None),
    ("County of Fairfax", None),
    # Share every token with a tracked firm but are not that firm. These are
    # the cases token_set_ratio alone gets wrong with a perfect 100.
    ("Stanley Martin Excavating Subcontractors of Virginia LLC", None),
    ("Martin Stanley Plumbing and Heating", None),
]


class TestAccuracyOnLabeledApplicants:
    """The Phase 1 exit gate."""

    @pytest.fixture
    def outcomes(self) -> list[tuple[str, str | None, str | None]]:
        resolver = EntityResolver(FIRMS)
        return [
            (raw, expected, resolver.resolve(Mention(raw)).firm_id)
            for raw, expected in LABELED_APPLICANTS
        ]

    def test_no_mention_is_attributed_to_the_wrong_firm(
        self, outcomes: list[tuple[str, str | None, str | None]]
    ) -> None:
        """The failure that matters most.

        A miss leaves a visible gap; a wrong link silently credits a
        competitor's activity to the wrong developer and is invisible.
        """
        wrong = [
            (raw, expected, actual)
            for raw, expected, actual in outcomes
            if actual is not None and actual != expected
        ]
        assert not wrong, f"misattributed: {wrong}"

    def test_accuracy_clears_ninety_percent(
        self, outcomes: list[tuple[str, str | None, str | None]]
    ) -> None:
        correct = sum(1 for _, expected, actual in outcomes if actual == expected)
        accuracy = correct / len(outcomes)
        misses = [
            (raw, expected, actual) for raw, expected, actual in outcomes if actual != expected
        ]
        assert accuracy >= 0.90, f"accuracy {accuracy:.0%}; misses: {misses}"

    def test_unrelated_filers_are_left_unresolved(
        self, outcomes: list[tuple[str, str | None, str | None]]
    ) -> None:
        for raw, expected, actual in outcomes:
            if expected is None:
                assert actual is None, f"{raw!r} wrongly resolved to {actual}"
