import type { NextConfig } from "next";

// /api/* reaches the gateway through src/app/api/[...path]/route.ts, which
// reads VOYQI_GATEWAY_URL at request time.
const config: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
};

export default config;
