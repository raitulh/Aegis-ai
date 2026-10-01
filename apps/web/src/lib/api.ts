/**
 * Typed API client. In the browser every request goes to the same-origin BFF proxy (`/bff/api/v1`), which
 * forwards the httpOnly session cookie to the API.
 *
 * - Path parameters must be interpolated with {@link path} so they are URL-encoded (a crafted id can never
 *   turn into a different API path).
 * - Every request has a timeout; network failures surface as `ApiError` with code `network_error`.
 * - A 401 on an authenticated call emits `aegis:unauthenticated` so the app can send the user to sign in.
 */

export type ApiErrorBody = {
  error: { code: string; message: string; request_id?: string; details?: unknown };
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isPlanLimit() {
    return this.status === 403 && ["plan_limit_exceeded", "feature_not_in_plan", "feature_unavailable"].includes(this.code);
  }
}

export type Page<T> = { items: T[]; meta: { page: number; page_size: number; total: number; total_pages: number } };

export const API_BASE = "/bff/api/v1";
const DEFAULT_TIMEOUT_MS = 30_000;

/** Tagged template that URL-encodes every interpolated value: path`/findings/${id}/comments`. */
export function path(strings: TemplateStringsArray, ...values: (string | number)[]): string {
  return strings.reduce((out, s, i) => out + s + (i < values.length ? encodeURIComponent(String(values[i])) : ""), "");
}

async function parse(res: Response) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

type FetchOptions = RequestInit & { params?: Record<string, unknown>; timeoutMs?: number };

function buildUrl(p: string, params?: Record<string, unknown>) {
  const url = new URL(API_BASE + p, typeof window === "undefined" ? "http://internal" : window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  return url.pathname + url.search;
}

async function request(p: string, opts: FetchOptions = {}): Promise<Response> {
  const { params, timeoutMs = DEFAULT_TIMEOUT_MS, signal, ...init } = opts;
  const timeout = AbortSignal.timeout(timeoutMs);
  try {
    return await fetch(buildUrl(p, params), {
      ...init,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      headers: {
        ...(init.body && !(init.body instanceof FormData) ? { "content-type": "application/json" } : {}),
        ...init.headers,
      },
      credentials: "include",
      cache: "no-store",
    });
  } catch (e) {
    if ((e as Error)?.name === "AbortError" && signal?.aborted) throw e;
    const timedOut = (e as Error)?.name === "TimeoutError";
    throw new ApiError(0, timedOut ? "timeout" : "network_error", timedOut ? "The request timed out." : "Network unavailable — check your connection.");
  }
}

async function failure(res: Response, p: string): Promise<never> {
  const body = await parse(res);
  const e = (body as ApiErrorBody | null)?.error;
  if (res.status === 401 && typeof window !== "undefined" && !p.startsWith("/auth/")) {
    window.dispatchEvent(new CustomEvent("aegis:unauthenticated"));
  }
  throw new ApiError(res.status, e?.code ?? "error", e?.message ?? (res.statusText || `Request failed (${res.status})`), e?.request_id, e?.details);
}

export async function apiFetch<T>(p: string, opts: FetchOptions = {}): Promise<T> {
  const res = await request(p, opts);
  if (!res.ok) return failure(res, p);
  return (await parse(res)) as T;
}

/** POST/GET that returns a file; triggers a browser download and returns the response headers. */
export async function download(p: string, opts: FetchOptions & { filename?: string } = {}): Promise<Headers> {
  const { filename, ...rest } = opts;
  const res = await request(p, { timeoutMs: 120_000, ...rest });
  if (!res.ok) return failure(res, p);
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const name = filename ?? /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? "download";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return res.headers;
}

const json = (body: unknown) => (body === undefined ? undefined : JSON.stringify(body));

export const api = {
  get: <T>(p: string, params?: Record<string, unknown>, signal?: AbortSignal) => apiFetch<T>(p, { method: "GET", params, signal }),
  post: <T>(p: string, body?: unknown, headers?: Record<string, string>) => apiFetch<T>(p, { method: "POST", body: json(body), headers }),
  patch: <T>(p: string, body?: unknown) => apiFetch<T>(p, { method: "PATCH", body: json(body) }),
  put: <T>(p: string, body?: unknown) => apiFetch<T>(p, { method: "PUT", body: json(body) }),
  delete: <T>(p: string) => apiFetch<T>(p, { method: "DELETE" }),
  upload: <T>(p: string, form: FormData, params?: Record<string, unknown>) => apiFetch<T>(p, { method: "POST", body: form, params, timeoutMs: 120_000 }),
};

export function errorMessage(e: unknown, fallback = "Something went wrong."): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}
