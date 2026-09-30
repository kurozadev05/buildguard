import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  compress: true, // gzip for pages/JS. The SSE stream is exempt because the BFF marks it `Cache-Control: no-transform`; verified by tests-integration/bff.e2e.mjs (incremental deltas).
  async headers() {
    const common = [
      { key: "X-Content-Type-Options", value: "nosniff" }, { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Permissions-Policy", value: "camera=(self), geolocation=(self), microphone=(), payment=(), usb=()" },
    ];
    return [{ source: "/:path*", headers: common }, { source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "Service-Worker-Allowed", value: "/" }] }];
  },
  // Nothing secret is ever put in `env`: BACKEND_URL is read at request time on the server only.
};
export default config;
