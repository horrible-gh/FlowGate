import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi, type MockInstance } from 'vitest'
import { defineComponent, h } from 'vue'
import { RouterView } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import router from '@/app/router/index'
import { setCurrentUser } from '@/core/auth/currentUser'
import { issueAgentEnrollment, revokeAgentCredential, updateSystemAgent } from '@/features/settings/api'
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
  remote_workdir?: string | null
  /** flowgate.v02test.0019: workspace cleanup policy (server default ON / 24 h). */
  cleanup?: boolean
  hours?: number
  /** flowgate.v02test.0019: a CLAIMED/STARTED Job leased by it, or a NEW Job only it can take. */
  activeJobs?: boolean
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
  holdCall: null as RegExp | null,
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
    remote_workdir: r.remote_workdir ?? null,
    workspace_cleanup_enabled: r.cleanup ?? true,
    workspace_cleanup_retention_hours: r.hours ?? 24,
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
    const fields = ['name', 'enabled', 'location', 'connection_mode', 'local_executable_path', 'remote_workdir',
      'workspace_cleanup_enabled', 'workspace_cleanup_retention_hours', 'configured_capabilities']
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
    if ('workspace_cleanup_enabled' in body && typeof body.workspace_cleanup_enabled !== 'boolean') throw invalid('workspace_cleanup_enabled', 'invalid_type')
    const hours = body.workspace_cleanup_retention_hours
    if ('workspace_cleanup_retention_hours' in body && (typeof hours !== 'number' || !Number.isInteger(hours) || hours < 1 || hours > 720)) {
      throw invalid('workspace_cleanup_retention_hours', 'out_of_range')
    }
    if ('remote_workdir' in body) r.remote_workdir = (String(body.remote_workdir ?? '').trim() || null)
    if ('workspace_cleanup_enabled' in body) r.cleanup = body.workspace_cleanup_enabled as boolean
    if ('workspace_cleanup_retention_hours' in body) r.hours = hours as number
    return { data: detail(r) }
  }

  const handle = async (method: string, path: string, body?: unknown) => {
    const call = `${method} ${path}`
    net.requests.push(call)
    if (body !== undefined) net.bodies.push({ call, body })
    if ((method !== 'GET' && net.holdNext) || net.holdCall?.test(call)) {
      net.holdCall = null
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
      // flowgate.v02test.0019: registration history no longer blocks deleting; Jobs in progress do.
      if (r.activeJobs) throw agentError(409, 'agent_has_active_jobs', 'Agent has Jobs in progress; delete it after they finish')
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
        r.pending = { token: `enr_secret_${net.tokens}_xyz`, expires_at: new Date(Date.now() + 600_000).toISOString() }
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
  await click(w, `[data-edit-agent="${id}"]`)
}

const writes = () => net.requests.filter((r) => !r.startsWith('GET '))
const bodiesOf = (pattern: RegExp) => net.bodies.filter((b) => pattern.test(b.call)).map((b) => b.body)
const server = (id: string) => net.agents.find((a) => a.agent_id === id)
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
  net.holdCall = null
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
  vi.useRealTimers()
  setCurrentUser(null)
  delete window.__accessToken__
})


async function create(w: VueWrapper, name = 'new-machine') {
  await click(w, '[data-testid="agent-new"]')
  await type(w, '[data-testid="agent-name"]', name)
  await click(w, '[data-testid="agent-create-submit"]')
}
async function focus() { window.dispatchEvent(new Event('focus')); await flushPromises() }
const deleteRow = (w: VueWrapper, id = 'agt_e_new') => click(w, `[data-delete-agent="${id}"]`)


describe('0120 — distinct personal and system menu entries', () => {
  it('opens System Agents under System and exposes a separate personal entry', async () => {
    const w = await open('/settings/system/agents')
    expect(w.get('.scope.open [data-scope]').attributes('data-scope')).toBe('system')
    const link = w.get('[data-subnav="agents"]')
    expect(link.text()).toBe('시스템 에이전트')
    expect(link.attributes('href')).toBe('/app/settings/system/agents')
    expect(link.attributes('aria-current')).toBe('page')
    await click(w, '.nav-home')
    await waitForPath('/settings')
    expect(w.get('[data-scope-card="me"]').text()).toContain('에이전트')
    expect(w.get('[data-scope-card="system"]').text()).toContain('시스템 에이전트')
    await router.push('/settings/system/agents')
    await flushPromises()
    await click(w, '[data-subnav="agents"]')
    expect(router.currentRoute.value.name).toBe('settings-system-agents')
    expect(w.find('[data-panel="system-agents"]').exists()).toBe(true)
    expect(writes()).toEqual([])
  })

  it.each([false, null])('removes system access and late data on privilege/session loss (%s)', async (admin) => {
    net.holdList = true
    const w = await open('/settings/system/agents')
    const release = net.release!
    expect(w.get('[data-subnav="agents"]').text()).toBe('시스템 에이전트')
    setCurrentUser(admin === null ? null : { ...ADMIN, isAdmin: false })
    await waitForPath('/settings')
    release()
    await flushPromises()
    expect(w.find('[data-panel="system-agents"]').exists()).toBe(false)
    expect(w.text()).not.toContain('build-online')
    expect(w.get('[data-scope-card="me"]').text()).toContain('에이전트')
    expect(w.find('a[href="/app/settings/system/agents"]').exists()).toBe(false)
    expect(net.requests).toEqual(['GET /api/v1/system/agents'])
    expect(writes()).toEqual([])
  })
})

