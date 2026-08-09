import { api } from "./client";
import type { ApiKeyOut, ApiKeyCreated } from "./types";

export function listKeys(): Promise<ApiKeyOut[]> {
  return api.get<ApiKeyOut[]>("/keys");
}

export function createKey(name: string): Promise<ApiKeyCreated> {
  return api.post<ApiKeyCreated>("/keys", { name });
}

export function revokeKey(id: string): Promise<null> {
  return api.delete<null>(`/keys/${id}`);
}
