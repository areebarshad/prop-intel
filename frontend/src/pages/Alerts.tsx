import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listAlerts, toggleAlert, deleteAlert } from "../api/alerts";
import type { AlertOut } from "../api/types";

const USER_ID_KEY = "propintel_user_id";

function fmtDate(s: string | null) {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export default function Alerts() {
  const [userId, setUserId] = useState(() => localStorage.getItem(USER_ID_KEY) ?? "");
  const [inputId, setInputId] = useState(userId);
  const queryClient = useQueryClient();

  const { data: alerts, isLoading, isError } = useQuery({
    queryKey: ["alerts", userId],
    queryFn: () => listAlerts(userId),
    enabled: !!userId,
  });

  const toggleMutation = useMutation({
    mutationFn: (id: string) => toggleAlert(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts", userId] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAlert(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts", userId] }),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    localStorage.setItem(USER_ID_KEY, inputId);
    setUserId(inputId);
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-semibold text-gray-900 mb-6">Alerts</h1>

      <form onSubmit={handleSubmit} className="flex gap-2 mb-6">
        <input
          type="text"
          placeholder="User ID (UUID)"
          value={inputId}
          onChange={(e) => setInputId(e.target.value)}
          className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono"
        />
        <button
          type="submit"
          className="bg-blue-600 text-white text-sm px-4 py-2 rounded hover:bg-blue-700"
        >
          Load
        </button>
      </form>

      {!userId && (
        <p className="text-sm text-gray-400">Enter a user ID to view alerts.</p>
      )}

      {isLoading && <p className="text-sm text-gray-500">Loading…</p>}
      {isError && <p className="text-sm text-red-500">Failed to load alerts.</p>}

      {alerts && alerts.length === 0 && (
        <p className="text-sm text-gray-400">No alerts configured.</p>
      )}

      {alerts && alerts.length > 0 && (
        <ul className="divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden">
          {alerts.map((alert: AlertOut) => (
            <li key={alert.id} className="px-4 py-3 flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900">{alert.name}</p>
                <p className="text-xs text-gray-400 mt-0.5">
                  {alert.channel}
                  {alert.channel_target ? ` → ${alert.channel_target}` : ""}
                  {" · "}created {fmtDate(alert.created_at)}
                  {alert.last_fired_at && ` · last fired ${fmtDate(alert.last_fired_at)}`}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    alert.is_active
                      ? "bg-green-50 text-green-700"
                      : "bg-gray-100 text-gray-400"
                  }`}
                >
                  {alert.is_active ? "active" : "paused"}
                </span>
                <button
                  onClick={() => toggleMutation.mutate(alert.id)}
                  className="text-xs text-blue-600 hover:underline"
                >
                  {alert.is_active ? "Pause" : "Resume"}
                </button>
                <button
                  onClick={() => deleteMutation.mutate(alert.id)}
                  className="text-xs text-red-500 hover:underline"
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
