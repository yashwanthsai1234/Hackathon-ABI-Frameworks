import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Static SPA — builds to dist/, reads public/export.json at runtime. No backend.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // On-demand AI summaries: proxy /api to the `woundpipe serve` FastAPI backend.
  server: { proxy: { "/api": "http://localhost:8787" } },
  build: {
    chunkSizeWarningLimit: 1400,
    rollupOptions: {
      output: {
        manualChunks: {
          flow: ["@xyflow/react"],
          charts: ["recharts"],
          table: ["@tanstack/react-table"],
          motion: ["framer-motion"],
        },
      },
    },
  },
});