describe('0120 T#2 — list, accessible edit and preserved system registry', () => {
  it('one GET; only names, states, last seen and two named icon actions, without the old table, counts, search or drawer', async () => {
    const w = await open('/settings/system/agents')
    expect(net.requests).toEqual(['GET /api/v1/system/agents'])
    expect(w.get('[data-panel="system-agents"]').text()).toContain('등록된 시스템 에이전트')
    expect(w.get('[data-testid="agent-new"]').text()).toContain('에이전트 등록')
    const rows = w.findAll('.agent-row')
    expect(rows.map((r) => r.attributes('data-agent'))).toEqual(fixtures().map((r) => r.agent_id))
    expect(rows.map((r) => r.get('[data-runtime]').text())).toEqual(['온라인', '오프라인', '프로토콜 비호환', '자격 증명 해지', '등록 대기', '로컬 실행'])
    expect(rows[4].text()).toContain('최근 접속 연결 전')
    expect(rows[0].text()).not.toContain('linux')
    expect(rows[0].text()).not.toContain('amd64')
    expect(rows[0].text()).not.toContain('AI CLI')
    expect(rows[0].text()).not.toContain('상세')
    expect(w.find('input[type="search"], [data-testid="agent-stats"], [data-filter], table, .legend, .fgs-drawer').exists()).toBe(false)
    expect(rows[0].get('[data-edit-agent]').attributes('title')).toBe('수정')
    expect(rows[0].get('[data-edit-agent]').attributes('aria-label')).toBe('build-online 수정')
    expect(rows[0].findAll('button')).toHaveLength(2)
    expect(w.findAll('.subnav [data-subnav]').map((i) => i.attributes('data-subnav'))).toEqual(['general', 'agents', 'users', 'projects'])
  })

  it('disabled takes priority; incompatible and revoked are never presented as online; edit has read-only status and OS', async () => {
    net.agents[0].enabled = false
    const w = await open('/settings/system/agents')
    expect(w.get('[data-agent="agt_a_online"] [data-runtime]').text()).toBe('사용 중지')
    await pick(w, 'agt_c_incompat')
    expect(w.get('[data-field="connection"]').text()).toBe('프로토콜 비호환')
    expect(w.get('[data-field="platform"]').text()).toBe('linux / amd64')
    expect(w.find('[data-testid="agent-status"] input').exists()).toBe(false)
    expect(w.find('[data-edit-cap="storage"], [data-testid="edit-path"], [data-testid="agent-enable"], [data-testid="agent-revoke"]').exists()).toBe(false)
    expect(w.findAll('[data-edit-cap]').map((c) => c.attributes('data-edit-cap'))).toEqual(['ai_cli', 'ai_api'])
  })

  it('new remote: POST with all required fields, then same-ID POST {} enrollment, then GET', async () => {
    const w = await open('/settings/system/agents')
    await create(w)
    expect(writes()).toEqual(['POST /api/v1/system/agents', 'POST /api/v1/system/agents/agt_new1/enrollments'])
    expect(bodiesOf(/^POST \/api\/v1\/system\/agents$/)).toEqual([{
      name: 'new-machine', enabled: true, location: 'remote', connection_mode: 'agent_pull', local_executable_path: null,
      configured_capabilities: { ai_cli: false, ai_api: false, storage: false },
    }])
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    expect(w.find('[data-testid="agent-create-form"]').exists()).toBe(false)
    expectTokenOnlyInBox(w, server('agt_new1')!.pending!.token, true)
    expect(w.get('[data-testid="enrollment-expiry"]').text()).toContain(server('agt_new1')!.pending!.expires_at)
  })

  it.each(['local', 'remote'] as const)('restores %s registration fields and skips enrollment when disabled', async location => {
    const w = await open('/settings/system/agents')
    await click(w, '[data-testid="agent-new"]')
    await type(w, '[data-testid="agent-name"]', 'restored-agent')
    await click(w, `[data-location="${location}"]`)
    if (location === 'local') await type(w, '[data-testid="agent-path"]', '/opt/restored-agent')
    await w.get('[data-cap="storage"]').setValue(true)
    await w.get('[data-testid="agent-enabled"]').setValue(false)
    await click(w, '[data-testid="agent-create-submit"]')
    expect(writes()).toEqual(['POST /api/v1/system/agents'])
    expect(server('agt_new1')).toMatchObject({
      name: 'restored-agent', enabled: false, location,
      connection_mode: location === 'remote' ? 'agent_pull' : null,
      local_executable_path: location === 'local' ? '/opt/restored-agent' : null,
      configured: { ai_cli: false, ai_api: false, storage: true },
    })
    expect(w.find('[data-testid="agent-create-form"]').exists()).toBe(false)
    expect(w.find('[data-testid="agent-enrollment"]').exists()).toBe(false)
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
  })

  it('enabled local registration never attempts remote enrollment', async () => {
    const w = await open('/settings/system/agents')
    await click(w, '[data-testid="agent-new"]')
    await type(w, '[data-testid="agent-name"]', 'a'.repeat(100))
    await click(w, '[data-location="local"]')
    await w.get('[data-cap="ai_cli"]').setValue(true)
    await click(w, '[data-testid="agent-create-submit"]')
    expect(writes()).toEqual(['POST /api/v1/system/agents'])
    expect(server('agt_new1')).toMatchObject({ enabled: true, location: 'local', configured: { ai_cli: true } })
    expect(toastText(w)).toContain('등록')
  })

  it('local and disabled agents remain editable, preserving hidden storage, enabled, location and executable path', async () => {
    Object.assign(net.agents[5], { enabled: false, configured: { ai_cli: true, ai_api: false, storage: true } })
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_f_local')
    await type(w, '[data-testid="edit-name"]', 'local-renamed')
    await w.get('[data-edit-cap="ai_api"]').setValue(true)
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /)).toEqual([{ name: 'local-renamed', configured_capabilities: { ai_cli: true, ai_api: true, storage: true } }])
    expect(server('agt_f_local')).toMatchObject({ enabled: false, location: 'local', connection_mode: null, local_executable_path: '/opt/flowgate-agent', configured: { storage: true } })
  })

  it('flowgate.v02.0135 CH0006: the edit dialog shows 원격 작업 디렉터리 (label and input only), saves and reads it back', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    const dialog = w.get('[data-testid="agent-detail"]')
    expect(w.text()).toContain('에이전트 수정')
    expect(dialog.text()).not.toContain('나중에 바꿀 수 있습니다')
    expect(dialog.text()).not.toContain('heartbeat로 보고한')
    const field = dialog.get('[data-testid="edit-remote-workdir"]')
    expect(dialog.get('label[for="agent-dialog-workdir"]').text()).toBe('원격 작업 디렉터리')
    expect(field.element.closest('.agent-form-group')!.textContent!.trim()).toBe('원격 작업 디렉터리')
    expect(field.attributes('placeholder')).toBeUndefined()
    await type(w, '[data-testid="edit-remote-workdir"]', '/tmp/flowgate/${AGENT_JOB_ID}')
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /)).toEqual([{ remote_workdir: '/tmp/flowgate/${AGENT_JOB_ID}' }])
    expect(server('agt_a_online')!.remote_workdir).toBe('/tmp/flowgate/${AGENT_JOB_ID}')
    await pick(w, 'agt_a_online')
    expect((w.get('[data-testid="edit-remote-workdir"]').element as HTMLInputElement).value).toBe('/tmp/flowgate/${AGENT_JOB_ID}')
    await type(w, '[data-testid="edit-remote-workdir"]', '  ')
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /).at(-1)).toEqual({ remote_workdir: null })
    await pick(w, 'agt_f_local')
    expect(w.find('[data-testid="edit-remote-workdir"]').exists()).toBe(false)
  })

  it('flowgate.v02test.0019: 작업 폴더 자동 정리 ON/OFF and 보관 시간 are shown, sent as changed fields only and read back', async () => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    const group = w.get('[data-testid="edit-workspace-cleanup"]')
    expect(group.text()).toContain('작업 폴더 자동 정리')
    expect(group.text()).toContain('완료 후 보관 시간(시간)')
    expect((w.get('[data-testid="edit-cleanup-enabled"]').element as HTMLInputElement).checked).toBe(true)
    expect((w.get('[data-testid="edit-cleanup-hours"]').element as HTMLInputElement).value).toBe('24')
    await type(w, '[data-testid="edit-cleanup-hours"]', '48')
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /)).toEqual([{ workspace_cleanup_retention_hours: 48 }])
    expect(server('agt_a_online')!.hours).toBe(48)
    expect(server('agt_a_online')!.cleanup).toBeUndefined()   // only the changed field was sent
    await pick(w, 'agt_a_online')
    await w.get('[data-testid="edit-cleanup-enabled"]').setValue(false)
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /).at(-1)).toEqual({ workspace_cleanup_enabled: false })
    expect(server('agt_a_online')).toMatchObject({ cleanup: false, hours: 48 })
    await pick(w, 'agt_a_online')
    expect((w.get('[data-testid="edit-cleanup-enabled"]').element as HTMLInputElement).checked).toBe(false)
    expect((w.get('[data-testid="edit-cleanup-hours"]').element as HTMLInputElement).value).toBe('48')
    await pick(w, 'agt_f_local')
    expect(w.find('[data-testid="edit-workspace-cleanup"]').exists()).toBe(false)
  })

  it.each(['0', '721', '1.5', ''])('flowgate.v02test.0019: 보관 시간 %j is refused on screen and nothing is sent', async (value) => {
    const w = await open('/settings/system/agents')
    await pick(w, 'agt_a_online')
    await type(w, '[data-testid="edit-cleanup-hours"]', value)
    expect(w.get('[data-testid="edit-cleanup-hours-invalid"]').text()).toBe('보관 시간은 1~720 사이의 정수로 입력하세요.')
    expect(w.get('[data-testid="edit-save"]').attributes('disabled')).toBeDefined()
    await click(w, '[data-testid="edit-save"]')
    expect(writes()).toEqual([])
    await type(w, '[data-testid="edit-cleanup-hours"]', '720')
    expect(w.find('[data-testid="edit-cleanup-hours-invalid"]').exists()).toBe(false)
    await click(w, '[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /)).toEqual([{ workspace_cleanup_retention_hours: 720 }])
  })

  it('empty/long names send nothing; create 403 keeps the form and input without reporting success', async () => {
    const w = await open('/settings/system/agents')
    await click(w, '[data-testid="agent-new"]')
    expect(w.findAll('[data-cap]').map((c) => c.attributes('data-cap'))).toEqual(['ai_cli','ai_api','storage'])
    expect(w.find('[data-testid="agent-location"]').exists()).toBe(true)
    expect(w.find('[data-testid="agent-enabled"]').exists()).toBe(true)
    expect(w.find('[data-testid="agent-path"]').exists()).toBe(false)
    expect(w.get('[data-testid="agent-name"]').attributes('maxlength')).toBe('100')
    await click(w, '[data-testid="agent-create-submit"]')
    await type(w, '[data-testid="agent-name"]', 'a'.repeat(101))
    expect(w.get('[data-testid="agent-create-submit"]').attributes('disabled')).toBeDefined()
    expect(writes()).toEqual([])
    await type(w, '[data-testid="agent-name"]', 'kept-name')
    net.failNext = { call: /^POST \/api\/v1\/system\/agents$/, status: 403, data: { detail: 'Forbidden' } }
    await click(w, '[data-testid="agent-create-submit"]')
    expect(w.get('[data-testid="agent-form-error"]').text()).toBe('권한이 없습니다.')
    expect((w.get('[data-testid="agent-name"]').element as HTMLInputElement).value).toBe('kept-name')
    expect(w.find('[data-testid="agent-enrollment"]').exists()).toBe(false)
    expect(toastText(w)).toBe('')
  })

  it.each([401,403,500])('list GET %i is an error with GET-only retry, never an empty list', async (status) => {
    net.failListGet = status
    const w = await open('/settings/system/agents')
    expect(w.find('[data-testid="agents-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="agents-empty"]').exists()).toBe(false)
    net.failListGet = 0
    net.agents = []
    await click(w, '[data-testid="agents-retry"]')
    expect(w.find('[data-testid="agents-empty"]').exists()).toBe(true)
    expect(writes()).toEqual([])
  })

  it('a non-administrator cannot open the management view or send its GET', async () => {
    setCurrentUser({ ...ADMIN, isAdmin: false })
    const w = await open('/settings/system/agents')
    expect(w.find('[data-panel="system-agents"]').exists()).toBe(false)
    expect(net.requests).toEqual([])
  })
})

