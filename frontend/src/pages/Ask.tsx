import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { askQuestion } from "../api/rag";
import type { AskAnswer } from "../api/types";

export default function Ask() {
  const [searchParams] = useSearchParams();
  const prefillFirmId = searchParams.get("firm_id") ?? "";

  const [q, setQ] = useState("");
  const [firmId, setFirmId] = useState(prefillFirmId);

  const mutation = useMutation<AskAnswer, Error, { q: string; firm_id?: string }>({
    mutationFn: ({ q, firm_id }) => askQuestion({ q, firm_id: firm_id || undefined }),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    mutation.mutate({ q: q.trim(), firm_id: firmId || undefined });
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-semibold text-gray-900 mb-2">Ask</h1>
      <p className="text-sm text-gray-500 mb-6">
        Ask a question about Virginia commercial real estate. Answers are grounded in the
        PropIntel document corpus.
      </p>

      <form onSubmit={handleSubmit} className="space-y-3 mb-8">
        <textarea
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. What permits did Comstock file in Fairfax in the last quarter?"
          rows={3}
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
        />
        <div className="flex gap-2">
          <input
            type="text"
            value={firmId}
            onChange={(e) => setFirmId(e.target.value)}
            placeholder="Firm ID (optional)"
            className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            disabled={mutation.isPending || !q.trim()}
            className="bg-blue-600 text-white text-sm px-5 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {mutation.isPending ? "Thinking…" : "Ask"}
          </button>
        </div>
      </form>

      {mutation.isError && (
        <div className="bg-red-50 border border-red-200 rounded p-4 text-sm text-red-700 mb-4">
          {mutation.error.message}
        </div>
      )}

      {mutation.data && (
        <div className="space-y-6">
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-5">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
              Answer
            </p>
            <p className="text-sm text-gray-900 whitespace-pre-wrap leading-relaxed">
              {mutation.data.answer}
            </p>
            <p className="text-xs text-gray-400 mt-3">
              {mutation.data.passages_found} passage
              {mutation.data.passages_found !== 1 ? "s" : ""} retrieved
            </p>
          </div>

          {mutation.data.citations.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
                Sources
              </p>
              <ul className="space-y-2">
                {mutation.data.citations.map((c, i) => (
                  <li
                    key={i}
                    className="border border-gray-200 rounded p-3 text-sm bg-white"
                  >
                    <p className="text-gray-700 italic text-xs mb-1 line-clamp-3">
                      "{c.excerpt}"
                    </p>
                    <div className="flex gap-3 text-xs text-gray-400 mt-1">
                      {c.url && (
                        <a
                          href={c.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 hover:underline truncate max-w-xs"
                        >
                          {c.url}
                        </a>
                      )}
                      {c.page_number != null && <span>p.{c.page_number}</span>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
