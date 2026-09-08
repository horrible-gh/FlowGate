/**
 * flowgate.default.0481 T0010 rev1 — 승인 대기 화면의 채팅이 "제자리에서 기다리면 답이
 * 온다"인지 실제 크롬에서 실측한다. 반려 사유(2026-09-08): "난 채팅을 치면 기다렸다가 바로
 * 답장 받는걸 원했는데 아예 다이얼로그 밖으로 빠져나가서 기본 AI실행 다이얼로그 보는걸
 * 원하지 않는다."
 *
 * 서술이 아니라 좌표로 판정하는 것 네 가지:
 *   1) 대기 줄이 대화 로그(.gmr-conv-log) 안에, 다이얼로그 상자 안에 그려진다.
 *   2) 대기 중에도 화면이 살아 있다 — 파일 목록/디프가 그대로 있고 전체 로딩 상자가 없다.
 *   3) 대기 상태가 컨트롤을 하나도 늘리지 않는다(시안 v13 화면 2 대비 추가 컨트롤 금지).
 *      [전송]은 지워지지 않고 비활성으로 남는다.
 *   4) 답이 도착하면 그 자리, 사람 말풍선 아래에 붙는다.
 *
 *   cd client && npm run build && node tests/browser/merge-review-wait.0481.mjs
 *
 * 종료코드 0 = 통과. FLOWGATE_SCRATCH 아래에 채록 JSON 을 남긴다.
 */
import { mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn, spawnSync } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const delay = (ms) => new Promise((done) => setTimeout(done, ms))

const fixture = spawnSync(process.execPath, [
  'node_modules/vitest/vitest.mjs', 'run', 'tests/browser/GitMergeReview.waitInPlace.0481.fixture.spec.ts',
], { stdio: 'inherit', env: process.env })
if (fixture.status !== 0) throw new Error(`component fixture failed: ${fixture.status}`)

const cssName = (await readdir(resolve('dist/assets'))).find((n) => n.startsWith('main-') && n.endsWith('.css'))
if (!cssName) throw new Error('production CSS bundle is missing — run `npm run build` first')
const builtCss = await readFile(resolve('dist/assets', cssName), 'utf8')
// Scoped-CSS identity of this component in the bundle, so the fixture's own hash is
// rewritten to the built one and the measurement runs under the real shipped rules.
const builtScope = builtCss.match(/gmr-conv-log\[data-v-([a-f0-9]+)\]/)?.[1]
if (!builtScope) throw new Error('GitMergeReviewDialog scoped CSS identity is missing from the bundle')

const states = {}
for (const name of ['idle', 'waiting', 'answered']) {
  const raw = await readFile(resolve(scratch, `merge-review.${name}.html`), 'utf8')
  const fixtureScope = raw.match(/data-v-([a-f0-9]+)/)?.[1]
  if (!fixtureScope) throw new Error(`fixture ${name} carries no scoped attribute`)
  states[name] = raw.replaceAll('data-v-' + fixtureScope, 'data-v-' + builtScope)
}

const PROBE = String.raw`(() => {
  const dialog = document.querySelector('.gmr-modal');
  if (!dialog) return { error: 'no dialog' };
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const log = dialog.querySelector('.gmr-conv-log');
  const wait = dialog.querySelector('[data-test="gmr-waiting-turn"]');
  const conv = dialog.querySelector('.gmr-conversation');
  const send = dialog.querySelector('.gmr-send-btn');
  const humanTurns = [...dialog.querySelectorAll('.gmr-turn-human')];
  const aiTurns = [...dialog.querySelectorAll('.gmr-turn-ai')].filter((el) => !el.hasAttribute('data-test'));
  const controls = conv ? [...conv.querySelectorAll('button, select, textarea, input')] : [];
  const dialogBox = box(dialog);
  const waitBox = box(wait);
  const inside = (a, b) => !!(a && b && a.x >= b.x - 1 && a.y >= b.y - 1 && a.x + a.w <= b.x + b.w + 1 && a.y + a.h <= b.y + b.h + 1);
  return {
    modalCount: document.querySelectorAll('.modal-bg').length,
    fullScreenStateBox: !!dialog.querySelector('.gmr-state'),
    fileCount: dialog.querySelectorAll('.gcd-file').length,
    diffLineCount: dialog.querySelectorAll('.gcd-line').length,
    hasWaitingTurn: !!wait,
    waitingTurnInLog: !!(wait && log && log.contains(wait)),
    waitingTurnInDialog: inside(waitBox, dialogBox),
    waitingTurnVisible: !!(waitBox && waitBox.w > 0 && waitBox.h > 0),
    waitingText: wait ? wait.textContent.replace(/\s+/g, ' ').trim() : null,
    controlCount: controls.length,
    controlKinds: controls.map((el) => el.tagName.toLowerCase() + (el.type ? ':' + el.type : '')).join(','),
    sendPresent: !!send,
    sendDisabled: !!(send && send.disabled),
    humanTurnCount: humanTurns.length,
    aiTurnCount: aiTurns.length,
    lastAiText: aiTurns.length ? aiTurns[aiTurns.length - 1].textContent.replace(/\s+/g, ' ').trim() : null,
    aiBelowHuman: !!(aiTurns.length && humanTurns.length && box(aiTurns[aiTurns.length - 1]).y >= box(humanTurns[0]).y),
    lastLogChildIsWait: !!(log && wait && log.lastElementChild === wait),
  };
})()`

