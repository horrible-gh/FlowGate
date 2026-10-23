import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi, type MockInstance } from 'vitest'
import { defineComponent, h } from 'vue'
import { RouterView } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import router from '@/app/router/index'
import { setCurrentUser } from '@/core/auth/currentUser'
import SystemAgentSettingsView from '@/features/settings/views/SystemAgentSettingsView.vue'

// flowgate.v02.0076 T#4 (D §2.6, §5.2 V16·V17): 시스템 › 시스템 Agent on the real /app router
// (installAuthGuard + installSettingsAccessGuard + SettingsLayout) with the real view. Only the
// transport is replaced: @shared/api's request helpers talk to an in-memory server that keeps the
// Agents the way services/agent_registry.py does — credential history, one pending enrollment per
// Agent, detail() for every answer (online only with enabled + an active credential + compatible
// protocol + a fresh heartbeat; local = not_applicable), the route models (extra fields → 422, the
// Empty body required, location immutable) and the route's error envelope
// {"ok":false,"error":{"code"}}. Every call is recorded with its body, so "요청 0건", "1건", "바꾼
// 필드만" and "재조회값" are read off the request list and the server state. The real routers /
// auth / RBAC / store are exercised separately by server/tests/test_system_agents_0076.py.

type Caps = { ai_cli: boolean; ai_api: boolean; storage: boolean }
interface Rec {
  agent_id: string
  name: string
  enabled: boolean
  location: 'local' | 'remote'
  connection_mode: 'agent_pull' | null
  local_executable_path: string | null
  configured: Caps
  reported: Caps | null
  credentialActive: boolean
  credentialHistory: boolean
  pending: { token: string; expires_at: string } | null
  fresh: boolean
  last_seen_at: string | null
  agent_version: string | null
  protocol_version: number | null
  protocol_compatibility: 'unknown' | 'compatible' | 'incompatible'
  last_rejected_protocol_version: number | null
  last_protocol_rejection_at: string | null
  os: string | null
  architecture: string | null
}

const net = vi.hoisted(() => ({
  requests: [] as string[],
  bodies: [] as { call: string; body: unknown }[],
  agents: [] as Rec[],
  seq: 0,
  tokens: 0,
  /** Answer the next request matching `call` (method + path regexp) with this error. */
  failNext: null as null | { call: RegExp; status: number; data: unknown },
  /** Answer GET /system/agents with this status. */
  failListGet: 0,
  /** Hold the next write until `release()`; lets a test click again while it is out. */
  holdNext: false,
  /** Hold the next GET /system/agents until `release()`. */
  holdList: false,
  release: null as null | (() => void),
}))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const fail = (status: number, data: unknown) => Object.assign(new Error(`HTTP ${status}`), { response: { status, data } })
  const agentError = (status: number, code: string, message: string, details: Record<string, unknown> = {}) =>
    fail(status, { ok: false, error: { code, message, details } })
  const invalid = (field: string, reason: string) => agentError(422, 'validation_failed', 'Invalid Agent field', { field, reason })
  const CAP_KEYS = ['ai_cli', 'ai_api', 'storage']

  const detail = (r: Rec) => ({
    agent_id: r.agent_id,
    name: r.name,
    enabled: r.enabled,
    location: r.location,
    connection_mode: r.connection_mode,
    local_executable_path: r.local_executable_path,
    configured_capabilities: { ...r.configured },
    reported_capabilities: r.reported ? { ...r.reported } : { ai_cli: null, ai_api: null, storage: null },
    effective_capabilities: {
      ai_cli: r.configured.ai_cli && r.reported?.ai_cli === true,
      ai_api: r.configured.ai_api && r.reported?.ai_api === true,
      storage: r.configured.storage && r.reported?.storage === true,
    },
    registration_status: r.credentialActive ? 'registered' : r.credentialHistory ? 'revoked' : 'unregistered',
    credential_status: r.credentialActive ? 'active' : r.credentialHistory ? 'revoked' : 'none',
    has_registration_history: r.credentialHistory,
    enrollment_status: r.pending ? 'pending' : 'none',
    enrollment_expires_at: r.pending?.expires_at ?? null,
    online_status: r.location === 'local' ? 'not_applicable'
      : r.enabled && r.credentialActive && r.protocol_compatibility === 'compatible' && r.fresh ? 'online' : 'offline',
    last_seen_at: r.last_seen_at,
    agent_version: r.agent_version,
    protocol_version: r.protocol_version,
    protocol_compatibility: r.protocol_compatibility,
    last_rejected_protocol_version: r.last_rejected_protocol_version,
    last_protocol_rejection_at: r.last_protocol_rejection_at,
    os: r.os,
    architecture: r.architecture,
  })

  const find = (id: string) => {
    const r = net.agents.find((a) => a.agent_id === id)
    if (!r) throw agentError(404, 'agent_not_found', 'Agent not found')
    return r
  }

  const caps = (value: unknown): Caps => {
    const c = value as Record<string, unknown>
    if (!c || typeof c !== 'object' || Object.keys(c).sort().join() !== [...CAP_KEYS].sort().join() || !CAP_KEYS.every((k) => typeof c[k] === 'boolean')) {
      throw invalid('configured_capabilities', 'invalid')
    }
    return { ai_cli: c.ai_cli as boolean, ai_api: c.ai_api as boolean, storage: c.storage as boolean }
  }

  /** class Empty(Strict): pass — a body is required and it carries nothing. */
  const empty = (body: unknown) => {
    if (body === undefined) throw invalid('body', 'missing')
    if (!body || typeof body !== 'object' || Object.keys(body).length) throw invalid('body', 'extra_forbidden')
  }

  function create(body: Record<string, unknown>) {
    const fields = ['name', 'enabled', 'location', 'connection_mode', 'local_executable_path', 'configured_capabilities']
    const extra = Object.keys(body).filter((k) => !fields.includes(k))
    if (extra.length) throw invalid(extra[0], 'extra_forbidden')
    const missing = fields.filter((k) => !(k in body))
    if (missing.length) throw invalid(missing[0], 'missing')
    const name = String(body.name)
    if (!name.trim() || name.length > 100) throw invalid('name', 'invalid')
    const location = body.location as Rec['location']
    if ((location === 'remote' && (body.connection_mode !== 'agent_pull' || body.local_executable_path !== null))
      || (location === 'local' && body.connection_mode !== null)) throw invalid('body', 'invalid Agent field combination')
    net.seq += 1
    const r: Rec = {
      agent_id: `agt_new${net.seq}`, name: name.trim(), enabled: Boolean(body.enabled), location,
      connection_mode: body.connection_mode as Rec['connection_mode'], local_executable_path: body.local_executable_path as string | null,
      configured: caps(body.configured_capabilities), reported: null, credentialActive: false, credentialHistory: false,
      pending: null, fresh: false, last_seen_at: null, agent_version: null, protocol_version: null, protocol_compatibility: 'unknown',
      last_rejected_protocol_version: null, last_protocol_rejection_at: null, os: null, architecture: null,
    }
    net.agents.push(r)
    net.agents.sort((a, b) => a.agent_id.localeCompare(b.agent_id))
    return { status: 201, data: detail(r) }
  }

  function patch(id: string, body: Record<string, unknown>) {
    const fields = ['name', 'enabled', 'location', 'connection_mode', 'local_executable_path', 'configured_capabilities']
    const extra = Object.keys(body).filter((k) => !fields.includes(k))
    if (extra.length) throw invalid(extra[0], 'extra_forbidden')
    if (!Object.keys(body).length) throw invalid('body', 'required')
    if ('location' in body) throw invalid('location', 'immutable')
    const r = find(id)
    const mode = 'connection_mode' in body ? body.connection_mode : r.connection_mode
    const path = 'local_executable_path' in body ? body.local_executable_path : r.local_executable_path
    if ((r.location === 'remote' && (mode !== 'agent_pull' || path !== null)) || (r.location === 'local' && mode !== null)) {
      throw invalid('connection_mode', 'invalid_combination')
    }
    if ('name' in body) r.name = String(body.name).trim()
    if ('enabled' in body) r.enabled = Boolean(body.enabled)
    r.connection_mode = mode as Rec['connection_mode']
    r.local_executable_path = path as string | null
    if ('configured_capabilities' in body) r.configured = caps(body.configured_capabilities)
    return { data: detail(r) }
  }

  const handle = async (method: string, path: string, body?: unknown) => {
    const call = `${method} ${path}`
    net.requests.push(call)
    if (body !== undefined) net.bodies.push({ call, body })
    if (method !== 'GET' && net.holdNext) {
      net.holdNext = false
      await new Promise<void>((resolve) => { net.release = resolve })
    }
    if (call === 'GET /api/v1/system/agents' && net.holdList) {
      net.holdList = false
      await new Promise<void>((resolve) => { net.release = resolve })
    }
    if (net.failNext && net.failNext.call.test(call)) {
      const f = net.failNext
      net.failNext = null
      throw fail(f.status, f.data)
    }
    if (call === 'GET /api/v1/system/agents') {
      if (net.failListGet) throw fail(net.failListGet, { detail: net.failListGet === 401 ? 'Not authenticated' : net.failListGet === 403 ? 'Forbidden' : 'boom' })
      return { data: { agents: net.agents.map(detail) } }
    }
    if (call === 'POST /api/v1/system/agents') return create(body as Record<string, unknown>)
    // 사용자 (moving away and back in the tests below); its own behaviour is pinned by systemSettings.0076.
    if (call === 'GET /api/v1/system/users') {
      return { data: { users: [], workspaces: [], summary: { total: 0, active: 0, invited: 0, disabled: 0, admins: 0, tfa_missing: 0, tfa_enabled: 0, no_workspace: 0 } } }
    }
    let m = /^(GET|PATCH|DELETE) \/api\/v1\/system\/agents\/([^/]+)$/.exec(call)
    if (m) {
      const id = decodeURIComponent(m[2])
      if (m[1] === 'GET') return { data: detail(find(id)) }
      if (m[1] === 'PATCH') return patch(id, body as Record<string, unknown>)
      const r = find(id)
      if (r.credentialHistory) throw agentError(409, 'agent_has_registration_history', 'Registered Agent cannot be deleted')
      net.agents = net.agents.filter((a) => a !== r)
      return { status: 204, data: '' }
    }
    m = /^POST \/api\/v1\/system\/agents\/([^/]+)\/(enrollments|credential\/revoke)$/.exec(call)
    if (m) {
      empty(body)
      const r = find(decodeURIComponent(m[1]))
      if (m[2] === 'enrollments') {
        if (r.location !== 'remote' || !r.enabled) throw agentError(409, 'enrollment_not_allowed', 'Enrollment requires an enabled Remote Agent')
        net.tokens += 1
        r.pending = { token: `enr_secret_${net.tokens}_xyz`, expires_at: `2026-10-20T05:${String(10 + net.tokens).padStart(2, '0')}:00Z` }
        return { status: 201, data: { agent_id: r.agent_id, enrollment_token: r.pending.token, expires_at: r.pending.expires_at } }
      }
      if (r.credentialActive) r.credentialHistory = true
      r.credentialActive = false
      r.pending = null
      return { data: detail(r) }
    }
    throw fail(500, { detail: `unexpected request in test: ${call}` })
  }
  return {
    ...actual,
    getRequest: vi.fn((path: string) => handle('GET', path)),
    postRequest: vi.fn((path: string, data: unknown) => handle('POST', path, data)),
    patchRequest: vi.fn((path: string, data: unknown) => handle('PATCH', path, data)),
    putRequest: vi.fn((path: string, data: unknown) => handle('PUT', path, data)),
    deleteRequest: vi.fn((path: string) => handle('DELETE', path)),
  }
})

