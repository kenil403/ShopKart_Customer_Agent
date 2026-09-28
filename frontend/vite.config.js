import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server forwards /api to FastAPI.
// Use 127.0.0.1, NOT "localhost": Node 17+ resolves localhost to IPv6 (::1) first,
// while uvicorn listens on IPv4, which caused "connect ECONNREFUSED ::1:8000".
const API = process.env.VITE_API_URL || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  // Local production preview writes into the backend, which serves it.
  // The Docker build overrides this with --outDir dist.
  build: { outDir: "../backend/static", emptyOutDir: true },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: API, changeOrigin: true } },
  },
});
