import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Public build configuration. The UI talks only to the local ARGUS services, through this
// proxy, so the browser sees one origin. Every port is configurable:
//   ARGUS_UI_PORT         the dev server (default 5173)
//   ARGUS_API_PORT        the read-only observatory service (default 8787)
//   ARGUS_TRANSPORT_PORT  the optional UI command transport (default 8789)
const env = (name: string, fallback: number) => Number(process.env[name] ?? fallback);
const api = env("ARGUS_API_PORT", 8787);
const transport = env("ARGUS_TRANSPORT_PORT", 8789);

export default defineConfig({
  define: {
    // The public build never embeds the builder's checkout path in the bundle. The identity
    // banner then relies on the release build SHA alone (see BuildIdentityBanner in App.tsx).
    __ARGUS_UI_SOURCE_ROOT__: JSON.stringify("not-embedded"),
    __ARGUS_UI_BUILD_SHA__: JSON.stringify(process.env.ARGUS_BUILD_SHA ?? "unknown"),
  },
  appType: "spa",
  server: {
    port: env("ARGUS_UI_PORT", 5173),
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/api": { target: `http://127.0.0.1:${api}`, changeOrigin: false },
      "/ws": { target: `ws://127.0.0.1:${api}`, ws: true, changeOrigin: false },
      "/ui": { target: `http://127.0.0.1:${transport}`, changeOrigin: false },
    },
  },
  preview: {
    port: env("ARGUS_UI_PORT", 4173),
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/api": { target: `http://127.0.0.1:${api}`, changeOrigin: false },
      "/ws": { target: `ws://127.0.0.1:${api}`, ws: true, changeOrigin: false },
      "/ui": { target: `http://127.0.0.1:${transport}`, changeOrigin: false },
    },
  },
  // No source maps in production: they would ship the original tree to anyone with devtools.
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        // The viewers are optional workbench tools. Keep their large libraries out of the
        // navigation and route chunks so opening Home, Sources or System does not pay for 3-D.
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("@kitware\\vtk.js") || id.includes("@kitware/vtk.js")) {
            return "vendor-vtk";
          }
          if (id.includes("openseadragon")) return "vendor-openseadragon";
          return undefined;
        },
      },
    },
  },
});
