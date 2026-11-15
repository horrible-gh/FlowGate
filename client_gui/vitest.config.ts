import { dirname, resolve } from 'node:path'
import { createRequire } from 'node:module'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

const require = createRequire(import.meta.url)
// Package directory, not `require.resolve(name)` (the Node/CJS entry) — see vite.config.ts.
const pkgDir = (name: string) => dirname(require.resolve(`${name}/package.json`))

export default defineConfig({
  plugins: [vue()],
  // vite.config.ts injects the real build date; tests pin a fixed value.
  define: {
    __FG_GUI_BUILD__: JSON.stringify('test-build'),
  },
  test: {
    environment: 'jsdom',
    globals: true,
    // 0394 T0004, NR0003 §5.4/§13-11: a unit test must never reach a real server.
    setupFiles: ['./tests/setup/blockNetwork.ts'],
  },
  resolve: {
    preserveSymlinks: true,
    alias: {
      '@shared': resolve(__dirname, 'src/shared'),
      '@': resolve(__dirname, 'src'),
      // Keep a single vue/vue-i18n/axios instance in tests too (see vite.config.ts).
      vue: pkgDir('vue'),
      'vue-i18n': pkgDir('vue-i18n'),
      axios: pkgDir('axios'),
    },
  },
})
