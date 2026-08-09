import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getLatestDigest, generateDigest } from "../api/digest";

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
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["digest-latest"],
    queryFn: getLatestDigest,
    retry: false,
  });

  const { mutate: triggerGenerate, isPending: isGenerating } = useMutation({
    mutationFn: generateDigest,
    onSuccess: (generated) => {
      queryClient.setQueryData(["digest-latest"], generated);
    },
  });

  const is404 = isError && error instanceof Error && error.message.startsWith("404");

  useEffect(() => {
    if (is404) {
      triggerGenerate();
    }
  }, [is404, triggerGenerate]);

  const showLoading = isLoading || isGenerating;

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-semibold text-gray-900 mb-2">Weekly Digest</h1>

      {showLoading && (
        <p className="text-gray-500 text-sm">
          {isGenerating ? "Generating digest…" : "Loading…"}
        </p>
      )}

      {isError && !is404 && !isGenerating && (
        <div className="bg-red-50 border border-red-200 rounded p-4 text-sm text-red-800">
          Failed to load digest. Please try again later.
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
