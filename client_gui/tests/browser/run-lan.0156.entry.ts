// flowgate.v02.0156 T1 W6 (R4-B) / W7 — page driven by server/tests/flowgate/lan_run_harness_0156.py.
//
// The real run path of the Designer, nothing stubbed in it: createFlowRunControl -> runCommandStore (default wiring: on
// this insecure origin the cross-tab lease and the default identity source) -> serverFlowGraphSource -> flowRunApi ->
// @shared/api (axios) -> POST /api/v1/flows/{id}/runs of a real FastAPI graph run app on real SQLite.
// Served at http://<LAN-IP>:<port> — an insecure context like the deployed http://192.168.0.252:8080 — and at
// http://127.0.0.1:<port> for the contrast.
//
// Chrome refuses DevTools remote debugging inside the Self-check sandbox, so the page drives itself: it asks the harness
// server (?harness=) for its next step, meets the other tab at a barrier for a simultaneous click, reports what it saw,
// and reloads itself for F5. `lose=1`: the first start answer that reaches this tab is turned into "no response" after
// the server committed it (the browser-side counterpart of a dropped response).
// Query: flow, tab, phase (w7 | click | f5), iter, lose, mode, harness, user, inject=uuid (pre-fix wiring: no lease, a v4
// source injected to keep R1 apart), spawn (a URL this tab opens as the second tab).
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { reactive, watch } from 'vue'
import api from '@shared/api'
import i18n from '@shared/i18n'
import { randomUuid } from '@shared/utils/uuid'
import { registerAppMessages } from '@/app/i18n'
import { startFlowRun, fetchCurrentFlowRun, pauseFlowRun, resumeFlowRun, stopFlowRun, lookupFlowRunCommand, voidFlowRunCommand } from '@/features/project/flowRunApi'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'
import { createFlowRunControl, createFlowRunState, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { createRunCommandStore } from '@/features/designer/center/run/runCommandStore'
import { recentRunFailureDiagnostics } from '@/features/designer/center/run/runFailureDiagnostic'
import { runErrorText } from '@/features/designer/shell/runFailureToast'

registerAppMessages(i18n)
i18n.global.locale.value = 'ko'
const query = new URLSearchParams(location.search)
const flow = query.get('flow') ?? 'flowgate.v02.0091'
const user = query.get('user') ?? 'u_kim'
const tab = query.get('tab') ?? 'A'
const phase = query.get('phase') ?? 'manual'
const harness = query.get('harness')
const lose = query.get('lose') === '1'
const projectId = 'flowgate'
// The harness server's auth stub accepts "Bearer user:<id>" (the 0149 server suites' convention).
;(window as unknown as { __accessToken__: string }).__accessToken__ = `user:${user}`

const startPath = /\/api\/v1\/flows\/[^/]+\/runs$/
const starts: { id: string | null; status: number | null; dropped: boolean }[] = []
const bodyId = (c: InternalAxiosRequestConfig) => {
  try { return (typeof c.data === 'string' ? JSON.parse(c.data) : c.data)?.command_request_id ?? null } catch { return null }
}
api.interceptors.request.use((c) => { c.headers.set('x-harness-tab', tab); c.headers.set('x-harness-phase', phase); return c })
let dropped = false
api.interceptors.response.use((response) => {
  const c = response.config
  if (c.method === 'post' && startPath.test(c.url ?? '')) {
    if (lose && !dropped) {
      dropped = true
      starts.push({ id: bodyId(c), status: response.status, dropped: true })
      throw new AxiosError('Network Error', AxiosError.ERR_NETWORK, c, response.request)
    }
    starts.push({ id: bodyId(c), status: response.status, dropped: false })
  }
  return response
}, (error: AxiosError) => {
  const c = error.config
  if (c && c.method === 'post' && startPath.test(c.url ?? '')) starts.push({ id: bodyId(c), status: error.response?.status ?? null, dropped: false })
  return Promise.reject(error)
})

const states = reactive<Record<string, FlowRunState>>({ [flow]: createFlowRunState() })
const confirmed: string[] = []
watch(() => states[flow]?.confirmedCommand, (c) => { if (c) confirmed.push(c.commandRequestId) }, { flush: 'sync' })
const source = createServerFlowGraphSource({ loadFlowCanvas: async () => { throw new Error('not used') }, saveFlowCanvasLayout: async () => ({ ignored: [] }),
  loadFlowRun: fetchCurrentFlowRun, startFlowRun, pauseFlowRun, stopFlowRun, resumeFlowRun, lookupFlowRunCommand, voidFlowRunCommand } as never)
const control = createFlowRunControl({
  source, projectId: () => projectId, isOpen: (p, f) => p === projectId && f === flow, username: () => user,
  ...(query.get('inject') === 'uuid' ? { commandStore: createRunCommandStore({ uuid: randomUuid }) } : {}),
  prepare: async () => true, refresh: () => {}, flowLabel: () => 'LAN harness',
}, states)

const t = (k: string, v?: Record<string, string>) => (v ? i18n.global.t(k, v) : i18n.global.t(k))
const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms))
const clicks: { mode: string; ok: boolean | null; error: unknown; toast: string | null }[] = []

