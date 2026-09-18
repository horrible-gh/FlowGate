/**
 * flowgate.default.0560 T0016 §4-3 — measure the migrated footers in a real browser.
 *
 *   node tests/browser/dialog-footer-geometry.0560.mjs
 *
 * Needs `npm run build` (for the production stylesheets) and FLOWGATE_SCRATCH. The DOM
 * comes from `DialogFooterGeometry.0560.fixture.spec.ts`, which this script runs first.
 *
 * What it proves that jsdom cannot: the buttons' painted left-to-right order, that 취소
 * really is adjacent to the primary button, and that the cancel button resolves to ONE
 * computed appearance in all five dialogs — R0001's actual complaint.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run', 'tests/browser/DialogFooterGeometry.0560.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const cases = JSON.parse(await readFile(resolve(scratch, 'dialog-footer-geometry.0560.json'), 'utf8'))

const assetNames = await readdir(resolve('dist/assets'))
async function bundle(prefix) {
  const name = assetNames.find((n) => n.startsWith(prefix) && n.endsWith('.css'))
  if (!name) throw new Error(`production CSS bundle ${prefix}*.css is missing`)
  return readFile(resolve('dist/assets', name), 'utf8')
}
/*
 * Three chunks, and all three are load-bearing:
 *   AppIcon-*    — where `shared/app.css` and therefore every design token (`--danger`,
 *                  `--surface`, `--border`) ended up. Without it `color-mix(in srgb,
 *                  var(--danger) 7%, var(--surface))` is invalid, every dialog button
 *                  paints transparent, and the "one cancel appearance" check below passes
 *                  for the worst possible reason.
 *   ConfirmDialog-* — the common dialog layer (`dialog.css`, loaded unscoped by DialogShell).
 *   main-*       — the scoped per-component styles.
 */
const builtCss = [
  await bundle('AppIcon-'),
  await bundle('ConfirmDialog-'),
  await bundle('main-'),
].join('\n')

/**
 * Scoped-CSS hashes differ between the vitest transform and the production build, so a
 * fixture's `data-v-*` has to be rewritten to whatever the bundle used. Each case names a
 * class that only its own component defines, and that class's `[data-v-…]` in the bundle
 * is the answer.
 */
const SIGNATURE = {
  'confirm-modal': 'confirm-msg',
  'group-discard-modal': 'gd-textarea',
  'git-base-dirty-dialog': 'gbd-commit-input',
  'workflow-decision-modal': 'wdm-ft-row',
  'workflow-edit-modal': 'wdm-ft-row',
  'review-reject-dialog': 'rrd-textarea',
}

const EXPECTED_ROLES = {
  'confirm-modal': ['cancel', 'primary'],
  'group-discard-modal': ['cancel', 'primary'],
  'git-base-dirty-dialog': ['danger', 'cancel', 'primary'],
  'workflow-decision-modal': ['cancel', 'primary'],
  'workflow-edit-modal': ['aux', 'aux', 'cancel', 'primary'],
  'review-reject-dialog': ['aux', 'cancel', 'primary'],
}

function remap(name, html) {
  const signature = SIGNATURE[name]
  const built = builtCss.match(new RegExp(`\\.${signature}\\[data-v-([a-f0-9]+)\\]`))?.[1]
  const fixtureScope = html.match(new RegExp(`class="[^"]*${signature}[^"]*"[^>]*data-v-([a-f0-9]+)`))?.[1]
    ?? html.match(/data-v-([a-f0-9]+)/)?.[1]
  if (!built || !fixtureScope) throw new Error(`${name}: cannot pair scoped hash for .${signature}`)
  return html.replaceAll('data-v-' + fixtureScope, 'data-v-' + built)
}

