import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true, // reachable from outside the container
    port: 5173,
    watch: {
      // chokidar doesn't see change events across the Windows -> container bind mount without
      // this (same issue the backend's WATCHFILES_FORCE_POLLING works around).
      usePolling: true,
    },
  },
})
