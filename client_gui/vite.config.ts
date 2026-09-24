import { resolve } from 'node:path'
import { createRequire } from 'node:module'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const require = createRequire(import.meta.url)

// T0005 §3: v0.2 is a single SPA served under /app, with its own hashed assets under
// /app/assets/* so a rebuild can never collide with v0.1's /assets/* (server/routers/main.py).
export default defineConfig({
  base: '/app/',
  plugins: [vue()],
  resolve: {
    // Windows: the source tree can live on a drive letter mapped to a UNC share.
    // Vite's realpath pass rewrites resolved ids onto whichever drive letter
    // `net use` lists last; the html entry then no longer sits under the root
    // and rollup rejects the emitted absolute fileName. Keep ids on the root's
    // own drive. No effect on a plain (non-symlinked) node_modules install.
    preserveSymlinks: true,
    alias: {
      // T0005 §2/§7: client/shared owns the reusable auth/transport/i18n boundary; v0.2
      // reuses it directly instead of forking a second copy.
      '@shared': resolve(__dirname, '../client/shared'),
      '@': resolve(__dirname, 'src'),
      // client/shared/i18n creates its i18n instance (and AppIcon.vue its components)
      // against whichever 'vue'/'vue-i18n' resolve from client/shared's own directory.
      // Node's plain upward module resolution would find THOSE from client/node_modules
      // (shared physically lives inside the client/ package), a different install than
      // client_gui/src's own client_gui/node_modules copy — app.use(i18n) would then
      // wire together two different Vue runtimes (broken provide/inject, mismatched
      // Plugin types) or two different vue-i18n copies (useI18n()'s injection key is a
      // module-scoped Symbol, so two copies can't see each other's provide()).
      //
      // client_gui declares vue/vue-i18n/axios as its own direct dependencies
      // (package.json) precisely so it never needs client/ installed first or a
      // shared/hoisted node_modules — these aliases just force every importer inside
      // THIS build (client_gui/src and the @shared files it pulls in) onto client_gui's
      // own single copy of each, resolved with plain `require.resolve` so this keeps
      // working whatever the installed version actually is (no hardcoded path/version).
      //
      // axios needs the same treatment as vue/vue-i18n even though it has no
      // provide/inject-style singleton requirement: client/shared/api.ts does a bare
      // `import axios from 'axios'`, and Node's bare-specifier resolution walks UP from
      // the importing file's own directory looking for a node_modules that has it. From
      // client/shared/api.ts that walk finds (or fails to find) something under client/,
      // never the sibling client_gui/node_modules this build actually installed axios
      // into — a clean `client_gui`-only install (no client/node_modules, e.g. the
      // Docker client-gui-build stage) can resolve every other import but fails on this
      // one alone, dropping vue-tsc/vite out to a broken build. Aliasing it here — like
      // vue/vue-i18n above — makes the resolution explicit instead of relying on
      // whatever happens to be installed as a side effect under client/.
      vue: require.resolve('vue'),
      'vue-i18n': require.resolve('vue-i18n'),
      axios: require.resolve('axios'),
    },
  },
  build: {
    outDir: 'dist',
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
