/**
 * flowgate.default.0560 T0018 §2.3-7 / §4-10 — measure what the two non-Teleport overlays
 * actually covered before the migration and what they cover after it.
 *
 *   node tests/browser/dialog-local-overlay-geometry.0560.mjs
 *
 * Needs `npm run build` (for the production stylesheets) and FLOWGATE_SCRATCH. The AFTER DOM
 * comes from `DialogLocalOverlayGeometry.0560.fixture.spec.ts`, which this script runs first.
 *
 * The question §2.3-7 asks is a coordinate question, and jsdom cannot answer it: both states
 * are `inset: 0` and differ only in which box `inset` is resolved against — `position:
 * absolute` inside `.wp-editor` (a `position: relative` card) versus `position: fixed` against
 * the viewport. So each case is laid out twice in a real browser, inside the same simulated
 * editor panel, and the overlay's rect is compared with the panel's and with the viewport's.
 *
 * BEFORE is reconstructed from the pre-migration source rather than mounted: the markup being
 * measured is a static frame (an overlay div wrapping a card div), and the geometry comes
 * entirely from the two rules quoted below. Both are copied verbatim from the files this T
 * edited, at their pre-T0018 state:
 *   `client/src/main/components/WorkPlanAiScopeDialog.vue` <style scoped> .wp-ai-scope /
 *   .wp-ai-scope-card   (the T0018 §2.1 table's "current overlay" column, NR0011 원장 ID 32)
 *   `client/src/main/components/WorkPlanEditor.vue` <style scoped> .wp-raw-overlay /
 *   .wp-raw-box         (NR0011 원장 ID 45)
 * AFTER is the real mounted component's DOM under the real built stylesheet.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run',
  'tests/browser/DialogLocalOverlayGeometry.0560.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const after = JSON.parse(
  await readFile(resolve(scratch, 'dialog-local-overlay-geometry.0560.json'), 'utf8'),
)

const assetNames = await readdir(resolve('dist/assets'))
async function bundle(prefix) {
  const name = assetNames.find((n) => n.startsWith(prefix) && n.endsWith('.css'))
  if (!name) throw new Error(`production CSS bundle ${prefix}*.css is missing`)
  return readFile(resolve('dist/assets', name), 'utf8')
}
/*
 * AppIcon-*    — where `shared/app.css` and every design token ended up. Without it the
 *                `var(--border-d)` / `var(--surface)` in the BEFORE rules are invalid and the
 *                boxes paint as nothing, which would make the comparison vacuous.
 * ConfirmDialog-* — the common dialog layer (`dialog.css`, loaded unscoped by DialogShell).
 * main-*       — the scoped per-component styles.
 */
const builtCss = [
  await bundle('AppIcon-'),
  await bundle('ConfirmDialog-'),
  await bundle('main-'),
].join('\n')

/** The pre-migration rules, verbatim. See the header note for their provenance. */
const BEFORE_CSS = `
.wp-editor-panel { position: relative; }
.wp-ai-scope { position:absolute; inset:0; z-index:30; display:flex; align-items:center; justify-content:center; padding:18px; background:rgba(15,23,42,.38); }
.wp-ai-scope-card { width:min(760px,100%); max-height:calc(100% - 24px); overflow:auto; padding:18px; border:1px solid var(--border-d); border-radius:var(--r); background:var(--surface); box-shadow:0 16px 40px rgba(15,23,42,.22); }
.wp-raw-overlay { position: absolute; inset: 0; background: rgba(15,23,42,.45); display: flex; align-items: center; justify-content: center; z-index: 50; padding: 24px; }
.wp-raw-box { background: #fff; border-radius: var(--r, 8px); width: 100%; max-width: 720px; max-height: 100%; display: flex; flex-direction: column; overflow: hidden; }
.wp-raw-hd { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--border, #e2e8f0); font-weight: 700; font-size: .84rem; }
.wp-raw-content { margin: 0; padding: 14px; overflow: auto; font-size: .74rem; background: #0f172a; color: #e2e8f0; flex: 1; }
`

const BEFORE_HTML = {
  'ai-scope': `
    <div class="wp-ai-scope" role="dialog" aria-modal="true">
      <div class="wp-ai-scope-card">
        <header><h3>AI에게 맡길 범위</h3><p>체크한 것 안에서만 AI가 정합니다.</p></header>
        <section><div class="scope-heading"><strong>1. 수량을 AI가 정할 타입</strong></div></section>
        <footer><button type="button">취소</button><button type="button">프로젝트 배정표로 채우기</button><button type="button">AI에게 맡기기</button></footer>
      </div>
    </div>`,
  'raw-view': `
    <div class="wp-raw-overlay">
      <div class="wp-raw-box">
        <div class="wp-raw-hd"><span>작업계획 원문 (읽기 전용)</span><div><button type="button">복사</button><button type="button">닫기</button></div></div>
        <pre class="wp-raw-content">{ "wp_version": 1 }</pre>
      </div>
    </div>`,
}

