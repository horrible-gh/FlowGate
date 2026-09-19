/**
 * flowgate.default.0560 T0039 §8 — measure the six NR0029 §4.3 footers in a real browser.
 *
 *   node tests/browser/dialog-remaining-footer-geometry.0560.mjs
 *
 * Needs `npm run build` (for the production stylesheets) and FLOWGATE_SCRATCH. The DOM comes
 * from `DialogRemainingFooterGeometry.0560.fixture.spec.ts`, which this script runs first.
 *
 * What it proves that jsdom cannot: the buttons' PAINTED left-to-right order, that 취소/닫기
 * really is adjacent to (or, where a footer has no primary, last before) the main action, and
 * that the cancel/close button resolves to ONE computed appearance across all six — which is
 * R0001's actual complaint and the reason NR0029 §4.3 listed these six by name.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run', 'tests/browser/DialogRemainingFooterGeometry.0560.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const cases = JSON.parse(await readFile(resolve(scratch, 'dialog-remaining-footer-geometry.0560.json'), 'utf8'))

const assetNames = await readdir(resolve('dist/assets'))
async function bundle(prefix) {
  const name = assetNames.find((n) => n.startsWith(prefix) && n.endsWith('.css'))
  if (!name) throw new Error(`production CSS bundle ${prefix}*.css is missing`)
  return readFile(resolve('dist/assets', name), 'utf8')
}
/*
 * Three chunks, and all three are load-bearing:
 *   AppIcon-*      — where `shared/app.css` and therefore every design token (`--danger`,
 *                    `--surface`, `--border`) ended up. Without it the buttons paint
 *                    transparent and the "one cancel appearance" check passes vacuously.
 *   ConfirmDialog-* — the common dialog layer (`dialog.css`, loaded unscoped by DialogShell).
 *   main-*         — the scoped per-component styles.
 */
const builtCss = [
  await bundle('AppIcon-'),
  await bundle('ConfirmDialog-'),
  await bundle('main-'),
].join('\n')

/**
 * Scoped-CSS hashes differ between the vitest transform and the production build, so every
 * `data-v-*` in a fixture has to be rewritten to whatever the bundle used. Each case carries
 * two of them (the feature's own and the common layer's), so the pairing is derived rather
 * than tabulated: for each hash, look at the classes the elements carrying it use and ask the
 * bundle which scope it attached to those classes. The winner is the most frequent answer.
 */
function remap(name, html) {
  const scopes = [...new Set([...html.matchAll(/data-v-([a-f0-9]+)/g)].map((m) => m[1]))]
  let out = html
  const pairs = []
  for (const scope of scopes) {
    const classes = new Set()
    for (const tag of html.match(/<[a-zA-Z][^>]*>/g) ?? []) {
      if (!tag.includes(`data-v-${scope}`)) continue
      for (const cls of (tag.match(/class="([^"]*)"/)?.[1] ?? '').split(/\s+/)) {
        if (cls) classes.add(cls)
      }
    }
    const votes = new Map()
    for (const cls of classes) {
      const escaped = cls.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      for (const hit of builtCss.matchAll(new RegExp(`\\.${escaped}\\[data-v-([a-f0-9]+)\\]`, 'g'))) {
        votes.set(hit[1], (votes.get(hit[1]) ?? 0) + 1)
      }
    }
    const best = [...votes.entries()].sort((a, b) => b[1] - a[1])[0]
    if (!best) throw new Error(`${name}: cannot pair scoped hash data-v-${scope}`)
    pairs.push(`${scope}->${best[0]}`)
    out = out.replaceAll('data-v-' + scope, 'data-v-' + best[0])
  }
  return { html: out, pairs }
}

/**
 * NR0029 §4.3's six rows, with the order DS0007 §3.1 / D0008 §6 require instead. Two of the
 * six have no primary action at all — `GroupInfoModal` is `readonly` (its 이름변경 is an aux
 * helper) and `CommandSelectorModal`'s result screen only dismisses — so for those the rule
 * reads "닫기 is last", not "취소 is immediately left of the primary".
 */
const EXPECTED_ROLES = {
  'continuous-warning-dialog': ['aux', 'aux', 'cancel', 'primary'],
  'design-handoff-dialog': ['aux', 'cancel', 'primary'],
  'mention-message-dialog': ['aux', 'cancel', 'primary'],
  'time-machine-dialog-result': ['cancel', 'primary'],
  'time-machine-dialog-picker-long': ['cancel', 'primary'],
  'group-info-modal': ['aux', 'cancel'],
  'command-selector-modal-result': ['cancel'],
}

