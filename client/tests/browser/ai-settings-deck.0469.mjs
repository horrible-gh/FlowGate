/**
 * flowgate.default.0469 T0018 §1 — v3 시안 덱(8bqoacqs)과 실제 /settings/system/ai 화면을
 * 같은 viewport(1440x900)의 헤드리스 크롬에서 나란히 열어 DOM/CSS 수치를 채록한다.
 *
 * 시안 6개 URL 은 MirageGlass(127.0.0.1:8100)에서, 실제 화면은 vite dev 위의
 * /tests/browser/ai-settings-deck.0469.html 하네스에서 읽는다.
 * (client/tests/browser/git-status-v9.geometry.mjs 의 CDP 사용 방식을 따랐다.)
 *
 *   cd client && node tests/browser/ai-settings-deck.0469.mjs
 *
 * 결과 JSON·PNG 는 FLOWGATE_SCRATCH 아래에 남긴다. 종료코드 0 = 채록 성공(판정은 사람이
 * 대조표로 한다), 1 = 채록 실패.
 */
import { mkdir, rm, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawn } from 'node:child_process'

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const outDir = resolve(scratch, 'deck-compare')
await mkdir(outDir, { recursive: true })

const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const deckBase = process.env.DECK_BASE || 'http://127.0.0.1:8100/v/8bqoacqs'
const vitePort = Number(process.env.VITE_PORT || 3131)
const harnessBase = `http://localhost:${vitePort}/tests/browser/ai-settings-deck.0469.html`
const delay = (ms) => new Promise((done) => setTimeout(done, ms))

const VIEWS = [
  { key: 'list', deck: `${deckBase}/`, harness: `${harnessBase}?view=list` },
  { key: 'edit', deck: `${deckBase}/s02-edit.html`, harness: `${harnessBase}?view=edit` },
  { key: 'add', deck: `${deckBase}/s03-add.html`, harness: `${harnessBase}?view=add` },
  { key: 'command', deck: `${deckBase}/s04-command.html`, harness: `${harnessBase}?view=command` },
  { key: 'delete', deck: `${deckBase}/s05-delete.html`, harness: `${harnessBase}?view=delete` },
  { key: 'drag', deck: `${deckBase}/s06-drag.html`, harness: `${harnessBase}?view=drag` },
  { key: 'list-empty', deck: null, harness: `${harnessBase}?view=list&rows=empty` },
  { key: 'list-long', deck: null, harness: `${harnessBase}?view=list&rows=long` },
  { key: 'list-narrow', deck: null, harness: `${harnessBase}?view=list`, width: 900 },
  { key: 'list-narrow-long', deck: null, harness: `${harnessBase}?view=list&rows=long`, width: 900 },
  { key: 'list-en', deck: null, harness: `${harnessBase}?view=list&lang=en` },
  { key: 'list-ja', deck: null, harness: `${harnessBase}?view=list&lang=ja` },
  { key: 'edit-en', deck: null, harness: `${harnessBase}?view=edit&lang=en` },
  { key: 'edit-ja', deck: null, harness: `${harnessBase}?view=edit&lang=ja` },
]

