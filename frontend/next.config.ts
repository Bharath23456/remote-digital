import type { NextConfig } from "next";

const apiOrigin = process.env.DJANGO_API_URL || "http://127.0.0.1:8000";
const storageOrigin = process.env.STORAGE_GATEWAY_URL || "http://127.0.0.1:9000";
const identityOrigin = process.env.IDENTITY_SERVICE_URL || "http://127.0.0.1:8100";
const allowedDevOrigins = [
  "localhost",
  "*.localhost",
  "127.0.0.1",
  "*.sslip.io",
  "*.nip.io",
  "192.168.1.*",
  ...(process.env.NGROK_DOMAIN ? [process.env.NGROK_DOMAIN] : []),
];

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: { root: __dirname },
  poweredByHeader: false,
  allowedDevOrigins,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
        { key: "Content-Security-Policy", value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" },
      ],
    }];
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiOrigin}/api/:path*` },
      { source: "/storage/:path*", destination: `${storageOrigin}/:path*` },
      { source: "/identity-api/:path*", destination: `${identityOrigin}/:path*` },
    ];
  },
};

export default nextConfig;
