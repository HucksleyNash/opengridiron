import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, ".", "").FOOTBALL_API_TARGET || "http://127.0.0.1:8000";
  return {
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": target, "/healthz": target },
  },
  build: { sourcemap: true },
  };
});
