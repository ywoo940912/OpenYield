import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/panels":   { target: "http://localhost:8000", changeOrigin: true },
      "/yield":    { target: "http://localhost:8000", changeOrigin: true },
      "/genealogy":{ target: "http://localhost:8000", changeOrigin: true },
      "/ingest":   { target: "http://localhost:8000", changeOrigin: true },
      "/classify":  { target: "http://localhost:8000", changeOrigin: true },
      "/products":  { target: "http://localhost:8000", changeOrigin: true },
      "/simulate":  { target: "http://localhost:8000", changeOrigin: true },
      "/defects":   { target: "http://localhost:8000", changeOrigin: true },
      "/pareto":    { target: "http://localhost:8000", changeOrigin: true },
      "/spc":       { target: "http://localhost:8000", changeOrigin: true },
      "/trends":    { target: "http://localhost:8000", changeOrigin: true },
      "/lots":      { target: "http://localhost:8000", changeOrigin: true },
      "/correlation":{ target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist" },
});
