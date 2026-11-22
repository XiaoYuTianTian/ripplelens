import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies /api to a running `ripplelens review --port 8765 --no-open`
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/ripplelens/web_dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