registerAppMessages(i18n)

const Host = defineComponent({ render: () => h(RouterView) })
const ADMIN = { username: 'admin', email: 'admin@flowgate.local', isAdmin: true }
const CAPS_OFF = { ai_cli: false, ai_api: false, storage: false }

function rec(id: string, name: string, extra: Partial<Rec> = {}): Rec {
  return {
    agent_id: id, name, enabled: true, location: 'remote', connection_mode: 'agent_pull', local_executable_path: null,
    configured: { ai_cli: true, ai_api: false, storage: true }, reported: null, credentialActive: false, credentialHistory: false,
    pending: null, fresh: false, last_seen_at: null, agent_version: null, protocol_version: null, protocol_compatibility: 'unknown',
    last_rejected_protocol_version: null, last_protocol_rejection_at: null, os: null, architecture: null, ...extra,
  }
}

const registered = (extra: Partial<Rec> = {}): Partial<Rec> => ({
  credentialActive: true, credentialHistory: true, reported: { ai_cli: true, ai_api: true, storage: false },
  last_seen_at: '2026-10-20T04:00:00Z', agent_version: '1.4.2', protocol_version: 1, protocol_compatibility: 'compatible',
  os: 'linux', architecture: 'amd64', ...extra,
})

/** The V16 status fixtures: online, offline, incompatible, revoked, a never-registered remote and a local. */
function fixtures(): Rec[] {
  return [
    rec('agt_a_online', 'build-online', registered({ fresh: true })),
    rec('agt_b_offline', 'build-offline', registered({ fresh: false, last_seen_at: '2026-10-19T22:00:00Z' })),
    rec('agt_c_incompat', 'build-incompatible', registered({
      fresh: true, protocol_compatibility: 'incompatible', last_rejected_protocol_version: 2, last_protocol_rejection_at: '2026-10-20T04:01:00Z',
    })),
    rec('agt_d_revoked', 'build-revoked', registered({ credentialActive: false, fresh: false })),
    rec('agt_e_new', 'fresh-remote', { configured: { ...CAPS_OFF } }),
    rec('agt_f_local', 'local-runner', { location: 'local', connection_mode: null, local_executable_path: '/opt/flowgate-agent', configured: { ai_cli: true, ai_api: false, storage: false } }),
  ]
}

let wrapper: VueWrapper | null = null
let consoleSpies: MockInstance[] = []

