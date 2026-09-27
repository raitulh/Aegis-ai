import { cookies, headers } from "next/headers";
import { SESSION_COOKIE } from "@/lib/constants";

const API_BASE = process.env.API_INTERNAL_URL || process.env.API_BASE_URL || "http://localhost:8000";

/** Server-side API fetch that forwards the session cookie (for RSC auth guards & SSR data). */
export async function serverFetch<T>(path: string): Promise<T | null> {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/api/v1${path}`, {
      headers: { cookie: `${SESSION_COOKIE}=${token}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export async function requestId(): Promise<string | undefined> {
  return (await headers()).get("x-request-id") ?? undefined;
}
