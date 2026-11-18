import path from "path"
import { fileURLToPath } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { loadBuildInfo } from "./scripts/buildInfo"

const currentDir = fileURLToPath(new URL(".", import.meta.url))
// Real-platform browser tests keep the entry Host so Django can compare it
// with the browser Origin. Ordinary local development keeps its existing proxy.
const changeApiOrigin = process.env.VITE_DEV_API_PRESERVE_HOST !== "1"

// https://vite.dev/config/
export default defineConfig(() => ({
  define: {
    __AIVISION_BUILD_INFO__: JSON.stringify(
      loadBuildInfo(currentDir, process.env.AIVISION_BUILD_INFO_BASE64)
    ),
  },
  base: "/",
  build: {
    assetsDir: "assets",
    outDir: path.resolve(currentDir, "../dist"),
  },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(currentDir, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5174,
    proxy: {
      "/api": {
        target: process.env.VITE_DEV_API_TARGET || "http://localhost:8000",
        changeOrigin: changeApiOrigin,
      },
      "/openapi": {
        target: process.env.VITE_DEV_API_TARGET || "http://localhost:8000",
        changeOrigin: changeApiOrigin,
      },
      "/project/": {
        target: process.env.VITE_DEV_API_TARGET || "http://localhost:8000",
        changeOrigin: changeApiOrigin,
      },
    },
  },
}))