async function open(url: string): Promise<VueWrapper> {
  window.history.replaceState({}, '', `/app${url}`)
  await router.replace(url)
  wrapper = mount(Host, { global: { plugins: [i18n, router], stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}

async function click(w: VueWrapper, selector: string) {
  await w.get(selector).trigger('click')
  await flushPromises()
}

async function type(w: VueWrapper, selector: string, value: string) {
  await w.get(selector).setValue(value)
  await flushPromises()
}

async function pick(w: VueWrapper, id: string) {
  await click(w, `.agent-row[data-agent="${id}"]`)
}

const writes = () => net.requests.filter((r) => !r.startsWith('GET '))
const bodiesOf = (pattern: RegExp) => net.bodies.filter((b) => pattern.test(b.call)).map((b) => b.body)
const server = (id: string) => net.agents.find((a) => a.agent_id === id)
const field = (w: VueWrapper, name: string) => w.get(`[data-testid="agent-status"] [data-field="${name}"]`).text().replace(/\s+/g, ' ')
const toastText = (w: VueWrapper) => w.findAll('.ui-toast-host').map((t) => t.text()).join(' ')

async function waitForPath(target: string): Promise<void> {
  for (let i = 0; i < 100 && router.currentRoute.value.path !== target; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10))
    await flushPromises()
  }
  await flushPromises()
}

/**
 * The view's own state, read through the instance (it stays readable after unmount): an answer
 * that arrives after the screen is gone must not put anything back into it.
 */
function viewState(w: VueWrapper): Record<string, unknown> {
  return (w.findComponent(SystemAgentSettingsView).vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState
}

const editName = (w: VueWrapper) => (w.get('[data-testid="edit-name"]').element as HTMLInputElement).value
const capChecked = (w: VueWrapper, cap: string) => (w.get(`[data-edit-cap="${cap}"]`).element as HTMLInputElement).checked

/** The token may exist in exactly one place: the one-time box, never storage / URL / log / toast. */
function expectTokenOnlyInBox(w: VueWrapper, token: string, shown: boolean) {
  const html = w.html()
  expect(html.split(token).length - 1).toBe(shown ? 1 : 0)
  if (shown) expect(w.get('[data-testid="enrollment-token"]').text()).toBe(token)
  expect(toastText(w)).not.toContain(token)
  expect(window.location.href).not.toContain(token)
  for (const store of [window.sessionStorage, window.localStorage]) {
    for (let i = 0; i < store.length; i += 1) expect(store.getItem(store.key(i)!) ?? '').not.toContain(token)
  }
  for (const spy of consoleSpies) {
    for (const args of spy.mock.calls) expect(JSON.stringify(args)).not.toContain(token)
  }
}

beforeEach(async () => {
  i18n.global.locale.value = 'ko'
  window.__accessToken__ = 'token-agents-0076'
  net.agents = fixtures()
  net.seq = 0
  net.tokens = 0
  net.failNext = null
  net.failListGet = 0
  net.holdNext = false
  net.holdList = false
  net.release = null
  setCurrentUser(ADMIN)
  window.history.replaceState({}, '', '/app/')
  await router.replace('/')
  net.requests.length = 0
  net.bodies.length = 0
  consoleSpies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m))
})

afterEach(() => {
  // D §2.6 / T#4 지시 1·6: only the /system/agents management family, never the Agent runtime
  // (/agent/enrollments, /agent/heartbeat), no other registry or personal-Agent API, and no PATCH
  // ever carries location / connection_mode.
  for (const call of net.requests.filter((r) => r !== 'GET /api/v1/system/users')) {
    expect(call).toMatch(/^(GET|POST|PATCH|DELETE) \/api\/v1\/system\/agents(\/[^/]+(\/enrollments|\/credential\/revoke)?)?$/)
  }
  expect(net.requests.some((r) => r.includes('/api/v1/agent/'))).toBe(false)
  for (const body of bodiesOf(/^PATCH /)) {
    expect(Object.keys(body as object)).not.toContain('location')
    expect(Object.keys(body as object)).not.toContain('connection_mode')
  }
  for (const body of bodiesOf(/\/(enrollments|credential\/revoke)$/)) expect(body).toEqual({})
  wrapper?.unmount()
  wrapper = null
  consoleSpies.forEach((spy) => spy.mockRestore())
  setCurrentUser(null)
  delete window.__accessToken__
})

