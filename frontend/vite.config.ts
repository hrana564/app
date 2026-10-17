import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The frontend talks to FastAPI only through Vite's dev proxy, so the browser
// sees a single origin in development. That keeps the eventual Power Apps
// embedding path simple: one base URL to configure, no CORS preflight chatter.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: "127.0.0.1",
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/docs": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
});