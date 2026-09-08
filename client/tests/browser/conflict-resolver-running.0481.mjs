/**
 * flowgate.default.0481 T0010 rev5 (반려 #2) — 충돌 해결 다이얼로그가 "실행 중"이 되었을 때
 * 액션바가 어떻게 서는지를 실제 크롬에서 실측한다. 반려 사진의 두 문장이 같은 줄에서 폭을
 * 다투다가 가드 문장("README.md: 16, 28행")이 글자마다 줄바꿈해 세로로 섰던 사고를,
 * 설명이 아니라 픽셀로 확인한다. 대조군으로 rev4 의 배치를 되살려 그 사고가 실제로
 * 재현되는 것까지 같은 실행에서 확인한다.
 *
 *   cd client && npm run build && node tests/browser/conflict-resolver-running.0481.mjs
 *
 * 종료코드 0 = 통과. FLOWGATE_SCRATCH 아래에 실측 JSON 을 남긴다.
 * CHROME_PATH 로 크롬 경로를 바꿀 수 있다.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const delay = (ms) => new Promise((done) => setTimeout(done, ms))

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run', 'tests/browser/GitConflictResolver.runningLayout.0481.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const cssName = (await readdir(resolve('dist/assets'))).find((n) => n.startsWith('main-') && n.endsWith('.css'))
if (!cssName) throw new Error('production CSS bundle is missing — run `npm run build` first')
const builtCss = await readFile(resolve('dist/assets', cssName), 'utf8')
const builtScope = builtCss.match(/git-conflict-guard\[data-v-([a-f0-9]+)\]/)?.[1]
if (!builtScope) throw new Error('GitConflictResolverDialog scoped CSS identity is missing from the bundle')

const STATES = ['running', 'starting', 'noprovider', 'providerloading', 'providererror', 'idle']
const states = {}
for (const name of STATES) {
  const raw = await readFile(resolve(scratch, `conflict-resolver-running.${name}.html`), 'utf8')
  const fixtureScope = raw.match(/data-v-([a-f0-9]+)/)?.[1]
  if (!fixtureScope) throw new Error(`fixture ${name} carries no scoped attribute`)
  states[name] = raw.replaceAll('data-v-' + fixtureScope, 'data-v-' + builtScope)
}

const PROBE = String.raw`(() => {
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; };
  const dialog = document.querySelector('.git-conflict-dialog');
  if (!dialog) return { error: 'no dialog' };
  const ft = dialog.querySelector('.git-conflict-dialog-ft');
  const guard = dialog.querySelector('.git-conflict-guard');
  const guardText = guard && guard.querySelector('span');
  const strip = dialog.querySelector('.git-conflict-ai-strip');
  const context = ft && ft.querySelector(':scope > .git-conflict-footer-context');
  // line-height 가 'normal' 이면 parseFloat 은 NaN 이다 — 그때는 폰트 크기로 한 줄 높이를 잡는다.
  const guardStyle = guardText ? getComputedStyle(guardText) : null;
  const lineHeight = guardStyle ? (parseFloat(guardStyle.lineHeight) || parseFloat(guardStyle.fontSize) * 1.5) : 0;
  return {
    footer: box(ft),
    guard: box(guard),
    guardText: box(guardText),
    guardWhiteSpace: guardText ? getComputedStyle(guardText).whiteSpace : null,
    guardOverflow: guardText ? getComputedStyle(guardText).textOverflow : null,
    guardLines: guardText && lineHeight ? Math.round(guardText.getBoundingClientRect().height / lineHeight) : 0,
    guardChars: guardText ? guardText.textContent.trim().length : 0,
    guardTitle: guard ? guard.getAttribute('title') : null,
    hasStrip: !!strip,
    stripInContext: !!(context && context.querySelector('.git-conflict-ai-strip')),
    stripIsFullWidth: !!(strip && Math.abs(strip.getBoundingClientRect().width - dialog.getBoundingClientRect().width) < 2),
    stripText: strip ? strip.textContent.replace(/\s+/g, ' ').trim() : null,
    stripRetry: !!(strip && strip.querySelector('[data-test="conflict-provider-retry"]')),
    invokeDisabled: (() => { const b = dialog.querySelector('[data-test="conflict-ai-invoke"]'); return b ? b.disabled : null; })(),
    invokeTitle: (() => { const b = dialog.querySelector('[data-test="conflict-ai-invoke"]'); return b ? b.getAttribute('title') : null; })(),
  };
})()`

// rev4 를 되살리는 조작: 실행 문구를 가드와 같은 줄로 되돌리고, 가드의 옛 규칙을 덮어씌운다.
const REGRESS = String.raw`(() => {
  const ft = document.querySelector('.git-conflict-dialog-ft');
  const context = ft && ft.querySelector('.git-conflict-footer-context');
  const strip = document.querySelector('.git-conflict-ai-strip');
  const guard = document.querySelector('.git-conflict-guard');
  if (!context || !strip || !guard) return false;
  strip.classList.remove('git-conflict-ai-strip');
  strip.classList.add('git-conflict-ai-run');
  context.insertBefore(strip, guard.nextSibling);
  const style = document.createElement('style');
  style.textContent =
    '.git-conflict-ai-run { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; font-size: .74rem; }' +
    '.git-conflict-guard { white-space: normal !important; }' +
    '.git-conflict-guard span { white-space: normal !important; overflow: visible !important; text-overflow: clip !important; overflow-wrap: anywhere !important; }';
  document.head.appendChild(style);
  return true;
})()`

const profile = resolve(scratch, 'chrome-resolver-running-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9700 + Math.floor(Math.random() * 200)
const proc = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--remote-allow-origins=*',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })

function connect(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  let id = 0
  const pending = new Map()
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data)
    const done = pending.get(msg.id)
    if (done) { pending.delete(msg.id); done(msg) }
  })
  const ready = new Promise((ok, bad) => {
    ws.addEventListener('open', ok, { once: true })
    ws.addEventListener('error', bad, { once: true })
  })
  const call = (method, params = {}) => new Promise((done) => {
    const next = ++id
    pending.set(next, done)
    ws.send(JSON.stringify({ id: next, method, params }))
  })
  return { ws, ready, call }
}

async function newPage() {
  for (let i = 0; i < 80; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })
      if (response.ok) return await response.json()
    } catch {}
    await delay(100)
  }
  throw new Error('Chrome connection timeout')
}

async function measure(html, { regress = false } = {}) {
  const page = await newPage()
  const { ws, ready, call } = connect(page)
  await ready
  await call('Runtime.enable')
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
  const payload = JSON.stringify({ html, css: builtCss })
  await call('Runtime.evaluate', {
    expression: `(() => { const f = ${payload};
      document.head.innerHTML = '<style>' + f.css + '</style>';
      document.body.innerHTML = f.html; })()`,
  })
  if (regress) {
    const applied = await call('Runtime.evaluate', { expression: REGRESS, returnByValue: true })
    if (applied.result.result.value !== true) { ws.close(); throw new Error('the rev4 reconstruction did not apply') }
  }
  const out = await call('Runtime.evaluate', { expression: PROBE, returnByValue: true })
  ws.close()
  if (out.result.exceptionDetails) throw new Error(JSON.stringify(out.result.exceptionDetails))
  return out.result.result.value
}

const failures = []
const measured = {}
try {
  for (const name of STATES) measured[name] = await measure(states[name])
  measured.running_rev4 = await measure(states.running, { regress: true })

  const running = measured.running
  // 반려 #2 그 자체: 가드 문장은 한 줄이고, 잘려도 title 로 읽을 수 있다.
  if (running.guardLines !== 1) failures.push(`running.guardLines: expected 1, got ${running.guardLines}`)
  if (running.guardWhiteSpace !== 'nowrap') failures.push(`running.guard white-space: expected nowrap, got ${running.guardWhiteSpace}`)
  if (running.guardOverflow !== 'ellipsis') failures.push(`running.guard text-overflow: expected ellipsis, got ${running.guardOverflow}`)
  if (!running.guardTitle || !running.guardTitle.includes('16, 28')) failures.push(`running.guard title must carry the full sentence, got ${JSON.stringify(running.guardTitle)}`)
  // 실행 문구는 액션바 밖의 제 줄이다.
  if (!running.hasStrip) failures.push('running: the AI run strip is missing')
  if (running.stripInContext) failures.push('running: the run strip is back inside the action bar')
  if (!running.stripIsFullWidth) failures.push('running: the run strip is not a full-width row')
  if (!/0:28/.test(running.stripText || '')) failures.push(`running.stripText: ${JSON.stringify(running.stripText)}`)
  // 액션바가 그 때문에 두꺼워지지 않는다.
  if (running.footer.h > measured.idle.footer.h + 1) {
    failures.push(`running.footer.h ${running.footer.h} grew past idle ${measured.idle.footer.h}`)
  }

  // 대조군: rev4 의 배치를 되살리면 정확히 반려된 모양이 나온다.
  if (measured.running_rev4.guardLines <= 1) {
    failures.push(`the rev4 reconstruction did not reproduce the reported break-up (guardLines=${measured.running_rev4.guardLines})`)
  }

  // 반려 #1: 호출 직후의 창.
  if (!measured.starting.hasStrip) failures.push('starting: nothing is drawn between the press and the run entry')
  if (measured.starting.invokeDisabled !== true) failures.push('starting: the call button must be held while the first call is in flight')

  // 반려 #4: 공급자가 없는 세 조건이 서로 다른 문장을 낸다.
  const sentences = new Set([
    measured.noprovider.stripText, measured.providerloading.stripText, measured.providererror.stripText,
  ])
  if (sentences.size !== 3) failures.push(`the three empty-provider conditions must read differently: ${JSON.stringify([...sentences])}`)
  if (!measured.noprovider.stripRetry) failures.push('noprovider: no way to re-read the list from inside the dialog')
  if (!measured.providererror.stripRetry) failures.push('providererror: no way to re-read the list from inside the dialog')
  if (measured.noprovider.invokeDisabled !== true) failures.push('noprovider: the call button must be disabled')
  if (!measured.noprovider.invokeTitle) failures.push('noprovider: the disabled call button says nothing')

  // 시안이 그리는 상태에는 이 띠가 없어야 한다(액션바 시안 대조는 conflict-resolver-deck.0481.mjs).
  if (measured.idle.hasStrip) failures.push('idle: the deck state must carry no strip')

  await writeFile(
    resolve(scratch, 'conflict-resolver-running.0481.json'),
    JSON.stringify({ cssBundle: cssName, measured, failures }, null, 2),
    'utf8',
  )
  console.log(JSON.stringify(measured, null, 2))
} finally {
  if (proc.exitCode === null) proc.kill()
  await delay(300)
  await rm(profile, { recursive: true, force: true })
}
if (failures.length) {
  console.error('RUNNING LAYOUT FAILED:\n' + failures.join('\n'))
  process.exit(1)
}
console.log('conflict resolver running layout ok')
