import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/constants";

const API_BASE = process.env.API_INTERNAL_URL || process.env.API_BASE_URL || "http://localhost:8000";

export type ServerResult<T> =
  | { ok: true; data: T }
  | { ok: false; reason: "unauthenticated" | "forbidden" | "unavailable" | "error"; status?: number };

/**
 * Server-side API fetch that forwards the session cookie (RSC auth guards & SSR data).
 * Distinguishes "not signed in" from "API down" so an outage is never presented as a logout.
 */
export async function serverFetch<T>(path: string): Promise<ServerResult<T>> {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE)?.value;
  if (!token) return { ok: false, reason: "unauthenticated" };
  try {
    const res = await fetch(`${API_BASE}/api/v1${path}`, {
      headers: { cookie: `${SESSION_COOKIE}=${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    if (res.status === 401) return { ok: false, reason: "unauthenticated", status: 401 };
    if (res.status === 403) return { ok: false, reason: "forbidden", status: 403 };
    if (!res.ok) return { ok: false, reason: res.status >= 500 ? "unavailable" : "error", status: res.status };
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return { ok: false, reason: "unavailable" };
  }
}