/* ── 채록 스크립트 (시안/실제 양쪽에서 같은 코드가 돈다) ───────────────────── */
const PROBE = String.raw`(() => {
  const px = (v) => v;
  const cs = (el, props) => {
    const s = getComputedStyle(el);
    const out = {};
    for (const p of props) out[p] = s.getPropertyValue(p);
    return out;
  };
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
  };
  const txt = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const one = (sel, props) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    return { text: txt(el), cls: el.className, rect: rect(el), css: cs(el, props) };
  };
  const many = (sel, props) => Array.from(document.querySelectorAll(sel)).map((el) => ({
    text: txt(el), cls: el.className, rect: rect(el), css: cs(el, props),
    tag: el.tagName.toLowerCase(), disabled: !!el.disabled,
    title: el.getAttribute('title'), tip: el.getAttribute('data-tip'),
    aria: el.getAttribute('aria-label'), role: el.getAttribute('role'),
    ariaHidden: el.getAttribute('aria-hidden'), checked: el.checked === true,
  }));

  const out = {};
  out.viewport = { w: innerWidth, h: innerHeight };

  /* ① 카드 머리 / 설명 / 추가 버튼 */
  out.cardTitle = one('.card .card-hd .card-title', ['font-size', 'font-weight', 'color']);
  out.cardHd = one('.card .card-hd', ['padding', 'display', 'justify-content', 'align-items']);
  out.cardHint = one('.card .card-bd.pad > p.form-hint', ['font-size', 'color', 'margin-bottom']);
  const addBtn = Array.from(document.querySelectorAll('button')).find((b) => txt(b).includes('프로바이더 추가'));
  out.addButton = addBtn ? {
    text: txt(addBtn), cls: addBtn.className, rect: rect(addBtn),
    css: cs(addBtn, ['font-size', 'padding', 'background-color', 'color', 'border-radius', 'margin-left']),
    parentCls: addBtn.parentElement ? addBtn.parentElement.className : null,
    inCardHeader: !!addBtn.closest('.card-hd'),
    hasIcon: !!addBtn.querySelector('svg'),
  } : null;

  /* ① 목록 / 행 */
  out.list = one('.ai-list', ['display']);
  const listEl = document.querySelector('.ai-list');
  out.listOverflow = listEl ? { scrollW: listEl.scrollWidth, clientW: listEl.clientWidth } : null;
  out.docOverflow = { scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth };
  out.rows = many('.ai-row', ['height', 'padding', 'border-top-width', 'border-bottom-width',
    'border-top-color', 'border-radius', 'background-color', 'margin-bottom', 'gap',
    'display', 'align-items', 'opacity', 'box-shadow', 'margin-top', 'border-top-style']);
  out.rowChildren = Array.from(document.querySelectorAll('.ai-row')).map((row) => Array.from(row.children).map(
    (c) => c.tagName.toLowerCase() + '.' + (typeof c.className === 'string' ? c.className : '')));
  out.dragHandle = many('.ai-drag-handle', ['opacity', 'cursor', 'font-size', 'color']);
  out.rank = many('.ai-rank', ['width', 'height', 'border-radius', 'background-color', 'border-top-width', 'font-size', 'font-weight', 'color']);
  out.radios = many('.ai-row input[type=radio]', ['width', 'height']);
  out.names = many('.ai-name', ['font-size', 'font-weight', 'color']);
  out.kinds = many('.ai-kind', ['font-size', 'color']);
  out.iconGroups = Array.from(document.querySelectorAll('.ai-icons')).map((g) => ({
    css: cs(g, ['display', 'gap', 'align-items']),
    badges: Array.from(g.querySelectorAll('.ai-badge-icon')).map((b) => ({
      cls: b.className, tip: b.getAttribute('data-tip'), title: b.getAttribute('title'),
      aria: b.getAttribute('aria-label'), role: b.getAttribute('role'), ariaHidden: b.getAttribute('aria-hidden'),
      css: cs(b, ['width', 'height', 'border-radius', 'background-color', 'color', 'cursor']),
      svg: b.querySelector('svg') ? cs(b.querySelector('svg'), ['width', 'height']) : null,
      svgPaths: Array.from(b.querySelectorAll('path,circle,rect,line')).map((s) => s.tagName.toLowerCase()),
    })),
  }));
  out.spacer = one('.ai-row-spacer', ['flex-grow']);
  const handleSvg = document.querySelector('.ai-drag-handle svg');
  out.dragHandleSvg = handleSvg ? { rect: rect(handleSvg), css: cs(handleSvg, ['width', 'height']) } : null;
  const titleIcon = document.querySelector('.card-hd .card-title .app-icon');
  out.cardTitleIcon = titleIcon ? {
    rect: rect(titleIcon), css: cs(titleIcon, ['display', 'line-height', 'font-size', 'color']),
    path: titleIcon.querySelector('path') ? titleIcon.querySelector('path').getAttribute('d').slice(0, 60) : null,
  } : null;
  const nameEl = document.querySelector('.ai-name');
  out.nameMetrics = nameEl ? {
    exactWidth: nameEl.getBoundingClientRect().width,
    css: cs(nameEl, ['font-family', 'font-size', 'font-weight', 'letter-spacing', 'font-stretch']),
    bodyFont: getComputedStyle(document.body).fontFamily,
    text: JSON.stringify(nameEl.textContent),
  } : null;
  const cards = Array.from(document.querySelectorAll('.card'));
  out.cards = cards.map((c) => ({
    title: txt(c.querySelector('.card-title') || c),
    hint: txt(c.querySelector('.form-hint') || c.querySelector('.card-bd') || c).slice(0, 200),
    buttons: Array.from(c.querySelectorAll('button')).map((b) => txt(b) + '|' + b.className),
    labels: Array.from(c.querySelectorAll('.form-label')).map(txt),
    numbers: Array.from(c.querySelectorAll('input[type=number]')).map((i) => i.value),
  }));
  out.rowBtnGroups = Array.from(document.querySelectorAll('.ai-row-btns')).map((g) => ({
    css: cs(g, ['display', 'gap', 'align-items']),
    kids: Array.from(g.children).map((c) => ({
      tag: c.tagName.toLowerCase(), cls: c.className, title: c.getAttribute('title'),
      aria: c.getAttribute('aria-label'), disabled: !!c.disabled,
      css: cs(c, ['width', 'height', 'border-radius', 'border-top-width', 'border-top-color',
        'background-color', 'color', 'font-size', 'margin-left', 'margin-right', 'opacity']),
    })),
  }));
  out.rowTexts = Array.from(document.querySelectorAll('.ai-row')).map(txt);

  /* 모달 */
  out.modalCount = document.querySelectorAll('.modal-bg').length;
  out.modalBg = one('.modal-bg', ['background-color', 'align-items', 'justify-content', 'z-index']);
  const box = document.querySelector('.modal-bg .modal-box');
  out.modalBox = box ? {
    cls: box.className, rect: rect(box), role: box.getAttribute('role'),
    ariaModal: box.getAttribute('aria-modal'), ariaLabel: box.getAttribute('aria-label'),
    css: cs(box, ['width', 'border-radius', 'background-color']),
    dismiss: box.parentElement ? box.parentElement.getAttribute('data-dismiss') : null,
  } : null;
  out.modalTitle = one('.modal-bg .modal-title', ['font-size', 'font-weight']);
  out.modalClose = one('.modal-bg .modal-close', ['width', 'height', 'border-radius']);
  out.modalBd = one('.modal-bg .modal-bd', ['padding']);
  out.modalBdChildren = Array.from(document.querySelectorAll('.modal-bg .modal-bd > *')).map(
    (c) => c.tagName.toLowerCase() + '.' + (typeof c.className === 'string' ? c.className : ''));
  out.formRows = document.querySelectorAll('.modal-bg .form-row').length;
  out.formSections = document.querySelectorAll('.modal-bg .form-section').length;
  out.labels = Array.from(document.querySelectorAll('.modal-bg .form-label')).map(txt);
  out.fields = Array.from(document.querySelectorAll('.modal-bg .modal-bd input, .modal-bg .modal-bd select')).map((el) => ({
    tag: el.tagName.toLowerCase(), type: el.getAttribute('type'), cls: el.className,
    placeholder: el.getAttribute('placeholder'), value: el.value,
    checked: el.checked === true, rect: rect(el),
    options: el.tagName === 'SELECT' ? Array.from(el.options).map((o) => o.textContent.trim()) : null,
    css: cs(el, ['width', 'max-width', 'font-size']),
  }));
  out.hints = Array.from(document.querySelectorAll('.modal-bg .form-hint')).map((el) => ({
    text: txt(el), cls: el.className, css: cs(el, ['color', 'font-size']),
  }));
  out.codeBlocks = many('.modal-bg .code-block', ['background-color', 'color', 'padding', 'font-family', 'font-size', 'white-space', 'border-radius']);
  out.footer = one('.modal-bg .modal-ft', ['padding', 'display', 'justify-content', 'gap']);
  out.footerButtons = many('.modal-bg .modal-ft button', ['background-color', 'color', 'font-size', 'padding']);
  out.modalBodyText = document.querySelector('.modal-bg .modal-bd') ? txt(document.querySelector('.modal-bg .modal-bd')) : null;
  out.modalControls = Array.from(document.querySelectorAll('.modal-bg button, .modal-bg a')).map(txt);
  out.focused = document.activeElement ? (document.activeElement.tagName.toLowerCase() + '.' +
    (typeof document.activeElement.className === 'string' ? document.activeElement.className : '')) : null;
  out.focusedPlaceholder = document.activeElement ? document.activeElement.getAttribute('placeholder') : null;

  /* 실행 정책 카드(제품 계약: 독립 저장 버튼 유지) */
  out.primaryButtons = Array.from(document.querySelectorAll('button.btn-primary')).map((b) => ({
    text: txt(b), cls: b.className, inCardHeader: !!b.closest('.card-hd'),
    inModal: !!b.closest('.modal-bg'), dataTest: b.getAttribute('data-test'),
  }));
  out.pageButtons = Array.from(document.querySelectorAll('.settings-content button, .card button')).map(txt);
  return out;
})()`

