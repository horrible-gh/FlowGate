import { mkdir, readFile, readdir, rm } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run', 'tests/browser/GitStatusPanel.geometry.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const rawHtml = await readFile(resolve(scratch, 'git-status-v9.actual-component.html'), 'utf8')
const cssName = (await readdir(resolve('dist/assets'))).find((name) => name.startsWith('main-') && name.endsWith('.css'))
if (!cssName) throw new Error('production CSS bundle is missing')
const builtCss = await readFile(resolve('dist/assets', cssName), 'utf8')
const builtScope = builtCss.match(/git-v9-scroll--220\[data-v-([a-f0-9]+)\]/)?.[1]
const fixtureScope = rawHtml.match(/data-v-([a-f0-9]+)/)?.[1]
if (!builtScope || !fixtureScope) throw new Error('GitStatusPanel scoped CSS identity is missing')
const html = rawHtml.replaceAll('data-v-' + fixtureScope, 'data-v-' + builtScope)
const profile = resolve(scratch, 'chrome-v9-profile')
await rm(profile, { recursive: true, force: true }); await mkdir(profile, { recursive: true })
const port = 9700 + Math.floor(Math.random() * 200)
const proc = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-sandbox', '--remote-allow-origins=*', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' })
const delay = (ms) => new Promise((done) => setTimeout(done, ms))
let page
try {
  for (let i = 0; i < 80; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })
      if (response.ok) { page = await response.json(); break }
    } catch {}
    await delay(100)
  }
  if (!page) throw new Error('Chrome connection timeout')
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((ok, bad) => { ws.addEventListener('open', ok, { once: true }); ws.addEventListener('error', bad, { once: true }) })
  let id = 0; const pending = new Map()
  ws.addEventListener('message', (event) => { const msg = JSON.parse(event.data); const done = pending.get(msg.id); if (done) { pending.delete(msg.id); done(msg) } })
  const call = (method, params = {}) => new Promise((done) => { const next = ++id; pending.set(next, done); ws.send(JSON.stringify({ id: next, method, params })) })
  await call('Runtime.enable')
  const payload = JSON.stringify({ html, css: builtCss })
  const expr = `(() => {
    const fixture = ${payload};
    document.head.innerHTML = '<style>' + fixture.css + '</style>';
    document.body.innerHTML = fixture.html;
    const selectors = {
      dirty: { list: '#git-base-dirty-files', outer: '.git-base-dirty-alert__body' },
      newFiles: { list: '.git-base-untracked__files', outer: '.git-base-untracked__body' },
      conflict: { list: '.git-v9-conflict-summary .git-v9-scroll--190', outer: '.git-v9-conflict-summary' },
    };
    const measurements = Object.fromEntries(Object.entries(selectors).map(([key, selector]) => {
      const element = document.querySelector(selector.list);
      const outer = document.querySelector(selector.outer);
      if (!element || !outer) return [key, null];
      return [key, {
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        overflowY: getComputedStyle(element).overflowY,
        scrollable: element.scrollHeight > element.clientHeight,
        outerClientHeight: outer.clientHeight,
        outerScrollHeight: outer.scrollHeight,
        outerIsList: outer === element,
        outerDoesNotGrowToContent: outer !== element && outer.clientHeight !== element.scrollHeight,
      }];
    }));
    const required = [
      ['.git-branch-badge', 1], ['.git-ab-meta', 1], ['.card-hd .btn-primary', 1], ['.git-refresh-btn', 1],
      ['.git-base-dirty-alert', 1], ['.git-v9-summary .git-v9-chip', 1], ['.git-v9-summary button', 2], ['.git-base-dirty-filerow', 7],
      ['.git-base-untracked__files', 1], ['.git-base-untracked-row', 20], ['.git-base-untracked__more', 1],
      ['.git-base-untracked .git-base-commit-row button', 2], ['.git-base-untracked-remove-btn', 1],
      ['.git-unpushed-row', 1], ['.git-status-row', 2], ['.git-status-commit', 1],
      ['.git-status-row-main select', 1], ['.git-status-row-main button', 4],
      ['.git-v9-conflict-summary', 1], ['.git-v9-file', 12],
      ['.git-status-slot-card', 4], ['.git-status-branch', 4], ['.git-status-slot-gid', 4], ['.git-status-slot-card .badge', 4],
      ['.git-trc-badge', 4], ['.git-trc-preview', 4], ['.git-trc-preview-row', 11], ['.git-trc-preview-more', 1],
      ['.git-trc-conflict', 1], ['.git-trc-conflict button', 2],
      ['.git-cleanup-never', 1], ['.git-cleanup-pending', 1], ['.git-ra-placeholder', 1],
    ];
    const missingMockupElements = required.reduce(
      (sum, [selector, count]) => sum + Math.max(0, count - document.querySelectorAll(selector).length), 0,
    );
    const allowedControlSelectors = [
      '.card-hd .btn-primary', '.git-refresh-btn',
      '.git-v9-summary button', '.git-base-dirty-filerow button', '#git-base-dirty-files .git-base-commit-row input', '#git-base-dirty-files .git-base-commit-row button',
      '.git-base-untracked-row input', '.git-base-untracked .git-base-commit-row input', '.git-base-untracked .git-base-commit-row button',
      '.git-unpushed-row button', '.git-status-row-main select', '.git-status-row-main button',
      '.git-status-commit input', '.git-status-commit a', '.git-v9-conflict-summary button',
      '.git-trc-badge', '.git-trc-preview-more', '.git-trc-conflict button', '.git-trc-list button',
      '.git-cleanup-pending button',
    ];
    const allowedControls = new Set(allowedControlSelectors.flatMap((selector) => [...document.querySelectorAll(selector)]));
    const renderedControls = [...document.querySelectorAll('button, input, select, a')];
    const extraneousControls = renderedControls.filter((element) => !allowedControls.has(element)).length;
    return { measurements, requiredCounts: Object.fromEntries(required.map(([s]) => [s, document.querySelectorAll(s).length])), renderedControls: renderedControls.length, allowedControls: allowedControls.size, missingMockupElements, extraneousControls };
  })()`
  const out = await call('Runtime.evaluate', { expression: expr, returnByValue: true })
  if (out.result.exceptionDetails) throw new Error(JSON.stringify(out.result.exceptionDetails))
  const measured = out.result.result.value
  // 0482 R0001 rev2 — 기대치는 시안(deck 4543n0ab v9)의 css/main.css 가 실제로 만드는
  // 안쪽 높이다. box-sizing: border-box 아래에서
  //   .detail-disclosure  max-height 220 + border-top 1  -> clientHeight 219
  //   .untracked-raw-list max-height 220                 -> clientHeight 220
  //   .conflict-raw-list  max-height 190 + border-top 1  -> clientHeight 189
  // 예전 [220,220,190]은 시안 자신도 통과하지 못하는 값이었다(파선 구분선을 뺀 수치).
  for (const [key, want] of [['dirty', 219], ['newFiles', 220], ['conflict', 189]]) {
    const value = measured.measurements[key]
    if (!value || value.clientHeight !== want || !value.scrollable || value.overflowY !== 'auto' || !value.outerDoesNotGrowToContent) {
      throw new Error(`${key} geometry failed: ${JSON.stringify(value)}`)
    }
  }
  if (measured.missingMockupElements || measured.extraneousControls) throw new Error(`DOM parity failed: ${JSON.stringify(measured)}`)
  console.log(JSON.stringify({ chrome, cssBundle: cssName, componentFixture: 'GitStatusPanel.vue', ...measured }, null, 2))
  ws.close()
} finally {
  if (proc.exitCode === null) proc.kill()
  await delay(300)
  await rm(profile, { recursive: true, force: true })
}
