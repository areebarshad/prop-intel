import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listFirms } from "../api/firms";

const PAGE_SIZE = 20;

export default function FirmList() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["firms", search, page],
    queryFn: () => listFirms({ q: search || undefined, page, page_size: PAGE_SIZE }),
    placeholderData: (prev) => prev,
  });

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setSearch(q);
    setPage(1);
  }

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1;

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-semibold text-gray-900 mb-6">Firms</h1>

      <form onSubmit={handleSearch} className="flex gap-2 mb-6">
        <input
          type="text"
          placeholder="Search firms…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          type="submit"
          className="bg-blue-600 text-white text-sm px-4 py-2 rounded hover:bg-blue-700"
        >
          Search
        </button>
      </form>

      {isLoading && <p className="text-gray-500 text-sm">Loading…</p>}
      {isError && <p className="text-red-500 text-sm">Failed to load firms.</p>}

      {data && (
        <>
          <p className="text-xs text-gray-400 mb-3">{data.total} firms</p>
          <ul className="divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden">
            {data.items.map((firm) => (
              <li key={firm.id}>
                <Link
                  to={`/firms/${firm.id}`}
                  className="flex items-center justify-between px-4 py-3 hover:bg-gray-50"
                >
                  <div>
                    <p className="text-sm font-medium text-gray-900">{firm.name}</p>
                    {firm.localities.length > 0 && (
                      <p className="text-xs text-gray-400 mt-0.5">
                        {firm.localities.join(", ")}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-1 flex-wrap justify-end max-w-xs">
                    {firm.asset_classes.slice(0, 3).map((ac) => (
                      <span
                        key={ac}
                        className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full"
                      >
                        {ac}
                      </span>
                    ))}
                  </div>
                </Link>
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
        </>
      )}
    </div>
  );
}
