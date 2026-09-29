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
    // Windows: the source tree can live on a drive letter mapped to a UNC share.
    // Vite's realpath pass rewrites resolved ids onto whichever drive letter
    // `net use` lists last; the html entries then no longer sit under the root
    // and rollup rejects the emitted absolute fileName. Keep ids on the root's
    // own drive. No effect on a plain (non-symlinked) node_modules install.
    preserveSymlinks: true,
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
      // T0005 §5 (rejection round 2): client_gui (v0.2) has its own dev server
      // (client_gui/vite.config.ts, port 3001) so its guard can redirect to the
      // relative `/login` this app owns and a post-login `next=/app/...` can pass
      // client/shared/auth.ts's same-origin check — neither works if a browser opens
      // client_gui's dev server directly on a different port. client_gui sets
      // `base: '/app/'`, so Vite prefixes literally everything it serves in dev
      // (the HTML, every module import, and the HMR websocket) under `/app/` — proxying
      // that one prefix here reproduces the same single-origin layout production gets
      // from server/routers/main.py serving both dists off one port. `ws: true` carries
      // the HMR socket through so client_gui's own dev server still hot-reloads.
      // 'localhost', not '127.0.0.1': Vite's dev server (client_gui) binds its default
      // host, which resolves to the IPv6 loopback (`::1`) on this stack — a literal
      // 127.0.0.1 target got ECONNREFUSED even with client_gui's server up and reachable
      // at http://localhost:3001 directly.
      '/app': {
        target: 'http://localhost:3001',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
