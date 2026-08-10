import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  FileText,
  Database,
  Zap,
  BarChart3,
  Search,
  Bell,
  ChevronRight,
  CheckCircle,
  Mail,
} from "lucide-react";
import { api } from "../api/client";

type ContactRequest = {
  full_name: string;
  work_email: string;
  reason: string;
  company?: string;
  use_case_notes?: string;
};

const REASONS = ["Request a Demo", "Learn More", "Other"] as const;
type Reason = (typeof REASONS)[number];

const FEATURES = [
  {
    icon: FileText,
    title: "Automated Document Parsing & Ingestion",
    desc: "Crawls permit portals, team pages, careers pages, and press releases. A 6-rung entity resolution system links raw permit applicant LLCs to known parent developers — zero misattributions across 395 name mentions.",
  },
  {
    icon: Database,
    title: "Vector Search & Semantic Entity Extraction",
    desc: "384-dimension embeddings via fastembed (ONNX Runtime) with pgvector HNSW indexing. RAG-powered Q&A returns grounded answers with source URL and page-level citations.",
  },
  {
    icon: Zap,
    title: "High-Concurrency Pipeline Architecture",
    desc: "Async FastAPI on Neon PostgreSQL 17 with pgvector 0.8.6. Per-tier JWT + API key auth with Redis-backed atomic rate limiting. Alembic-managed schema with hand-written HNSW and trigram indexes.",
  },
  {
    icon: BarChart3,
    title: "Trend & Anomaly Detection",
    desc: "Four automated signal detectors: hiring surge (1.5× threshold), asset-class pivot (30%/10% threshold), geographic expansion (first permit in a new locality), and permit volume Z-score anomaly.",
  },
  {
    icon: Search,
    title: "Unified Signal Stream",
    desc: "Key hires, permit filings, press announcements, and derived trend signals all land in a single table. Alerts, weekly digest, and firm timelines all consume the same feed with no per-surface schema changes.",
  },
  {
    icon: Bell,
    title: "Alert Delivery & Weekly Digest",
    desc: "Configurable alert rules with email (Resend) and Slack delivery. At-most-once semantics via pending-before-send pattern. Claude-backed weekly digest with broker-register narrative per firm.",
  },
];

const ARCHITECTURE_STAGES = [
  {
    label: "Data Sources",
    items: ["Fairfax County ArcGIS", "Socrata Open Data", "Company Websites", "Trade Press"],
  },
  {
    label: "Ingest Layer",
    items: ["Depth-Aware Crawler", "Playwright Fallback", "Bot-Challenge Detection", "PDF Extraction"],
  },
  {
    label: "Intelligence",
    items: ["Entity Resolution (6 rungs)", "Embedding Pipeline", "Trend Detection", "RAG Service"],
  },
  {
    label: "API & Auth",
    items: ["FastAPI + Pydantic", "JWT + API Keys", "Per-tier Rate Limiting", "CORS + Preflight"],
  },
  {
    label: "Output Surfaces",
    items: ["REST API", "Firm Timelines", "Weekly Digest", "Alert Delivery"],
  },
];

const STATS = [
  { label: "Firms Tracked", value: "20+" },
  { label: "Permits Ingested", value: "419+" },
  { label: "Signals Generated", value: "148+" },
  { label: "Misattributions", value: "0" },
];

function scrollTo(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
}

