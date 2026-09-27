/**
 * Typed API client. In the browser, requests go to the same-origin BFF proxy (`/bff/*`) which forwards
 * to the API and carries the httpOnly session cookie. Server components pass an absolute base + cookie.
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
}

function browserBase() {
  return "/bff/api/v1";
}

export type Page<T> = { items: T[]; meta: { page: number; page_size: number; total: number; total_pages: number } };

async function parse(res: Response) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function apiFetch<T>(
  path: string,
  opts: RequestInit & { params?: Record<string, unknown>; base?: string } = {},
): Promise<T> {
  const { params, base, ...init } = opts;
  const url = new URL((base ?? browserBase()) + path, typeof window === "undefined" ? "http://internal" : window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(base ? url.toString() : url.pathname + url.search, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData) ? { "content-type": "application/json" } : {}),
      ...init.headers,
    },
    credentials: "include",
    cache: "no-store",
  });
  const body = await parse(res);
  if (!res.ok) {
    const e = (body as ApiErrorBody | null)?.error;
    throw new ApiError(res.status, e?.code ?? "error", e?.message ?? res.statusText, e?.request_id, e?.details);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string, params?: Record<string, unknown>) => apiFetch<T>(path, { method: "GET", params }),
  post: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => apiFetch<T>(path, { method: "POST", body: form }),
};

/** Subscribe to an audit's SSE progress stream. Returns an unsubscribe function. */
export function streamAudit(auditId: string, onEvent: (ev: AuditStreamEvent) => void, onDone?: () => void): () => void {
  const source = new EventSource(`/bff/api/v1/audits/${auditId}/stream`, { withCredentials: true });
  const handler = (e: MessageEvent) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore keep-alive comments */
    }
  };
  for (const type of [
    "message",
    "audit.started",
    "audit.setup",
    "audit.tests_generated",
    "audit.probe",
    "audit.stage",
    "audit.inference_progress",
    "audit.eval_progress",
    "audit.finding_signal",
    "audit.evidence",
    "audit.policy_mapping",
    "audit.finding_created",
    "audit.completed",
    "audit.failed",
    "audit.warning",
  ]) {
    source.addEventListener(type, handler as EventListener);
  }
  source.addEventListener("done", () => {
    source.close();
    onDone?.();
  });
  source.onerror = () => {
    source.close();
    onDone?.();
  };
  return () => source.close();
}

export type AuditStreamEvent = {
  seq: number;
  type: string;
  stage?: string | null;
  level: "info" | "success" | "warning" | "error";
  message: string;
  progress: number;
  data: Record<string, unknown>;
};
