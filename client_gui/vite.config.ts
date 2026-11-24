import { dirname, resolve } from 'node:path'
import { createRequire } from 'node:module'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import thirdPartyNotices from './scripts/third-party-notices'
import { PRODUCT_NAME } from './src/shared/productName'

const require = createRequire(import.meta.url)
// Alias target = the package DIRECTORY, never `require.resolve(name)`. The latter returns
// the CommonJS/Node entry (vue/index.js -> vue.cjs.prod.js with the template compiler,
// vue-i18n/index.js -> vue-i18n.prod.cjs, axios -> dist/node/axios.cjs). Aliasing onto a
// file skips Vite's own package-entry resolution (browser/module/exports conditions), so
// the /app production bundle shipped axios's Node build: axios.cjs -> https-proxy-agent@5
// -> agent-base@6 `class Agent extends require('events').EventEmitter`, where 'events' is
// Vite's empty __vite-browser-external stub -> "Class extends value undefined is not a
// constructor or null" at module init and a blank /app (T0009 TR0010 rev2). A directory
// alias still pins the single client_gui copy but lets Vite pick the browser ESM entry.
const pkgDir = (name: string) => dirname(require.resolve(`${name}/package.json`))

// Sidebar version block (T0011 §7): "Build YYYY.MM.DD" is the day this bundle was built,
// not a hand-kept constant that goes stale.
const buildDate = new Date()
const BUILD_STAMP = [
  buildDate.getFullYear(),
  String(buildDate.getMonth() + 1).padStart(2, '0'),
  String(buildDate.getDate()).padStart(2, '0'),
].join('.')

// T0005 §3: v0.2 is served under /app with its hashed assets under /app/assets/*
// (server/routers/main.py). flowgate.v02gui.0024: the build has two HTML entries — index.html
// (the /app SPA) and login.html (the sign-in page the server returns for / and /login).
export default defineConfig({
  base: '/app/',
  plugins: [
    vue(),
    thirdPartyNotices(resolve(__dirname, '..')),
    // F86-7 U3: the HTML <title> of both entries reads the single product-name constant.
    { name: 'flowgate-product-name', transformIndexHtml: { order: 'pre', handler: (html: string) => html.replace(/%PRODUCT_NAME%/g, PRODUCT_NAME) } },
  ],
  define: {
    __FG_GUI_BUILD__: JSON.stringify(BUILD_STAMP),
  },
  resolve: {
    // Windows: the source tree can live on a drive letter mapped to a UNC share.
    // Vite's realpath pass rewrites resolved ids onto whichever drive letter
    // `net use` lists last; the html entry then no longer sits under the root
    // and rollup rejects the emitted absolute fileName. Keep ids on the root's
    // own drive. No effect on a plain (non-symlinked) node_modules install.
    preserveSymlinks: true,
    alias: {
      // flowgate.v02gui.0024: the reusable auth/transport/i18n modules (formerly v0.1's
      // client/shared) now live inside this package at src/shared, still imported as @shared.
      '@shared': resolve(__dirname, 'src/shared'),
      '@': resolve(__dirname, 'src'),
      // Pin every importer in this build onto client_gui's own single copy of vue, vue-i18n and
      // axios (package directories located with `require.resolve` of their package.json — see
      // pkgDir above — so this keeps working whatever version is installed). Two Vue runtimes
      // would break provide/inject and Plugin typing, two vue-i18n copies can't see each other's
      // useI18n() injection Symbol, and the explicit axios alias keeps the browser ESM entry.
      vue: pkgDir('vue'),
      'vue-i18n': pkgDir('vue-i18n'),
      axios: pkgDir('axios'),
    },
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: {
        index: resolve(__dirname, 'index.html'),
        login: resolve(__dirname, 'login.html'),
      },
    },
  },
  server: {
    port: 3001,
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
