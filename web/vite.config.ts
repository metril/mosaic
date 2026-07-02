import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

// Backend (FastAPI) origin for the dev proxy. Override with VITE_API_TARGET.
const API_TARGET = process.env.VITE_API_TARGET ?? 'http://localhost:8080'
const WS_TARGET = API_TARGET.replace(/^http/, 'ws')

// Paths the SPA fetches from the backend during development. The root "/" is
// intentionally NOT proxied so Vite serves the SPA shell with HMR.
const proxied = {
  '/api': { target: API_TARGET, changeOrigin: true },
  '/auth': { target: API_TARGET, changeOrigin: true },
  '/images': { target: API_TARGET, changeOrigin: true },
  '/thumbnail': { target: API_TARGET, changeOrigin: true },
  '/metadata': { target: API_TARGET, changeOrigin: true },
  '/health': { target: API_TARGET, changeOrigin: true },
  '/metrics': { target: API_TARGET, changeOrigin: true },
  '/css': { target: API_TARGET, changeOrigin: true },
  '/ws': { target: WS_TARGET, ws: true, changeOrigin: true },
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // Built assets land inside the Python package so FastAPI can serve them.
    outDir: '../app/static/spa',
    emptyOutDir: true,
    assetsDir: 'assets',
    // Never inline assets as data: URIs — the app's CSP is default-src 'self'
    // with no font-src, so data: fonts are blocked. Keep every font/asset as a
    // real /assets/* file served from 'self'.
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        // Framework code changes far less often than app code — a separate
        // vendor chunk survives deploys in the browser cache. (Function form:
        // Rolldown-Vite doesn't accept the object shorthand.)
        manualChunks(id: string) {
          if (/node_modules\/(react|react-dom|scheduler|react-router|react-router-dom|@tanstack|zustand)\//.test(id)) {
            return 'vendor'
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: proxied,
  },
})
