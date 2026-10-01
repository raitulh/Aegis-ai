/**
 * API client.
 *
 * The browser calls the FastAPI backend through the Next.js rewrite at `/api/v1/*`, so the session cookie
 * is first-party and HttpOnly. Unsafe requests echo the CSRF cookie in `X-CSRF-Token` (double submit).
 * All errors are normalized to `ApiError` with the backend's stable error `code`.
 */

export const API_BASE = "/api/v1";
const CSRF_COOKIE = "db_csrf";

export type FieldErrors = Record<string, string>;

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown> | null;
  requestId: string | null;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> | null, requestId: string | null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  get fields(): FieldErrors {
    const f = this.details?.fields;
    return f && typeof f === "object" ? (f as FieldErrors) : {};
  }

  get isNotFound() {
    return this.status === 404;
  }
  get isForbidden() {
    return this.status === 403;
  }
  get isUnauthenticated() {
    return this.status === 401;
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

async function ensureCsrf(): Promise<string | null> {
  let token = readCookie(CSRF_COOKIE);
  if (!token) {
    await fetch(`${API_BASE}/auth/csrf`, { credentials: "same-origin" });
    token = readCookie(CSRF_COOKIE);
  }
  return token;
}

type Query = Record<string, string | number | boolean | null | undefined | string[]>;

export function buildQuery(params?: Query): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((item) => sp.append(k, item));
    else sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function parseError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error (proxy, network) */
  }
  const err = (body as { error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string } })?.error;
  if (err) return new ApiError(res.status, err.code ?? "error", err.message ?? "Request failed.", err.details ?? null, err.request_id ?? null);
  const fallback =
    res.status === 502 || res.status === 503 || res.status === 504
      ? "The service is temporarily unavailable. Please try again in a moment."
      : "Something went wrong. Please try again.";
  return new ApiError(res.status, res.status === 404 ? "not_found" : "http_error", fallback, null, res.headers.get("x-request-id"));
}

export interface RequestOptions {
  method?: string;
  query?: Query;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers ?? {}) };
  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) {
    body = opts.body;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (method !== "GET" && method !== "HEAD") {
    const token = await ensureCsrf();
    if (token) headers["X-CSRF-Token"] = token;
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}${buildQuery(opts.query)}`, {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: opts.signal,
      cache: "no-store",
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "You appear to be offline or the server is unreachable.", null, null);
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ctype = res.headers.get("content-type") ?? "";
  if (ctype.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export const get = <T>(path: string, query?: Query, signal?: AbortSignal) => api<T>(path, { query, signal });
export const post = <T>(path: string, body?: unknown, query?: Query) => api<T>(path, { method: "POST", body, query });
export const patch = <T>(path: string, body?: unknown) => api<T>(path, { method: "PATCH", body });
export const put = <T>(path: string, body?: unknown) => api<T>(path, { method: "PUT", body });
export const del = <T>(path: string, body?: unknown) => api<T>(path, { method: "DELETE", body });

/**
 * Upload with progress (fetch has no upload progress). Uses XHR with the same CSRF + error contract.
 */
export async function upload<T>(
  path: string,
  form: FormData,
  { onProgress, headers, signal }: { onProgress?: (fraction: number) => void; headers?: Record<string, string>; signal?: AbortSignal } = {},
): Promise<T> {
  const token = await ensureCsrf();
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}${path}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    if (token) xhr.setRequestHeader("X-CSRF-Token", token);
    for (const [k, v] of Object.entries(headers ?? {})) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText || "null");
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data as T);
      const err = (data as { error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string } })?.error;
      reject(
        new ApiError(
          xhr.status,
          err?.code ?? (xhr.status === 413 ? "payload_too_large" : "upload_failed"),
          err?.message ?? (xhr.status === 413 ? "The file is too large." : "Upload failed. Please try again."),
          err?.details ?? null,
          err?.request_id ?? null,
        ),
      );
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "Upload interrupted — check your connection and retry.", null, null));
    xhr.onabort = () => reject(new ApiError(0, "aborted", "Upload canceled.", null, null));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(form);
  });
}

/** Random idempotency key for retry-safe uploads (Idempotency-Key header). */
export function idempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function errorMessage(e: unknown, fallback = "Something went wrong."): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message || fallback;
  return fallback;
}
