import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

const backend = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/health": { target: backend, changeOrigin: true },
    },
  },
});
