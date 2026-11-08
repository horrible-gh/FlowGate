// T-c: launch through the Python browser self-check, which supplies a temporary real HTTP server.
// Every generated bundle, profile, log, measurement and capture goes to the scratch outDir.
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
const [outArg, baseUrl] = process.argv.slice(2)
const cwd = process.cwd(), out = resolve(outArg), here = dirname(fileURLToPath(import.meta.url))
if (!relative(resolve(cwd, '..'), out).startsWith('..')) throw Error('outDir must be external scratch')
const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex')
const mock = join(here, 'mockup.html')
if (sha(mock) !== 'ab33f07329b4d9371054692e9cfc4159b1a440bb7aea2bee28d6a3b90598d37b') throw Error('Unapproved mockup')
const require = createRequire(join(cwd, 'package.json'))
const pkgDir = (name) => {
  try { return dirname(require.resolve(`${name}/package.json`)) }
  catch { return resolve(cwd, 'node_modules', name) }
}
process.env.NODE_ENV = 'production'
const vite = await import(pathToFileURL(join(pkgDir('vite'), 'dist/node/index.js')).href)
const vueMod = await import(pathToFileURL(require.resolve('@vitejs/plugin-vue')).href)
const vue = typeof vueMod.default === 'function' ? vueMod.default : vueMod.default.default
mkdirSync(out, { recursive: true })
await vite.build({ configFile: false, mode: 'production', root: out, base: './', logLevel: 'warn',
  cacheDir: join(out, '.vite-cache'), plugins: [vue()],
  define: { __FG_GUI_BUILD__: JSON.stringify('archive-browser'), 'process.env.NODE_ENV': JSON.stringify('production'),
    __VUE_OPTIONS_API__: 'true', __VUE_PROD_DEVTOOLS__: 'false', __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: 'false' },
  resolve: { preserveSymlinks: true, alias: { '@shared': resolve(cwd, '../client/shared'), '@': resolve(cwd, 'src'),
    ...Object.fromEntries(['vue', 'vue-i18n', 'axios', 'pinia', 'vue-router', '@vue-flow/core', '@vue-flow/background'].map((n) => [n, pkgDir(n)])) } },
  build: { outDir: join(out, 'bundle'), emptyOutDir: true, minify: false, cssCodeSplit: false,
    lib: { entry: join(here, 'harness.ts'), name: 'FgArchiveHarness', formats: ['iife'], fileName: () => 'harness.js' } },
})
const bundle = join(out, 'bundle')
copyFileSync(mock, join(bundle, 'mockup.html'))
writeFileSync(join(bundle, 'compare.html'), `<!doctype html><html lang="ko"><head><meta charset="utf-8">
${readdirSync(bundle).filter((f) => f.endsWith('.css')).map((f) => `<link rel="stylesheet" href="${f}">`).join('\n')}
<style>body{margin:0;padding:16px;background:#0d1b2e;color:#c7d8e9}.cap{font:600 13px sans-serif;margin:12px 0}
#mockframe{width:1202px;height:55px;border:1px dashed #5a6b80}#real{width:1202px}#fg-measure{display:none}</style></head><body>
<div class="cap">승인 시안 — 버튼 위치와 아이콘</div><iframe id="mockframe" src="mockup.html"></iframe>
<div class="cap">현재 구현 — 완료 Flow 아카이브 → 우클릭 복원 후</div><div id="real"></div>
<pre id="fg-measure"></pre><script src="harness.js"></script></body></html>`)
const browser = [join(process.env['ProgramFiles(x86)'] ?? 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe'),
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium'].find(existsSync)
if (!browser) throw Error('NO_BROWSER')
const capture = join(out, 'capture.png')
const result = spawnSync(browser, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-extensions', `--user-data-dir=${join(out, '.browser-profile')}`, '--hide-scrollbars',
  '--force-device-scale-factor=1', '--virtual-time-budget=30000', '--window-size=1260,650',
  '--dump-dom', `--screenshot=${capture}`, `${baseUrl}/visual/bundle/compare.html`],
  { encoding: 'utf8', windowsHide: true, timeout: 120000, maxBuffer: 16 << 20 })
writeFileSync(join(out, 'browser.log'), result.stderr ?? '')
writeFileSync(join(out, 'browser.html'), result.stdout ?? '')
const match = /FGMEASURE(.*?)FGMEASURE/s.exec(result.stdout ?? '')
const data = match ? JSON.parse(match[1].replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')) : { error: 'NO_MEASURE', status: result.status }
data.mockupSha256 = sha(mock)
data.capture = existsSync(capture) ? { path: capture, sha256: sha(capture) } : null
writeFileSync(join(out, 'results.json'), JSON.stringify(data, null, 2) + '\n')
console.log(JSON.stringify(data))
process.exit(data.error || !data.capture ? 1 : 0)
