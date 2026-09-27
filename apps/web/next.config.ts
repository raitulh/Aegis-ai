import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
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
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
