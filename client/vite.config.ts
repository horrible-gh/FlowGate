import { resolve } from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { defineConfig } from 'vite'
import type { ViteDevServer } from 'vite'
import vue from '@vitejs/plugin-vue'

const multiPageHistoryFallback = () => ({
  name: 'flowgate-multi-page-history-fallback',
  configureServer(server: ViteDevServer) {
    server.middlewares.use((req: IncomingMessage, _res: ServerResponse, next: () => void) => {
      if (!req.url || (req.method !== 'GET' && req.method !== 'HEAD')) {
        next()
        return
      }

      const accept = req.headers.accept ?? ''
      if (!accept.includes('text/html')) {
        next()
        return
      }

      const url = new URL(req.url, 'http://localhost')
      if (url.pathname === '/main' || url.pathname.startsWith('/main/')) {
        req.url = `/main.html${url.search}`
      } else if (url.pathname === '/settings' || url.pathname.startsWith('/settings/')) {
        req.url = `/settings.html${url.search}`
      }
      next()
    })
  },
})

export default defineConfig({
  plugins: [vue(), multiPageHistoryFallback()],
  resolve: {
    alias: {
      '@shared': resolve(__dirname, 'shared'),
      '@login': resolve(__dirname, 'src/login'),
      '@main': resolve(__dirname, 'src/main'),
      '@': resolve(__dirname, 'src'),
    },
  },
  build: {
    rollupOptions: {
      input: {
        login: resolve(__dirname, 'index.html'),
        main: resolve(__dirname, 'main.html'),
        settings: resolve(__dirname, 'settings.html'),
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/flowgate': {
        target: 'http://127.0.0.1:8088',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8088',
        changeOrigin: true,
      },
    },
  },
})
