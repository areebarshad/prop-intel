"""Name normalization.

The tests encode the actual failure modes seen in Virginia permit data: legal
suffixes that vary by county, project LLCs named after phases, and firms whose
names differ only by a descriptor word.
"""

from __future__ import annotations

import pytest

from app.services.naming import (
    basic_normalize,
    canonicalize,
    distinctive_tokens,
    normalize_address,
    normalize_person,
    normalize_phone,
    slugify,
)


class TestBasicNormalize:
    def test_lowercases_and_strips_punctuation(self) -> None:
        assert basic_normalize("Comstock Holding Companies, Inc.") == (
            "comstock holding companies inc"
        )

    def test_expands_ampersand(self) -> None:
        assert basic_normalize("Smith & Jones") == "smith and jones"

    def test_collapses_runs_of_whitespace(self) -> None:
        assert basic_normalize("  Comstock   Holding  ") == "comstock holding"

    def test_strips_accents(self) -> None:
        assert basic_normalize("Société Générale") == "societe generale"


class TestCanonicalize:
    @pytest.mark.parametrize(
        "variant",
        [
            "Comstock Holding Companies, Inc.",
            "COMSTOCK HOLDING COMPANIES INC",
            "Comstock Holding Companies Incorporated",
            "Comstock Holding Cos., Inc.",
            "  comstock   holding companies, inc.  ",
        ],
    )
    def test_renderings_of_one_firm_share_a_key(self, variant: str) -> None:
        """The same developer files differently in every jurisdiction.

        The exact key matters less than the invariant that every rendering
        produces the same one — that is what makes the alias lookup a hit.
        """
        assert canonicalize(variant) == canonicalize("Comstock Holding Companies, Inc.")

    def test_corporate_form_words_are_treated_as_legal_boilerplate(self) -> None:
        """ "Companies"/"Cos" vary by filing clerk, so they carry no identity."""
        assert canonicalize("Comstock Holding Companies, Inc.") == "comstock holding"

    @pytest.mark.parametrize(
        "name",
        ["Acme LLC", "Acme L.L.C.", "Acme, LP", "Acme Ltd.", "Acme Corp"],
    )
    def test_trailing_legal_form_is_dropped(self, name: str) -> None:
        assert canonicalize(name) == "acme"

    def test_phase_markers_are_dropped(self) -> None:
        """Project LLCs are named per phase; the parent is the same firm."""
        assert canonicalize("Reston Station Phase III LLC") == "reston station"

    def test_different_phases_collapse_to_the_same_project(self) -> None:
        assert canonicalize("Reston Station Phase III LLC") == canonicalize(
            "Reston Station Phase IV LLC"
        )

    def test_leading_corporate_word_is_kept(self) -> None:
        """ "Corporate" here is part of the name, not a legal form."""
        assert canonicalize("Corporate Office Properties Trust").startswith("corporate")

    def test_firms_differing_only_by_descriptor_stay_distinct(self) -> None:
        """Collapsing these would attribute one firm's filings to another."""
        assert canonicalize("Rappahannock Capital") != canonicalize("Rappahannock Partners")

    def test_a_name_that_is_only_a_legal_form_is_not_erased(self) -> None:
        """An empty key would collide with every other empty key."""
        assert canonicalize("LLC") == "llc"

    def test_empty_input_stays_empty(self) -> None:
        assert canonicalize("") == ""

    def test_punctuation_only_input_does_not_crash(self) -> None:
        assert canonicalize("...") == ""


class TestDistinctiveTokens:
    def test_descriptor_words_are_removed(self) -> None:
        assert distinctive_tokens("Comstock Holding Companies") == frozenset({"comstock"})

    def test_two_firms_sharing_a_family_name_share_a_token(self) -> None:
        assert distinctive_tokens("Comstock Partners") & distinctive_tokens(
            "Comstock Holding Companies"
        )

    def test_unrelated_firms_share_nothing(self) -> None:
        assert not (distinctive_tokens("Comstock Holding") & distinctive_tokens("JBG Smith"))

    def test_an_all_descriptor_name_still_yields_tokens(self) -> None:
        """Otherwise "The Property Group" would match every firm."""
        assert distinctive_tokens("The Property Group")


class TestNormalizePhone:
    @pytest.mark.parametrize(
        "raw", ["(703) 555-0100", "703-555-0100", "+1 703 555 0100", "17035550100"]
    )
    def test_renderings_compare_equal(self, raw: str) -> None:
        assert normalize_phone(raw) == "7035550100"

    def test_too_short_is_rejected_rather_than_half_matched(self) -> None:
        assert normalize_phone("555-0100") is None

    def test_none_passes_through(self) -> None:
        assert normalize_phone(None) is None


class TestNormalizeAddress:
    def test_street_types_are_abbreviated_consistently(self) -> None:
        assert normalize_address("1900 Reston Boulevard, Suite 200") == (
            normalize_address("1900 Reston Blvd Ste 200")
        )

    def test_directionals_are_abbreviated(self) -> None:
        assert normalize_address("100 North Main Street") == (normalize_address("100 N Main St"))

    def test_none_passes_through(self) -> None:
        assert normalize_address(None) is None


class TestNormalizePerson:
    def test_credentials_are_dropped(self) -> None:
        assert normalize_person("Robert Smith, CCIM") == "robert smith"

    def test_generational_suffix_is_dropped(self) -> None:
        assert normalize_person("Robert Smith Jr.") == "robert smith"

    def test_honorific_is_dropped(self) -> None:
        assert normalize_person("Dr. Jane Doe") == "jane doe"

    def test_nicknames_are_not_guessed(self) -> None:
        """Merging Bob and Robert would silently fabricate a departure+hire."""
        assert normalize_person("Bob Smith") != normalize_person("Robert Smith")


class TestSlugify:
    def test_produces_a_url_safe_identifier(self) -> None:
        assert slugify("Comstock Holding Companies, Inc.") == ("comstock-holding-companies-inc")

    def test_never_returns_empty(self) -> None:
        assert slugify("...") == "firm"

    def test_respects_max_length(self) -> None:
        assert len(slugify("a" * 500)) == 160
