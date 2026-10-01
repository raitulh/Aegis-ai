import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";
// HTTPS-only directives are emitted only when the public site URL is https (never for local http runs).
const isHttps = (process.env.NEXT_PUBLIC_SITE_URL ?? "").startsWith("https://");

/**
 * Content Security Policy. All API traffic goes through the same-origin BFF, so `connect-src 'self'` is
 * sufficient and blocks exfiltration to other origins. Next's bootstrap uses inline scripts, so
 * `script-src` allows `'unsafe-inline'` (no nonces: pages stay statically renderable); everything else
 * is locked down. See docs/security.md for the nonce-based hardening option.
 */
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "img-src 'self' data: blob:",
  `connect-src 'self'${isDev ? " ws: http://localhost:*" : ""}`,
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(isHttps ? ["upgrade-insecure-requests"] : []),
].join("; ");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Emit a self-contained server bundle (.next/standalone) for the minimal Docker image.
  // Opt-in (the Docker build sets NEXT_BUILD_STANDALONE=1) so local `next start` and the
  // Playwright E2E server keep working without the standalone warning.
  output: process.env.NEXT_BUILD_STANDALONE === "1" ? "standalone" : undefined,
  outputFileTracingRoot: process.env.NEXT_OUTPUT_TRACING_ROOT ?? undefined,
  transpilePackages: ["three"],
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          ...(isHttps ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }] : []),
        ],
      },
    ];
  },
};

export default nextConfig;
