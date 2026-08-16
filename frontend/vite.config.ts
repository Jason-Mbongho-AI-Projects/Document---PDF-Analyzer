import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The dev server proxies the API so the browser sees a single origin and CORS
// never enters the picture during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // PDF.js pulls in a worker and canvas; component tests stub it instead.
    exclude: ["node_modules/**", "dist/**"],
  },
});
