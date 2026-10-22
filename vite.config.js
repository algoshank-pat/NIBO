import { defineConfig } from "vite";
export default defineConfig({
  server: {
    proxy: {
      "/analyze": "http://127.0.0.1:8000",
      "/applications": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
  build: { outDir: "dist" },
});