describe('V17 — enrollment, failures and token lifetime in the new flow', () => {
  const REFUSALS = [
    { status: 403, data: { detail: 'Forbidden' }, text: '권한이 없습니다.' },
    { status: 409, data: { ok: false, error: { code: 'enrollment_not_allowed', message: 'x', details: {} } }, text: '사용 중인 원격 에이전트만 등록 토큰을 발급할 수 있습니다.' },
    { status: 503, data: { ok: false, error: { code: 'temporarily_unavailable', message: 'x', details: {} } }, text: '서버에 에이전트 비밀키(FLOWGATE_AGENT_PEPPER)가 설정되어 있지 않습니다. 다시 시도해도 해결되지 않으니 서버 관리자에게 비밀 설정 확인과 서버 재시작을 요청하세요.' },
  ]

  it.each(REFUSALS)('issuance $status retains the created ID; retry issues only, never creates a duplicate', async ({status,data,text}) => {
    const w = await open('/settings/system/agents')
    net.failNext = { call: /enrollments$/, status, data }
    await create(w)
    expect(w.get('[data-testid="enrollment-error"]').text()).toBe(text)
    expect(w.find('[data-testid="enrollment-token"]').exists()).toBe(false)
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    expect(toastText(w)).toBe('')
    await click(w, '[data-testid="enrollment-retry"]')
    expect(writes()).toEqual(['POST /api/v1/system/agents','POST /api/v1/system/agents/agt_new1/enrollments','POST /api/v1/system/agents/agt_new1/enrollments'])
    expect(net.agents.filter((a) => a.name === 'new-machine')).toHaveLength(1)
    expectTokenOnlyInBox(w, server('agt_new1')!.pending!.token, true)
  })

  it('cancelling registration sends nothing, while repeated submit during issuance sends exactly one create and one issue', async () => {
    const w = await open('/settings/system/agents')
    await click(w, '[data-testid="agent-new"]')
    await click(w, '[data-testid="agent-create-cancel"]')
    expect(writes()).toEqual([])
    net.holdCall = /enrollments$/
    await create(w)
    expect(w.find('[data-testid="agent-create-submit"]').exists()).toBe(false)
    await click(w, '[data-testid="agent-new"]')
    expect(writes()).toHaveLength(2)
    net.release!()
    await flushPromises()
    expectTokenOnlyInBox(w, server('agt_new1')!.pending!.token, true)
  })

  it('an issuance failure and failed confirmation GET still retain the same ID for retry', async () => {
    const w = await open('/settings/system/agents')
    net.failNext = { call: /enrollments$/, status: 503, data: { detail: 'boom' } }
    net.failListGet = 500
    await create(w)
    expect(w.find('[data-testid="enrollment-retry"]').exists()).toBe(true)
    expect(w.find('[data-testid="agents-refresh-error"]').exists()).toBe(true)
    net.failListGet = 0
    await click(w, '[data-testid="enrollment-retry"]')
    expect(net.seq).toBe(1)
    expectTokenOnlyInBox(w, server('agt_new1')!.pending!.token, true)
  })

  it('issuance succeeds but confirmation fails: retain the only token and retry only GET', async () => {
    const w = await open('/settings/system/agents')
    net.failListGet = 500
    await create(w)
    const token = server('agt_new1')!.pending!.token
    expectTokenOnlyInBox(w, token, true)
    expect(toastText(w)).toContain('결과를 확인하지 못했습니다')
    net.failListGet = 0
    const before = writes().length
    await click(w, '[data-testid="agents-refresh-retry"]')
    expect(writes()).toHaveLength(before)
    expectTokenOnlyInBox(w, token, true)
  })

  it('token expires at the server expiry (600 seconds), is discarded, and retry invalidates the old pending token', async () => {
    vi.useFakeTimers({ toFake: ['Date','setTimeout','clearTimeout'] })
    const w = await open('/settings/system/agents')
    await create(w)
    const token = server('agt_new1')!.pending!.token
    await vi.advanceTimersByTimeAsync(599_000)
    await flushPromises()
    expectTokenOnlyInBox(w, token, true)
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expectTokenOnlyInBox(w, token, false)
    expect(viewState(w).enrollment).toBeNull()
    expect(w.find('[data-testid="enrollment-expired"]').exists()).toBe(true)
    await click(w, '[data-testid="enrollment-retry"]')
    expect(net.seq).toBe(1)
    expect(server('agt_new1')!.pending!.token).not.toBe(token)
    expectTokenOnlyInBox(w, server('agt_new1')!.pending!.token, true)
  })

  it('close, another Agent and screen re-entry never restore a token from GET', async () => {
    const w = await open('/settings/system/agents')
    await create(w)
    const token = server('agt_new1')!.pending!.token
    await click(w, '[data-testid="enrollment-close"]')
    expect(viewState(w).enrollment).toBeNull()
    await pick(w, 'agt_new1')
    expectTokenOnlyInBox(w, token, false)
    await click(w, '[data-testid="agent-editor-close"]')
    await router.push('/settings/system/users')
    await flushPromises()
    await router.push('/settings/system/agents')
    await flushPromises()
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
    await pick(w, 'agt_new1')
    expectTokenOnlyInBox(w, token, false)
  })

  it('an account change discards the displayed token immediately', async () => {
    const w = await open('/settings/system/agents')
    await create(w)
    const token = server('agt_new1')!.pending!.token
    setCurrentUser({ ...ADMIN, username: 'other-admin' })
    await flushPromises()
    expect(viewState(w).enrollment).toBeNull()
    expect(w.find('[data-testid="agent-enrollment"]').exists()).toBe(false)
    expectTokenOnlyInBox(w, token, false)
  })

  it('focus and polling refresh GET without overlap; registration clears the consumed token; close/unmount clear timers', async () => {
    vi.useFakeTimers({ toFake: ['Date','setTimeout','clearTimeout'] })
    const w = await open('/settings/system/agents')
    await create(w)
    const token = server('agt_new1')!.pending!.token
    net.holdList = true
    const before = net.requests.length
    await focus()
    await focus()
    await vi.advanceTimersByTimeAsync(15_000)
    expect(net.requests.length).toBe(before + 1)
    Object.assign(server('agt_new1')!, registered({ fresh: true, pending: null }))
    net.release!()
    await flushPromises()
    expectTokenOnlyInBox(w, token, false)
    expect(w.get('[data-testid="enrollment-state"]').text()).toBe('온라인')
    await click(w, '[data-testid="enrollment-done"]')
    const closed = net.requests.length
    await vi.advanceTimersByTimeAsync(20_000)
    expect(net.requests.length).toBe(closed)
    w.unmount(); wrapper = null
    await focus()
    await vi.advanceTimersByTimeAsync(20_000)
    expect(net.requests.length).toBe(closed)
  })

  it('retry waiting for a GET does not issue another token after the machine has enrolled', async () => {
    const w = await open('/settings/system/agents')
    net.failNext = { call: /enrollments$/, status: 503, data: { detail: 'retry' } }
    await create(w)
    net.holdList = true
    await focus()
    await click(w, '[data-testid="enrollment-retry"]')
    Object.assign(server('agt_new1')!, registered({ fresh: true, pending: null }))
    net.release!()
    await flushPromises()
    expect(writes()).toEqual(['POST /api/v1/system/agents', 'POST /api/v1/system/agents/agt_new1/enrollments'])
    expect(w.get('[data-testid="enrollment-state"]').text()).toBe('온라인')
    expect(w.find('[data-testid="enrollment-error"]').exists()).toBe(false)
  })

  it('copy reports only a generic result, never a token in toast, URL, storage or console', async () => {
    const writeText = vi.fn(async (_text: string) => undefined)
    const previous = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    onTestFinished(() => {
      if (previous) Object.defineProperty(navigator, 'clipboard', previous)
      else delete (navigator as { clipboard?: unknown }).clipboard
    })
    const w = await open('/settings/system/agents')
    await create(w)
    const token = server('agt_new1')!.pending!.token
    await click(w, '[data-testid="enrollment-copy"]')
    expect(writeText).toHaveBeenCalledWith(token)
    expect(toastText(w)).toContain('복사했습니다')
    expectTokenOnlyInBox(w, token, true)
  })
})

