import { dirname, resolve } from 'node:path'
import { createRequire } from 'node:module'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

const require = createRequire(import.meta.url)
// Package directory, not `require.resolve(name)` (the Node/CJS entry) — see vite.config.ts.
const pkgDir = (name: string) => dirname(require.resolve(`${name}/package.json`))

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    // Mirrors client/tests/setup/blockNetwork.ts (0394 T0004, NR0003 §5.4/§13-11): a
    // unit test must never reach a real server.
    setupFiles: ['./tests/setup/blockNetwork.ts'],
  },
  resolve: {
    preserveSymlinks: true,
    alias: {
      '@shared': resolve(__dirname, '../client/shared'),
      '@': resolve(__dirname, 'src'),
      // Keep a single vue/vue-i18n instance across client_gui/src and client/shared in
      // tests too (see vite.config.ts for why two copies breaks provide/inject, useI18n
      // injection, and Plugin typing), and resolve axios onto client_gui's own
      // node_modules copy for the same clean-install reason vite.config.ts documents
      // (client/shared/api.ts's bare `axios` import can't see a sibling
      // client_gui/node_modules through Node's upward resolution). No npm workspace
      // root needed — see vite.config.ts for why.
      vue: pkgDir('vue'),
      'vue-i18n': pkgDir('vue-i18n'),
      axios: pkgDir('axios'),
    },
  },
})
