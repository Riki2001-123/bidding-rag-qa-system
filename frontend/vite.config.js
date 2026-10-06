import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig(({ mode }) => ({
  base: mode === "pages" ? (process.env.VITE_SITE_BASE || "/bidding-rag-qa-system/") : "/",
  resolve: { alias: { "@application": fileURLToPath(new URL(mode === "pages" ? "./src/PagesApp.jsx" : "./src/App.jsx", import.meta.url)) } },
  plugins: [react()],
  build: {
    outDir: mode === "pages" ? "../dist/pages-deployment/site" : "../dist/showcase",
    emptyOutDir: true,
  },
  server: {
    port: 5173
  }
}));
