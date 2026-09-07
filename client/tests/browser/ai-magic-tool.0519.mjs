/**
 * flowgate.default.0519 T0009 §확인 — 매직툴 버튼을 헤드리스 크롬에서 실측한다.
 *
 * "CLI 실행 커맨드 입력창 우측에 조그맣게" 라는 지시를 눈이 아니라 수치로 확인한다:
 * 버튼이 입력창과 같은 줄에 있고(세로 겹침), 입력창 오른쪽에 있으며(x 가 더 큼),
 * 다이얼로그 폭의 한 자리만 차지하는 작은 크기인지. 이어서 CDP 로 진짜 클릭을 넣어
 * 커맨드가 실제로 채워지는지, rej_01M1YTRN0SD379SG 대응대로 [권한 확인 생략 (무인 실행용)]
 * 체크박스와 그 밑에 딸렸던 안내/경고 문구가 다이얼로그에서 완전히 사라졌는지(클릭 전/후
 * 모두), 사람이 커맨드를 손으로 안전 폼으로 고쳐도 매직툴을 다시 누르면 항상 전체 옵션
 * 폼으로 되돌아오는지, custom 종류에서는 버튼이 사라지는지 본다.
 *
 *   cd client && node tests/browser/ai-magic-tool.0519.mjs
 *
 * 결과 JSON·PNG 는 FLOWGATE_SCRATCH 아래에 남는다. 종료코드 0 = 모든 단언 통과.
 * (client/tests/browser/ai-settings-deck.0469.mjs 의 CDP 사용 방식을 따랐다.)
 */
import { mkdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const outDir = resolve(scratch, 'magic-tool-0519')
await mkdir(outDir, { recursive: true })

const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const vitePort = Number(process.env.VITE_PORT || 3151)
const harness = `http://localhost:${vitePort}/tests/browser/ai-magic-tool.0519.html`
const delay = (ms) => new Promise((done) => setTimeout(done, ms))

const PROBE = String.raw`(() => {
  const rect = (el) => {
    const r = el.getBoundingClientRect()
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
  }
  const dialog = document.querySelector('.modal-bg .modal-box')
  const row = document.querySelector('.cli-cmd-row')
  const input = row ? row.querySelector('input') : null
  const btn = document.querySelector('.cli-magic-btn')
  const out = {
    dialog: dialog ? rect(dialog) : null,
    row: row ? rect(row) : null,
    input: input ? { rect: rect(input), value: input.value } : null,
    // rej_01M1YTRN0SD379SG: only the "enabled" checkbox should remain in the dialog, and no
    // permission-skip .form-hint paragraph — that checkbox and its hint/warning text are gone.
    // rej_01M1YY7MQRQZAZG1: a *different* .form-hint takes its place once the command still
    // carries the literal model_name placeholder — it reminds the user to replace it.
    checkboxCount: document.querySelectorAll('.modal-bg .modal-bd input[type="checkbox"]').length,
    formHintCount: document.querySelectorAll('.modal-bg .modal-bd .form-hint').length,
    modelHintText: (() => {
      const el = document.querySelector('.modal-bg .modal-bd .form-hint')
      return el ? el.textContent.trim() : null
    })(),
    button: btn ? {
      rect: rect(btn), title: btn.getAttribute('title'), aria: btn.getAttribute('aria-label'),
      disabled: !!btn.disabled, tag: btn.tagName.toLowerCase(),
      css: (() => { const s = getComputedStyle(btn); return {
        display: s.display, width: s.width, height: s.height, padding: s.padding,
        border: s.border, borderRadius: s.borderRadius, backgroundColor: s.backgroundColor,
        color: s.color,
      } })(),
      svg: btn.querySelector('svg') ? rect(btn.querySelector('svg')) : null,
      text: (btn.textContent || '').trim(),
    } : null,
    error: (() => { const e = document.querySelector('.ai-magic-error'); return e ? e.textContent.trim() : null })(),
    // 반려된 패널이 정말 사라졌는지: 다이얼로그의 입력/선택 개수가 이전 계약 그대로여야 한다
    dialogInputsMono: document.querySelectorAll('.modal-bg .modal-bd input.mono').length,
    dialogSelects: document.querySelectorAll('.modal-bg .modal-bd select').length,
    dialogLabels: Array.from(document.querySelectorAll('.modal-bg .modal-bd .form-label'))
      .map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim()),
    dialogButtons: Array.from(document.querySelectorAll('.modal-bg .modal-bd button'))
      .map((el) => ((el.textContent || '').replace(/\s+/g, ' ').trim() || '(icon)')),
    presetPanelPresent: !!document.querySelector('.ai-preset-panel'),
  }
  return out
})()`

function cdp(ws) {
  let id = 0
  const pending = new Map()
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data)
    const done = pending.get(msg.id)
    if (done) { pending.delete(msg.id); done(msg) }
  })
  return (method, params = {}) => new Promise((done) => {
    const next = ++id
    pending.set(next, done)
    ws.send(JSON.stringify({ id: next, method, params }))
  })
}

