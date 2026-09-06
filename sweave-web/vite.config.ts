import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  // R4.1 step 1c: Tailwind v4 is CSS-first; the Vite plugin
  // replaces the PostCSS chain. Drop postcss.config.js + the
  // tailwind.config.js (tokens now live in globals.css @theme).
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8100',
        ws: true,
        changeOrigin: true,
      },
    },
  },
})