/* ── CDP ──────────────────────────────────────────────────────────────────── */
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
  await call('Emulation.setDeviceMetricsOverride', {
    width, height: 900, deviceScaleFactor: 1, mobile: false,
  })
  return { call, ws, id: page.id }
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

async function gotoAndProbe(call, url, { waitHarness, label }) {
  await call('Page.navigate', { url })
  for (let i = 0; i < 200; i += 1) {
    const state = await evaluate(call, 'document.readyState')
    if (state === 'complete') break
    await delay(100)
  }
  if (waitHarness) {
    let ok = false
    for (let i = 0; i < 200; i += 1) {
      if (await evaluate(call, 'window.__harnessReady === true')) { ok = true; break }
      await delay(100)
    }
    if (!ok) {
      const err = await evaluate(call, 'document.body ? document.body.innerHTML.slice(0,400) : "no body"')
      throw new Error(`harness never became ready: ${url}\n${err}`)
    }
  } else {
    await delay(400)
  }
  const probe = await evaluate(call, PROBE)

  /* :hover 는 computed style 로 못 읽으므로 실제 마우스를 올려 다시 잰다 */
  const hoverTarget = await evaluate(call, `(() => {
    const row = document.querySelector('.ai-row');
    if (!row) return null;
    const r = row.getBoundingClientRect();
    return { x: Math.round(r.x + 40), y: Math.round(r.y + r.height / 2) };
  })()`)
  let hover = null
  if (hoverTarget) {
    await call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: hoverTarget.x, y: hoverTarget.y, button: 'none' })
    await delay(250)
    hover = await evaluate(call, `(() => {
      const row = document.querySelector('.ai-row');
      const handle = document.querySelector('.ai-drag-handle');
      const s = getComputedStyle(row);
      return {
        rowBoxShadow: s.boxShadow,
        handleOpacity: handle ? getComputedStyle(handle).opacity : null,
      };
    })()`)
    await call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 5, y: 5, button: 'none' })
    await delay(120)
  }

  const shot = await call('Page.captureScreenshot', { format: 'png' })
  if (shot.result?.data) {
    await writeFile(resolve(outDir, `${label}.png`), Buffer.from(shot.result.data, 'base64'))
  }
  return { url, probe, hover }
}