describe('V16 Agent 등록·상태 — list, status display, register, enable toggle', () => {
  it('entry: one GET, the six fixtures with online / offline / incompatible / revoked / unregistered / local told apart', async () => {
    const w = await open('/settings/system/agents')
    expect(net.requests).toEqual(['GET /api/v1/system/agents'])
    expect(w.findAll('.subnav [data-subnav]').map((i) => i.attributes('data-subnav'))).toEqual(['general', 'agents', 'users', 'projects'])
    expect(w.get('.subnav [data-subnav="agents"]').attributes('aria-current')).toBe('page')
    const rows = w.findAll('.agent-row')
    expect(rows.map((r) => r.attributes('data-agent'))).toEqual(['agt_a_online', 'agt_b_offline', 'agt_c_incompat', 'agt_d_revoked', 'agt_e_new', 'agt_f_local'])
    expect(rows.map((r) => r.get('[data-runtime]').attributes('data-runtime'))).toEqual(['online', 'offline', 'incompatible', 'revoked', 'unregistered', 'local'])
    expect(rows.map((r) => r.get('[data-runtime]').text())).toEqual(['온라인', '오프라인', '프로토콜 비호환', '자격 증명 해지', '미등록', '로컬 실행'])
    // flowgate.v02.0077 (시안 ⑩): no form or detail kept open beside the list — nothing is open until a row is chosen.
    expect(w.find('[data-testid="agent-drawer"]').exists()).toBe(false)
    expect(w.find('[data-testid="agent-create-form"]').exists()).toBe(false)
    expect(w.find('[data-testid="agent-detail"]').exists()).toBe(false)
    expect(writes()).toEqual([])
  })

  it('detail shows each status field apart; online is never "실행 가능/실행 중"; local online = 해당 없음', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    expect(field(w, 'registration_status')).toBe('등록됨')
    expect(field(w, 'credential_status')).toBe('사용 중')
    expect(field(w, 'enrollment_status')).toBe('없음')
    expect(field(w, 'online_status')).toBe('온라인')
    expect(w.get('[data-field="last_seen_at"]').attributes('title')).toBe('2026-10-20T04:00:00Z') // shown as relative time
    expect(field(w, 'protocol_compatibility')).toBe('1 · 호환')
    expect(field(w, 'agent_version')).toBe('1.4.2')
    expect(field(w, 'platform')).toBe('linux / amd64')
    expect(w.get('[data-testid="agent-enabled-state"]').text()).toBe('사용 중')
    // configured (ai_cli, storage) / reported (ai_cli, ai_api) / effective = both; storage is allowed but not reported
    const cell = (cap: string, col: string) => w.get(`[data-cap-row="${cap}"] [data-col="${col}"]`).text()
    expect(['ai_cli', 'ai_api', 'storage'].map((c) => capChecked(w, c))).toEqual([true, false, true])
    expect(['ai_cli', 'ai_api', 'storage'].map((c) => [cell(c, 'reported'), cell(c, 'effective')])).toEqual([
      ['가능', '사용 가능'], ['가능', '—'], ['불가', '불일치'],
    ])
    expect(w.get('[data-testid="agent-banner"]').text()).toContain('Storage을(를) 허용했지만')
    const note = w.get('[data-testid="agent-online-note"]').text()
    expect(note).toContain('실행할 수 있거나 실행 중이라는 뜻은 아닙니다')

    await pick(w, 'agt_c_incompat')
    expect(field(w, 'online_status')).toBe('오프라인')
    expect(field(w, 'protocol_compatibility')).toBe('1 · 호환되지 않음 · 거부된 버전 2 · 2026-10-20T04:01:00Z')

    await pick(w, 'agt_d_revoked')
    expect(field(w, 'registration_status')).toBe('자격 해지됨')
    expect(field(w, 'credential_status')).toBe('해지됨')
    expect(w.find('[data-testid="agent-revoke"]').exists()).toBe(false)
    expect(w.find('[data-testid="agent-delete"]').exists()).toBe(false)
    expect(w.get('[data-testid="delete-protected"]').text()).toContain('삭제할 수 없습니다')

    await pick(w, 'agt_f_local')
    expect(field(w, 'online_status')).toBe('해당 없음 (로컬)')
    expect(field(w, 'last_seen_at')).toBe('기록 없음')
    expect(w.get('[data-cap-row="ai_cli"] [data-col="reported"]').text()).toBe('—')
    expect(w.get('[data-cap-row="ai_cli"] [data-col="effective"]').text()).toBe('사용')
    expect(w.get('[data-testid="agent-online-note"]').text()).toContain('heartbeat를 보내지 않으므로')
    expect(w.find('[data-testid="agent-issue"]').exists()).toBe(false)
    expect(w.get('[data-testid="issue-local"]').exists()).toBe(true)
    expect(writes()).toEqual([])
  })

  it('no personal-Agent, job-run or storage-run control: the detail has only the allowed buttons', async () => {
    const w = await open('/settings/system/agents')
    for (const id of ['agt_a_online', 'agt_e_new', 'agt_f_local']) {
      await pick(w, id)
      const buttons = w.get('[data-testid="agent-detail"]').findAll('button').map((b) => b.attributes('data-testid'))
      expect(buttons.every((b) => ['edit-save', 'agent-disable', 'agent-enable', 'agent-issue', 'agent-revoke', 'agent-delete'].includes(b ?? ''))).toBe(true)
      expect(w.get('[data-testid="agent-detail"]').text()).not.toMatch(/실행하기|작업 실행|개인 Agent|내 Agent/)
    }
    expect(writes()).toEqual([])
  })

  it('register local: POST with connection_mode null and the path, then a re-read; the new Agent is selected', async () => {
    const w = await open('/settings/system/agents')
    await click(w, '[data-testid="agent-new"]')
    await click(w, '[data-location="local"]')
    expect(w.find('[data-testid="agent-remote-fields"]').exists()).toBe(false)
    await type(w, '[data-testid="agent-name"]', '  local-two  ')
    await type(w, '[data-testid="agent-path"]', '/opt/agent-2')
    await w.get('[data-cap="ai_api"]').setValue(true)
    await click(w, '[data-testid="agent-create-submit"]')
    expect(writes()).toEqual(['POST /api/v1/system/agents'])
    expect(bodiesOf(/^POST \/api\/v1\/system\/agents$/)).toEqual([{
      name: 'local-two', enabled: true, location: 'local', connection_mode: null, local_executable_path: '/opt/agent-2',
      configured_capabilities: { ai_cli: false, ai_api: true, storage: false },
    }])
    expect(net.requests.slice(-2)).toEqual(['POST /api/v1/system/agents', 'GET /api/v1/system/agents'])
    expect(w.get('[data-testid="agent-detail"]').attributes('data-agent')).toBe('agt_new1')
    expect(w.get('[data-testid="agent-detail-name"]').text()).toBe('local-two')
    expect(field(w, 'online_status')).toBe('해당 없음 (로컬)')
    expect(w.get('.agent-row[data-agent="agt_new1"] [data-runtime]').attributes('data-runtime')).toBe('local')
    expect(server('agt_new1')).toMatchObject({ location: 'local', connection_mode: null, local_executable_path: '/opt/agent-2' })
    expect(toastText(w)).toContain('local-two 등록됨')
  })

  it('register remote: agent_pull, no path, no host/IP/port input; enabled off is sent as asked', async () => {
    const w = await open('/settings/system/agents')
    await click(w, '[data-testid="agent-new"]')
    expect(w.get('[data-testid="agent-remote-fields"]').text()).toContain('Agent Pull')
    expect(w.find('[data-testid="agent-path"]').exists()).toBe(false)
    expect(w.get('[data-testid="agent-create-form"]').findAll('input:not([type="checkbox"])').length).toBe(1) // the name only
    await type(w, '[data-testid="agent-name"]', 'remote-two')
    await w.get('[data-testid="agent-enabled"]').setValue(false)
    await click(w, '[data-testid="agent-create-submit"]')
    expect(bodiesOf(/^POST \/api\/v1\/system\/agents$/)).toEqual([{
      name: 'remote-two', enabled: false, location: 'remote', connection_mode: 'agent_pull', local_executable_path: null,
      configured_capabilities: { ai_cli: false, ai_api: false, storage: false },
    }])
    expect(field(w, 'registration_status')).toBe('미등록')
    expect(w.get('[data-testid="agent-enabled-state"]').text()).toBe('사용 중지됨')
    expect(w.find('[data-testid="agent-issue"]').exists()).toBe(false)
    expect(w.get('[data-testid="issue-disabled"]').exists()).toBe(true)
  })

  it('register: an empty or over-long name sends nothing; a 403 keeps the form and its input and shows no success', async () => {
    const w = await open('/settings/system/agents')
    await click(w, '[data-testid="agent-new"]')
    expect(w.get('[data-testid="agent-create-submit"]').attributes('disabled')).toBeDefined()
    await type(w, '[data-testid="agent-name"]', '   ')
    await click(w, '[data-testid="agent-create-submit"]')
    await type(w, '[data-testid="agent-name"]', 'x'.repeat(101))
    expect(w.get('[data-testid="agent-name-invalid"]').text()).toContain('100자')
    await click(w, '[data-testid="agent-create-submit"]')
    expect(writes()).toEqual([])
    await type(w, '[data-testid="agent-name"]', 'remote-403')
    net.failNext = { call: /^POST \/api\/v1\/system\/agents$/, status: 403, data: { detail: 'Forbidden' } }
    await click(w, '[data-testid="agent-create-submit"]')
    expect(w.get('[data-testid="agent-form-error"]').text()).toBe('권한이 없습니다.')
    expect((w.get('[data-testid="agent-name"]').element as HTMLInputElement).value).toBe('remote-403')
    expect(toastText(w)).not.toContain('등록됨')
    expect(net.requests.slice(-2)).toEqual(['POST /api/v1/system/agents', 'GET /api/v1/system/agents']) // re-read kept
    expect(net.agents).toHaveLength(6)
  })

  it('edit: PATCH carries only the changed fields (never location); the re-read confirms it', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    expect(w.get('[data-testid="edit-location"]').text()).toBe('원격 · Agent Pull')
    expect(w.find('[data-testid="edit-path"]').exists()).toBe(false)
    expect(w.get('[data-testid="edit-save"]').attributes('disabled')).toBeDefined()
    await type(w, '[data-testid="edit-name"]', 'fresh-remote-2')
    await click(w, '[data-testid="edit-save"]')
    await w.get('[data-edit-cap="storage"]').setValue(true)
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /)).toEqual([
      { name: 'fresh-remote-2' },
      { configured_capabilities: { ai_cli: false, ai_api: false, storage: true } },
    ])
    expect(writes()).toEqual(['PATCH /api/v1/system/agents/agt_e_new', 'PATCH /api/v1/system/agents/agt_e_new'])
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    expect(w.get('[data-testid="edit-saved"]').exists()).toBe(true)
    expect(server('agt_e_new')).toMatchObject({ name: 'fresh-remote-2', configured: { ai_cli: false, ai_api: false, storage: true }, location: 'remote' })
    expect(w.get('.agent-row[data-agent="agt_e_new"]').text()).toContain('fresh-remote-2')

    await pick(w, 'agt_f_local')
    await type(w, '[data-testid="edit-path"]', '  ')
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /).at(-1)).toEqual({ local_executable_path: null })
    expect(server('agt_f_local')!.local_executable_path).toBeNull()
  })

  it('사용 중지: asked first with its impact (취소 = 0 requests); confirm = PATCH {enabled:false} only; 다시 사용 = PATCH {enabled:true}', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    await click(w, '[data-testid="agent-disable"]')
    const modal = w.get('[data-testid="agent-confirm"]')
    expect(modal.attributes('data-action')).toBe('disable')
    expect(modal.text()).toContain('build-online · 사용 중지')
    expect(modal.text()).toContain('삭제나 자격 해지가 아닙니다')
    expect(modal.text()).toContain('작업 취소가 아닙니다')
    await click(w, '[data-testid="agent-confirm-cancel"]')
    expect(w.find('[data-testid="agent-confirm"]').exists()).toBe(false)
    expect(writes()).toEqual([])

    await click(w, '[data-testid="agent-disable"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['PATCH /api/v1/system/agents/agt_a_online'])
    expect(bodiesOf(/^PATCH /)).toEqual([{ enabled: false }])
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    expect(server('agt_a_online')).toMatchObject({ enabled: false, credentialActive: true }) // registration / credential kept
    expect(w.get('[data-testid="agent-enabled-state"]').text()).toBe('사용 중지됨')
    expect(field(w, 'online_status')).toBe('오프라인')
    expect(field(w, 'credential_status')).toBe('사용 중')
    expect(w.find('[data-testid="agent-issue"]').exists()).toBe(false)
    expect(toastText(w)).toContain('build-online 사용 중지됨')

    await click(w, '[data-testid="agent-enable"]')
    expect(bodiesOf(/^PATCH /)).toEqual([{ enabled: false }, { enabled: true }])
    expect(w.get('[data-testid="agent-enabled-state"]').text()).toBe('사용 중')
    expect(field(w, 'online_status')).toBe('온라인')
  })

  it('list GET 401 / 403 / 500 is an error with retry, never an empty list; a real [] is the empty state', async () => {
    for (const status of [401, 403, 500]) {
      net.failListGet = status
      const w = await open('/settings/system/agents')
      expect(w.get('[data-testid="agents-error"]').text()).toContain('Agent 목록을 불러오지 못했습니다')
      expect(w.find('[data-testid="agents-empty"]').exists()).toBe(false)
      expect(w.find('.agent-row').exists()).toBe(false)
      net.failListGet = 0
      await click(w, '[data-testid="agents-retry"]')
      expect(w.findAll('.agent-row')).toHaveLength(6)
      w.unmount()
      wrapper = null
    }
    net.agents = []
    const w = await open('/settings/system/agents')
    expect(w.get('[data-testid="agents-empty"]').text()).toBe('등록된 Agent가 없습니다.')
    expect(writes()).toEqual([])
  })
})