export default function LandingPage() {
  const [reason, setReason] = useState<Reason>("Request a Demo");
  const [fullName, setFullName] = useState("");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");

  const mutation = useMutation<{ status: string }, Error, ContactRequest>({
    mutationFn: (data) => api.post<{ status: string }>("/contact", data),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    mutation.mutate({
      full_name: fullName.trim(),
      work_email: email.trim(),
      reason,
      company: company.trim() || undefined,
      use_case_notes: reason === "Other" ? message.trim() || undefined : undefined,
    });
  }

  return (
    <div className="min-h-screen bg-white text-gray-900">
      {/* ── Header ── */}
      <header className="sticky top-0 z-20 border-b border-gray-200 bg-white">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-8">
          <span className="font-bold text-blue-700 text-base tracking-tight">PropIntel</span>
          <nav className="hidden sm:flex gap-6 text-sm text-gray-500">
            <a href="#features" className="hover:text-gray-900 transition-colors">Features</a>
            <a href="#architecture" className="hover:text-gray-900 transition-colors">Architecture</a>
            <a href="#contact" className="hover:text-gray-900 transition-colors">Contact</a>
          </nav>
          <div className="ml-auto">
            <button
              onClick={() => scrollTo("contact")}
              className="bg-blue-600 text-white text-sm px-4 py-2 rounded hover:bg-blue-700 transition-colors"
            >
              Get in Touch
            </button>
          </div>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="max-w-6xl mx-auto px-4 py-24 text-center">
        <div className="inline-block bg-blue-50 text-blue-700 text-xs font-semibold px-3 py-1 rounded-full mb-5 uppercase tracking-wide">
          Virginia Commercial Real Estate Intelligence
        </div>
        <h1 className="text-4xl sm:text-5xl font-bold text-gray-900 mb-5 leading-tight">
          Automated Transaction Intelligence<br className="hidden sm:block" />
          for CRE Professionals
        </h1>
        <p className="text-lg text-gray-500 max-w-2xl mx-auto mb-8">
          PropIntel tracks permit filings, team changes, and development announcements
          across Virginia's commercial real estate market — surfacing signals before
          they appear in the trade press.
        </p>
        <button
          onClick={() => scrollTo("contact")}
          className="bg-blue-600 text-white px-7 py-3 rounded font-medium hover:bg-blue-700 transition-colors text-sm"
        >
          Request a Demo
        </button>
      </section>

      {/* ── Stats ── */}
      <div className="border-t border-b border-gray-100 bg-gray-50">
        <div className="max-w-6xl mx-auto px-4 py-8 grid grid-cols-2 sm:grid-cols-4 gap-6 text-center">
          {STATS.map(({ label, value }) => (
            <div key={label}>
              <div className="text-2xl font-bold text-blue-700">{value}</div>
              <div className="text-sm text-gray-500 mt-1">{label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Features ── */}
      <section id="features" className="max-w-6xl mx-auto px-4 py-20">
        <h2 className="text-2xl font-bold text-gray-900 mb-2 text-center">Platform Capabilities</h2>
        <p className="text-gray-500 text-center mb-12 text-sm">
          Built for institutional-grade intelligence workflows
        </p>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {FEATURES.map(({ icon: Icon, title, desc }) => (
            <div key={title} className="border border-gray-200 rounded-lg p-5">
              <Icon className="w-5 h-5 text-blue-600 mb-3" />
              <h3 className="font-semibold text-gray-900 mb-2 text-sm">{title}</h3>
              <p className="text-xs text-gray-500 leading-relaxed">{desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Architecture ── */}
      <section id="architecture" className="bg-gray-50 py-20">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="text-2xl font-bold text-gray-900 mb-2 text-center">System Architecture</h2>
          <p className="text-gray-500 text-center mb-12 text-sm">
            End-to-end intelligence pipeline from raw public data to structured signals
          </p>
          <div className="flex flex-col sm:flex-row gap-3 items-stretch justify-center">
            {ARCHITECTURE_STAGES.map(({ label, items }, i) => (
              <div key={label} className="flex items-center gap-3">
                <div className="flex-1 border border-gray-200 rounded-lg bg-white p-4 min-w-36">
                  <p className="text-xs font-semibold text-blue-700 uppercase tracking-wide mb-2">
                    {label}
                  </p>
                  <ul className="space-y-1">
                    {items.map((item) => (
                      <li key={item} className="text-xs text-gray-600">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                {i < ARCHITECTURE_STAGES.length - 1 && (
                  <ChevronRight className="w-4 h-4 text-gray-300 flex-shrink-0 hidden sm:block" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Contact ── */}
      <section id="contact" className="max-w-6xl mx-auto px-4 py-20">
        <div className="max-w-md mx-auto">
          <div className="flex justify-center mb-4">
            <Mail className="w-8 h-8 text-blue-600" />
          </div>
          <h2 className="text-2xl font-bold text-gray-900 mb-2 text-center">Get in Touch</h2>
          <p className="text-gray-500 text-center mb-8 text-sm">
            Interested in a walkthrough or have questions? Send a message and we'll follow up directly.
          </p>

          {mutation.isSuccess ? (
            <div className="flex flex-col items-center py-10 text-center">
              <CheckCircle className="w-10 h-10 text-green-500 mb-4" />
              <h3 className="font-semibold text-gray-900 mb-1">Message sent</h3>
              <p className="text-sm text-gray-500">We'll reach out to {email} shortly.</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Your name
                </label>
                <input
                  type="text"
                  required
                  maxLength={200}
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Jane Doe"
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Work email
                </label>
                <input
                  type="email"
                  required
                  maxLength={320}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Company <span className="text-gray-400 font-normal">(optional)</span>
                </label>
                <input
                  type="text"
                  maxLength={200}
                  value={company}
                  onChange={(e) => setCompany(e.target.value)}
                  placeholder="Acme Development"
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Reason
                </label>
                <select
                  value={reason}
                  onChange={(e) => setReason(e.target.value as Reason)}
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                >
                  {REASONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>

              {reason === "Other" && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Message
                  </label>
                  <textarea
                    required
                    maxLength={2000}
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    rows={4}
                    placeholder="What would you like to know?"
                    className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                  />
                </div>
              )}

              {mutation.isError && (
                <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">
                  Something went wrong. Please email{" "}
                  <a href="mailto:areebarshad@vt.edu" className="underline">
                    areebarshad@vt.edu
                  </a>{" "}
                  directly.
                </p>
              )}

              <button
                type="submit"
                disabled={mutation.isPending}
                className="w-full bg-blue-600 text-white py-2.5 rounded font-medium text-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {mutation.isPending ? "Sending…" : "Send"}
              </button>
            </form>
          )}
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-gray-100 py-8">
        <div className="max-w-6xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-3">
          <span className="font-semibold text-blue-700 text-sm">PropIntel</span>
          <p className="text-xs text-gray-400 text-center">
            Competitive intelligence for Virginia commercial real estate. Open source.
          </p>
          <a
            href="mailto:areebarshad@vt.edu"
            className="text-xs text-gray-400 hover:text-gray-700 transition-colors"
          >
            areebarshad@vt.edu
          </a>
        </div>
      </footer>
    </div>
  );
}
