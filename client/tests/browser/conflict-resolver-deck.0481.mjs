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

/*
 * 0560 T0024 — 프로덕션 CSS 는 한 파일이 아니다. 디자인 토큰(`--border`, `--surface` …)은
 * `AppIcon-*.css`, 공통 dialog 계층(`dialog.css`: overlay/surface/header/footer/버튼)은
 * `ConfirmDialog-*.css`, 이 컴포넌트의 scoped 규칙과 surface 훅은 `main-*.css` 에 있다.
 * 이관 전에는 다이얼로그가 제 shell 을 직접 칠했으므로 `main-*.css` 하나로 충분했지만 이제는
 * 아니다 — 빠진 청크는 "전부 0px / 투명" 으로 조용히 통과하는 실패다. 전부 싣되 `main-*.css`
 * 를 마지막에 둬서 동점 규칙에서 이 파일 쪽이 이기게 한다(실제 페이지와 같은 순서다).
 */
const cssFiles = (await readdir(resolve('dist/assets'))).filter((n) => n.endsWith('.css')).sort()
const cssName = cssFiles.find((n) => n.startsWith('main-'))
if (!cssName) throw new Error('production CSS bundle is missing — run `npm run build` first')
const cssOrder = [...cssFiles.filter((n) => n !== cssName), cssName]
const builtCss = (
  await Promise.all(cssOrder.map((n) => readFile(resolve('dist/assets', n), 'utf8')))
).join('\n')
// The scoped-CSS id the bundle uses for this component. `.git-conflict-guard` is the anchor
// on purpose: it exists in every revision of the dialog, so a missing v13 class shows up
// below as a PARITY failure with a readable diff instead of as a harness crash here.
const builtScope = builtCss.match(/git-conflict-guard\[data-v-([a-f0-9]+)\]/)?.[1]
if (!builtScope) throw new Error('GitConflictResolverDialog scoped CSS identity is missing from the bundle')

