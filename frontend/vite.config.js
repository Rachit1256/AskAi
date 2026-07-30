import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the browser only ever talks to this origin and Vite proxies
// /api to the backend, so CORS is a production concern only.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
