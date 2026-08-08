"""Linking raw name mentions to firms.

This is the step that turns a pile of public records into competitive
intelligence. A permit filed by "Reston Station Phase III LLC" is worthless
until it is attributed to Comstock; once it is, it joins that firm's timeline,
feeds its asset-class mix, and can fire an alert.

The ladder, cheapest and most certain first:

  1. exact match on the canonical name
  2. exact match on a known alias
  3. fuzzy match, high confidence          (rapidfuzz)
  4. fuzzy match, corroborated by a shared address, phone, or parcel
  5. LLM adjudication for the ambiguous band
  6. review queue — never a guess

Steps 1-2 are free and cover most repeat filings, because every accepted match
from steps 3-5 writes an alias back. The corpus teaches itself: the first
encounter with a new LLC may cost an LLM call, the next thousand cost a
single indexed lookup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from rapidfuzz import fuzz, process

from app.models.enums import ResolutionMethod, ResolutionStatus
from app.services.naming import (
    canonicalize,
    distinctive_tokens,
    normalize_address,
    normalize_phone,
)

log = logging.getLogger(__name__)

# At or above this, a fuzzy match is accepted on its own.
AUTO_ACCEPT_SCORE = 92.0

# Between this and AUTO_ACCEPT_SCORE, the match needs corroboration or an LLM
# adjudication. Below it, the candidate is not considered at all.
REVIEW_FLOOR_SCORE = 80.0

# Corroborating evidence (shared address, phone, or parcel) is worth this much
# added score. Tuned so a corroborated 82 clears the auto-accept bar but an
# uncorroborated one never does.
CORROBORATION_BONUS = 12.0

# A candidate that shares no distinctive token with the mention is rejected
# regardless of character-level similarity. "Rappahannock Capital" and
# "Rappahannock Partners" score highly and are different firms; requiring a
# shared distinctive token is what keeps them apart.
REQUIRE_SHARED_TOKEN = True

# Dilution guard. token_set_ratio scores 100 whenever the firm's tokens are a
# subset of the mention's, so "Stanley Martin Excavating Subcontractors of
# Virginia LLC" scores a perfect 100 against "Stanley Martin" — an unrelated
# subcontractor that would be auto-attributed to a tracked developer.
# token_sort_ratio penalizes the extra content (42 for that string, 82 for the
# genuine "Stanley Martin Homes"), so requiring a floor on it rejects the
# diluted matches while keeping real subsidiaries.
MIN_TOKEN_SORT_SCORE = 60.0

# How many candidates to score. The trigram index narrows the field first; this
# bounds the fuzzy pass.
MAX_CANDIDATES = 25


@dataclass(frozen=True, slots=True)
class FirmRecord:
    """The minimum a resolver needs to know about a firm.

    A plain value object rather than the ORM model so the ladder can be unit
    tested without a database.
    """

    id: str
    name: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    address: str | None = None
    phone: str | None = None
    parcel_ids: tuple[str, ...] = ()

    @property
    def tokens(self) -> frozenset[str]:
        return distinctive_tokens(self.name)


@dataclass(frozen=True, slots=True)
class Mention:
    """A name encountered in a document, plus whatever context came with it."""

    text: str
    address: str | None = None
    phone: str | None = None
    parcel_id: str | None = None
    context: str | None = None

    @property
    def canonical(self) -> str:
        return canonicalize(self.text)

    @property
    def tokens(self) -> frozenset[str]:
        return distinctive_tokens(self.text)


@dataclass(frozen=True, slots=True)
class Candidate:
    firm: FirmRecord
    score: float
    corroborated: bool = False
    corroboration: tuple[str, ...] = ()

    @property
    def effective_score(self) -> float:
        bonus = CORROBORATION_BONUS if self.corroborated else 0.0
        return min(100.0, self.score + bonus)


@dataclass(slots=True)
class Resolution:
    """The outcome, with enough detail to audit or reverse it later."""

    status: ResolutionStatus
    firm_id: str | None = None
    method: ResolutionMethod | None = None
    confidence: float = 0.0
    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.status is ResolutionStatus.RESOLVED and self.firm_id is not None

    @property
    def should_write_alias(self) -> bool:
        """Whether to memoize this match so the next encounter is an exact hit.

        Exact and alias hits are already exact, so there is nothing to learn.
        """
        return self.is_resolved and self.method in {
            ResolutionMethod.FUZZY,
            ResolutionMethod.CORROBORATED,
            ResolutionMethod.LLM,
        }


class Adjudicator(Protocol):
    """Breaks ties in the ambiguous band.

    A protocol so the resolver can be tested with a deterministic fake — the
    ladder's behavior must be verifiable without spending money or depending on
    a network.
    """

    def choose(
        self, mention: Mention, candidates: list[Candidate]
    ) -> tuple[str | None, float, str]:
        """Return (firm_id or None, confidence 0-1, one-line rationale)."""
        ...


class EntityResolver:
    """Resolves mentions against a fixed set of firms.

    Construct once per batch with the firms in scope, then call `resolve` per
    mention. Alias lookups are built once up front rather than per call.
    """

    def __init__(
        self,
        firms: list[FirmRecord],
        *,
        adjudicator: Adjudicator | None = None,
        auto_accept_score: float = AUTO_ACCEPT_SCORE,
        review_floor_score: float = REVIEW_FLOOR_SCORE,
    ) -> None:
        self.firms = firms
        self.adjudicator = adjudicator
        self.auto_accept_score = auto_accept_score
        self.review_floor_score = review_floor_score

        self._by_canonical: dict[str, FirmRecord] = {}
        self._by_alias: dict[str, FirmRecord] = {}
        for firm in firms:
            self._by_canonical.setdefault(firm.canonical_name, firm)
            for alias in firm.aliases:
                self._by_alias.setdefault(canonicalize(alias), firm)

        # Parallel lists for rapidfuzz's vectorized scorer.
        self._choices = [firm.canonical_name for firm in firms]

    def resolve(self, mention: Mention) -> Resolution:
        key = mention.canonical
        if not key:
            return Resolution(
                status=ResolutionStatus.UNMATCHED, reason="mention normalized to empty"
            )

        exact = self._by_canonical.get(key)
        if exact is not None:
            return Resolution(
                status=ResolutionStatus.RESOLVED,
                firm_id=exact.id,
                method=ResolutionMethod.EXACT,
                confidence=1.0,
                reason="canonical name matched exactly",
            )

        aliased = self._by_alias.get(key)
        if aliased is not None:
            return Resolution(
                status=ResolutionStatus.RESOLVED,
                firm_id=aliased.id,
                method=ResolutionMethod.ALIAS,
                confidence=1.0,
                reason="matched a known alias",
            )

        candidates = self._score_candidates(mention)
        if not candidates:
            return Resolution(
                status=ResolutionStatus.UNMATCHED,
                reason="no candidate above the review floor",
            )

        best = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None

        # Two candidates that score the same are a genuine ambiguity. Picking
        # either would be a coin flip attributing a competitor's activity to the
        # wrong firm, so this always goes to adjudication or review.
        tie = runner_up is not None and abs(best.effective_score - runner_up.effective_score) < 2.0

        if best.effective_score >= self.auto_accept_score and not tie:
            method = ResolutionMethod.CORROBORATED if best.corroborated else ResolutionMethod.FUZZY
            detail = (
                f" corroborated by {', '.join(best.corroboration)}" if best.corroborated else ""
            )
            return Resolution(
                status=ResolutionStatus.RESOLVED,
                firm_id=best.firm.id,
                method=method,
                confidence=best.effective_score / 100.0,
                candidates=candidates,
                reason=f"fuzzy score {best.score:.1f}{detail}",
            )

        if self.adjudicator is not None:
            firm_id, confidence, rationale = self.adjudicator.choose(mention, candidates)
            if firm_id is not None:
                return Resolution(
                    status=ResolutionStatus.RESOLVED,
                    firm_id=firm_id,
                    method=ResolutionMethod.LLM,
                    confidence=confidence,
                    candidates=candidates,
                    reason=rationale,
                )
            return Resolution(
                status=ResolutionStatus.AMBIGUOUS,
                candidates=candidates,
                reason=rationale or "adjudicator declined to choose",
            )

        return Resolution(
            status=ResolutionStatus.AMBIGUOUS,
            candidates=candidates,
            reason=(
                f"best score {best.effective_score:.1f} below auto-accept"
                if not tie
                else "top candidates too close to separate"
            ),
        )

    def _score_candidates(self, mention: Mention) -> list[Candidate]:
        if not self._choices:
            return []

        # token_set_ratio ignores word order and duplication, which is what
        # "Comstock Holding Companies" vs "Holding Companies Comstock" needs.
        matches = process.extract(
            mention.canonical,
            self._choices,
            scorer=fuzz.token_set_ratio,
            limit=MAX_CANDIDATES,
            score_cutoff=self.review_floor_score,
        )

        mention_tokens = mention.tokens
        candidates: list[Candidate] = []
        for _, score, index in matches:
            firm = self.firms[index]

            if REQUIRE_SHARED_TOKEN and not (mention_tokens & firm.tokens):
                continue

            # Reject matches that are mostly other content — see the comment on
            # MIN_TOKEN_SORT_SCORE.
            sort_score = fuzz.token_sort_ratio(mention.canonical, firm.canonical_name)
            if sort_score < MIN_TOKEN_SORT_SCORE:
                log.debug(
                    "rejecting diluted match %r ~ %r (set=%.1f sort=%.1f)",
                    mention.text,
                    firm.name,
                    score,
                    sort_score,
                )
                continue

            corroboration = self._corroborate(mention, firm)
            candidates.append(
                Candidate(
                    firm=firm,
                    score=float(score),
                    corroborated=bool(corroboration),
                    corroboration=corroboration,
                )
            )

        candidates.sort(key=lambda c: c.effective_score, reverse=True)
        return candidates

    @staticmethod
    def _corroborate(mention: Mention, firm: FirmRecord) -> tuple[str, ...]:
        """Non-name evidence that this mention and this firm are the same entity.

        A single-purpose LLC typically files from the parent developer's
        address and phone, which is exactly the signal that rescues a name that
        looks nothing like the parent's.
        """
        evidence: list[str] = []

        mention_phone = normalize_phone(mention.phone)
        if mention_phone and mention_phone == normalize_phone(firm.phone):
            evidence.append("phone")

        mention_address = normalize_address(mention.address)
        if mention_address and mention_address == normalize_address(firm.address):
            evidence.append("address")

        if mention.parcel_id and mention.parcel_id in firm.parcel_ids:
            evidence.append("parcel")

        return tuple(evidence)