describe('V17 Agent 민감 동작 — token, revoke, delete, refusals', () => {
  it('토큰 발급: cancel = 0 requests; confirm = one POST {} ; the 201 token shows once and nowhere else', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    const modal = w.get('[data-testid="agent-confirm"]')
    expect(modal.text()).toContain('fresh-remote · 등록 토큰 발급')
    expect(modal.text()).toContain('이전에 발급한 대기 중 토큰이 있으면 즉시 무효')
    expect(modal.text()).toContain('한 번만 표시')
    await click(w, '[data-testid="agent-confirm-cancel"]')
    expect(writes()).toEqual([])

    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['POST /api/v1/system/agents/agt_e_new/enrollments'])
    expect(bodiesOf(/enrollments$/)).toEqual([{}])
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    const token = server('agt_e_new')!.pending!.token
    expectTokenOnlyInBox(w, token, true)
    expect(w.get('[data-testid="enrollment-expires"]').text()).toContain(server('agt_e_new')!.pending!.expires_at)
    expect(field(w, 'enrollment_status')).toContain('대기 중')
  })

  it('flowgate.v02.0091 T#1: HTTPS 권장 · HTTP 위험 안내가 등록 영역과 발급 확인 창에 보이고(ko/en/ja), 상태 표시는 없고, 토큰은 한 번만 보인다', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    const note = w.get('[data-testid="agent-http-note"]')
    expect(note.text()).toContain('HTTPS를 권장')
    expect(note.text()).toContain('HTTP 위험을 명시적으로 허용')
    expect(note.text()).toContain('암호화하지 않아')
    expect(note.text()).toContain('내부망이나 암호화된 VPN')
    // The server does not know the Agent's address or its allowance: no such status is shown.
    expect(w.text()).not.toMatch(/HTTP 허용됨|보안 연결됨/)
    await click(w, '[data-testid="agent-issue"]')
    expect(w.get('[data-testid="agent-confirm"]').text()).toContain('토큰이 평문으로 전송될 수 있습니다')
    await click(w, '[data-testid="agent-confirm-cancel"]')
    expect(writes()).toEqual([])

    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(w.get('[data-testid="agent-http-note"]').text()).toContain('HTTPS is recommended')
    expect(w.get('[data-testid="agent-http-note"]').text()).toContain('explicitly allowed on the Agent')
    await click(w, '[data-testid="agent-issue"]')
    expect(w.get('[data-testid="agent-confirm"]').text()).toContain('sent in plain text')
    await click(w, '[data-testid="agent-confirm-cancel"]')

    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(w.get('[data-testid="agent-http-note"]').text()).toContain('HTTPS を推奨')
    expect(w.get('[data-testid="agent-http-note"]').text()).toContain('明示的に許可')
    await click(w, '[data-testid="agent-issue"]')
    expect(w.get('[data-testid="agent-confirm"]').text()).toContain('平文で送信')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['POST /api/v1/system/agents/agt_e_new/enrollments'])
    expect(bodiesOf(/enrollments$/)).toEqual([{}])
    expectTokenOnlyInBox(w, server('agt_e_new')!.pending!.token, true)
  })

  it('토큰 발급 while it is out: further clicks send nothing (1 request)', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    net.holdNext = true
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(w.get('[data-testid="agent-confirm-ok"]').attributes('disabled')).toBeDefined()
    await click(w, '[data-testid="agent-confirm-ok"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['POST /api/v1/system/agents/agt_e_new/enrollments'])
    net.release!()
    await flushPromises()
    expect(writes()).toEqual(['POST /api/v1/system/agents/agt_e_new/enrollments'])
    expect(w.find('[data-testid="enrollment-token"]').exists()).toBe(true)
  })

  it('the token goes on: another Agent, coming back, leaving the screen; GET never brings it back', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    const token = server('agt_e_new')!.pending!.token
    expectTokenOnlyInBox(w, token, true)

    await pick(w, 'agt_a_online')
    expectTokenOnlyInBox(w, token, false)
    await pick(w, 'agt_e_new')
    expectTokenOnlyInBox(w, token, false)
    expect(field(w, 'enrollment_status')).toContain('대기 중') // still pending on the server, the raw value is gone

    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    const second = server('agt_e_new')!.pending!.token
    expectTokenOnlyInBox(w, second, true)
    await router.push('/settings/system/users').catch(() => undefined)
    await flushPromises()
    await router.push('/settings/system/agents')
    await flushPromises()
    await pick(w, 'agt_e_new')
    expectTokenOnlyInBox(w, second, false)
    expect(w.find('[data-testid="enrollment-secret"]').exists()).toBe(false)
  })

  it('re-issue: the dialog says the pending token is replaced; the server keeps only the new one', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    const first = server('agt_e_new')!.pending!.token
    await click(w, '[data-testid="agent-issue"]')
    expect(w.get('[data-testid="agent-confirm"]').text()).toContain('대기 중인 이전 등록 토큰은 즉시 무효')
    await click(w, '[data-testid="agent-confirm-ok"]')
    const second = server('agt_e_new')!.pending!.token
    expect(second).not.toBe(first)
    expectTokenOnlyInBox(w, first, false)
    expectTokenOnlyInBox(w, second, true)
    await click(w, '[data-testid="enrollment-dismiss"]')
    expectTokenOnlyInBox(w, second, false)
  })

  it('issuing for a registered Agent warns that enrolling replaces the current credential', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    await click(w, '[data-testid="agent-issue"]')
    expect(w.get('[data-testid="agent-confirm"]').text()).toContain('지금의 자격은 해지되고 새 자격으로 바뀝니다')
    await click(w, '[data-testid="agent-confirm-cancel"]')
    expect(writes()).toEqual([])
  })

  it('자격 해지: cancel = 0; confirm = one POST {} ; token and pending go; the re-read shows revoked, delete stays impossible', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    const token = server('agt_a_online')!.pending!.token
    expectTokenOnlyInBox(w, token, true)
    const before = writes().length

    await click(w, '[data-testid="agent-revoke"]')
    const modal = w.get('[data-testid="agent-confirm"]')
    expect(modal.text()).toContain('build-online · 자격 해지')
    expect(modal.text()).toContain('대기 중인 등록 토큰도 함께 무효')
    expect(modal.text()).toContain('재등록해야 합니다')
    await click(w, '[data-testid="agent-confirm-cancel"]')
    expect(writes().length).toBe(before)
    expectTokenOnlyInBox(w, token, true)

    await click(w, '[data-testid="agent-revoke"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes().slice(before)).toEqual(['POST /api/v1/system/agents/agt_a_online/credential/revoke'])
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    expectTokenOnlyInBox(w, token, false)
    expect(server('agt_a_online')).toMatchObject({ credentialActive: false, credentialHistory: true, pending: null })
    expect(field(w, 'credential_status')).toBe('해지됨')
    expect(field(w, 'enrollment_status')).toBe('없음')
    expect(field(w, 'registration_status')).toBe('자격 해지됨')
    expect(w.find('[data-testid="agent-revoke"]').exists()).toBe(false)
    expect(w.get('[data-testid="delete-protected"]').exists()).toBe(true)
    expect(toastText(w)).toContain('build-online 자격 해지됨')
  })

  it('삭제 (미등록): cancel = 0; confirm = one DELETE, 204, the row goes after the re-read', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-delete"]')
    const modal = w.get('[data-testid="agent-confirm"]')
    expect(modal.text()).toContain('fresh-remote · Agent 삭제')
    expect(modal.text()).toContain('한 번도 등록되지 않은 Agent만')
    await click(w, '[data-testid="agent-confirm-cancel"]')
    expect(writes()).toEqual([])
    await click(w, '[data-testid="agent-delete"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['DELETE /api/v1/system/agents/agt_e_new'])
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    expect(w.find('.agent-row[data-agent="agt_e_new"]').exists()).toBe(false)
    expect(w.find('[data-testid="agent-detail"]').exists()).toBe(false)
    expect(server('agt_e_new')).toBeUndefined()
    expect(toastText(w)).toContain('fresh-remote 삭제됨')
  })

  it('삭제 with registration history: no button in the UI; a stale screen gets the server 409 and no success', async () => {
    const w = await open('/settings/system/agents')
    for (const id of ['agt_a_online', 'agt_b_offline', 'agt_c_incompat', 'agt_d_revoked']) {
      await pick(w, id)
      expect(w.find('[data-testid="agent-delete"]').exists()).toBe(false)
      expect(w.get('[data-testid="delete-protected"]').exists()).toBe(true)
    }
    // The Agent enrolled after the list was read: the screen still offers delete, the server refuses.
    await pick(w, 'agt_e_new')
    Object.assign(server('agt_e_new')!, { credentialActive: true, credentialHistory: true })
    await click(w, '[data-testid="agent-delete"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['DELETE /api/v1/system/agents/agt_e_new'])
    expect(w.get('[data-testid="agent-action-error"]').text()).toBe('등록 이력이 있는 Agent는 삭제할 수 없습니다.')
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    expect(w.get('[data-testid="delete-protected"]').exists()).toBe(true)
    expect(toastText(w)).not.toContain('삭제됨')
    expect(server('agt_e_new')).toBeDefined()
  })

  const REFUSALS = [
    { status: 403, data: { detail: 'Forbidden' }, text: '권한이 없습니다.' },
    { status: 409, data: { ok: false, error: { code: 'enrollment_not_allowed', message: 'x', details: {} } }, text: '사용 중인 원격 Agent만 등록 토큰을 발급할 수 있습니다.' },
    { status: 503, data: { ok: false, error: { code: 'temporarily_unavailable', message: 'x', details: {} } }, text: '서버에 Agent 비밀키(FLOWGATE_AGENT_PEPPER)가 설정되어 있지 않습니다. 다시 시도해도 해결되지 않으니 서버 관리자에게 비밀 설정 확인과 서버 재시작을 요청하세요.' },
  ]

  it.each(REFUSALS)('토큰 발급 refused with $status: its message stays, no token, no success, the list is read again', async ({ status, data, text }) => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    net.failNext = { call: /enrollments$/, status, data }
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['POST /api/v1/system/agents/agt_e_new/enrollments'])
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    expect(w.get('[data-testid="agent-action-error"]').text()).toBe(text)
    expect(w.find('[data-testid="enrollment-secret"]').exists()).toBe(false)
    expect(w.find('[data-testid="agent-confirm"]').exists()).toBe(false)
    expect(toastText(w)).toBe('')
    expect(server('agt_e_new')!.pending).toBeNull()
  })

  it.each(REFUSALS.filter((r) => r.status !== 409))('자격 해지 / 삭제 / 사용 중지 refused with $status: never shown as done', async ({ status, data, text }) => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    for (const [button, call] of [['agent-revoke', /credential\/revoke$/], ['agent-disable', /^PATCH /]] as const) {
      await click(w, `[data-testid="${button}"]`)
      net.failNext = { call, status, data }
      await click(w, '[data-testid="agent-confirm-ok"]')
      expect(w.get('[data-testid="agent-action-error"]').text()).toBe(text)
      expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    }
    expect(server('agt_a_online')).toMatchObject({ enabled: true, credentialActive: true })
    expect(w.get('[data-testid="agent-enabled-state"]').text()).toBe('사용 중')
    expect(field(w, 'credential_status')).toBe('사용 중')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-delete"]')
    net.failNext = { call: /^DELETE /, status, data }
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(w.get('[data-testid="agent-action-error"]').text()).toBe(text)
    expect(w.find('.agent-row[data-agent="agt_e_new"]').exists()).toBe(true)
    expect(toastText(w)).toBe('')
  })

  it('a change answered 200 whose re-read fails is "not confirmed": no success, the re-read error stays (GET-only retry)', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-delete"]')
    net.failListGet = 500
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(toastText(w)).toContain('결과를 확인하지 못했습니다')
    expect(toastText(w)).not.toContain('삭제됨')
    expect(w.get('[data-testid="agents-refresh-error"]').exists()).toBe(true)
    net.failListGet = 0
    const count = writes().length
    await click(w, '[data-testid="agents-refresh-retry"]')
    expect(writes().length).toBe(count) // the retry is the GET only
    expect(w.find('.agent-row[data-agent="agt_e_new"]').exists()).toBe(false)
    expect(w.find('[data-testid="agents-refresh-error"]').exists()).toBe(false)
  })

  it('a change of the Agent (PATCH) drops a token shown for it', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    const token = server('agt_e_new')!.pending!.token
    await type(w, '[data-testid="edit-name"]', 'renamed')
    await click(w, '[data-testid="edit-save"]')
    expectTokenOnlyInBox(w, token, false)
  })

  it('copy: the toast says copied, never the token', async () => {
    const writeText = vi.fn(async (_text: string) => undefined)
    const previous = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    onTestFinished(() => {
      if (previous) Object.defineProperty(navigator, 'clipboard', previous)
      else delete (navigator as { clipboard?: unknown }).clipboard
    })
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    const token = server('agt_e_new')!.pending!.token
    await click(w, '[data-testid="enrollment-copy"]')
    expect(writeText).toHaveBeenCalledWith(token)
    expect(toastText(w)).toContain('복사했습니다')
    expectTokenOnlyInBox(w, token, true)
  })
})

