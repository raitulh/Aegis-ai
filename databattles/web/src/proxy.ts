import { NextResponse, type NextRequest } from "next/server";

/**
 * Lightweight route guard (Next.js 16 "proxy", formerly middleware).
 * It only checks for the presence of the session cookie to redirect signed-out visitors early —
 * real authorization is always enforced by the API on every request.
 */
const PROTECTED = ["/dashboard", "/settings", "/notifications", "/organize", "/judge", "/admin", "/moderation", "/onboarding",
  "/projects/new", "/datasets/new", "/invites", "/discussions/new", "/orgs/new", "/orgs/mine", "/learn/authoring"];

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const needsAuth = PROTECTED.some((p) => pathname === p || pathname.startsWith(p + "/"));
  if (needsAuth && !request.cookies.get("db_session")) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api/|_next/|favicon.ico|robots.txt|sitemap.xml).*)"],
};
