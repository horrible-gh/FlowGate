// flowgate.v02test.0002 R0001 #2 (0010-T §5) - browser measurement of the Git box against the approved mockup.
//
//   cd client_gui
//   node tests/visual/gitSettingsMockup/measure.mjs <outDir> <flowgate_git_settings_mockup.html>
//
// <outDir> must be outside the repository (a scratch folder); nothing is written to the source tree.
// The mockup is the R0001 attachment; its sha256 must be the approved one.
// 1) bundles harness.ts (the real CheckSettings.vue + the mockup in an iframe) with this package's own vite and
//    @vitejs/plugin-vue, in production mode like `vite build` (templates without comments, production runtime),
// 2) opens it in a headless Chromium (Edge, else Chrome) per width case: --dump-dom for the measurements,
//    --screenshot for the side-by-side capture,
// 3) writes <outDir>/results.json and capture_<case>.png and prints a short summary.
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { basename, dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const APPROVED_MOCKUP_SHA256 = 'effa4c1da97a7c084aad12fb4408efed4053b85311e460de990a6cbc921caeb3'
const here = dirname(fileURLToPath(import.meta.url))
const cwd = process.cwd()
const [outArg, mockArg] = process.argv.slice(2)
if (!outArg || !mockArg) { console.log('usage: node measure.mjs <outDir> <mockup.html>'); process.exit(2) }
const out = resolve(outArg)
const repoRoot = resolve(cwd, '..')
if (!relative(repoRoot, out).startsWith('..')) { console.log(`outDir must be outside the repository: ${out}`); process.exit(2) }
const sha = p => createHash('sha256').update(readFileSync(p)).digest('hex')
if (sha(mockArg) !== APPROVED_MOCKUP_SHA256) { console.log(`mockup sha256 ${sha(mockArg)} is not the approved one`); process.exit(2) }

const require = createRequire(join(cwd, 'package.json'))
const pkgDir = name => dirname(require.resolve(`${name}/package.json`))
console.log(`cwd=${cwd} NODE_ENV(before)=${process.env.NODE_ENV ?? '-'}`)
process.env.NODE_ENV = 'production'

const tracked = [
  'src/features/designer/inspector/features/checkSettings/CheckSettings.vue',
  'src/components/ui/Toggle.vue',
  'src/components/ui/Select.vue',
  'src/features/designer/inspector/parts/InspectorBlock.vue',
  'src/features/designer/inspector/parts/InspectorControlRow.vue',
  'src/features/designer/inspector/parts/InspectorSegment.vue',
  'tests/visual/gitSettingsMockup/harness.ts',
  'tests/visual/gitSettingsMockup/measure.mjs',
]
const hashes = Object.fromEntries(tracked.map(p => [p, sha(join(cwd, p))]))
hashes.mockup = sha(mockArg)
for (const [k, v] of Object.entries(hashes)) console.log(`sha256 ${v} ${k}`)

const viteMod = await import(pathToFileURL(join(pkgDir('vite'), 'dist/node/index.js')).href)
const vueMod = await import(pathToFileURL(require.resolve('@vitejs/plugin-vue')).href)
const vue = typeof vueMod.default === 'function' ? vueMod.default : vueMod.default.default
console.log(`vite ${viteMod.version}`)
mkdirSync(out, { recursive: true })
await viteMod.build({
  configFile: false,
  mode: 'production',
  root: out,
  base: './',
  logLevel: 'warn',
  cacheDir: join(out, '.vite-cache'),
  plugins: [vue()],
  define: {
    __FG_GUI_BUILD__: JSON.stringify('visual-harness'),
    'process.env.NODE_ENV': JSON.stringify('production'),
    __VUE_OPTIONS_API__: 'true',
    __VUE_PROD_DEVTOOLS__: 'false',
    __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: 'false',
  },
  resolve: {
    preserveSymlinks: true,
    alias: {
      '@shared': resolve(cwd, '../client/shared'),
      '@': resolve(cwd, 'src'),
      vue: pkgDir('vue'),
      'vue-i18n': pkgDir('vue-i18n'),
      axios: pkgDir('axios'),
      pinia: pkgDir('pinia'),
      'vue-router': pkgDir('vue-router'),
    },
  },
  build: {
    outDir: join(out, 'bundle'),
    emptyOutDir: true,
    minify: false,
    cssCodeSplit: false,
    lib: { entry: join(here, 'harness.ts'), name: 'FgGitSettingsHarness', formats: ['iife'], fileName: () => 'harness.js' },
  },
})
const bundle = join(out, 'bundle')
const cssFiles = readdirSync(bundle).filter(f => f.endsWith('.css'))
console.log(`built: ${readdirSync(bundle).join(', ')}`)
copyFileSync(mockArg, join(bundle, 'flowgate_git_settings_mockup.html'))
writeFileSync(join(bundle, 'compare.html'), `<!doctype html><html lang="ko"><head><meta charset="utf-8">
${cssFiles.map(f => `<link rel="stylesheet" href="${f}">`).join('\n')}
<style>body{margin:0;padding:16px 20px;background:#07111d}.cap{font:600 13px/1.5 'Malgun Gothic',sans-serif;color:#ffd65c;margin:10px 0 6px}
#mockframe{display:block;border:1px dashed #5a6b80;height:420px}.real-host{margin-bottom:14px;outline:1px dashed #5a6b80}#fg-measure{display:none}</style>
</head><body><div class="cap" id="mock-cap"></div><iframe id="mockframe" src="flowgate_git_settings_mockup.html"></iframe>
<div class="cap" id="real-cap"></div><div id="real"></div><pre id="fg-measure"></pre><script src="harness.js"></script></body></html>`)

const exe = [
  join(process.env['ProgramFiles(x86)'] ?? 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe'),
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].find(p => existsSync(p))
if (!exe) { console.log('NO_BROWSER'); process.exit(3) }
console.log(`browser ${exe}`)

// box = the Git box's outer width (the Inspector pane width - 2 x 14px .designer-inspector-body padding);
// mock = the mockup viewport whose panel has about the same outer width (stage padding 27px above 860px, 15px below).
const CASES = [
  { id: 'wide1026', box: 1026, mock: 1080, note: 'mockup reference width (panel at a 1080px viewport)' },
  { id: 'normal910', box: 910, mock: 964, note: 'Inspector 938px (1920px window, default docks 280/300, 7:3)' },
  { id: 'mid570', box: 570, mock: 600, note: 'Inspector 598px (about a 1440px window)' },
  { id: 'narrow392', box: 392, mock: 422, note: 'Inspector 420px' },
  { id: 'min236', box: 236, mock: 266, note: 'Inspector 264px (DESIGNER_BOTTOM_INSPECTOR_MIN_WIDTH)' },
]
const common = ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-extensions',
  `--user-data-dir=${join(out, '.browser-profile')}`, '--allow-file-access-from-files', '--hide-scrollbars',
  '--force-device-scale-factor=1', '--virtual-time-budget=6000']
const results = { hashes, browser: basename(exe), vite: viteMod.version, cases: {} }
let failed = false
for (const c of CASES) {
  const url = `${pathToFileURL(join(bundle, 'compare.html')).href}?box=${c.box}&mock=${c.mock}`
  const width = Math.max(c.box, c.mock) + 60
  const dump = spawnSync(exe, [...common, `--window-size=${width},1600`, '--dump-dom', url], { encoding: 'utf8', timeout: 120000, maxBuffer: 64 << 20 })
  const m = /FGMEASURE(.*?)FGMEASURE/s.exec(dump.stdout ?? '')
  if (!m) { failed = true; console.log(`${c.id}: NO_MEASURE status=${dump.status}`); continue }
  const data = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&'))
  const height = Math.min(4000, (data.pageHeight ?? 1200) + 24)
  const png = join(out, `capture_${c.id}.png`)
  spawnSync(exe, [...common, `--window-size=${width},${height}`, `--screenshot=${png}`, url], { encoding: 'utf8', timeout: 120000 })
  data.capture = existsSync(png) ? { file: basename(png), sha256: sha(png), width, height } : { error: 'no screenshot' }
  results.cases[c.id] = { ...c, ...data }
  console.log(`== ${c.id} (box ${c.box}, mockup viewport ${c.mock}) ${data.error ?? ''}`)
  for (const k of ['mock', 'real_timeout', 'real_ok', 'real_kept']) {
    const d = data[k]
    if (!d?.box) { failed = true; console.log(`${k}: ${JSON.stringify(d)}`); continue }
    if (k !== 'mock' && (d.overlaps.length || d.outsideContent.length)) failed = true
    console.log(`${k}: box ${d.box.w}x${d.box.h} content ${d.box.contentWidth} overlaps=${JSON.stringify(d.overlaps)} outside=${JSON.stringify(d.outsideContent)} layout=${JSON.stringify(d.layout)}`)
  }
}
writeFileSync(join(out, 'results.json'), JSON.stringify(results, null, 1) + '\n')
console.log(`results ${join(out, 'results.json')}`)
// Exit 1 when a case could not be measured or the real box has an overlap / a control outside its content.
process.exit(failed ? 1 : 0)
