"""Name normalization for entity resolution.

Everything here exists to answer one question: are two strings referring to the
same firm? Permit portals, news articles, and company websites each render the
same developer differently — "Comstock Holding Companies, Inc.", "COMSTOCK
HOLDING COS INC", "Comstock" — and permits are usually filed by a
single-purpose LLC named after the project rather than the parent at all.

Normalization is deliberately conservative. Stripping too much collapses
genuinely different firms ("Rappahannock Capital" and "Rappahannock Partners"
are not the same company); stripping too little leaves every filing unmatched.
"""

from __future__ import annotations

import re
import unicodedata

# Corporate form suffixes. These carry no identifying information — the same
# firm files as "Inc." in one county and "Incorporated" in the next.
_ENTITY_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "lc",
        "llp",
        "lllp",
        "lp",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "companies",
        "cos",
        "plc",
        "pc",
        "pa",
        "trust",
        "reit",
        "gp",
        "sarl",
        "nv",
        "sa",
        "ag",
    }
)

# Words that describe what a firm does rather than who it is. Removed only when
# they are not the entire name, so a firm literally called "The Property Group"
# does not normalize to nothing.
_DESCRIPTOR_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "group",
        "holding",
        "holdings",
        "development",
        "developments",
        "developers",
        "properties",
        "property",
        "realty",
        "real",
        "estate",
        "partners",
        "partnership",
        "associates",
        "ventures",
        "venture",
        "capital",
        "investments",
        "investment",
        "management",
        "enterprises",
        "builders",
        "building",
        "construction",
        "homes",
        "communities",
    }
)

# "Reston Station Phase III LLC" and "Reston Station Phase IV LLC" are two
# project entities of one developer. The phase marker is noise for matching the
# parent but must not be confused with a different project.
_PHASE_PATTERN = re.compile(
    r"\b(?:phase|ph|tract|parcel|section|sect|bldg|building|lot|unit)\s*"
    r"(?:[0-9]+|[ivxlcdm]+|[a-z])\b",
    re.IGNORECASE,
)

_ROMAN_NUMERAL = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def basic_normalize(name: str) -> str:
    """Lowercase, de-accent, drop punctuation, collapse whitespace.

    The floor every other function builds on. Does not remove any words, so it
    stays safe for display and for exact-match lookups.
    """
    text = strip_accents(name).lower()
    text = text.replace("&", " and ")
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def canonicalize(name: str) -> str:
    """The matching key: normalized, with corporate suffixes and phases removed.

    Returns a string suitable for equality comparison and for storage in
    ``firms.canonical_name`` / ``firm_aliases.canonical_alias``.

    Falls back progressively rather than ever returning an empty string — an
    empty key would collide with every other empty key and silently merge
    unrelated firms.
    """
    text = _PHASE_PATTERN.sub(" ", basic_normalize(name))
    tokens = [t for t in text.split() if t]
    if not tokens:
        return basic_normalize(name)

    # Drop trailing corporate suffixes only. A leading "Corp" is part of a name
    # ("Corporate Office Properties Trust"); a trailing one is a legal form.
    while len(tokens) > 1 and tokens[-1] in _ENTITY_SUFFIXES:
        tokens.pop()

    # Also drop suffixes that ended up interior via "LLC" in the middle.
    tokens = [t for t in tokens if t not in _ENTITY_SUFFIXES] or tokens

    # Bare roman numerals left over from a stripped phase marker.
    tokens = [t for t in tokens if not _ROMAN_NUMERAL.match(t)] or tokens

    return " ".join(tokens)


def distinctive_tokens(name: str) -> frozenset[str]:
    """The tokens that actually identify a firm.

    "Comstock Holding Companies" and "Comstock Partners" share only "comstock",
    which is precisely the token that matters. Descriptor words are removed
    here but *not* in `canonicalize`, because two firms can legitimately differ
    only by descriptor and the canonical key must keep them apart.
    """
    tokens = {t for t in canonicalize(name).split() if t not in _DESCRIPTOR_WORDS}
    return frozenset(tokens) or frozenset(canonicalize(name).split())


def normalize_person(name: str) -> str:
    """Canonical form for a person's name.

    Team pages render the same person as "Robert Smith", "Bob Smith, CCIM", and
    "Robert Smith Jr." Honorifics and credentials go; given names are left
    alone, since guessing nicknames would merge distinct people.
    """
    text = basic_normalize(name)
    tokens = [t for t in text.split() if t]

    honorifics = {"mr", "mrs", "ms", "miss", "dr", "prof"}
    credentials = {
        "jr",
        "sr",
        "ii",
        "iii",
        "iv",
        "phd",
        "mba",
        "esq",
        "ccim",
        "sior",
        "leed",
        "ap",
        "aia",
        "pe",
        "cpa",
        "cfa",
    }

    tokens = [t for t in tokens if t not in honorifics]
    while tokens and tokens[-1] in credentials:
        tokens.pop()

    return " ".join(tokens)


def normalize_phone(phone: str | None) -> str | None:
    """Digits only, US country code dropped.

    A shared phone number is strong corroborating evidence that two names are
    one firm, but only if "(703) 555-0100" and "+1 703-555-0100" compare equal.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


_STREET_TYPES = {
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "road": "rd",
    "lane": "ln",
    "court": "ct",
    "circle": "cir",
    "parkway": "pkwy",
    "highway": "hwy",
    "place": "pl",
    "terrace": "ter",
    "square": "sq",
    "turnpike": "tpke",
    "suite": "ste",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}


def normalize_address(address: str | None) -> str | None:
    """Normalize a street address enough to compare two renderings of one place.

    Not a substitute for a real geocoder — it will not match "11465 Sunset Hills"
    to its Rt-267 alias. It handles the common case: the same address written
    with "Suite" vs "Ste" and "Boulevard" vs "Blvd".
    """
    if not address:
        return None
    text = basic_normalize(address)
    tokens = [_STREET_TYPES.get(t, t) for t in text.split()]
    return " ".join(tokens) or None


def slugify(name: str, *, max_length: int = 160) -> str:
    """URL-safe identifier for a firm."""
    text = basic_normalize(name).replace(" ", "-")
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:max_length] or "firm"