const profile = resolve(scratch, 'chrome-dialog-footer-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9400 + Math.floor(Math.random() * 200)
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

  for (const [name, rawHtml] of Object.entries(cases)) {
    const payload = JSON.stringify({ html: remap(name, rawHtml), css: builtCss })
    const expr = `(() => {
      const fixture = ${payload};
      document.head.innerHTML = '<style>' + fixture.css + '</style>';
      document.body.innerHTML = fixture.html;
      const measure = (el) => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return {
          role: el.getAttribute('data-dialog-action-role'),
          id: el.getAttribute('data-dialog-action-id'),
          label: (el.textContent || '').trim(),
          left: Math.round(r.left), right: Math.round(r.right),
          width: Math.round(r.width), height: Math.round(r.height),
          color: s.color, background: s.backgroundColor, border: s.borderColor,
          fontSize: s.fontSize, padding: s.padding,
        };
      };
      const probe = document.createElement('div');
      probe.style.color = 'var(--danger)';
      document.body.appendChild(probe);
      const dangerToken = getComputedStyle(probe).color;
      probe.remove();
      const buttons = Array.from(document.querySelectorAll('[data-dialog-action-role]')).map(measure);
      const surface = document.querySelector('.fg-dialog-surface');
      const surfaceRect = surface ? surface.getBoundingClientRect() : null;
      // The sheet variant hands its body's padding to the feature (dialog.css, T0016
      // §2.3). A full-bleed strip therefore has to start at the surface's own edge.
      const bleed = document.querySelector('.wdm-preset-section');
      const bleedRect = bleed ? bleed.getBoundingClientRect() : null;
      return JSON.stringify({
        buttons,
        dangerToken,
        surface: surfaceRect ? { width: Math.round(surfaceRect.width), height: Math.round(surfaceRect.height) } : null,
        bleedInset: (bleedRect && surfaceRect) ? Math.round(bleedRect.left - surfaceRect.left) : null,
      });
    })()`
    const result = await call('Runtime.evaluate', { expression: expr, returnByValue: true })
    if (result.result?.exceptionDetails) throw new Error(`${name}: ${JSON.stringify(result.result.exceptionDetails)}`)
    const measured = JSON.parse(result.result.result.value)
    report[name] = measured

    // 1. painted order, left to right — not DOM order.
    const painted = [...measured.buttons].sort((a, b) => a.left - b.left)
    const paintedRoles = painted.map((b) => b.role)
    const expected = EXPECTED_ROLES[name]
    if (JSON.stringify(paintedRoles) !== JSON.stringify(expected)) {
      failures.push(`${name}: painted order ${JSON.stringify(paintedRoles)} != ${JSON.stringify(expected)}`)
    }
    // 2. 취소 is immediately left of the primary button, with nothing between them.
    const cancelIndex = paintedRoles.indexOf('cancel')
    const primaryIndex = paintedRoles.indexOf('primary')
    if (cancelIndex === -1 || primaryIndex !== cancelIndex + 1) {
      failures.push(`${name}: cancel is not immediately left of primary (${JSON.stringify(paintedRoles)})`)
    }
    // 3. every button actually got painted.
    for (const button of measured.buttons) {
      if (button.width <= 0 || button.height <= 0) failures.push(`${name}: ${button.id} has no box`)
    }
    // 3b. the design tokens really loaded — otherwise every colour assertion is vacuous.
    if (measured.dangerToken !== 'rgb(220, 38, 38)') {
      failures.push(`${name}: --danger resolved to ${measured.dangerToken}; the token stylesheet did not load`)
    }
    // 3c. sheet bodies are full-bleed: the notice/preset strip touches the surface edge.
    if (name.startsWith('workflow-') && measured.bleedInset !== 0) {
      failures.push(`${name}: the sheet body insets its full-bleed strip by ${measured.bleedInset}px`)
    }
  }

  // 4. the R0001 assertion: ONE cancel appearance across all five dialogs.
  const cancels = Object.entries(report).map(([name, data]) => {
    const cancel = data.buttons.find((b) => b.role === 'cancel')
    return [name, cancel]
  })
  const signatures = new Set(cancels.map(([, c]) => `${c.color}|${c.background}|${c.border}|${c.fontSize}|${c.padding}|${c.height}`))
  if (signatures.size !== 1) {
    failures.push(`cancel button has ${signatures.size} different appearances: ${JSON.stringify([...signatures], null, 2)}`)
  }

  await writeFile(resolve(scratch, 'dialog-footer-geometry.0560.measured.json'), JSON.stringify(report, null, 2), 'utf8')
  console.log(JSON.stringify(
    Object.fromEntries(Object.entries(report).map(([name, data]) => [
      name,
      {
        surface: data.surface,
        painted: [...data.buttons].sort((a, b) => a.left - b.left).map((b) => `${b.role}:${b.label}@${b.left}`),
      },
    ])),
    null, 2,
  ))
  console.log('CANCEL_APPEARANCE=' + JSON.stringify(cancels[0][1] && {
    color: cancels[0][1].color, background: cancels[0][1].background, border: cancels[0][1].border,
    fontSize: cancels[0][1].fontSize, padding: cancels[0][1].padding, height: cancels[0][1].height,
  }))
} finally {
  proc.kill()
}

if (failures.length > 0) {
  console.error('FAILURES:\n' + failures.map((f) => ' - ' + f).join('\n'))
  process.exit(1)
}
console.log('OK — six footers measured under the production stylesheet, one cancel appearance.')
