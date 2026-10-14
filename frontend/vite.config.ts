import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

const localApiTarget = process.env.CODEGUARD_API_PROXY || 'http://localhost:8000'

// The dev server proxies /api and /health to the backend so that the browser
// sees a single origin during development. This keeps cookie and CORS
// behaviour in development close to production, where the SPA and the API are
// served from sibling hosts.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: localApiTarget, changeOrigin: true },
      '/health': { target: localApiTarget, changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
})