describe('V17 — row deletion and failed mutation confirmation', () => {
  it('cancel sends nothing; confirm sends one DELETE and removes the row after GET', async () => {
    const w = await open('/settings/system/agents')
    await deleteRow(w)
    await click(w, '[data-testid="agent-confirm-cancel"]')
    expect(writes()).toEqual([])
    await deleteRow(w)
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['DELETE /api/v1/system/agents/agt_e_new'])
    expect(w.find('[data-agent="agt_e_new"]').exists()).toBe(false)
    expect(net.requests.at(-1)).toBe('GET /api/v1/system/agents')
  })

  it('flowgate.v02test.0019: registration history no longer disables delete; a registered row is deleted', async () => {
    const w = await open('/settings/system/agents')
    for (const id of ['agt_a_online','agt_b_offline','agt_c_incompat','agt_d_revoked']) {
      expect(w.get(`[data-delete-agent="${id}"]`).attributes('disabled')).toBeUndefined()
      expect(w.get(`[data-delete-agent="${id}"]`).element.parentElement?.title).toBe('삭제')
    }
    await deleteRow(w, 'agt_d_revoked')
    expect(w.get('[data-testid="agent-confirm"]').text()).toContain('등록·실행 이력은 남습니다')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(writes()).toEqual(['DELETE /api/v1/system/agents/agt_d_revoked'])
    expect(w.find('[data-agent="agt_d_revoked"]').exists()).toBe(false)
    expect(toastText(w)).toBeTruthy()
  })

  it('flowgate.v02test.0019: 409 agent_has_active_jobs keeps the row and says to delete after the Jobs finish', async () => {
    const w = await open('/settings/system/agents')
    Object.assign(server('agt_a_online')!, { activeJobs: true })
    await deleteRow(w, 'agt_a_online')
    await click(w, '[data-testid="agent-confirm-ok"]')
    expect(w.get('[data-testid="agent-action-error"]').text()).toBe('진행 중인 작업이 있습니다. 작업이 끝난 뒤 삭제하세요.')
    expect(w.find('[data-agent="agt_a_online"]').exists()).toBe(true)
    expect(w.get('[data-delete-agent="agt_a_online"]').attributes('disabled')).toBeUndefined()
    expect(server('agt_a_online')).toBeDefined()
    expect(toastText(w)).toBe('')
  })

  it.each([403,503])('PATCH and DELETE %i retain input and rows without success', async (status) => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    await type(w,'[data-testid="edit-name"]','kept-input')
    net.failNext = { call: /^PATCH /, status, data: { detail: 'refused' } }
    await click(w,'[data-testid="edit-save"]')
    expect(editName(w)).toBe('kept-input')
    expect(server('agt_e_new')!.name).toBe('fresh-remote')
    expect(w.get('[data-testid="agent-action-error"]').text()).toBeTruthy()
    await click(w,'[data-testid="agent-editor-close"]')
    await click(w,'[data-testid="leave-discard"]')
    await deleteRow(w)
    net.failNext = { call: /^DELETE /, status, data: { detail: 'refused' } }
    await click(w,'[data-testid="agent-confirm-ok"]')
    expect(w.find('[data-agent="agt_e_new"]').exists()).toBe(true)
    expect(w.get('[data-testid="agent-action-error"]').text()).toBeTruthy()
    expect(toastText(w)).toBe('')
  })

  it('DELETE 204 followed by a failed GET is unconfirmed; retry sends GET only', async () => {
    const w = await open('/settings/system/agents')
    await deleteRow(w)
    net.failListGet = 500
    await click(w,'[data-testid="agent-confirm-ok"]')
    expect(w.find('[data-agent="agt_e_new"]').exists()).toBe(true)
    expect(toastText(w)).toContain('결과를 확인하지 못했습니다')
    expect(toastText(w)).not.toContain('삭제됨')
    net.failListGet = 0
    await click(w,'[data-testid="agents-refresh-retry"]')
    expect(writes()).toEqual(['DELETE /api/v1/system/agents/agt_e_new'])
    expect(w.find('[data-agent="agt_e_new"]').exists()).toBe(false)
  })

  it('PATCH success with failed GET keeps the dialog unconfirmed and a retry does not repeat PATCH', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    await type(w,'[data-testid="edit-name"]','saved-name')
    net.failListGet = 500
    await click(w,'[data-testid="edit-save"]')
    expect(w.find('[data-testid="edit-saved"]').exists()).toBe(false)
    expect(toastText(w)).toContain('결과를 확인하지 못했습니다')
    net.failListGet = 0
    await click(w,'[data-testid="agents-refresh-retry"]')
    expect(bodiesOf(/^PATCH /)).toEqual([{ name: 'saved-name' }])
    expect(editName(w)).toBe('saved-name')
  })
})

