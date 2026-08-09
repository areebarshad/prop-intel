import { api } from "./client";
import type { DigestOut } from "./types";

export function getLatestDigest(): Promise<DigestOut> {
  return api.get<DigestOut>("/digests/latest");
}

export function generateDigest(): Promise<DigestOut> {
  return api.post<DigestOut>("/digests/generate", {});
}
