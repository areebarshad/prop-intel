import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listKeys, createKey, revokeKey } from "../api/keys";
import { getToken } from "../api/auth";
import type { ApiKeyOut, ApiKeyCreated } from "../api/types";

function fmtDate(s: string | null) {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export default function Settings() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isLoggedIn = !!getToken();

  const [newKeyName, setNewKeyName] = useState("");
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [nameError, setNameError] = useState("");

  const { data: keys, isLoading, isError } = useQuery({
    queryKey: ["keys"],
    queryFn: listKeys,
    enabled: isLoggedIn,
  });

  const createMutation = useMutation({
    mutationFn: (name: string) => createKey(name),
    onSuccess: (data) => {
      setCreatedKey(data);
      setNewKeyName("");
      queryClient.invalidateQueries({ queryKey: ["keys"] });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeKey(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["keys"] }),
  });

  function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const name = newKeyName.trim();
    if (!name) {
      setNameError("Name is required.");
      return;
    }
    setNameError("");
    setCreatedKey(null);
    createMutation.mutate(name);
  }

  if (!isLoggedIn) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-8">
        <p className="text-sm text-gray-400">
          <button
            onClick={() => navigate("/login")}
            className="text-blue-600 hover:underline"
          >
            Sign in
          </button>{" "}
          to manage API keys.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-10">
      <h1 className="text-2xl font-semibold text-gray-900">Settings</h1>

      {/* Create key */}
      <section>
        <h2 className="text-base font-medium text-gray-900 mb-3">Create API key</h2>
        <form onSubmit={handleCreate} className="flex gap-2 items-start">
          <div className="flex-1 max-w-xs">
            <input
              type="text"
              placeholder="Key name (e.g. Production)"
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              className="w-full border border-gray-200 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            {nameError && <p className="text-xs text-red-500 mt-1">{nameError}</p>}
          </div>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="px-4 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {createMutation.isPending ? "Creating…" : "Create"}
          </button>
        </form>
        {createMutation.isError && (
          <p className="text-xs text-red-500 mt-2">Failed to create key.</p>
        )}

        {createdKey && (
          <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-4 max-w-xl">
            <p className="text-sm font-medium text-green-800 mb-1">
              Key created — copy it now. It will not be shown again.
            </p>
            <code className="block text-xs bg-white border border-green-200 rounded px-3 py-2 font-mono break-all select-all">
              {createdKey.key}
            </code>
          </div>
        )}
      </section>

      {/* Key list */}
      <section>
        <h2 className="text-base font-medium text-gray-900 mb-3">Active keys</h2>

        {isLoading && <p className="text-sm text-gray-500">Loading…</p>}
        {isError && <p className="text-sm text-red-500">Failed to load keys.</p>}

        {keys && keys.length === 0 && (
          <p className="text-sm text-gray-400">No active keys.</p>
        )}

        {keys && keys.length > 0 && (
          <ul className="divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden">
            {keys.map((key: ApiKeyOut) => (
              <li key={key.id} className="px-4 py-3 flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900">{key.name}</p>
                  <p className="text-xs text-gray-400 mt-0.5">
                    <code className="font-mono">{key.key_prefix}…</code>
                    {" · "}
                    {key.tier}
                    {" · "}
                    created {fmtDate(key.created_at)}
                    {key.last_used_at && ` · last used ${fmtDate(key.last_used_at)}`}
                  </p>
                </div>
                <button
                  onClick={() => revokeMutation.mutate(key.id)}
                  disabled={revokeMutation.isPending}
                  className="text-xs text-red-500 hover:text-red-700 shrink-0 disabled:opacity-50"
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