describe('0120 modals and unsaved edits', () => {
  it('backdrop preserves unsaved input; explicit close asks first and Escape still closes', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))
    await flushPromises()
    expect(w.find('[data-testid="agent-detail"]').exists()).toBe(false)
    await pick(w,'agt_e_new')
    await type(w,'[data-testid="edit-name"]','unsaved')
    const overlay = w.get('.ui-modal-overlay')
    await overlay.trigger('mousedown'); await overlay.trigger('mouseup'); await flushPromises()
    expect(w.find('[data-testid="agent-detail"]').exists()).toBe(true)
    expect(editName(w)).toBe('unsaved')
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
    await click(w,'[data-testid="agent-editor-close"]')
    await click(w,'[data-testid="leave-stay"]')
    expect(editName(w)).toBe('unsaved')
    await click(w,'[data-testid="agent-editor-close"]')
    await click(w,'[data-testid="leave-discard"]')
    await pick(w,'agt_e_new')
    expect(editName(w)).toBe('fresh-remote')
    expect(writes()).toEqual([])
  })

  it('an unsaved route move is held; role loss never waits for confirmation', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    await type(w,'[data-testid="edit-name"]','unsaved')
    const move = router.push('/settings/system/users')
    await flushPromises()
    await click(w,'[data-testid="leave-stay"]'); await move
    expect(router.currentRoute.value.path).toBe('/settings/system/agents')
    expect(editName(w)).toBe('unsaved')
    setCurrentUser({ ...ADMIN, isAdmin: false })
    await waitForPath('/settings')
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
    expect(writes()).toEqual([])
  })
})