/* ── 실제 화면의 동작(닫힘 정책·Esc·초점 복귀)을 진짜 입력으로 확인 ──────────
   합성 dispatchEvent 를 modal-bg 에 직접 쏘면 실제 키보드와 달리 항상 핸들러에 닿는다.
   CDP Input 으로 진짜 클릭/키를 넣어야 "focus 가 dialog 밖이라 Esc 가 안 먹는" 결함이
   드러난다. */
async function behaviour(call) {
  const count = () => evaluate(call, 'document.querySelectorAll(".modal-bg").length')
  const active = () => evaluate(call, `(() => {
    const a = document.activeElement;
    if (!a) return null;
    return a.tagName.toLowerCase() + '|' + (a.getAttribute('aria-label') || a.getAttribute('title') ||
      a.getAttribute('placeholder') || (a.className || '')); })()`)
  const out = {}
  out.openBefore = await count()
  out.focusOnOpen = await active()

  /* backdrop 의 왼쪽 위 구석 — modal-box 바깥이다 */
  for (const type of ['mousePressed', 'mouseReleased']) {
    await call('Input.dispatchMouseEvent', { type, x: 20, y: 20, button: 'left', clickCount: 1 })
  }
  await delay(200)
  out.openAfterBackdropClick = await count()

  if (out.openAfterBackdropClick > 0) {
    for (const type of ['rawKeyDown', 'keyUp']) {
      await call('Input.dispatchKeyEvent', {
        type, key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27,
      })
    }
    await delay(250)
    out.openAfterRealEscape = await count()
    out.focusAfterClose = await active()
  }
  return out
}

/* ── vite dev + chrome ────────────────────────────────────────────────────── */
const vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--port', String(vitePort), '--strictPort'], {
  stdio: ['ignore', 'pipe', 'pipe'], env: process.env,
})
let viteLog = ''
vite.stdout.on('data', (b) => { viteLog += b.toString() })
vite.stderr.on('data', (b) => { viteLog += b.toString() })

const profile = resolve(scratch, 'chrome-0469-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const port = 9400 + Math.floor(Math.random() * 300)
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
      const r = await fetch(`http://localhost:${vitePort}/tests/browser/ai-settings-deck.0469.html`)
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

  const report = { generated_at: new Date().toISOString(), viewport: '1440x900 (list-narrow* 은 900x900)', views: {} }
  for (const view of VIEWS) {
    const entry = {}
    if (view.deck) {
      const { call, ws } = await openPage(port, view.width)
      entry.deck = await gotoAndProbe(call, view.deck, { waitHarness: false, label: `${view.key}.deck` })
      ws.close()
    }
    const { call, ws } = await openPage(port, view.width)
    entry.app = await gotoAndProbe(call, view.harness, { waitHarness: true, label: `${view.key}.app` })
    if (['edit', 'add', 'command', 'delete'].includes(view.key)) {
      entry.app.behaviour = await behaviour(call)
    }
    ws.close()
    report.views[view.key] = entry
    console.log(`[${view.key}] deck=${view.deck ? 'ok' : '-'} app=ok`)
  }

  await writeFile(resolve(outDir, 'measurements.json'), JSON.stringify(report, null, 2), 'utf8')
  console.log(`written: ${resolve(outDir, 'measurements.json')}`)
} catch (e) {
  console.error(e)
  exitCode = 1
} finally {
  browser.kill()
  vite.kill()
}
process.exit(exitCode)