/**
 * Scoped-CSS hashes differ between the vitest transform and the production build, so the
 * fixture's `data-v-*` has to be rewritten to whatever the bundle used — without this the two
 * components' own body rules silently do not apply and every "the body still looks like this"
 * check passes for the worst possible reason. Each case names a class only its own component
 * defines; that class's `[data-v-…]` in the bundle is the answer.
 */
const SIGNATURE = { 'ai-scope': 'scope-grid', 'raw-view': 'wp-raw-content' }

function remap(name, html) {
  const signature = SIGNATURE[name]
  const built = builtCss.match(new RegExp(`\\.${signature}\\[data-v-([a-f0-9]+)\\]`))?.[1]
  const fixtureScope = html.match(new RegExp(`class="[^"]*${signature}[^"]*"[^>]*data-v-([a-f0-9]+)`))?.[1]
    ?? html.match(/data-v-([a-f0-9]+)/)?.[1]
  if (!built || !fixtureScope) throw new Error(`${name}: cannot pair scoped hash for .${signature}`)
  return html.replaceAll('data-v-' + fixtureScope, 'data-v-' + built)
}

/*
 * The editor panel's track. `.wp-editor` is a card inside MainPanel's document column, so it
 * never fills the window: the app header is above it and the explorer sidebar is to its left.
 * These numbers only have to be a proper sub-rect of the viewport — that is the whole property
 * under test — and they are chosen to look like the real layout at 1440x900.
 */
const PANEL = { left: 320, top: 56, width: 1000, height: 760 }

const profile = resolve(scratch, 'chrome-local-overlay-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9600 + Math.floor(Math.random() * 200)
const proc = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--remote-allow-origins=*',
  '--window-size=1440,900', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })

const delay = (ms) => new Promise((done) => setTimeout(done, ms))
const failures = []
const report = {}

