import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// AgentGeeX SPA — Vite config
// Notes:
// - demo.tsx under registry-items/ is loaded dynamically via Vite glob + alias.
// - Standalone open-source project: zero custom injection, `npm install` reproduces it.
export default defineConfig({
  plugins: [react()],
  // [2026-10-20] Build fingerprint: shown in the footer, to confirm 'is the browser running the latest frontend'.
  // Feng hit 'changed it but the page did not change' several times — it was actually the browser/dev server serving an old bundle,
  // with this fingerprint a comparison tells you whether a hard refresh is needed. Refreshed on dev start/restart.
  define: {
    __BUILD_STAMP__: JSON.stringify(
      (() => {
        const d = new Date();
        const p2 = (n: number) => String(n).padStart(2, "0");
        return `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
      })()
    ),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5178,
    fs: {
      // Allow loading registry-items/ source from the repo root
      allow: [path.resolve(__dirname, ".")],
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
