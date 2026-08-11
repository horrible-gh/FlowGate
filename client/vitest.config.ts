import { resolve } from 'node:path'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    // 0394 T0004 (NR0003 §5.4): unit tests must not reach a real server. Four specs
    // were opening XHRs at the dev server's address and passing only because nothing
    // answered; see tests/setup/blockNetwork.ts.
    setupFiles: ['./tests/setup/blockNetwork.ts'],
  },
  resolve: {
    // Windows: the source tree can live on a drive letter mapped to a UNC share.
    // Vite's realpath pass rewrites resolved ids onto whichever drive letter
    // `net use` lists last, which no longer matches the project root and makes
    // every import fail to load. Keep resolved ids on the root's own drive.
    preserveSymlinks: true,
    alias: {
      '@shared': resolve(__dirname, 'shared'),
      '@login': resolve(__dirname, 'src/login'),
      '@main': resolve(__dirname, 'src/main'),
      '@': resolve(__dirname, 'src'),
    },
  },
})
