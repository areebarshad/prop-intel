import { api } from "./client";
import type { AskAnswer } from "./types";

export function askQuestion(params: {
  q: string;
  firm_id?: string;
  top_k?: number;
  min_similarity?: number;
}): Promise<AskAnswer> {
  return api.post<AskAnswer>("/ask", params);
}
