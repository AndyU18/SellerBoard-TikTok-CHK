import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  server: {
    // scripts/dev.ps1 define CHK_BACKEND_PORT cuando el 8000 está ocupado por otro programa.
    proxy: { '/api': `http://127.0.0.1:${process.env.CHK_BACKEND_PORT ?? '8000'}` },
  },
})
