import { resolve } from 'node:path'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// flowgate.default.0615 T0004 §7/rev2 — a SEPARATE, opt-in config for the one
// connected regression that deliberately talks to a real server
// (client/tests/integration/**). The default client/vitest.config.ts loads
// tests/setup/blockNetwork.ts specifically so ordinary specs can never reach a real
// server by accident (0394 T0004 / R0001 §5.1); this config exists so that
// exception stays explicit, isolated to its own npm script (`npm run
// test:integration`), and never silently included in the default `npm test` run
// (client/vitest.config.ts excludes tests/integration/** for the same reason).
const LIVE_SERVER_PORT = 18789

export default defineConfig({
  plugins: [vue()],
  define: {
    'import.meta.env.VITE_API_BASE_URL': JSON.stringify(`http://127.0.0.1:${LIVE_SERVER_PORT}/flowgate`),
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/integration/**/*.spec.ts'],
    // No setupFiles here — no blockNetwork.ts. Requests from these specs must
    // reach the real live server spawned in each spec's own beforeAll/afterAll.
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
  resolve: {
    preserveSymlinks: true,
    alias: {
      '@shared': resolve(__dirname, 'shared'),
      '@login': resolve(__dirname, 'src/login'),
      '@main': resolve(__dirname, 'src/main'),
      '@': resolve(__dirname, 'src'),
    },
  },
})
