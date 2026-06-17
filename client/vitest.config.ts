import { resolve } from 'node:path'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
  },
  resolve: {
    alias: {
      '@shared': resolve(__dirname, 'shared'),
      '@login': resolve(__dirname, 'src/login'),
      '@main': resolve(__dirname, 'src/main'),
      '@': resolve(__dirname, 'src'),
    },
  },
})
