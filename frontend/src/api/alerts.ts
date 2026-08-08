import { api } from "./client";
import type { AlertCreate, AlertOut } from "./types";

export function listAlerts(userId: string): Promise<AlertOut[]> {
  return api.get<AlertOut[]>(`/alerts?user_id=${encodeURIComponent(userId)}`);
}

export function createAlert(body: AlertCreate): Promise<AlertOut> {
  return api.post<AlertOut>("/alerts", body);
}

export function toggleAlert(id: string): Promise<AlertOut> {
  return api.patch<AlertOut>(`/alerts/${id}/toggle`);
}

export function deleteAlert(id: string): Promise<void> {
  return api.delete<void>(`/alerts/${id}`);
}
