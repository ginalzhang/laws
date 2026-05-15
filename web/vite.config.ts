import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/review/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/projects": "http://127.0.0.1:8000",
    },
  },
});
