import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API + auth routes to the FastAPI backend so the SPA can
// run with hot reload while the backend serves data and the session cookie.
const BACKEND = process.env.WEBUI_BACKEND ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/login": { target: BACKEND, changeOrigin: true },
      "/logout": { target: BACKEND, changeOrigin: true },
    },
  },
});