async function openPage(port, width = 1440) {
  const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })
  if (!response.ok) throw new Error(`cannot open a page: ${response.status}`)
  const page = await response.json()
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((ok, bad) => {
    ws.addEventListener('open', ok, { once: true })
    ws.addEventListener('error', bad, { once: true })
  })
  const call = cdp(ws)
  await call('Page.enable')
  await call('Runtime.enable')
  await call('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false })
  return { call, ws }
}

async function evaluate(call, expression, awaitPromise = false) {
  const res = await call('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise, allowUnsafeEvalBlockedByCSP: true,
  })
  if (res.result?.exceptionDetails) {
    throw new Error(`evaluate failed: ${JSON.stringify(res.result.exceptionDetails).slice(0, 400)}`)
  }
  return res.result?.result?.value
}

async function goto(call, url) {
  await call('Page.navigate', { url })
  for (let i = 0; i < 200; i += 1) {
    if (await evaluate(call, 'document.readyState') === 'complete') break
    await delay(100)
  }
  for (let i = 0; i < 200; i += 1) {
    if (await evaluate(call, 'window.__harnessReady === true')) return
    await delay(100)
  }
  throw new Error(`harness never became ready: ${url}`)
}

async function realClick(call, selector) {
  const at = await evaluate(call, `(() => {
    const el = document.querySelector(${JSON.stringify(selector)})
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
  })()`)
  if (!at) throw new Error(`no element to click: ${selector}`)
  for (const type of ['mousePressed', 'mouseReleased']) {
    await call('Input.dispatchMouseEvent', { type, x: at.x, y: at.y, button: 'left', clickCount: 1 })
  }
  await delay(250)
}

async function shot(call, label) {
  const png = await call('Page.captureScreenshot', { format: 'png' })
  if (png.result?.data) await writeFile(resolve(outDir, `${label}.png`), Buffer.from(png.result.data, 'base64'))
}

const failures = []
function check(name, ok, detail) {
  if (!ok) failures.push({ name, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — ${JSON.stringify(detail)}`}`)
}

const vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--port', String(vitePort), '--strictPort'], {
  stdio: ['ignore', 'pipe', 'pipe'], env: process.env,
})
let viteLog = ''
vite.stdout.on('data', (b) => { viteLog += b.toString() })
vite.stderr.on('data', (b) => { viteLog += b.toString() })

const profile = resolve(scratch, 'chrome-0519-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9700 + Math.floor(Math.random() * 200)
const browser = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--remote-allow-origins=*',
  '--hide-scrollbars', '--force-device-scale-factor=1',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })

