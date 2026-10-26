import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The console talks only to the API gateway. In development Vite proxies
// /api to it, so the session cookie is same-origin; in a deployment the
// ingress serves this app and the gateway under one host.
const gateway = process.env.VOYQI_GATEWAY_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5180, proxy: { "/api": { target: gateway, changeOrigin: true } } },
  preview: { port: 5180, proxy: { "/api": { target: gateway, changeOrigin: true } } },
});