try {
  let page
  for (let i = 0; i < 80; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })
      if (response.ok) { page = await response.json(); break }
    } catch { /* chrome not up yet */ }
    await delay(100)
  }
  if (!page) throw new Error('Chrome connection timeout')

  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((ok, bad) => {
    ws.addEventListener('open', ok, { once: true })
    ws.addEventListener('error', bad, { once: true })
  })
  let id = 0
  const pending = new Map()
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data)
    const done = pending.get(msg.id)
    if (done) { pending.delete(msg.id); done(msg) }
  })
  const call = (method, params = {}) => new Promise((done) => {
    const next = ++id
    pending.set(next, done)
    ws.send(JSON.stringify({ id: next, method, params }))
  })
  await call('Runtime.enable')

  async function measure(stage, name, html, extraCss, overlaySelector, inPanel) {
    const payload = JSON.stringify({
      html, css: builtCss + extraCss, panel: PANEL, overlaySelector, inPanel,
    })
    // `.fg-dialog-surface` opens with `animation: mIn .15s` whose first keyframe is
    // `scale(.97)`. Measuring synchronously after the innerHTML write catches that first frame
    // and reports every migrated surface 3% narrow (720px reads as 698px). Wait for the
    // element's own animations to finish before taking any coordinate.
    const expr = `(async () => {
      const f = ${payload};
      document.head.innerHTML = '<style>html,body{margin:0;padding:0;}'
        + '.wp-editor-panel{position:absolute;left:' + f.panel.left + 'px;top:' + f.panel.top + 'px;'
        + 'width:' + f.panel.width + 'px;height:' + f.panel.height + 'px;background:#fff;}</style>'
        + '<style>' + f.css + '</style>';
      // The panel is always present; only the overlay's placement differs between stages.
      document.body.innerHTML = '<div class="wp-editor-panel" id="panel">'
        + (f.inPanel ? f.html : '') + '</div>' + (f.inPanel ? '' : f.html);
      const rect = (el) => { const r = el.getBoundingClientRect(); return {
        left: Math.round(r.left), top: Math.round(r.top),
        right: Math.round(r.right), bottom: Math.round(r.bottom),
        width: Math.round(r.width), height: Math.round(r.height) }; };
      const panel = document.getElementById('panel');
      const overlay = document.querySelector(f.overlaySelector);
      if (!overlay) return JSON.stringify({ error: 'overlay ' + f.overlaySelector + ' not found' });
      await Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {})));
      await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
      const surface = overlay.querySelector('.fg-dialog-surface, .wp-ai-scope-card, .wp-raw-box');
      // The raw view's dark JSON block: it was full-bleed inside .wp-raw-box and has to stay
      // full-bleed inside the migrated surface. No backticks in here: this whole body is a
      // template literal, and one would end it early.
      const raw = overlay.querySelector('.wp-raw-content');
      const cs = getComputedStyle(overlay);
      return JSON.stringify({
        raw: raw ? { ...rect(raw), background: getComputedStyle(raw).backgroundColor } : null,
        // clientWidth/clientHeight, not innerWidth/innerHeight: a fixed layer is laid out
        // against the LAYOUT viewport, which excludes the scrollbar. innerWidth includes it,
        // and the panel below is tall enough to produce one.
        viewport: {
          width: document.documentElement.clientWidth,
          height: document.documentElement.clientHeight,
        },
        panel: rect(panel),
        overlay: rect(overlay),
        surface: surface ? rect(surface) : null,
        position: cs.position,
        zIndex: cs.zIndex,
        background: cs.backgroundColor,
      });
    })()`
    const result = await call('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
    if (result.result?.exceptionDetails) {
      throw new Error(`${stage}/${name}: ${JSON.stringify(result.result.exceptionDetails)}`)
    }
    const measured = JSON.parse(result.result.result.value)
    if (measured.error) throw new Error(`${stage}/${name}: ${measured.error}`)
    report[name] = { ...(report[name] ?? {}), [stage]: measured }
    return measured
  }

  const covers = (a, b) => a.left === b.left && a.top === b.top && a.right === b.right && a.bottom === b.bottom

  for (const name of ['ai-scope', 'raw-view']) {
    const before = await measure('before', name, BEFORE_HTML[name], BEFORE_CSS, name === 'ai-scope' ? '.wp-ai-scope' : '.wp-raw-overlay', true)
    const afterMeasured = await measure('after', name, remap(name, after[name]), '', '.fg-dialog-overlay', false)

    // 1. BEFORE really was local: the overlay's box is the editor panel's box, nothing more.
    if (!covers(before.overlay, before.panel)) {
      failures.push(`${name}: the pre-migration overlay did not match the panel — ${JSON.stringify(before.overlay)} vs ${JSON.stringify(before.panel)}`)
    }
    if (before.position !== 'absolute') {
      failures.push(`${name}: the pre-migration overlay is ${before.position}, expected absolute`)
    }

    // 2. AFTER is the viewport: this is the change §2.3-7 asked to be measured and judged.
    const viewport = { left: 0, top: 0, right: afterMeasured.viewport.width, bottom: afterMeasured.viewport.height }
    if (!covers(afterMeasured.overlay, viewport)) {
      failures.push(`${name}: the migrated overlay is not the full viewport — ${JSON.stringify(afterMeasured.overlay)} vs ${JSON.stringify(viewport)}`)
    }
    if (afterMeasured.position !== 'fixed') {
      failures.push(`${name}: the migrated overlay is ${afterMeasured.position}, expected fixed`)
    }

    // 3. Both states actually painted a surface — otherwise everything above is vacuous.
    for (const [stage, data] of [['before', before], ['after', afterMeasured]]) {
      if (!data.surface || data.surface.width <= 0 || data.surface.height <= 0) {
        failures.push(`${name}/${stage}: the dialog card has no box`)
      }
    }
    // 3b. the design tokens really loaded (a transparent-vs-transparent comparison is no test).
    if (afterMeasured.background === 'rgba(0, 0, 0, 0)') {
      failures.push(`${name}: the migrated overlay has no dim; the stylesheet did not load`)
    }

    // 4. The raw view's dark JSON block was full-bleed inside `.wp-raw-box`, and the migrated
    //    surface has to keep it that way — a `panel` body's 18px/20px padding would have inset
    //    it and taken the scrolling away from it. `sheet` is what preserves it, and this is the
    //    measurement that says so rather than the comment that claims it.
    if (name === 'raw-view') {
      for (const [stage, data] of [['before', before], ['after', afterMeasured]]) {
        if (!data.raw) { failures.push(`${name}/${stage}: the raw JSON block is missing`); continue }
        if (data.raw.background !== 'rgb(15, 23, 42)') {
          failures.push(`${name}/${stage}: the raw block is ${data.raw.background}; the scoped rule did not apply`)
        }
        const inset = data.raw.left - data.surface.left
        if (inset !== 0) {
          failures.push(`${name}/${stage}: the raw block is inset ${inset}px from the surface edge`)
        }
      }
    }
  }

  await writeFile(
    resolve(scratch, 'dialog-local-overlay-geometry.0560.measured.json'),
    JSON.stringify(report, null, 2), 'utf8',
  )
  console.log(JSON.stringify(
    Object.fromEntries(Object.entries(report).map(([name, data]) => [name, {
      panel: data.before.panel,
      beforeOverlay: data.before.overlay,
      beforeSurface: data.before.surface,
      afterOverlay: data.after.overlay,
      afterSurface: data.after.surface,
      viewport: data.after.viewport,
      position: { before: data.before.position, after: data.after.position },
      zIndex: { before: data.before.zIndex, after: data.after.zIndex },
    }])),
    null, 2,
  ))
} finally {
  proc.kill()
}

if (failures.length > 0) {
  console.error('FAILURES:\n' + failures.map((f) => ' - ' + f).join('\n'))
  process.exit(1)
}
console.log(
  'OK — both overlays measured. Before: confined to the editor panel (position: absolute). '
  + 'After: the full viewport (position: fixed), which is what L0009 §2 "Teleport" decides and '
  + 'what T0018 §2.3-7 accepts.',
)
