/**
 * flowgate.default.0481 T0010 #4 — 시안 덱 y5bwr1o0 v13 화면 1(충돌 해결 다이얼로그)과 실제
 * `GitConflictResolverDialog.vue` 를 같은 헤드리스 크롬에서 열어 액션바의 구조와 좌표를
 * 나란히 채록하고 대조한다. 시안 쪽 기대치를 이 파일에 베껴 적지 않는다 — 덱 자신의 DOM 을
 * 읽어 그것을 기대치로 쓴다(0469 T0018 의 방식).
 *
 *   cd client && node tests/browser/conflict-resolver-deck.0481.mjs
 *
 * 종료코드 0 = 대조 통과. FLOWGATE_SCRATCH 아래에 채록 JSON 을 남긴다.
 * DECK_BASE 로 덱 주소를, CHROME_PATH 로 크롬 경로를 바꿀 수 있다.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const deckUrl = process.env.DECK_BASE || 'http://127.0.0.1:8100/v/y5bwr1o0/v13/'
const delay = (ms) => new Promise((done) => setTimeout(done, ms))

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run', 'tests/browser/GitConflictResolver.deckParity.0481.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const cssName = (await readdir(resolve('dist/assets'))).find((n) => n.startsWith('main-') && n.endsWith('.css'))
if (!cssName) throw new Error('production CSS bundle is missing — run `npm run build` first')
const builtCss = await readFile(resolve('dist/assets', cssName), 'utf8')
// The scoped-CSS id the bundle uses for this component. `.git-conflict-guard` is the anchor
// on purpose: it exists in every revision of the dialog, so a missing v13 class shows up
// below as a PARITY failure with a readable diff instead of as a harness crash here.
const builtScope = builtCss.match(/git-conflict-guard\[data-v-([a-f0-9]+)\]/)?.[1]
if (!builtScope) throw new Error('GitConflictResolverDialog scoped CSS identity is missing from the bundle')

const states = {}
for (const name of ['ready', 'empty', 'error']) {
  const raw = await readFile(resolve(scratch, `conflict-resolver.${name}.html`), 'utf8')
  const fixtureScope = raw.match(/data-v-([a-f0-9]+)/)?.[1]
  if (!fixtureScope) throw new Error(`fixture ${name} carries no scoped attribute`)
  states[name] = raw.replaceAll('data-v-' + fixtureScope, 'data-v-' + builtScope)
}

/* 시안과 실제 양쪽에서 같은 코드가 돈다: 액션바 한 줄의 구조와 좌표. */
const PROBE = String.raw`(() => {
  const dialog = document.querySelector('.git-conflict-dialog');
  if (!dialog) return { error: 'no dialog' };
  const ft = dialog.querySelector('.git-conflict-dialog-ft');
  if (!ft) return { error: 'no footer' };
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const context = ft.querySelector(':scope > .git-conflict-footer-context');
  const actions = ft.querySelector(':scope > .git-conflict-footer-actions');
  const options = context && context.querySelector('.git-conflict-invoke-options');
  const buttons = actions ? [...actions.querySelectorAll('button')] : [];
  return {
    hasContext: !!context,
    hasActions: !!actions,
    guardInContext: !!(context && context.querySelector('.git-conflict-guard')),
    dividerInContext: !!(context && context.querySelector('.ft-divider')),
    optionsInContext: !!options,
    providerInOptions: !!(options && options.querySelector('select')),
    autoToggleInOptions: !!(options && options.querySelector('input[type=checkbox]')),
    providerInActions: !!(actions && actions.querySelector('select')),
    autoToggleInActions: !!(actions && actions.querySelector('input[type=checkbox]')),
    buttonCount: buttons.length,
    lastButtonIsPrimary: buttons.length > 0 && buttons[buttons.length - 1].classList.contains('btn-primary'),
    contextBox: box(context),
    actionsBox: box(actions),
    contextLeftOfActions: !!(context && actions && box(context).x + box(context).w <= box(actions).x + 1),
  };
})()`

const profile = resolve(scratch, 'chrome-resolver-deck-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9500 + Math.floor(Math.random() * 200)
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

async function probe(setup) {
  const page = await newPage()
  const { ws, ready, call } = connect(page)
  await ready
  await call('Runtime.enable')
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
  await setup(call)
  const out = await call('Runtime.evaluate', { expression: PROBE, returnByValue: true })
  ws.close()
  if (out.result.exceptionDetails) throw new Error(JSON.stringify(out.result.exceptionDetails))
  return out.result.result.value
}

const failures = []
try {
  const deck = await probe(async (call) => {
    await call('Page.enable')
    await call('Runtime.evaluate', { expression: `location.href = ${JSON.stringify(deckUrl)}` })
    await delay(1500)
  })
  if (deck.error) throw new Error(`deck probe failed: ${deck.error} (${deckUrl})`)

  const actual = {}
  for (const [name, html] of Object.entries(states)) {
    actual[name] = await probe(async (call) => {
      const payload = JSON.stringify({ html, css: builtCss })
      await call('Runtime.evaluate', {
        expression: `(() => { const f = ${payload};
          document.head.innerHTML = '<style>' + f.css + '</style>';
          document.body.innerHTML = f.html; })()`,
      })
    })
  }

  // 시안의 구조가 기대치다 — 여기에 값을 베껴 적지 않고 덱에서 읽은 것을 그대로 쓴다.
  const STRUCTURE_KEYS = [
    'hasContext', 'hasActions', 'guardInContext', 'dividerInContext', 'optionsInContext',
    'providerInOptions', 'autoToggleInOptions', 'providerInActions', 'autoToggleInActions',
    'buttonCount', 'lastButtonIsPrimary', 'contextLeftOfActions',
  ]
  for (const key of STRUCTURE_KEYS) {
    if (actual.ready[key] !== deck[key]) {
      failures.push(`ready.${key}: deck=${JSON.stringify(deck[key])} actual=${JSON.stringify(actual.ready[key])}`)
    }
  }
  // T0010 #2: the same action bar has to survive the two states that used to erase it.
  for (const name of ['empty', 'error']) {
    if (actual[name].buttonCount !== deck.buttonCount) {
      failures.push(`${name}.buttonCount: deck=${deck.buttonCount} actual=${actual[name].buttonCount}`)
    }
    if (!actual[name].hasActions) failures.push(`${name}: the action bar is missing`)
  }

  await writeFile(
    resolve(scratch, 'conflict-resolver-deck.0481.json'),
    JSON.stringify({ deckUrl, cssBundle: cssName, deck, actual, failures }, null, 2),
    'utf8',
  )
  console.log(JSON.stringify({ deckUrl, cssBundle: cssName, deck, actual }, null, 2))
} finally {
  if (proc.exitCode === null) proc.kill()
  await delay(300)
  await rm(profile, { recursive: true, force: true })
}
if (failures.length) {
  console.error('DECK PARITY FAILED:\n' + failures.join('\n'))
  process.exit(1)
}
console.log('deck parity ok')
