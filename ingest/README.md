# propintel-ingest

Scrapers, document ingesters, and open-data adapters that feed PropIntel.

HTML fetching, robots compliance, throttling, and the Playwright escalation
ladder all come from `webscraper-core`; this package adds the real-estate
parsers, the PyMuPDF document path, the ArcGIS/Socrata adapters, and the
persistence layer that lands everything in `raw_documents`.

```bash
uv run propintel-ingest --help
uv run propintel-ingest crawl-firms --limit 5 --dry-run
```
