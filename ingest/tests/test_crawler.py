"""Tests for depth-aware crawler (Phase 2, Step 1).

No network calls — link extraction is pure HTML parsing.
"""

from __future__ import annotations

from ingest.crawler import extract_links

BASE = "https://virginiabusiness.com/category/real-estate/"

_INDEX_HTML = """
<html><body>
<a href="/2024/01/comstock-breaks-ground/">Comstock article</a>
<a href="/2023/12/jbg-industrial/">JBG industrial</a>
<a href="/about">About</a>
<a href="https://otherdomain.com/ad">Ad</a>
<a href="#">anchor</a>
<a href="javascript:void(0)">JS link</a>
</body></html>
"""


def test_extract_links_same_domain_no_pattern() -> None:
    links = extract_links(_INDEX_HTML, BASE)
    # Only same-domain links, excluding anchors and JS
    assert all("virginiabusiness.com" in u for u in links)
    assert not any("otherdomain.com" in u for u in links)


def test_extract_links_with_date_pattern() -> None:
    pattern = r"virginiabusiness\.com/\d{4}/\d{2}/"
    links = extract_links(_INDEX_HTML, BASE, pattern)
    assert len(links) == 2
    assert all("/2024/" in u or "/2023/" in u for u in links)


def test_extract_links_skips_base_url() -> None:
    html = f'<a href="{BASE}">self</a><a href="/2024/01/story/">story</a>'
    links = extract_links(html, BASE)
    assert not any(u.rstrip("/") == BASE.rstrip("/") for u in links)


def test_extract_links_ats_pattern_allows_external() -> None:
    html = """
    <a href="https://boards.greenhouse.io/stanleymartin">Apply here</a>
    <a href="/careers">Careers</a>
    """
    pattern = r"greenhouse\.io|lever\.co"
    links = extract_links(html, "https://stanleymartin.com/careers", pattern)
    assert any("greenhouse.io" in u for u in links)


def test_extract_links_deduplicates() -> None:
    html = """
    <a href="/article/">link</a>
    <a href="/article/">link again</a>
    """
    links = extract_links(html, "https://example.com/", "")
    assert len(links) == 1


def test_extract_links_empty_html() -> None:
    assert extract_links("", "https://example.com/") == []
