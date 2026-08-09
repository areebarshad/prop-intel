import { api } from "./client";
import type { TokenResponse, UserOut } from "./types";

export const TOKEN_KEY = "propintel_token";

export async function login(email: string, password: string): Promise<TokenResponse> {
  const result = await api.post<TokenResponse>("/auth/login", { email, password });
  sessionStorage.setItem(TOKEN_KEY, result.access_token);
  return result;
}

export async function register(
  email: string,
  password: string,
  full_name?: string,
  company?: string
): Promise<TokenResponse> {
  const result = await api.post<TokenResponse>("/auth/register", {
    email,
    password,
    full_name,
    company,
  });
  sessionStorage.setItem(TOKEN_KEY, result.access_token);
  return result;
}

export function logout(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export async function getMe(): Promise<UserOut> {
  return api.get<UserOut>("/auth/me");
}
