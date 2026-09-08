/**
 * flowgate.default.0481 T0010 rev4 — "attempt_id must be a UUID" (2026-09-08 12:29).
 *
 * Serves the real GitMergeReviewDialog from Vite bound to the LAN interface and drives a real
 * headless Chrome at the deploy's own kind of origin (http://<LAN-IP>:PORT — an INSECURE
 * context, exactly like http://192.168.0.252:8080). There `crypto.randomUUID` does not exist,
 * which is why every [승인] answered 400 invalid_attempt_id. The approve request is captured at
 * the network layer and its attempt_id checked against the server's own regex.
 *
 *   cd client && node tests/browser/merge-approve-uuid.0481.mjs
 *
 * Exit code 0 = the browser sent a server-valid attempt_id from an insecure origin.
 * LAN_HOST / CHROME_PATH override the origin and the browser binary.
 */
import { mkdir, rm, writeFile } from 'node:fs/promises'
import { networkInterfaces } from 'node:os'
import { resolve } from 'node:path'
import { spawn } from 'node:child_process'

/** The exact regex server/modules/flow_gate/api/v1/git_routes.py validates against. */
const SERVER_UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

const scratch = process.env.FLOWGATE_SCRATCH
if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const delay = (ms) => new Promise((done) => setTimeout(done, ms))

/** A routable IPv4 of this host: 127.0.0.1 is a *secure* context and would hide the bug. */
function lanHost() {
  if (process.env.LAN_HOST) return process.env.LAN_HOST
  for (const addrs of Object.values(networkInterfaces())) {
    for (const a of addrs ?? []) {
      if (a.family === 'IPv4' && !a.internal) return a.address
    }
  }
  throw new Error('no non-loopback IPv4 interface — pass LAN_HOST=')
}

const host = lanHost()
const vitePort = 5100 + Math.floor(Math.random() * 300)
const cdpPort = 9300 + Math.floor(Math.random() * 200)
const pageUrl = `http://${host}:${vitePort}/tests/browser/merge-approve-uuid.0481.html`

const REVIEW = {
  ok: true,
  result: {
    group_id: 'test2.default.0009', merge_id: 4, review_state: 'resolved_pending_review',
    review_fingerprint: '20e8fbddb5e42b734f16081ab263d042ca6bd98d20edbeb9037c61bd720ee805',
    instruction_generation: 0, base_head: 'e25a7e8', merge_head: 'ee2709f', snapshot_tree: 'ad32e91',
    changes: [{ path: 'src/data/tasks.ts', status: 'M', old_path: null }],
    conflict_origins: [], conversation: [], held_test_operations: [], pending_conversation: null,
    resolver_provider: 'Claude Haiku 4.5', auto_authority: false, reconciliation_kind: null,
    last_error: null, can_approve: true, can_reject: true, can_send: true,
  },
}

const DIFF = {
  ok: true,
  data: {
    group_id: 'test2.default.0009', merge_id: 4, path: 'src/data/tasks.ts', status: 'M',
    old: { exists: true, binary: false, truncated: false, size: 2, content: 'a\n' },
    new: { exists: true, binary: false, truncated: false, size: 2, content: 'b\n' },
  },
}

const vite = spawn(process.execPath, [
  'node_modules/vite/bin/vite.js', '--host', host, '--port', String(vitePort), '--strictPort',
], { stdio: 'ignore', env: process.env })

const profile = resolve(scratch, 'chrome-approve-uuid-profile')
await rm(profile, { recursive: true, force: true })
await mkdir(profile, { recursive: true })
const browser = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--remote-allow-origins=*',
  `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })

function connect(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  let id = 0
  const pending = new Map()
  const listeners = []
  ws.addEventListener('message', (event) => {
    const msg = JSON.parse(event.data)
    if (msg.id != null) {
      const done = pending.get(msg.id)
      if (done) { pending.delete(msg.id); done(msg) }
    } else {
      for (const fn of listeners) fn(msg)
    }
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
  return { ws, ready, call, on: (fn) => listeners.push(fn) }
}

async function newPage() {
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${cdpPort}/json/new?about:blank`, { method: 'PUT' })
      if (r.ok) return await r.json()
    } catch {}
    await delay(100)
  }
  throw new Error('Chrome connection timeout')
}

async function waitForVite() {
  for (let i = 0; i < 150; i++) {
    try {
      const r = await fetch(pageUrl)
      if (r.ok) return
    } catch {}
    await delay(200)
  }
  throw new Error(`vite did not answer at ${pageUrl}`)
}

