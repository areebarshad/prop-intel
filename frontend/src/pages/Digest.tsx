import { useQuery } from "@tanstack/react-query";
import { getLatestDigest } from "../api/digest";

function fmtDate(s: string) {
  return new Date(s).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

function renderMarkdown(md: string) {
  const lines = md.split("\n");
  const elements: React.ReactNode[] = [];
  let key = 0;

  for (const line of lines) {
    if (line.startsWith("# ")) {
      elements.push(
        <h1 key={key++} className="text-xl font-bold text-gray-900 mb-4 mt-2">
          {line.slice(2)}
        </h1>,
      );
    } else if (line.startsWith("## ")) {
      elements.push(
        <h2 key={key++} className="text-base font-semibold text-gray-800 mt-6 mb-2">
          {line.slice(3)}
        </h2>,
      );
    } else if (line === "---") {
      elements.push(<hr key={key++} className="border-gray-200 my-4" />);
    } else if (line.trim()) {
      elements.push(
        <p key={key++} className="text-sm text-gray-700 leading-relaxed mb-2">
          {line}
        </p>,
      );
    }
  }
  return elements;
}

export default function Digest() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["digest-latest"],
    queryFn: getLatestDigest,
    retry: false,
  });

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-semibold text-gray-900 mb-2">Weekly Digest</h1>

      {isLoading && <p className="text-gray-500 text-sm">Loading…</p>}

      {isError && (
        <div className="bg-yellow-50 border border-yellow-200 rounded p-4 text-sm text-yellow-800">
          No digest available yet. Run <code className="bg-yellow-100 px-1 rounded">propintel-ingest digest</code> to generate one.
        </div>
      )}

      {data && (
        <>
          <div className="flex gap-4 text-xs text-gray-400 mb-6">
            <span>{data.firm_count} firms</span>
            <span>{data.signal_count} signals</span>
            <span>Generated {fmtDate(data.generated_at)}</span>
          </div>
          <div className="prose-sm">{renderMarkdown(data.markdown)}</div>
        </>
      )}
    </div>
  );
}