const profile = resolve(scratch, 'chrome-merge-review-profile')
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

async function probe(html) {
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
  const out = await call('Runtime.evaluate', { expression: PROBE, returnByValue: true })
  ws.close()
  if (out.result.exceptionDetails) throw new Error(JSON.stringify(out.result.exceptionDetails))
  return out.result.result.value
}

const failures = []
try {
  const actual = {}
  for (const [name, html] of Object.entries(states)) actual[name] = await probe(html)
  for (const [name, seen] of Object.entries(actual)) {
    if (seen.error) throw new Error(`${name} probe failed: ${seen.error}`)
  }

  const { idle, waiting, answered } = actual

  // 1) 대기는 대화 로그 안, 다이얼로그 안에서 눈에 보이게 그려진다.
  if (!waiting.hasWaitingTurn) failures.push('waiting: 대기 줄이 없다')
  if (!waiting.waitingTurnInLog) failures.push('waiting: 대기 줄이 대화 로그(.gmr-conv-log) 밖에 있다')
  if (!waiting.waitingTurnInDialog) failures.push('waiting: 대기 줄이 다이얼로그 상자 밖에 그려졌다')
  if (!waiting.waitingTurnVisible) failures.push('waiting: 대기 줄의 크기가 0이다')
  if (!waiting.lastLogChildIsWait) failures.push('waiting: 대기 줄이 로그의 마지막(사람 말풍선 아래)이 아니다')
  if (!/경과/.test(waiting.waitingText || '')) failures.push(`waiting: 대기 줄이 경과 시간을 말하지 않는다 (${waiting.waitingText})`)

  // 2) 기다리는 동안 화면이 살아 있다 — 통째로 스피너가 되지 않는다.
  for (const [name, seen] of Object.entries(actual)) {
    if (seen.modalCount !== 1) failures.push(`${name}: 다이얼로그가 1개가 아니다 (${seen.modalCount})`)
    if (seen.fullScreenStateBox) failures.push(`${name}: 전체 로딩/오류 상자가 화면을 덮고 있다`)
  }
  if (waiting.fileCount !== idle.fileCount) failures.push(`waiting.fileCount: idle=${idle.fileCount} waiting=${waiting.fileCount}`)
  if (waiting.diffLineCount !== idle.diffLineCount) failures.push(`waiting.diffLineCount: idle=${idle.diffLineCount} waiting=${waiting.diffLineCount}`)

  // 3) 대기는 컨트롤을 늘리지 않는다. [전송]은 지워지지 않고 비활성으로 남는다.
  if (waiting.controlKinds !== idle.controlKinds) {
    failures.push(`waiting.controlKinds: idle=${idle.controlKinds} waiting=${waiting.controlKinds}`)
  }
  if (answered.controlKinds !== idle.controlKinds) {
    failures.push(`answered.controlKinds: idle=${idle.controlKinds} answered=${answered.controlKinds}`)
  }
  if (!waiting.sendPresent) failures.push('waiting: [전송] 버튼이 사라졌다')
  if (!waiting.sendDisabled) failures.push('waiting: [전송] 이 비활성이 아니다')

  // 4) 답은 같은 자리, 사람 말풍선 아래에 도착한다.
  if (answered.hasWaitingTurn) failures.push('answered: 답이 왔는데 대기 줄이 남아 있다')
  if (answered.aiTurnCount !== 1) failures.push(`answered.aiTurnCount=${answered.aiTurnCount}`)
  if (!answered.aiBelowHuman) failures.push('answered: AI 말풍선이 사람 말풍선 위에 있다')
  if (!/키를 추가했습니다/.test(answered.lastAiText || '')) {
    failures.push(`answered: 답 본문이 로그에 없다 (${answered.lastAiText})`)
  }

  await writeFile(
    resolve(scratch, 'merge-review-wait.0481.json'),
    JSON.stringify({ cssBundle: cssName, actual, failures }, null, 2),
    'utf8',
  )
  console.log(JSON.stringify({ cssBundle: cssName, actual }, null, 2))
} finally {
  if (proc.exitCode === null) proc.kill()
  await delay(300)
  await rm(profile, { recursive: true, force: true })
}
if (failures.length) {
  console.error('WAIT-IN-PLACE CHECK FAILED:\n' + failures.join('\n'))
  process.exit(1)
}
console.log('wait-in-place ok')