const states = {}
for (const name of ['ready', 'empty', 'error']) {
  const raw = await readFile(resolve(scratch, `conflict-resolver.${name}.html`), 'utf8')
  // 8 hex digits exactly: a loose `[a-f0-9]+` also matches Vue's own `data-v-app` marker.
  const fixtureScope = raw.match(/data-v-([a-f0-9]{8})="/)?.[1]
  if (!fixtureScope) throw new Error(`fixture ${name} carries no scoped attribute`)
  states[name] = raw.replaceAll('data-v-' + fixtureScope, 'data-v-' + builtScope)
}

/*
 * 시안과 실제 양쪽에서 같은 코드가 돈다: 액션바 한 줄의 구조와 좌표.
 *
 * 0560 T0024 — 실제 쪽은 공통 dialog 계층으로 옮겨졌다. 덱은 손으로 짠 `.git-conflict-dialog`
 * / `.git-conflict-dialog-ft` / `.git-conflict-footer-actions` 를 그대로 그리고, 실제는
 * `.fg-dialog-surface` + (마커 가드 띠) + `.fg-dialog-footer__actions` 다. 그래서 이 프로브는
 * 양쪽에서 같은 *의미*의 엘리먼트를 찾는다 — 기대치는 여전히 덱에서 읽고, 이 파일에 베껴
 * 적지 않는다. 덱에는 없고 실제에만 있는 배치 사실(버튼 줄이 가드 띠 아래로 내려온 것)은
 * STRUCTURE_KEYS 가 아니라 아래 INTENDED 절에서 따로 기록·검사한다.
 */
const PROBE = String.raw`(() => {
  const dialog = document.querySelector('.git-conflict-dialog, .fg-dialog-surface');
  if (!dialog) return { error: 'no dialog' };
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const context = dialog.querySelector('.git-conflict-footer-context');
  // deck: .git-conflict-dialog-ft > .git-conflict-footer-actions / real: DialogFooter's row.
  const actions = dialog.querySelector('.git-conflict-footer-actions, .fg-dialog-footer__actions');
  if (!actions) return { error: 'no action row' };
  const options = context && context.querySelector('.git-conflict-invoke-options');
  const buttons = [...actions.querySelectorAll('button')];
  const isPrimary = (b) => b.classList.contains('btn-primary') || b.classList.contains('fg-dialog-btn--primary');
  const cBox = box(context);
  const aBox = box(actions);
  return {
    hasContext: !!context,
    hasActions: true,
    guardInContext: !!(context && context.querySelector('.git-conflict-guard')),
    dividerInContext: !!(context && context.querySelector('.ft-divider')),
    optionsInContext: !!options,
    providerInOptions: !!(options && options.querySelector('select')),
    autoToggleInOptions: !!(options && options.querySelector('input[type=checkbox]')),
    providerInActions: !!actions.querySelector('select'),
    autoToggleInActions: !!actions.querySelector('input[type=checkbox]'),
    buttonCount: buttons.length,
    lastButtonIsPrimary: buttons.length > 0 && isPrimary(buttons[buttons.length - 1]),
    buttonLabels: buttons.map((b) => b.textContent.replace(/\s+/g, ' ').trim()),
    // 마지막 낱말만 남긴 순서 키. 덱은 손으로 그린 시안이라 버튼 글자가 아이콘 이모지로
    // 시작하고(📋 🤖 🚫 ✔) 낱말도 제품 i18n 과 다르다 — 덱 '중단'/'해결 제출' vs 제품
    // '머지 중단'/'해소 제출'. 이 어긋남은 이 이관 이전부터 있던 것이고(0560 T0024 는
    // 라벨을 하나도 건드리지 않는다: HEAD 의 네 버튼도 같은 키를 같은 순서로 쓴다),
    // 그래서 글자 그대로의 동일성은 애초에 대조 가능한 값이 아니다. 대조 가능한 것은
    // 네 버튼의 '순서'이며, 양쪽 모두에서 마지막 낱말을 뽑으면 복사·호출·중단·제출로
    // 같은 축이 된다. 기대치는 여전히 덱 DOM 에서 읽고 이 파일에 베껴 적지 않는다.
    buttonOrderKeys: buttons.map((b) => b.textContent.replace(/\s+/g, ' ').trim().split(' ').pop()),
    contextBox: cBox,
    actionsBox: aBox,
    dialogBox: box(dialog),
    contextLeftOfActions: !!(cBox && aBox && cBox.x + cBox.w <= aBox.x + 1),
    contextAboveActions: !!(cBox && aBox && cBox.y + cBox.h <= aBox.y + 1),
    actionsRightAligned: !!(aBox && box(dialog) && Math.abs((aBox.x + aBox.w) - (box(dialog).x + box(dialog).w)) <= 24),
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
    'buttonCount', 'lastButtonIsPrimary',
  ]
  for (const key of STRUCTURE_KEYS) {
    if (actual.ready[key] !== deck[key]) {
      failures.push(`ready.${key}: deck=${JSON.stringify(deck[key])} actual=${JSON.stringify(actual.ready[key])}`)
    }
  }
  // 버튼 네 개의 좌→우 순서도 덱이 기대치다. 공통 계층에서는 이 순서가 DOM 순서가 아니라
  // `footerRolePriority` 의 결과이므로, 실제로 칠해진 줄을 읽어 대조한다. 비교 축은 글자
  // 그대로가 아니라 낱말 마지막 토큰(`buttonOrderKeys`)이다 — 이유는 PROBE 쪽 주석 참조:
  // 덱은 이모지로 시작하고 '중단'/'해결 제출' 이라고 쓰지만 제품 i18n 은 '머지 중단'/'해소
  // 제출' 이며, 이 어긋남은 이관 전 HEAD 에서도 똑같다(같은 네 키를 같은 순서로 쓴다).
  // 글자 자체는 아래 `intended.buttonLabels` 에 양쪽 모두 그대로 남겨 둔다.
  if (JSON.stringify(actual.ready.buttonOrderKeys) !== JSON.stringify(deck.buttonOrderKeys)) {
    failures.push(
      `ready.buttonOrderKeys: deck=${JSON.stringify(deck.buttonOrderKeys)} actual=${JSON.stringify(actual.ready.buttonOrderKeys)}`,
    )
  }

  /*
   * 0560 T0024 의 의도된 차이 — 유일하게 덱과 다른 한 가지.
   *
   * 덱(v13 화면 1)은 가드 띠와 버튼 줄을 한 줄에서 좌/우로 나눠 그린다. 공통 `DialogFooter`
   * 는 버튼 행만 소유하고 그 옆에 임의 콘텐츠를 끼울 슬롯이 없으므로(T0024 §2.5), 가드 띠는
   * 버튼 줄의 sibling 으로 바로 위에 선다 — `GitMergeReviewDialog` 의 `.gmr-ft-extras` 와
   * 같은 모양이다. 띠 안의 내용물과 버튼 네 개·그 순서는 위에서 덱과 동일함을 확인했고,
   * 바뀐 것은 그 둘의 상대 위치뿐이다. 여기서는 그 새 배치가 실제로 성립하는지를 재고,
   * 덱 쪽 값과 나란히 JSON 에 남긴다.
   */
  const intended = {
    contextLeftOfActions: { deck: deck.contextLeftOfActions, actual: actual.ready.contextLeftOfActions },
    contextAboveActions: { deck: deck.contextAboveActions, actual: actual.ready.contextAboveActions },
    // 이관과 무관한 선재 차이. 덱은 시안 글자, 실제는 제품 i18n 이고 T0024 는 라벨을
    // 하나도 건드리지 않는다. 판정에 쓰지 않고 근거로만 남긴다.
    buttonLabels: { deck: deck.buttonLabels, actual: actual.ready.buttonLabels },
  }
  if (actual.ready.contextAboveActions !== true) {
    failures.push('ready: the guard band is not the row directly above the buttons')
  }
  if (actual.ready.actionsRightAligned !== true) {
    failures.push(`ready: the button row is not flush right (${JSON.stringify(actual.ready.actionsBox)} in ${JSON.stringify(actual.ready.dialogBox)})`)
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
    JSON.stringify({ deckUrl, cssBundle: cssName, cssOrder, deck, actual, intended, failures }, null, 2),
    'utf8',
  )
  console.log(JSON.stringify({ deckUrl, cssBundle: cssName, deck, actual, intended }, null, 2))
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