describe('flowgate.v02.0077 시안 ⑩ — counts, filters, search and the drawer', () => {
  it('counts, filter chips and search read the one GET: no further request', async () => {
    const w = await open('/settings/system/agents')
    const stat = (key: string) => w.get(`[data-stat="${key}"] .v`).text()
    expect([stat('registered'), stat('online'), stat('attention')]).toEqual(['3대', '1대', '4대'])
    expect(['all', 'online', 'attention', 'remote', 'local'].map((key) => w.get(`[data-filter="${key}"] .n`).text())).toEqual(['6', '1', '4', '5', '1'])
    const ids = () => w.findAll('.agent-row').map((r) => r.attributes('data-agent'))
    await click(w, '[data-filter="attention"]')
    expect(ids()).toEqual(['agt_a_online', 'agt_b_offline', 'agt_c_incompat', 'agt_d_revoked'])
    await click(w, '[data-filter="local"]')
    expect(ids()).toEqual(['agt_f_local'])
    await click(w, '[data-filter="all"]')
    await type(w, '[data-testid="agent-search"]', 'BUILD-O')
    expect(ids()).toEqual(['agt_a_online', 'agt_b_offline'])
    await type(w, '[data-testid="agent-search"]', 'agt_e')
    expect(ids()).toEqual(['agt_e_new'])
    await type(w, '[data-testid="agent-search"]', 'nothing-here')
    expect(ids()).toEqual([])
    expect(w.get('[data-testid="agents-no-match"]').text()).toContain('조건에 맞는 Agent가 없습니다')
    expect(w.find('[data-testid="agents-empty"]').exists()).toBe(false)
    expect(net.requests).toEqual(['GET /api/v1/system/agents'])
  })

  it('the capability tags tell usable / allowed but not reported / reported but not allowed apart', async () => {
    const w = await open('/settings/system/agents')
    const tags = (id: string) => w.findAll(`.agent-row[data-agent="${id}"] [data-cap-tag]`)
      .map((c) => c.classes().filter((k) => ['on', 'miss', 'extra'].includes(k)).join(''))
    expect(tags('agt_a_online')).toEqual(['on', 'extra', 'miss'])
    expect(tags('agt_e_new')).toEqual(['', '', ''])
    expect(tags('agt_f_local')).toEqual(['on', '', ''])
  })

  it('the drawer closes with 닫기, the backdrop and Esc; an unsaved edit is asked about first and nothing is sent', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    expect(w.get('[data-testid="agent-detail"]').attributes('data-agent')).toBe('agt_e_new')
    await click(w, '[data-testid="agent-drawer-close"]')
    expect(w.find('[data-testid="agent-drawer"]').exists()).toBe(false)

    await pick(w, 'agt_e_new')
    await w.get('[data-testid="agent-drawer"]').trigger('click') // the backdrop itself
    await flushPromises()
    expect(w.find('[data-testid="agent-drawer"]').exists()).toBe(false)

    await pick(w, 'agt_e_new')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(w.find('[data-testid="agent-drawer"]').exists()).toBe(false)

    await pick(w, 'agt_e_new')
    await type(w, '[data-testid="edit-name"]', 'half-typed')
    await click(w, '[data-testid="agent-drawer-close"]')
    expect(w.get('[data-testid="leave-confirm"]').text()).toContain('저장하지 않은 Agent 입력')
    await click(w, '[data-testid="leave-stay"]')
    expect(w.get('[data-testid="agent-detail"]').attributes('data-agent')).toBe('agt_e_new')
    expect(editName(w)).toBe('half-typed')
    await click(w, '[data-testid="agent-drawer-close"]')
    await click(w, '[data-testid="leave-discard"]')
    expect(w.find('[data-testid="agent-drawer"]').exists()).toBe(false)
    await pick(w, 'agt_e_new')
    expect(editName(w)).toBe('fresh-remote')
    expect(writes()).toEqual([])
  })

  it('새 Agent opens in the drawer and closing it keeps nothing typed', async () => {
    const w = await open('/settings/system/agents')
    await click(w, '[data-testid="agent-new"]')
    expect(w.get('[data-testid="agent-drawer"]').find('[data-testid="agent-create-form"]').exists()).toBe(true)
    await type(w, '[data-testid="agent-name"]', 'not-sent')
    await click(w, '[data-testid="agent-drawer-close"]')
    await click(w, '[data-testid="leave-discard"]')
    expect(w.find('[data-testid="agent-drawer"]').exists()).toBe(false)
    await click(w, '[data-testid="agent-new"]')
    expect((w.get('[data-testid="agent-name"]').element as HTMLInputElement).value).toBe('')
    expect(writes()).toEqual([])
  })

  it('closing the drawer drops a token shown in it', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    const token = server('agt_e_new')!.pending!.token
    expectTokenOnlyInBox(w, token, true)
    expect(w.get('.agent-row[data-agent="agt_e_new"] [data-runtime]').attributes('data-runtime')).toBe('pending')
    await click(w, '[data-testid="agent-drawer-close"]')
    expectTokenOnlyInBox(w, token, false)
    await pick(w, 'agt_e_new')
    expectTokenOnlyInBox(w, token, false)
  })
})

