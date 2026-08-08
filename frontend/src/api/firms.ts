import { api } from "./client";
import type { Firm, FirmTimeline, PaginatedFirms } from "./types";

export function listFirms(params?: {
  q?: string;
  locality?: string;
  asset_class?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedFirms> {
  const qs = new URLSearchParams();
  if (params?.q) qs.set("q", params.q);
  if (params?.locality) qs.set("locality", params.locality);
  if (params?.asset_class) qs.set("asset_class", params.asset_class);
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  const query = qs.toString();
  return api.get<PaginatedFirms>(`/firms${query ? `?${query}` : ""}`);
}

export function getFirm(id: string): Promise<Firm> {
  return api.get<Firm>(`/firms/${id}`);
}

export function getFirmTimeline(
  id: string,
  params?: { page?: number; page_size?: number },
): Promise<FirmTimeline> {
  const qs = new URLSearchParams();
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  const query = qs.toString();
  return api.get<FirmTimeline>(`/firms/${id}/timeline${query ? `?${query}` : ""}`);
}
