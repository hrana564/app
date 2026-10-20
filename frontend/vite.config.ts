import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The frontend talks to FastAPI only through Vite's dev proxy, so the browser
// sees a single origin in development. That keeps the eventual Power Apps
// embedding path simple: one base URL to configure, no CORS preflight chatter.
//
// Where the API lives is the backend's business: `API_PORT` in `backend/.env`.
// Only that one key is read - the rest of that file holds the deployment's
// secrets and must not reach the dev server - so the proxy cannot end up
// pointing at a port the API is not listening on.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "../backend", ["API_PORT"]);
  const api = `http://127.0.0.1:${env.API_PORT || 3000}`;

  return {
    plugins: [react()],
    server: {
      port: 5173,
      host: "127.0.0.1",
      proxy: {
        "/api": { target: api, changeOrigin: true },
        "/health": { target: api, changeOrigin: true },
        "/docs": { target: api, changeOrigin: true },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
      chunkSizeWarningLimit: 1200,
    },
  };
});
