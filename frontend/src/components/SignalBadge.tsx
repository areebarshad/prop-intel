const TYPE_COLORS: Record<string, string> = {
  HIRING_SURGE: "bg-green-100 text-green-800",
  ASSET_CLASS_PIVOT: "bg-purple-100 text-purple-800",
  GEOGRAPHIC_EXPANSION: "bg-blue-100 text-blue-800",
  PERMIT_VOLUME_ANOMALY: "bg-orange-100 text-orange-800",
  PERMIT_FILED: "bg-gray-100 text-gray-700",
  JOB_POSTING: "bg-teal-100 text-teal-800",
};

function fmt(t: string) {
  return t.toLowerCase().replace(/_/g, " ");
}

export default function SignalBadge({ type }: { type: string }) {
  const cls = TYPE_COLORS[type] ?? "bg-gray-100 text-gray-700";
  return (
    <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${cls}`}>
      {fmt(type)}
    </span>
  );
}