describe('응답의 수명 — late answers never repopulate secrets or another session', () => {
  it.each(['close','route','role','account','token'] as const)('issuance out, %s changes: no token or post-issuance re-read', async (change) => {
    const w = await open('/settings/system/agents')
    net.holdCall = /enrollments$/
    await create(w)
    const state = viewState(w)
    if (change === 'close') await click(w,'[data-testid="enrollment-close"]')
    if (change === 'route') { await router.push('/settings/system/users'); await waitForPath('/settings/system/users') }
    if (change === 'role') { setCurrentUser({ ...ADMIN, isAdmin: false }); await waitForPath('/settings') }
    if (change === 'account') { setCurrentUser({ ...ADMIN, username: 'other-admin' }); await flushPromises() }
    if (change === 'token') delete window.__accessToken__
    const before = net.requests.length
    net.release!(); await flushPromises()
    expect(net.requests.slice(before)).toEqual([])
    expect(state.enrollment).toBeNull()
    expect(state.toasts).toEqual([])
    expectTokenOnlyInBox(w,server('agt_new1')!.pending!.token,false)
  })

  it('PATCH out, leave without saving: the late answer neither re-reads nor reports', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    const state = viewState(w)
    await type(w,'[data-testid="edit-name"]','late-name')
    net.holdNext = true
    await click(w,'[data-testid="edit-save"]')
    const move = router.push('/settings/system/users')
    await flushPromises()
    expect(w.find('[data-testid="leave-saving"]').exists()).toBe(true)
    await click(w,'[data-testid="leave-discard"]'); await move
    const before = net.requests.length
    net.release!(); await flushPromises()
    expect(net.requests.slice(before)).toEqual([])
    expect(state.editSaved).toBe(false)
    expect(state.actionError).toBe('')
  })

  it('post-PATCH re-read out, role loss: the late list is not applied and no success is reported', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    const state = viewState(w)
    await type(w,'[data-testid="edit-name"]','late-name')
    net.holdList = true
    await click(w,'[data-testid="edit-save"]')
    setCurrentUser({ ...ADMIN, isAdmin: false }); await waitForPath('/settings')
    const before = net.requests.length
    net.release!(); await flushPromises()
    expect(net.requests.slice(before)).toEqual([])
    expect(state.agents).toBeNull()
    expect(state.toasts).toEqual([])
  })
})