const b64 = (obj) => Buffer.from(JSON.stringify(obj), 'utf8').toString('base64')
// The app's axios base is an absolute dev API origin, so every intercepted answer is
// cross-origin and must carry CORS headers — including a real preflight reply.
const cors = (origin) => [
  { name: 'access-control-allow-origin', value: origin },
  { name: 'access-control-allow-headers', value: '*' },
  { name: 'access-control-allow-methods', value: 'GET,POST,OPTIONS' },
  { name: 'access-control-allow-credentials', value: 'true' },
]
const jsonHeaders = (origin) => [{ name: 'content-type', value: 'application/json' }, ...cors(origin)]
const failures = []
let record = {}

try {
  await waitForVite()
  const page = await newPage()
  const { ws, ready, call, on } = connect(page)
  await ready
  await call('Runtime.enable')
  await call('Page.enable')
  await call('Fetch.enable', { patterns: [{ urlPattern: '*/api/v1/groups/*', requestStage: 'Request' }] })

  let approveBody = null
  on(async (msg) => {
    if (msg.method !== 'Fetch.requestPaused') return
    const { requestId, request } = msg.params
    const url = request.url
    const origin = `http://${host}:${vitePort}`
    if (request.method === 'OPTIONS') {
      await call('Fetch.fulfillRequest', { requestId, responseCode: 204, responseHeaders: cors(origin) })
      return
    }
    if (url.endsWith('/approve')) {
      approveBody = request.postData ?? null
      const merged = { ok: true, result: { status: 'merged', review_state: 'completed', merge_commit: '091479c' } }
      await call('Fetch.fulfillRequest', { requestId, responseCode: 200, responseHeaders: jsonHeaders(origin), body: b64(merged) })
      return
    }
    if (url.includes('/review-diff')) {
      await call('Fetch.fulfillRequest', { requestId, responseCode: 200, responseHeaders: jsonHeaders(origin), body: b64(DIFF) })
      return
    }
    if (url.includes('/review')) {
      await call('Fetch.fulfillRequest', { requestId, responseCode: 200, responseHeaders: jsonHeaders(origin), body: b64(REVIEW) })
      return
    }
    await call('Fetch.continueRequest', { requestId })
  })

  // Surface transport-level failures (a missed CORS preflight looks exactly like "the
  // dialog never rendered" otherwise).
  await call('Network.enable')
  on((m) => {
    if (m.method === 'Network.loadingFailed') console.error('network failed:', JSON.stringify(m.params))
  })
  on((m) => {
    if (m.method === 'Runtime.exceptionThrown') console.error('page exception:', JSON.stringify(m.params).slice(0, 800))
  })
  await call('Runtime.evaluate', { expression: `location.href = ${JSON.stringify(pageUrl)}` })
  await delay(5000)

  const env = await call('Runtime.evaluate', {
    returnByValue: true,
    expression: `({ origin: location.origin, secure: window.isSecureContext,
                    randomUUID: typeof crypto !== 'undefined' && typeof crypto.randomUUID,
                    buttons: [...document.querySelectorAll('.gmr-ft-actions button')].map((b) => b.textContent.trim()),
                    state: document.querySelector('.gmr-state')?.textContent?.trim() ?? null })`,
  })
  const info = env.result.result.value
  if (info.secure) failures.push(`the page is a SECURE context (${info.origin}) — this harness must run on an insecure LAN origin`)
  if (info.randomUUID !== 'undefined') failures.push(`crypto.randomUUID is ${info.randomUUID} here — the broken branch is not the one being exercised`)
  if (!info.buttons || info.buttons.length < 2) failures.push(`the dialog did not render its action bar: ${JSON.stringify(info)}`)

  if (info.buttons && info.buttons.length >= 2) {
    // A real click on the real [승인] button.
    await call('Runtime.evaluate', {
      awaitPromise: true,
      expression: `(async () => { document.querySelectorAll('.gmr-ft-actions button')[1].click(); await new Promise((r) => setTimeout(r, 1500)) })()`,
    })
    await delay(600)
  }
  ws.close()

  if (!approveBody) failures.push('the browser never sent the approve request')
  else {
    const parsed = JSON.parse(approveBody)
    info.attempt_id = parsed.attempt_id
    info.review_fingerprint = parsed.review_fingerprint
    if (!SERVER_UUID_RE.test(parsed.attempt_id ?? '')) {
      failures.push(`attempt_id is not a UUID: ${JSON.stringify(parsed.attempt_id)}`)
    }
  }
  record = { pageUrl, ...info, failures }
  await writeFile(resolve(scratch, 'merge-approve-uuid.0481.json'), JSON.stringify(record, null, 2), 'utf8')
  console.log(JSON.stringify(record, null, 2))
} finally {
  if (browser.exitCode === null) browser.kill()
  if (vite.exitCode === null) vite.kill()
  await delay(400)
  await rm(profile, { recursive: true, force: true })
}

if (failures.length) {
  console.error('APPROVE ATTEMPT_ID CHECK FAILED:\n' + failures.join('\n'))
  process.exit(1)
}
console.log('approve attempt_id ok — a UUID left an insecure-origin browser')
