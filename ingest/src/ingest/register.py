"""Register PropIntel's parsers into webscraper-core's task registry.

This is what the upstream `register_parser()` hook exists for: PropIntel adds
real-estate tasks without forking the scraper, and in exchange inherits its
whole ladder — robots gating, per-host throttling, user-agent rotation,
retries, static fetch with Playwright escalation, and the opt-in Claude
fallback — unchanged.

Import this module once before calling `get_parser`. Registration is idempotent
so importing it twice (CLI plus a test) is safe.
"""

from __future__ import annotations

from webscraper_core.parsers.registry import register_parser

from ingest.parsers.careers import CareersParser
from ingest.parsers.press import PressReleaseParser
from ingest.parsers.team import TeamParser

PARSERS = (TeamParser, CareersParser, PressReleaseParser)

for _parser in PARSERS:
    register_parser(_parser)

__all__ = ["PARSERS", "CareersParser", "PressReleaseParser", "TeamParser"]
