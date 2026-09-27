import { type NextRequest, NextResponse } from "next/server";

/**
 * Backend-for-frontend proxy (runtime). The browser calls same-origin /bff/*, this handler forwards to
 * the API using the runtime API base URL, and passes cookies (incl. Set-Cookie) through. Evaluated per
 * request, so the API target is never baked in at build time.
 */
export const dynamic = "force-dynamic";

const API_BASE = () => process.env.API_INTERNAL_URL || process.env.API_BASE_URL || "http://localhost:8000";
const HOP_BY_HOP = new Set(["connection", "keep-alive", "transfer-encoding", "upgrade", "content-length", "host"]);

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  const search = req.nextUrl.search;
  const target = `${API_BASE()}/${path.join("/")}${search}`;
  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  headers.set("x-forwarded-host", req.headers.get("host") ?? "");

  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(req.method)) body = await req.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, { method: req.method, headers, body, redirect: "manual", cache: "no-store" });
  } catch {
    return NextResponse.json({ error: { code: "upstream_unavailable", message: "The API is unavailable." } }, { status: 502 });
  }

  const resHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) resHeaders.set(key, value);
  });
  // Preserve multiple Set-Cookie headers.
  const setCookie = (upstream.headers as unknown as { getSetCookie?: () => string[] }).getSetCookie?.();
  if (setCookie?.length) {
    resHeaders.delete("set-cookie");
    for (const cookie of setCookie) resHeaders.append("set-cookie", cookie);
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers: resHeaders });
}

type Ctx = { params: Promise<{ path: string[] }> };
async function handler(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export { handler as GET, handler as POST, handler as PATCH, handler as PUT, handler as DELETE, handler as HEAD, handler as OPTIONS };
