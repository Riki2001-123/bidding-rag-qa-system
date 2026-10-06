import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../dist/showcase",
    emptyOutDir: true,
  },
  server: {
    port: 5173
  }
});