describe('flowgate.v02.0077 개요 › 확인 필요', () => {
  it('the administrator overview reads the list once and lists what needs a look; 확인 opens that Agent', async () => {
    const w = await open('/settings')
    expect(net.requests).toEqual(['GET /api/v1/system/agents'])
    const items = w.findAll('[data-attention]')
    expect(items.map((i) => i.attributes('data-attention'))).toEqual(['agt_a_online', 'agt_b_offline', 'agt_b_offline', 'agt_c_incompat', 'agt_d_revoked'])
    expect(items[0].text()).toContain('build-online 기능 불일치')
    expect(items[1].text()).toContain('build-offline 오프라인')
    expect(items[3].text()).toContain('build-incompatible 프로토콜 비호환')
    await items[1].get('[data-testid="hub-attention-go"]').trigger('click')
    await waitForPath('/settings/system/agents')
    for (let i = 0; i < 50 && !w.find('[data-testid="agent-detail"]').exists(); i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 10))
      await flushPromises()
    }
    expect(w.get('[data-testid="agent-detail"]').attributes('data-agent')).toBe('agt_b_offline')
    expect(router.currentRoute.value.query).toEqual({})
    expect(writes()).toEqual([])
  })

  it('nothing to look at: the empty notice; a non-administrator overview sends nothing and shows no 확인 필요', async () => {
    net.agents = [rec('agt_f_local', 'local-runner', { location: 'local', connection_mode: null })]
    let w = await open('/settings')
    expect(w.get('[data-testid="hub-attention-empty"]').text()).toContain('확인이 필요한 항목이 없습니다')
    w.unmount()
    wrapper = null
    net.requests.length = 0
    setCurrentUser({ ...ADMIN, isAdmin: false })
    w = await open('/settings')
    expect(w.find('[data-testid="hub-attention"]').exists()).toBe(false)
    expect(net.requests).toEqual([])
  })
})

describe('미저장 이탈 보호 — the Agent form', () => {
  it('an unsaved edit holds a route move and a switch to another Agent; 계속 편집 = no request, input kept', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await type(w, '[data-testid="edit-name"]', 'half-typed')
    const move = router.push('/settings/system/users')
    await flushPromises()
    expect(w.get('[data-testid="leave-confirm"]').text()).toContain('저장하지 않은 Agent 입력')
    await click(w, '[data-testid="leave-stay"]')
    await move
    expect(router.currentRoute.value.path).toBe('/settings/system/agents')

    await w.get('.agent-row[data-agent="agt_a_online"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(true)
    await click(w, '[data-testid="leave-stay"]')
    expect(w.get('[data-testid="agent-detail"]').attributes('data-agent')).toBe('agt_e_new')
    expect((w.get('[data-testid="edit-name"]').element as HTMLInputElement).value).toBe('half-typed')

    await w.get('.agent-row[data-agent="agt_a_online"]').trigger('click')
    await flushPromises()
    await click(w, '[data-testid="leave-discard"]')
    expect(w.get('[data-testid="agent-detail"]').attributes('data-agent')).toBe('agt_a_online')
    expect(writes()).toEqual([])
    expect(server('agt_e_new')!.name).toBe('fresh-remote')
  })

  it('nothing unsaved: no question; losing the administrator role never waits for one', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await router.push('/settings/system/users')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/settings/system/users')
    await router.push('/settings/system/agents')
    await flushPromises()
    await pick(w, 'agt_e_new')
    await type(w, '[data-testid="edit-name"]', 'half-typed')
    setCurrentUser({ ...ADMIN, isAdmin: false })
    await waitForPath('/settings') // the overview view is imported lazily for the first time here
    expect(router.currentRoute.value.path).toBe('/settings')
    expect(w.find('[data-panel="system-agents"]').exists()).toBe(false)
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
    expect(writes()).toEqual([])
  })
})

