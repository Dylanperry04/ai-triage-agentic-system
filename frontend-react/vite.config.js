import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-server proxy so `npm run dev` talks to a locally running backend on :8000.
// In production the built app is served BY the FastAPI backend (same origin).
const apiRoots = ["/auth","/cases","/workflow","/audit","/system","/security","/governance","/health","/status","/runtime","/model","/cost","/docs","/openapi.json"];
export default defineConfig({
  plugins: [react()],
  server: { proxy: Object.fromEntries(apiRoots.map(p => [p, { target: "http://127.0.0.1:8000", changeOrigin: true }])) },
  build: { outDir: "dist", sourcemap: false },
});
