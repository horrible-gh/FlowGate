/** serves connection-recovery.v02gui0031.html for the Playwright scenarios.
 * node tests/browser/connection-recovery.v02gui0031.serve.mjs <api-base-url>   (cwd: client_gui)
 * Prints `READY <page url>` once listening; stops when stdin closes (the scenario script owns it). The fixture talks
 * to <api-base-url> (the scenario's fake FlowGate server) as VITE_API_BASE_URL. Vite's cache stays in node_modules.
 */
import { resolve, dirname } from 'node:path'
import { createRequire } from 'node:module'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiBase = process.argv[2]
if (!apiBase) throw new Error('Pass the fake API base URL')
const require = createRequire(import.meta.url)
const pkg = (name) => dirname(require.resolve(name + '/package.json'))
const server = await createServer({
  configFile: false, root: process.cwd(), cacheDir: resolve('node_modules/.vite-v02gui0031'), logLevel: 'error',
  plugins: [vue()],
  define: { __FG_GUI_BUILD__: JSON.stringify('test'), 'import.meta.env.VITE_API_BASE_URL': JSON.stringify(apiBase) },
  resolve: { preserveSymlinks: true, alias: {
    '@': resolve('src'), '@shared': resolve('src/shared'), vue: pkg('vue'), 'vue-i18n': pkg('vue-i18n'), axios: pkg('axios'),
  } },
  server: { host: '127.0.0.1', port: 0, hmr: false },
})
await server.listen()
const { port } = server.httpServer.address()
process.stdout.write(`READY http://127.0.0.1:${port}/tests/browser/connection-recovery.v02gui0031.html\n`)
const stop = async () => { await server.close(); process.exit(0) }
process.stdin.on('end', stop)
process.stdin.on('close', stop)
process.stdin.resume()
