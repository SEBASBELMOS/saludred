import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          query: ["@tanstack/react-query"],
          prime: [
            "primereact/api",
            "primereact/passthrough/tailwind",
            "primereact/dialog",
            "primereact/sidebar",
            "primereact/toast",
          ],
        },
      },
    },
  },
  // ws: true lets the live channel (/api/v1/events/ws) through the dev proxy.
  server: { proxy: { "/api": { target: "http://localhost:8000", ws: true } } },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    clearMocks: true,
  },
});
