# 🏗️ PropIntel

> **Competitive intelligence for Virginia commercial real estate — before it hits the trade press.**

PropIntel is an automated intelligence platform that tracks permit filings, team changes, and development announcements across Virginia's CRE market, and surfaces actionable signals in one place.

---

## 🔍 What It Does

Most CRE professionals learn about competitor moves *after* they're public. PropIntel flips that. It continuously crawls public data sources — permit portals, company websites, careers pages, and press releases — resolves messy LLC names back to their real developer parent, and delivers clean signals to your inbox or Slack before anyone else picks up the story.

---

## 📊 By the Numbers

| | |
|---|---|
| 🏢 **20+ firms** tracked | 📄 **419+ permits** ingested |
| ⚡ **148+ signals** generated | ✅ **0** misattributions |

---

## ✨ Platform Capabilities

### 📂 Automated Document Parsing & Ingestion
Crawls permit portals, team pages, careers pages, and press releases with Playwright-backed fallback for JavaScript-heavy sites. A **6-rung entity resolution system** maps raw permit applicant LLCs to known parent developers — zero misattributions across 395 name mentions.

### 🔎 Semantic Search & AI-Powered Q&A
Ask questions about any tracked firm and get grounded answers with source URL and page-level citations. Powered by 384-dimension embeddings (ONNX Runtime) and pgvector HNSW indexing under the hood.

### 📈 Trend & Anomaly Detection
Four automated signal detectors run continuously:
- **Hiring surge** — 1.5× above baseline
- **Asset-class pivot** — 30%/10% shift threshold
- **Geographic expansion** — first permit in a new locality
- **Permit volume anomaly** — Z-score statistical spike

### 🔔 Smart Alerts & Weekly Digest
Get notified via **email or Slack** the moment a signal fires. Every week, an AI-written digest summarizes each firm's activity in plain English — no dashboards to check.

### 📡 Unified Signal Stream
Hiring moves, permit filings, press announcements, and derived trend signals all land in a single feed. Alerts, digests, and firm timelines read from the same source — no duplicate data, no drift.

### 🔒 Enterprise-Grade Auth & Rate Limiting
Per-tier JWT + API key authentication with Redis-backed atomic rate limiting. Built for multi-tenant use from day one.

---

## ⚙️ How It Works

```
Public Data Sources
  ├── Fairfax County ArcGIS
  ├── Socrata Open Data
  ├── Company Websites
  └── Trade Press
         │
         ▼
    Ingest Layer
  ├── Depth-Aware Crawler
  ├── Playwright Fallback
  ├── Bot-Challenge Detection
  └── PDF Extraction
         │
         ▼
    Intelligence Engine
  ├── Entity Resolution (6 rungs)
  ├── Embedding Pipeline (384-dim)
  ├── Trend Detection (4 detectors)
  └── RAG Q&A Service
         │
         ▼
    Output Surfaces
  ├── Firm Timelines
  ├── Semantic Search
  ├── Weekly AI Digest
  └── Alert Delivery (Email + Slack)
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI + Pydantic, Python 3.11+ |
| Database | PostgreSQL 17 + pgvector 0.8.6 (Neon) |
| Cache / Rate Limiting | Redis |
| Migrations | Alembic (HNSW + trigram indexes) |
| Frontend | React + TypeScript (Vite) |
| AI / Embeddings | fastembed (ONNX Runtime), Claude API |
| Notifications | Resend (email), Slack |
| Crawling | Custom WebScraper core + Playwright |

---

## 📬 Get In Touch

Curious about the build, want a walkthrough, or thinking about something similar for your market?

**Areeb Arshad** · [areebarshad@vt.edu](mailto:areebarshad@vt.edu)

Happy to chat about the architecture, the data pipeline, or anything in between.

---

<p align="center">
  <sub>Built for Virginia CRE — open source under MIT.</sub>
</p>