let exitCode = 0
try {
  let viteUp = false
  for (let i = 0; i < 300; i += 1) {
    try {
      const r = await fetch(harness)
      if (r.ok) { viteUp = true; break }
    } catch { /* not up yet */ }
    await delay(200)
  }
  if (!viteUp) throw new Error(`vite dev server never came up on ${vitePort}\n${viteLog}`)

  let chromeUp = false
  for (let i = 0; i < 200; i += 1) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`)
      if (r.ok) { chromeUp = true; break }
    } catch { /* not up yet */ }
    await delay(100)
  }
  if (!chromeUp) throw new Error('Chrome connection timeout')

  const report = { generated_at: new Date().toISOString(), viewport: '1440x900', steps: {} }
  const { call, ws } = await openPage(port)
  await goto(call, `${harness}?view=edit`)

  const before = await evaluate(call, PROBE)
  report.steps.edit_open = before
  await shot(call, 'edit-open')

  check('the button exists next to the command input', !!before.button, before)
  check('the panel that T0009 rejected is gone', before.presetPanelPresent === false, before)
  check('the dialog gained no model field (one mono input, two selects)',
    before.dialogInputsMono === 1 && before.dialogSelects === 2,
    { mono: before.dialogInputsMono, selects: before.dialogSelects, labels: before.dialogLabels })
  check('the button is on the same line as the input (vertical overlap)',
    before.button.rect.y < before.input.rect.y + before.input.rect.h
    && before.input.rect.y < before.button.rect.y + before.button.rect.h,
    { input: before.input.rect, button: before.button.rect })
  check('the button sits to the right of the input',
    before.button.rect.x >= before.input.rect.x + before.input.rect.w,
    { input: before.input.rect, button: before.button.rect })
  check('the button is small — under 40px wide and under a tenth of the dialog',
    before.button.rect.w <= 40 && before.button.rect.w < before.dialog.w / 10,
    { button: before.button.rect, dialog: before.dialog })
  check('the button carries an icon and no label text',
    !!before.button.svg && before.button.text === '', before.button)
  check('the button is reachable by name (title + aria-label)',
    !!before.button.title && before.button.aria === before.button.title, before.button)
  check('the saved command is loaded untouched',
    before.input.value === 'claude --model claude-opus-4-8 -p -', before.input)
  check('no permission-skip checkbox in the dialog (only "enabled")',
    before.checkboxCount === 1, before)
  check('no permission-skip hint/warning text under the command input (a real model name is loaded)',
    before.formHintCount === 0, before)

  // rej_01M1YTRN0SD379SG: one click fills every unattended-run flag as an internal request
  // parameter — there is no checkbox to auto-check anymore, and none should appear.
  await realClick(call, '.cli-magic-btn')
  const afterSkip = await evaluate(call, PROBE)
  report.steps.after_click_skip = afterSkip
  await shot(call, 'after-click-skip')
  check('clicking fills the unattended-run command (all options) with the model_name placeholder',
    afterSkip.input.value === 'claude --model model_name --dangerously-skip-permissions -p -',
    afterSkip.input)
  check('no error line after a successful fill', afterSkip.error === null, afterSkip)
  check('clicking still shows no permission-skip checkbox', afterSkip.checkboxCount === 1, afterSkip)
  // rej_01M1YY7MQRQZAZG1: the fill left the placeholder in, so a reminder to replace model_name
  // appears — exactly one .form-hint, with the expected copy.
  check('a fill that leaves the placeholder in shows exactly one reminder to replace model_name',
    afterSkip.formHintCount === 1 && afterSkip.modelHintText === '저장하기 전에 커맨드의 model_name을 실제 모델명으로 바꾸세요.',
    afterSkip)

  // 사람이 커맨드를 손으로 다시 안전 폼으로 고쳐도(체크박스가 없으니 유일한 손편집 경로),
  // 매직툴을 다시 누르면 always-skip 형태로 되돌아와야 한다.
  await evaluate(call, `(() => {
    const input = document.querySelector('.cli-cmd-row input')
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(input, 'claude --model model_name -p -')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })()`)
  const afterHandEdit = await evaluate(call, PROBE)
  check('hand-editing the command back to the safe form is possible',
    afterHandEdit.input.value === 'claude --model model_name -p -', afterHandEdit.input)
  check('the reminder stays while the placeholder is still there', afterHandEdit.formHintCount === 1, afterHandEdit)

  // 반려 사유의 핵심: model_name 을 손으로 실제 모델명으로 바꾸면 알림이 사라져야 한다.
  await evaluate(call, `(() => {
    const input = document.querySelector('.cli-cmd-row input')
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(input, 'claude --model claude-opus-4-8 -p -')
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })()`)
  const afterModelReplaced = await evaluate(call, PROBE)
  check('replacing model_name with a real model name clears the reminder',
    afterModelReplaced.formHintCount === 0, afterModelReplaced)

  await realClick(call, '.cli-magic-btn')
  const afterReclick = await evaluate(call, PROBE)
  report.steps.after_reclick = afterReclick
  await shot(call, 'after-reclick')
  check('clicking again always refills every option even after a hand edit removed them',
    afterReclick.input.value === 'claude --model model_name --dangerously-skip-permissions -p -',
    afterReclick.input)
  check('the refill brings the reminder back', afterReclick.formHintCount === 1, afterReclick)

  // custom 종류에는 preset 이 없으므로 버튼이 사라져야 한다.
  await evaluate(call, `(() => {
    const sel = document.querySelectorAll('.modal-bg .modal-bd select')[1]
    sel.value = 'custom'
    sel.dispatchEvent(new Event('change', { bubbles: true }))
  })()`)
  await delay(200)
  const afterCustom = await evaluate(call, PROBE)
  report.steps.after_kind_custom = afterCustom
  check('no button for a kind the catalog publishes no preset for',
    afterCustom.button === null, afterCustom)
  check('switching kind never rewrites the command',
    afterCustom.input.value === 'claude --model model_name --dangerously-skip-permissions -p -',
    afterCustom.input)
  check('the reminder is unaffected by a kind switch that leaves the placeholder in place',
    afterCustom.formHintCount === 1, afterCustom)

  ws.close()

  // 추가 다이얼로그: 빈 커맨드에서 시작해 클릭 한 번으로 채워진다.
  const add = await openPage(port)
  await goto(add.call, `${harness}?view=add`)
  const addOpen = await evaluate(add.call, PROBE)
  report.steps.add_open = addOpen
  check('a new provider starts with an empty command', addOpen.input.value === '', addOpen.input)
  await realClick(add.call, '.cli-magic-btn')
  const addFilled = await evaluate(add.call, PROBE)
  report.steps.add_after_click = addFilled
  await shot(add.call, 'add-after-click')
  check('one click fills a new provider\'s command with every unattended-run option',
    addFilled.input.value === 'claude --model model_name --dangerously-skip-permissions -p -',
    addFilled.input)
  check('no permission-skip checkbox for a new provider either', addFilled.checkboxCount === 1, addFilled)
  check('a new provider gets the same model_name reminder after a fill',
    addFilled.formHintCount === 1 && addFilled.modelHintText === '저장하기 전에 커맨드의 model_name을 실제 모델명으로 바꾸세요.',
    addFilled)
  add.ws.close()

  // 영어/일본어에서도 같은 자리에 같은 크기로 붙어 있는지
  for (const lang of ['en', 'ja']) {
    const page = await openPage(port)
    await goto(page.call, `${harness}?view=edit&lang=${lang}`)
    const probe = await evaluate(page.call, PROBE)
    report.steps[`edit_${lang}`] = probe
    await shot(page.call, `edit-${lang}`)
    check(`${lang}: the button is still a small icon to the right of the input`,
      !!probe.button && probe.button.rect.w <= 40 && probe.button.text === ''
      && probe.button.rect.x >= probe.input.rect.x + probe.input.rect.w
      && !!probe.button.title,
      probe.button)
    page.ws.close()
  }

  report.failures = failures
  await writeFile(resolve(outDir, 'measurements.json'), JSON.stringify(report, null, 2), 'utf8')
  console.log(`written: ${resolve(outDir, 'measurements.json')}`)
  if (failures.length) exitCode = 1
} catch (e) {
  console.error(e)
  exitCode = 1
} finally {
  browser.kill()
  vite.kill()
}
process.exit(exitCode)