describe('응답의 수명 — an answer that arrives after the screen or the session is gone changes nothing', () => {
  it('토큰 발급 out, the person moves to 사용자: the late 201 keeps no token, no re-read follows', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    const state = viewState(w)
    await click(w, '[data-testid="agent-issue"]')
    net.holdNext = true
    await click(w, '[data-testid="agent-confirm-ok"]')
    await router.push('/settings/system/users')
    await waitForPath('/settings/system/users')
    expect(w.find('[data-panel="system-agents"]').exists()).toBe(false)
    const before = net.requests.length
    net.release!()
    await flushPromises()
    const token = server('agt_e_new')!.pending!.token // the server did issue it
    expect(net.requests.slice(before)).toEqual([]) // no GET /system/agents after the screen went
    expect(state.enrollment).toBeNull()
    expect(state.toasts).toEqual([])
    expectTokenOnlyInBox(w, token, false)

    await router.push('/settings/system/agents')
    await waitForPath('/settings/system/agents')
    await pick(w, 'agt_e_new')
    expectTokenOnlyInBox(w, token, false)
    expect(w.find('[data-testid="enrollment-secret"]').exists()).toBe(false)
  })

  it('토큰 발급 out, the administrator role goes: the late 201 keeps no token, no re-read follows', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    const state = viewState(w)
    await click(w, '[data-testid="agent-issue"]')
    net.holdNext = true
    await click(w, '[data-testid="agent-confirm-ok"]')
    setCurrentUser({ ...ADMIN, isAdmin: false })
    await waitForPath('/settings')
    expect(w.find('[data-panel="system-agents"]').exists()).toBe(false)
    const before = net.requests.length
    net.release!()
    await flushPromises()
    const token = server('agt_e_new')!.pending!.token
    expect(net.requests.slice(before)).toEqual([])
    expect(state.enrollment).toBeNull()
    expect(state.toasts).toEqual([])
    expectTokenOnlyInBox(w, token, false)
  })

  it('토큰 발급 out, the access token is lost while the screen stays: nothing shown, no re-read, controls free', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await click(w, '[data-testid="agent-issue"]')
    net.holdNext = true
    await click(w, '[data-testid="agent-confirm-ok"]')
    delete window.__accessToken__
    const before = net.requests.length
    net.release!()
    await flushPromises()
    const token = server('agt_e_new')!.pending!.token
    expect(net.requests.slice(before)).toEqual([])
    expectTokenOnlyInBox(w, token, false)
    expect(w.find('[data-testid="enrollment-secret"]').exists()).toBe(false)
    expect(w.find('[data-testid="agent-confirm"]').exists()).toBe(false)
    expect(toastText(w)).toBe('')
    expect(w.get('[data-testid="agent-issue"]').attributes('disabled')).toBeUndefined()
  })

  it('a save (PATCH) out, 저장하지 않고 나가기: the late answer neither re-reads nor reports', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    const state = viewState(w)
    await type(w, '[data-testid="edit-name"]', 'late-name')
    net.holdNext = true
    await click(w, '[data-testid="edit-save"]')
    const move = router.push('/settings/system/users')
    await flushPromises()
    expect(w.get('[data-testid="leave-saving"]').exists()).toBe(true)
    await click(w, '[data-testid="leave-discard"]')
    await move
    await waitForPath('/settings/system/users')
    const before = net.requests.length
    net.release!()
    await flushPromises()
    expect(net.requests.slice(before)).toEqual([])
    expect(state.editSaved).toBe(false)
    expect(state.actionError).toBe('')
  })

  it('the re-read after 사용 중지 is out when the role goes: the late list is not applied and nothing is reported', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    const state = viewState(w)
    await click(w, '[data-testid="agent-disable"]')
    net.holdList = true
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(net.requests.slice(-2)).toEqual(['PATCH /api/v1/system/agents/agt_a_online', 'GET /api/v1/system/agents'])
    setCurrentUser({ ...ADMIN, isAdmin: false })
    await waitForPath('/settings')
    const before = net.requests.length
    net.release!()
    await flushPromises()
    expect(net.requests.slice(before)).toEqual([])
    expect(state.toasts).toEqual([])
    expect((state.agents as { agent_id: string; enabled: boolean }[]).find((a) => a.agent_id === 'agt_a_online')!.enabled).toBe(true) // the list read before
  })
})

describe('편집 확정값 — a re-read never passes a server value off as the person\'s change', () => {
  it('nothing typed: a re-read shows another administrator\'s rename, and nothing is unsaved', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    server('agt_e_new')!.name = 'renamed-elsewhere'
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]') // its re-read brings the new name
    expect(editName(w)).toBe('renamed-elsewhere')
    expect(w.get('[data-testid="edit-save"]').attributes('disabled')).toBeDefined()
    await router.push('/settings/system/users')
    await waitForPath('/settings/system/users')
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
    expect(writes()).toEqual(['POST /api/v1/system/agents/agt_e_new/enrollments'])
  })

  it('one field typed while another administrator changes another: the untouched one follows the server, PATCH carries the typed one only', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await type(w, '[data-testid="edit-name"]', 'my-name')
    server('agt_e_new')!.configured = { ai_cli: false, ai_api: false, storage: true } // the other administrator
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(editName(w)).toBe('my-name') // still unsaved
    expect(capChecked(w, 'storage')).toBe(true)
    expect(w.get('[data-testid="edit-save"]').attributes('disabled')).toBeUndefined()
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /)).toEqual([{ name: 'my-name' }])
    expect(server('agt_e_new')).toMatchObject({ name: 'my-name', configured: { ai_cli: false, ai_api: false, storage: true } })

    // capabilities one by one: ai_cli typed here, ai_api switched on elsewhere
    await w.get('[data-edit-cap="ai_cli"]').setValue(true)
    server('agt_e_new')!.configured = { ai_cli: false, ai_api: true, storage: true }
    await click(w, '[data-testid="agent-issue"]')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect([capChecked(w, 'ai_cli'), capChecked(w, 'ai_api'), capChecked(w, 'storage')]).toEqual([true, true, true])
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /).at(-1)).toEqual({ configured_capabilities: { ai_cli: true, ai_api: true, storage: true } })
    expect(server('agt_e_new')!.configured).toEqual({ ai_cli: true, ai_api: true, storage: true })
    expect(w.get('[data-testid="edit-save"]').attributes('disabled')).toBeDefined()
  })

  it('a discarded edit stays discarded: 새 Agent → 저장하지 않고 나가기 → 취소 shows the confirmed values', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_e_new')
    await type(w, '[data-testid="edit-name"]', 'half-typed')
    await click(w, '[data-testid="agent-new"]')
    await click(w, '[data-testid="leave-stay"]') // 계속 편집: nothing changes
    expect(w.find('[data-testid="agent-create-form"]').exists()).toBe(false)
    expect(editName(w)).toBe('half-typed')

    await click(w, '[data-testid="agent-new"]')
    await click(w, '[data-testid="leave-discard"]')
    expect(w.find('[data-testid="agent-create-form"]').exists()).toBe(true)
    await click(w, '[data-testid="agent-create-cancel"]')
    expect(w.get('[data-testid="agent-detail"]').attributes('data-agent')).toBe('agt_e_new')
    expect(editName(w)).toBe('fresh-remote')
    expect(w.get('[data-testid="edit-save"]').attributes('disabled')).toBeDefined()
    await router.push('/settings/system/users')
    await waitForPath('/settings/system/users')
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
    expect(writes()).toEqual([])
    expect(server('agt_e_new')!.name).toBe('fresh-remote')
  })
})