describe('편집 확정값 — untouched fields follow GET; only touched fields are PATCHed', () => {
  it('nothing typed: another administrator rename is shown without making the form dirty', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    server('agt_e_new')!.name = 'renamed-elsewhere'
    await focus()
    expect(editName(w)).toBe('renamed-elsewhere')
    expect(w.get('[data-testid="edit-save"]').attributes('disabled')).toBeDefined()
    expect(writes()).toEqual([])
  })

  it('preserves typed fields and rebases untouched AI API and hidden storage before capability PATCH', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    await type(w,'[data-testid="edit-name"]','my-name')
    server('agt_e_new')!.configured = { ai_cli:false, ai_api:false, storage:true }
    await focus()
    expect(editName(w)).toBe('my-name')
    await click(w,'[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /)).toEqual([{name:'my-name'}])
    expect(w.find('[data-testid="agent-detail"]').exists()).toBe(false)
    await pick(w,'agt_e_new')
    await w.get('[data-edit-cap="ai_cli"]').setValue(true)
    server('agt_e_new')!.configured = { ai_cli:false, ai_api:true, storage:true }
    await focus()
    expect([capChecked(w,'ai_cli'),capChecked(w,'ai_api')]).toEqual([true,true])
    await click(w,'[data-testid="edit-save"]')
    expect(bodiesOf(/^PATCH /).at(-1)).toEqual({configured_capabilities:{ai_cli:true,ai_api:true,storage:true}})
  })

  it('an in-flight focus GET finishes before PATCH, preserving a concurrent hidden storage change', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    await w.get('[data-edit-cap="ai_cli"]').setValue(true)
    net.holdList = true
    await focus()
    await click(w,'[data-testid="edit-save"]')
    expect(writes()).toEqual([])
    server('agt_e_new')!.configured.storage = true
    net.release!(); await flushPromises()
    expect(bodiesOf(/^PATCH /)).toEqual([{configured_capabilities:{ai_cli:true,ai_api:false,storage:true}}])
  })

  it('discarded edit stays discarded when switching to create and cancelling', async () => {
    const w = await open('/settings/system/agents')
    await pick(w,'agt_e_new')
    await type(w,'[data-testid="edit-name"]','half-typed')
    await click(w,'[data-testid="agent-new"]')
    await click(w,'[data-testid="leave-stay"]')
    expect(editName(w)).toBe('half-typed')
    await click(w,'[data-testid="agent-new"]')
    await click(w,'[data-testid="leave-discard"]')
    await click(w,'[data-testid="agent-create-cancel"]')
    await pick(w,'agt_e_new')
    expect(editName(w)).toBe('fresh-remote')
    expect(writes()).toEqual([])
  })
})

