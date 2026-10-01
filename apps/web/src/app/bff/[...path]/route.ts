import { type NextRequest, NextResponse } from "next/server";

/**
 * Backend-for-frontend proxy. The browser calls same-origin `/bff/api/v1/*`; this handler forwards to the API
 * (runtime `API_INTERNAL_URL`) with the session cookie, streams request and response bodies (incl. SSE), and
 * relays Set-Cookie.
 *
 * Hardening:
 *  - only `/api/v1/...` is reachable (no API docs, health or root through the web origin);
 *  - each path segment is validated and re-encoded, so encoded `/`, `?`, `#` or `..` cannot change the upstream URL;
 *  - unsafe methods must come from this origin (Origin/Referer check; the API re-checks it too);
 *  - only the session cookie and an allowlist of headers are forwarded;
 *  - request bodies are capped; non-streaming requests time out;
 *  - upstream redirects to the internal API host are rewritten, and decoded bodies drop `content-encoding`.
 */
export const dynamic = "force-dynamic";

const API_BASE = () => (process.env.API_INTERNAL_URL || process.env.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const SESSION_COOKIE = "aegis_session";
const MAX_BODY = 12 * 1024 * 1024;
const TIMEOUT_MS = 60_000;
const FORWARD_REQUEST_HEADERS = [
  "accept",
  "accept-language",
  "authorization",
  "content-type",
  "idempotency-key",
  "last-event-id",
  "origin",
  "referer",
  "traceparent",
  "user-agent",
  "x-aegis-org",
  "x-forwarded-for",
  "x-request-id",
];
const DROP_RESPONSE_HEADERS = new Set(["connection", "keep-alive", "transfer-encoding", "upgrade", "content-length", "content-encoding"]);
const SEGMENT = /^[A-Za-z0-9._~:@!$&'()*+,;=-]+$/;
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function error(status: number, code: string, message: string) {
  return NextResponse.json({ error: { code, message } }, { status });
}

/** Origins this deployment is served from. Behind a TLS-terminating proxy the request URL may be http while the
 * browser origin is https, so the public site URL and the forwarded host are accepted as well. */
function expectedOrigins(req: NextRequest): Set<string> {
  const origins = new Set([req.nextUrl.origin]);
  const host = req.headers.get("x-forwarded-host") || req.headers.get("host");
  if (host) {
    origins.add(`https://${host}`);
    origins.add(`http://${host}`);
  }
  if (process.env.NEXT_PUBLIC_SITE_URL) {
    try {
      origins.add(new URL(process.env.NEXT_PUBLIC_SITE_URL).origin);
    } catch {
      /* ignore malformed configuration */
    }
  }
  return origins;
}

function sameOrigin(req: NextRequest): boolean {
  const expected = expectedOrigins(req);
  const origin = req.headers.get("origin");
  if (origin && origin !== "null") return expected.has(origin);
  const referer = req.headers.get("referer");
  if (!referer) return false;
  try {
    return expected.has(new URL(referer).origin);
  } catch {
    return false;
  }
}

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  if (path[0] !== "api" || path[1] !== "v1" || path.length < 3) return error(404, "not_found", "Not found");
  if (path.some((s) => s === "." || s === ".." || !SEGMENT.test(s))) return error(400, "bad_path", "Invalid path");
  if (UNSAFE.has(req.method) && !sameOrigin(req)) return error(403, "origin_rejected", "Cross-origin request rejected");
  const declared = Number(req.headers.get("content-length") || 0);
  if (declared > MAX_BODY) return error(413, "payload_too_large", "Request body is too large");

  const target = `${API_BASE()}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  const session = req.cookies.get(SESSION_COOKIE)?.value;
  if (session) headers.set("cookie", `${SESSION_COOKIE}=${session}`);
  headers.set("x-forwarded-host", req.headers.get("host") ?? "");
  headers.set("x-forwarded-proto", req.nextUrl.protocol.replace(":", ""));

  const streaming = path[path.length - 1] === "stream" || (req.headers.get("accept") ?? "").includes("text/event-stream");
  const signal = streaming ? req.signal : AbortSignal.any([req.signal, AbortSignal.timeout(TIMEOUT_MS)]);
  const hasBody = !["GET", "HEAD", "OPTIONS"].includes(req.method);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      redirect: "manual",
      cache: "no-store",
      signal,
      // Stream the request body instead of buffering it in memory.
      ...(hasBody ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch (e) {
    const timedOut = (e as Error)?.name === "TimeoutError";
    return error(timedOut ? 504 : 502, timedOut ? "upstream_timeout" : "upstream_unavailable", timedOut ? "The API did not respond in time." : "The API is unavailable.");
  }

  const resHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!DROP_RESPONSE_HEADERS.has(key.toLowerCase()) && key.toLowerCase() !== "set-cookie") resHeaders.set(key, value);
  });
  const location = upstream.headers.get("location");
  if (location?.startsWith(API_BASE())) resHeaders.set("location", `/bff${location.slice(API_BASE().length)}`);
  for (const cookie of upstream.headers.getSetCookie?.() ?? []) resHeaders.append("set-cookie", cookie);
  return new NextResponse(upstream.body, { status: upstream.status, headers: resHeaders });
}

type Ctx = { params: Promise<{ path: string[] }> };
async function handler(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export { handler as GET, handler as POST, handler as PATCH, handler as PUT, handler as DELETE, handler as HEAD, handler as OPTIONS };
