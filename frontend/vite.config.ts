import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { fileURLToPath, URL } from "node:url";

// The API lives at the container hostname `api`; the browser talks to /api
// which Vite proxies (stripping the prefix) to the FastAPI root.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://api:8000";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: {
        name: "metalmark",
        short_name: "metalmark",
        description: "Self-hosted personal finance",
        theme_color: "#0f172a",
        background_color: "#0f172a",
        display: "standalone",
        icons: [],
      },
      workbox: {
        // Cache the app SHELL only. Financial data (/api) is never cached
        // on-device (ARCHITECTURE §5) — always go to the network.
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api"),
            handler: "NetworkOnly",
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Allow the `web` compose-service hostname so Playwright can drive the dev
    // server from another container on the compose network (e2e/CI). localhost
    // and IP hosts are always permitted; this is additive and dev-only.
    allowedHosts: ["web"],
    watch: { usePolling: true },
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Playwright specs live under e2e/ and must not be collected by vitest.
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    // Vitest stubs CSS imports to an empty string by default, and it does so for
    // `?raw` too. `src/test/setup.ts` imports index.css that way to seed the
    // custom properties into jsdom, and an empty stylesheet there fails silently
    // — the import works, the tokens are simply absent, and `token()` throws
    // "Unknown design token" from whatever test happens to touch a chart.
    css: true,
  },
});