// The simplified UI has no enable/revoke/reissue controls. Preserve the existing API
// contracts here; server system_agents_0076 also exercises RBAC and actual history/TTL.
describe('existing management API contracts remain available', () => {
  it('enabled PATCH touches enabled only; revoke sends {}, clears pending and retains deletion protection', async () => {
    await updateSystemAgent('agt_a_online',{enabled:false})
    await updateSystemAgent('agt_a_online',{enabled:true})
    await issueAgentEnrollment('agt_a_online')
    await revokeAgentCredential('agt_a_online')
    expect(bodiesOf(/^PATCH /)).toEqual([{enabled:false},{enabled:true}])
    expect(server('agt_a_online')).toMatchObject({enabled:true,credentialActive:false,credentialHistory:true,pending:null})
  })
  it('re-issue replaces one pending token without creating another Agent', async () => {
    const first = await issueAgentEnrollment('agt_e_new')
    const second = await issueAgentEnrollment('agt_e_new')
    expect(first.enrollment_token).not.toBe(second.enrollment_token)
    expect(server('agt_e_new')!.pending!.token).toBe(second.enrollment_token)
    expect(net.seq).toBe(0)
  })
  it.each([403,503])('enable/revoke refusal %i does not mutate credentials or enabled state', async (status) => {
    net.failNext = {call:/^PATCH /,status,data:{detail:'refused'}}
    await expect(updateSystemAgent('agt_a_online',{enabled:false})).rejects.toThrow()
    net.failNext = {call:/credential\/revoke$/,status,data:{detail:'refused'}}
    await expect(revokeAgentCredential('agt_a_online')).rejects.toThrow()
    expect(server('agt_a_online')).toMatchObject({enabled:true,credentialActive:true,credentialHistory:true})
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

