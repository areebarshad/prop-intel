import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getFirmTimeline } from "../api/firms";
import SignalBadge from "../components/SignalBadge";

const PAGE_SIZE = 25;

function fmtDate(s: string | null) {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export default function FirmDetail() {
  const { id } = useParams<{ id: string }>();
  const [page, setPage] = useState(1);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["firm-timeline", id, page],
    queryFn: () => getFirmTimeline(id!, { page, page_size: PAGE_SIZE }),
    enabled: !!id,
    placeholderData: (prev) => prev,
  });

  if (isLoading) return <p className="text-gray-500 text-sm p-8">Loading…</p>;
  if (isError || !data)
    return (
      <p className="text-red-500 text-sm p-8">
        Failed to load firm.{" "}
        <Link to="/" className="underline text-blue-600">
          Back
        </Link>
      </p>
    );

  const { firm, signals, total } = data;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <Link to="/" className="text-xs text-blue-600 hover:underline mb-4 inline-block">
        ← All firms
      </Link>

      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">{firm.name}</h1>
        {firm.localities.length > 0 && (
          <p className="text-sm text-gray-500 mt-1">{firm.localities.join(" · ")}</p>
        )}
        {firm.asset_classes.length > 0 && (
          <div className="flex gap-1 mt-2 flex-wrap">
            {firm.asset_classes.map((ac) => (
              <span key={ac} className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full">
                {ac}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-700">Signals ({total})</h2>
        <Link
          to={`/ask?firm_id=${firm.id}`}
          className="text-xs text-blue-600 hover:underline"
        >
          Ask about this firm →
        </Link>
      </div>

      <ul className="divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden">
        {signals.map((sig) => (
          <li key={sig.id} className="px-4 py-3">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm text-gray-900 font-medium truncate">{sig.title}</p>
                {sig.body && (
                  <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{sig.body}</p>
                )}
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <SignalBadge type={sig.signal_type} />
                <span className="text-xs text-gray-400">{fmtDate(sig.occurred_at)}</span>
                <span className="text-xs text-gray-300">score {sig.score.toFixed(2)}</span>
              </div>
            </div>
          </li>
        ))}
      </ul>

      {totalPages > 1 && (
        <div className="flex justify-between items-center mt-4">
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="text-sm text-blue-600 disabled:text-gray-300"
          >
            ← Previous
          </button>
          <span className="text-xs text-gray-400">
            Page {page} of {totalPages}
          </span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
            className="text-sm text-blue-600 disabled:text-gray-300"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