function storageDump() {
  const out: Record<string, string> = {}
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)!
    if (key.startsWith('flowgate.runCommand')) out[key] = localStorage.getItem(key)!
  }
  return out
}
const env = () => ({ origin: location.origin, isSecureContext: window.isSecureContext,
  randomUUID: typeof crypto !== 'undefined' ? typeof crypto.randomUUID : 'no-crypto',
  locks: typeof (navigator as Navigator & { locks?: unknown }).locks, indexedDB: typeof globalThis.indexedDB, inject: query.get('inject') })
const snapshot = () => ({ clicks, starts, confirmed: [...confirmed], storage: storageDump(),
  run: states[flow].run ? JSON.parse(JSON.stringify(states[flow].run)) : null, readState: states[flow].readState,
  recovery: states[flow].commandRecovery?.key ?? null, error: states[flow].error ? JSON.parse(JSON.stringify(states[flow].error)) : null,
  diagnostics: recentRunFailureDiagnostics().slice(-3) })

async function click(mode: 'next' | 'all') {
  const entry = { mode, ok: null as boolean | null, error: null as unknown, toast: null as string | null }
  clicks.push(entry)
  entry.ok = await control.run(flow, mode)
  const e = states[flow].error
  entry.error = e ? JSON.parse(JSON.stringify(e)) : null
  entry.toast = e ? runErrorText(e, t, (k) => i18n.global.te(k)) : null
  return entry
}

Object.assign(window as unknown as Record<string, unknown>, { __fg: { env, click, snapshot } })

async function call(path: string, body?: unknown) {
  const response = await fetch(`${harness}${path}`, body === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
  return response.json()
}
const report = (kind: string, extra: Record<string, unknown> = {}) =>
  call('/harness/report', { kind, tab, flow, phase, iter: query.get('iter'), lose, env: env(), ...extra })
async function next() {
  const step = await call(`/harness/next?tab=${encodeURIComponent(tab)}`)
  if (step.url) location.replace(step.url)
  else await report('done')
}
async function main() {
  // Opening (and every F5) reads the run, which looks pending identities up — never resends (0149).
  control.read(flow)
  for (let i = 0; i < 200 && !['read', 'failed'].includes(states[flow].readState); i++) await sleep(50)
  if (!harness) return
  const spawn = query.get('spawn')
  if (spawn) window.open(spawn, '_blank', 'noopener')
  if (phase === 'w7') {
    const storageBefore = storageDump()
    const result = await click((query.get('mode') ?? 'next') as 'next' | 'all')
    await sleep(500)
    await report('w7', { storageBefore, result, after: snapshot() })
    return next()
  }
  if (phase === 'click') {
    let at: number | null = null
    for (let i = 0; i < 600 && at === null; i++) {
      at = (await call(`/harness/barrier?iter=${query.get('iter')}&tab=${encodeURIComponent(tab)}`)).at
      if (at === null) await sleep(50)
    }
    if (at === null) { await report('barrier_timeout'); return next() }
    await sleep(Math.max(0, at - Date.now()))
    await click('next')
    await sleep(600)
    await report('click', { after: snapshot() })
    const reload = new URL(location.href)
    reload.searchParams.set('phase', 'f5')
    reload.searchParams.delete('spawn')
    location.replace(reload.toString()) // F5: a new page instance on the same storage
    return
  }
  if (phase === 'f5') {
    await sleep(2500)
    await report('f5', { after: snapshot() })
    return next()
  }
}
void main().catch((error) => { void report('page_error', { name: (error as Error)?.name ?? null }) })