const profile = resolve(scratch, 'chrome-dialog-remaining-footer-profile')
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

  for (const [name, rawHtml] of Object.entries(cases)) {
    const { html, pairs } = remap(name, rawHtml)
    const payload = JSON.stringify({ html, css: builtCss })
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
      const legacyShell = document.querySelectorAll('.modal-bg, .modal-box, .modal-ft').length;
      const measureBox = (el) => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return {
          top: Math.round(r.top), bottom: Math.round(r.bottom),
          width: Math.round(r.width), height: Math.round(r.height),
          clientWidth: el.clientWidth, clientHeight: el.clientHeight, scrollHeight: el.scrollHeight,
          overflowY: s.overflowY,
        };
      };
      return JSON.stringify({
        buttons,
        dangerToken,
        legacyShell,
        viewportHeight: window.innerHeight,
        surface: measureBox(document.querySelector('.fg-dialog-surface')),
        body: measureBox(document.querySelector('.fg-dialog-body')),
        featureBody: measureBox(document.querySelector('.tmd-body')),
        footer: measureBox(document.querySelector('.fg-dialog-footer')),
      });
    })()`
    const result = await call('Runtime.evaluate', { expression: expr, returnByValue: true })
    if (result.result?.exceptionDetails) throw new Error(`${name}: ${JSON.stringify(result.result.exceptionDetails)}`)
    const measured = JSON.parse(result.result.result.value)
    measured.scopePairs = pairs
    report[name] = measured

    // 1. painted order, left to right — not DOM order.
    const painted = [...measured.buttons].sort((a, b) => a.left - b.left)
    const paintedRoles = painted.map((b) => b.role)
    const expected = EXPECTED_ROLES[name]
    if (!expected) failures.push(`${name}: no expected order declared`)
    else if (JSON.stringify(paintedRoles) !== JSON.stringify(expected)) {
      failures.push(`${name}: painted order ${JSON.stringify(paintedRoles)} != ${JSON.stringify(expected)}`)
    }
    // 2. DS0007 §3.1 — 취소/닫기 immediately left of the primary; last when there is none.
    const cancelIndex = paintedRoles.indexOf('cancel')
    const primaryIndex = paintedRoles.indexOf('primary')
    if (cancelIndex === -1) {
      failures.push(`${name}: no cancel/close button in the footer`)
    } else if (primaryIndex === -1) {
      if (cancelIndex !== paintedRoles.length - 1) {
        failures.push(`${name}: without a primary, 닫기 must be last (${JSON.stringify(paintedRoles)})`)
      }
    } else if (primaryIndex !== cancelIndex + 1) {
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
    // 3c. no hand-made shell survived the migration in these six.
    if (measured.legacyShell !== 0) {
      failures.push(`${name}: ${measured.legacyShell} legacy .modal-* shell nodes still render`)
    }
  }

  // 4. TimeMachine's panel contract: natural short height, capped long height, one scroll owner.
  const shortTimeMachine = report['time-machine-dialog-result']
  if (!shortTimeMachine?.surface) {
    failures.push('time-machine-dialog-result: surface was not measured')
  } else {
    if (shortTimeMachine.surface.clientHeight >= 600) {
      failures.push(`time-machine-dialog-result: short content is ${shortTimeMachine.surface.clientHeight}px tall; expected natural height below 600px`)
    }
    if (shortTimeMachine.surface.clientWidth < 470 || shortTimeMachine.surface.clientWidth > 490) {
      failures.push(`time-machine-dialog-result: layout width ${shortTimeMachine.surface.clientWidth}px no longer preserves the 480px dialog`)
    }
  }

  const longTimeMachine = report['time-machine-dialog-picker-long']
  if (!longTimeMachine?.surface || !longTimeMachine.body || !longTimeMachine.featureBody || !longTimeMachine.footer) {
    failures.push('time-machine-dialog-picker-long: surface/body/feature/footer geometry is incomplete')
  } else {
    const maxHeight = Math.ceil(longTimeMachine.viewportHeight * 0.88) + 1
    if (longTimeMachine.surface.clientHeight > maxHeight || longTimeMachine.surface.bottom > longTimeMachine.viewportHeight + 1) {
      failures.push(`time-machine-dialog-picker-long: surface ${longTimeMachine.surface.clientHeight}px exceeds the ${maxHeight}px viewport cap`)
    }
    if (longTimeMachine.body.scrollHeight <= longTimeMachine.body.clientHeight) {
      failures.push(`time-machine-dialog-picker-long: common body does not scroll (${longTimeMachine.body.scrollHeight}/${longTimeMachine.body.clientHeight})`)
    }
    if (!['auto', 'scroll'].includes(longTimeMachine.body.overflowY)) {
      failures.push(`time-machine-dialog-picker-long: common body overflow-y is ${longTimeMachine.body.overflowY}`)
    }
    if (longTimeMachine.featureBody.overflowY !== 'visible' ||
        longTimeMachine.featureBody.scrollHeight > longTimeMachine.featureBody.clientHeight + 1) {
      failures.push(`time-machine-dialog-picker-long: feature body became a second scroll owner (${longTimeMachine.featureBody.overflowY}, ${longTimeMachine.featureBody.scrollHeight}/${longTimeMachine.featureBody.clientHeight})`)
    }
    if (longTimeMachine.footer.height <= 0 ||
        longTimeMachine.footer.bottom > longTimeMachine.surface.bottom + 1 ||
        longTimeMachine.footer.top < longTimeMachine.body.bottom - 1) {
      failures.push('time-machine-dialog-picker-long: footer is clipped or overlaps the scrolling body')
    }
  }

  // 5. the R0001 assertion: ONE cancel/close appearance across all measured dialogs.
  const cancels = Object.entries(report).map(([name, data]) => [name, data.buttons.find((b) => b.role === 'cancel')])
  const signatures = new Set(cancels
    .filter(([, c]) => c)
    .map(([, c]) => `${c.color}|${c.background}|${c.border}|${c.fontSize}|${c.padding}|${c.height}`))
  if (signatures.size !== 1) {
    failures.push(`cancel button has ${signatures.size} different appearances: ${JSON.stringify([...signatures], null, 2)}`)
  }

  await writeFile(
    resolve(scratch, 'dialog-remaining-footer-geometry.0560.measured.json'),
    JSON.stringify(report, null, 2),
    'utf8',
  )
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
console.log('OK — footer geometry and TimeMachine natural/capped height verified under the production stylesheet.')
