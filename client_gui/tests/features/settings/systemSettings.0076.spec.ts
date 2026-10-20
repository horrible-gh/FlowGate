import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { RouterView } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import router from '@/app/router/index'
import { setCurrentUser } from '@/core/auth/currentUser'

// flowgate.v02.0076 T#3 (D §2.5/§2.7, §5.2 V01·V11–V15·V19·V20): 시스템 › 일반 and 시스템 › 사용자 on
// the real /app router (installAuthGuard + installSettingsAccessGuard + SettingsLayout) with the
// real views. Only the transport is replaced: @shared/api's request helpers talk to an in-memory
// server that keeps the system_settings rows, the storage-root answer, the users and a paused run's
// stored picks, and that answers the way the real routes do (422 texts/codes, 409 codes, the
// Scratch pair written all-or-nothing). Every call is recorded with its body, so "PATCH 1건",
// "두 키만", "요청 0건" and "재조회값" are read off the request list and the server state.
// The server's own behaviour on the real routers/auth/RBAC/store is pinned separately by
// server/tests/test_system_general_users_0076.py.

type Row = { setting_key: string; setting_value: string; value_type: string }
type User = { user_id: string; username: string; email: string | null; is_admin: boolean; state: 'active' | 'invited' | 'disabled'; tfa_enabled: boolean; workspaces: { workspace_id: string; name: string; role: string }[]; last_login_at: string | null; created_at: string | null }

const net = vi.hoisted(() => ({
  requests: [] as string[],
  bodies: [] as { call: string; body: unknown }[],
  rows: {} as Record<string, { value: string; type: string }>,
  root: null as unknown,
  failRoot: 0,
  failSettingsGet: 0,
  failPatch: 0,
  /** Answer the next PATCH with this error, the way the real route shapes it. */
  failPatchWith: null as null | { status: number; data: unknown },
  /** The store fails on the second key of a Scratch pair: the transaction rolls both back (500). */
  failSecondWrite: false,
  /** Answer the PATCH without `updated` (a response the screen cannot confirm). */
  dropUpdated: false,
  /** Store something other than what was sent (a re-read that disagrees). */
  storeOther: null as null | Record<string, string>,
  hold: null as null | (() => void),
  holdNext: false,
  users: [] as User[],
  /** Answer GET /system/users with this status (a re-read after a change that fails). */
  failUsersGet: 0,
  actor: 'u1',
  paused: { review_count: 8, restart_count: 8 },
}))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const fail = (status: number, data: unknown) => Object.assign(new Error(`HTTP ${status}`), { response: { status, data } })
  const ALLOW = new Set(['storage_root', 'log_level', 'ai_repeat_count_max', 'scratch_ttl_value', 'scratch_ttl_unit', 'source_mode'])
  const MINUTES: Record<string, bigint> = { minute: 1n, hour: 60n, day: 1440n }
  const row = (key: string): Row => ({ setting_key: key, setting_value: net.rows[key].value, value_type: net.rows[key].type })
  const listRows = () => Object.keys(net.rows).sort().map(row)
  const summary = () => {
    const live = net.users.filter((u) => u.state !== 'disabled')
    return {
      total: net.users.length,
      active: net.users.filter((u) => u.state === 'active').length,
      invited: net.users.filter((u) => u.state === 'invited').length,
      disabled: net.users.filter((u) => u.state === 'disabled').length,
      admins: live.filter((u) => u.is_admin).length,
      tfa_missing: live.filter((u) => !u.tfa_enabled).length,
      tfa_enabled: live.filter((u) => u.tfa_enabled).length,
      no_workspace: live.filter((u) => !u.workspaces.length).length,
    }
  }
  const findUser = (id: string) => {
    const u = net.users.find((x) => x.user_id === id)
    if (!u) throw fail(404, { detail: { code: 'user_not_found' } })
    return u
  }
  const activeAdmins = () => net.users.filter((u) => u.is_admin && u.state === 'active').length

  function patchSettings(body: { updates?: Record<string, string> }) {
    const updates = body?.updates ?? {}
    if (net.failPatchWith) throw fail(net.failPatchWith.status, net.failPatchWith.data)
    const bad = Object.keys(updates).filter((k) => !ALLOW.has(k))
    if (bad.length) throw fail(422, { detail: `Disallowed setting key(s): ${bad.join(', ')}` })
    if ('ai_repeat_count_max' in updates) {
      const v = updates.ai_repeat_count_max
      if (!/^\s*[0-9]+\s*$/.test(String(v)) || Number(v) < 1 || Number(v) > 30) {
        throw fail(422, { detail: { code: 'ai_repeat_count_out_of_range', params: { min: 1, max: 30 } } })
      }
    }
    if ('scratch_ttl_value' in updates || 'scratch_ttl_unit' in updates) {
      const value = updates.scratch_ttl_value ?? net.rows.scratch_ttl_value.value
      const unit = updates.scratch_ttl_unit ?? net.rows.scratch_ttl_unit.value
      if (!/^[1-9][0-9]*$/.test(value)) throw fail(422, { detail: 'scratch_ttl_value must be a positive integer (minimum 1 minute)' })
      if (!(unit in MINUTES)) throw fail(422, { detail: 'scratch_ttl_unit must be one of: minute, hour, day' })
      if (BigInt(value) * MINUTES[unit] > 999999999n * 1440n + 1439n) throw fail(422, { detail: 'scratch_ttl_value is too large' })
      if (net.failSecondWrite) throw fail(500, { detail: 'Internal Server Error' }) // both keys rolled back
    }
    if (net.failPatch) throw fail(net.failPatch, { detail: 'Internal Server Error' })
    for (const [key, value] of Object.entries(updates)) {
      const stored = net.storeOther?.[key] ?? value
      net.rows[key] = { value: stored, type: net.rows[key]?.type ?? 'string' }
    }
    if (net.dropUpdated) return { data: { ok: true } }
    return { data: { updated: Object.keys(updates).map((key) => ({ ...row(key), setting_value: updates[key] })) } }
  }

  const handle = async (method: string, path: string, body?: unknown) => {
    const call = `${method} ${path}`
    net.requests.push(call)
    if (body !== undefined) net.bodies.push({ call, body })
    if (call === 'GET /api/v1/system/storage-root') {
      if (net.failRoot) throw fail(net.failRoot, { detail: net.failRoot === 403 ? 'Forbidden' : 'boom' })
      return { data: net.root }
    }
    if (call === 'GET /api/v1/system/settings') {
      if (net.failSettingsGet) throw fail(net.failSettingsGet, { detail: 'boom' })
      return { data: { settings: listRows() } }
    }
    if (call === 'PATCH /api/v1/system/settings') {
      if (net.holdNext) {
        net.holdNext = false
        await new Promise<void>((resolve) => { net.hold = resolve })
      }
      return patchSettings(body as { updates?: Record<string, string> })
    }
    if (call === 'GET /api/v1/system/users' && net.failUsersGet) {
      throw fail(net.failUsersGet, { detail: net.failUsersGet === 401 ? 'Not authenticated' : net.failUsersGet === 403 ? 'Forbidden' : 'boom' })
    }
    if (call === 'GET /api/v1/system/users') return { data: { users: net.users.map((u) => ({ ...u })), summary: summary(), workspaces: [{ workspace_id: 'ws_default', name: 'FlowGate Workspace' }] } }
    if (call === 'POST /api/v1/system/users') {
      const b = body as Record<string, unknown>
      const u: User = { user_id: `u${net.users.length + 1}`, username: String(b.name), email: String(b.email), is_admin: Boolean(b.is_admin), state: b.start === 'invite' ? 'invited' : 'active', tfa_enabled: false, workspaces: [], last_login_at: null, created_at: '2026-10-20T00:00:00+00:00' }
      net.users.push(u)
      return { data: b.start === 'invite' ? { user: u, invite_path: '/login?invite=tok0076' } : { user: u, temp_password: 'Tmp-0076' } }
    }
    const action = /^(POST|PUT) \/api\/v1\/system\/users\/([^/]+)\/(admin|disable|enable|invite|password-reset|tfa-reset)$/.exec(call)
    if (action) {
      const u = findUser(decodeURIComponent(action[2]))
      const lastAdmin = u.is_admin && u.state === 'active' && activeAdmins() <= 1
      switch (action[3]) {
        case 'admin':
          if (!(body as { is_admin: boolean }).is_admin && lastAdmin) throw fail(409, { detail: { code: 'last_admin' } })
          u.is_admin = (body as { is_admin: boolean }).is_admin
          return { data: { ...u } }
        case 'disable':
          if (lastAdmin) throw fail(409, { detail: { code: 'last_admin' } })
          if (u.user_id === net.actor) throw fail(409, { detail: { code: 'cannot_disable_self' } })
          u.state = 'disabled'
          return { data: { ...u } }
        case 'enable':
          u.state = 'active'
          return { data: { ...u } }
        default:
          return { data: { user: { ...u }, temp_password: 'Tmp-0076' } }
      }
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

function user(id: string, name: string, extra: Partial<User> = {}): User {
  return { user_id: id, username: name, email: `${id}@flowgate.local`, is_admin: false, state: 'active', tfa_enabled: false, workspaces: [], last_login_at: null, created_at: '2026-03-02T00:00:00+00:00', ...extra }
}

let wrapper: VueWrapper | null = null

async function open(url: string): Promise<VueWrapper> {
  window.history.replaceState({}, '', `/app${url}`)
  await router.replace(url)
  wrapper = mount(Host, { global: { plugins: [i18n, router], stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}

async function go(url: string): Promise<void> {
  await router.push(url)
  await flushPromises()
}

/** Start a move without awaiting it: a held move (the leave question) only settles on an answer. */
async function start(url: string): Promise<{ done: Promise<unknown> }> {
  const done = router.push(url)
  await flushPromises()
  return { done }
}

const path = () => router.currentRoute.value.path

/** Fire a cancelable beforeunload (refresh / tab close) and tell whether the view asked to stay. */
function unloadHeld(): boolean {
  const event = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(event)
  return event.defaultPrevented
}

const patches = () => net.bodies.filter((b) => b.call === 'PATCH /api/v1/system/settings').map((b) => b.body)
const writes = () => net.requests.filter((r) => !r.startsWith('GET '))
const stored = (key: string) => net.rows[key]?.value

async function type(w: VueWrapper, selector: string, value: string) {
  await w.get(selector).setValue(value)
  await flushPromises()
}

async function click(w: VueWrapper, selector: string) {
  await w.get(selector).trigger('click')
  await flushPromises()
}

beforeEach(async () => {
  i18n.global.locale.value = 'ko'
  window.__accessToken__ = 'token-system-0076'
  net.rows = {
    ai_repeat_count_max: { value: '10', type: 'integer' },
    log_level: { value: 'INFO', type: 'string' },
    scratch_ttl_unit: { value: 'day', type: 'string' },
    scratch_ttl_value: { value: '7', type: 'integer' },
    source_mode: { value: 'remote', type: 'string' },
    storage_root: { value: '/data/stored-root', type: 'string' },
  }
  net.root = { project_id: null, configured_value: '/data/stored-root', project_override: null, effective_value: '/env/flowgate', effective_source: 'env', env_override: true }
  net.failRoot = 0
  net.failSettingsGet = 0
  net.failPatch = 0
  net.failPatchWith = null
  net.failSecondWrite = false
  net.dropUpdated = false
  net.storeOther = null
  net.hold = null
  net.holdNext = false
  net.actor = 'u1'
  net.failUsersGet = 0
  net.paused = { review_count: 8, restart_count: 8 }
  net.users = [
    user('u1', 'admin', { is_admin: true, tfa_enabled: true, workspaces: [{ workspace_id: 'ws_default', name: 'FlowGate Workspace', role: 'owner' }] }),
    user('u2', 'Kim Minseo', { workspaces: [{ workspace_id: 'ws_default', name: 'FlowGate Workspace', role: 'member' }] }),
    user('u3', 'Park Jiwon', { state: 'invited' }),
  ]
  setCurrentUser(ADMIN)
  window.history.replaceState({}, '', '/app/')
  await router.replace('/')
  net.requests.length = 0
  net.bodies.length = 0
})

afterEach(() => {
  // V20 (this screen's share): nothing here ever writes the Root, project paths/migrate, Git,
  // Workspace or the legacy /users/{uid} routes, and no PATCH carries storage_root or a third key.
  const forbidden = net.requests.filter((r) =>
    /storage\/migrate|\/paths|\/git\/|\/workspaces|^(PATCH|PUT|DELETE|POST) \/api\/v1\/users\/|^PATCH \/api\/v1\/projects/.test(r)
    || (/storage-root/.test(r) && !r.startsWith('GET ')))
  expect(forbidden).toEqual([])
  for (const body of patches()) {
    const keys = Object.keys((body as { updates: Record<string, string> }).updates).sort()
    expect(keys).not.toContain('storage_root')
    expect([['scratch_ttl_unit', 'scratch_ttl_value'], ['ai_repeat_count_max']]).toContainEqual(keys)
  }
  wrapper?.unmount()
  wrapper = null
  setCurrentUser(null)
  delete window.__accessToken__
})

describe('V01 (시스템 부분) — 일반 joins the menus', () => {
  // 시스템 Agent (T#4) sits between them in D §1.2 order, 전체 프로젝트 (T#5) comes last.
  it('A: hub 시스템 card opens 일반 and lists 일반 · 시스템 Agent · 사용자 · 전체 프로젝트; the sub-navigation is the same four', async () => {
    const hub = await open('/settings')
    expect(hub.get('[data-scope-card="system"] a').attributes('href')).toBe('/app/settings/system/general')
    expect(hub.get('[data-scope-card="system"]').findAll('.tag').map((t) => t.text())).toEqual(['일반', '시스템 Agent', '사용자', '전체 프로젝트'])
    expect(net.requests).toEqual([])
    hub.unmount()
    const page = await open('/settings/system/general')
    expect(page.findAll('.subnav [data-subnav]').map((i) => i.attributes('data-subnav'))).toEqual(['general', 'agents', 'users', 'projects'])
    expect(page.get('.subnav [data-subnav="general"]').attributes('aria-current')).toBe('page')
    expect(page.find('.disabled, [aria-disabled="true"]').exists()).toBe(false)
    expect(page.text()).not.toContain('준비 중')
  })
})

describe('V11 시스템 Root — GET /system/storage-root, read only', () => {
  it('shows the stored and the effective Root apart, with the FLOWGATE_STORAGE_DIR notice; no Root control', async () => {
    const w = await open('/settings/system/general')
    expect(net.requests).toEqual(['GET /api/v1/system/storage-root', 'GET /api/v1/system/settings'])
    expect(w.get('[data-testid="root-configured"]').text()).toBe('/data/stored-root')
    expect(w.get('[data-testid="root-effective"]').text()).toBe('/env/flowgate')
    expect(w.get('[data-testid="root-source"]').text()).toBe('환경 변수 FLOWGATE_STORAGE_DIR')
    expect(w.get('[data-testid="root-env-override"]').text()).toContain('우선 적용')
    const card = w.get('[data-card="storage-root"]')
    expect(card.findAll('input, select, textarea, button').length).toBe(0)
    expect(card.text()).toContain('바꾸거나 옮길 수 없습니다')
    expect(net.requests.some((r) => r.includes('/system/settings/storage-root'))).toBe(false)
    expect(writes()).toEqual([])
  })

  it('an unset stored value is "미설정", not a path; the source follows the answer', async () => {
    net.root = { project_id: null, configured_value: null, project_override: null, effective_value: '/srv/flowgate/storage', effective_source: 'default', env_override: false }
    const w = await open('/settings/system/general')
    expect(w.get('[data-testid="root-configured"]').text()).toBe('미설정')
    expect(w.get('[data-testid="root-source"]').text()).toBe('기본 경로')
    expect(w.find('[data-testid="root-env-override"]').exists()).toBe(false)
  })

  it.each([401, 403, 500])('a %s on the Root GET is an error with retry — no value, no fallback request; the settings still load', async (status) => {
    net.failRoot = status
    const w = await open('/settings/system/general')
    expect(w.find('[data-testid="root-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="root-effective"]').exists()).toBe(false)
    expect(net.requests).toEqual(['GET /api/v1/system/storage-root', 'GET /api/v1/system/settings'])
    expect((w.get('[data-testid="scratch-value"]').element as HTMLInputElement).value).toBe('7')
    net.failRoot = 0
    await click(w, '[data-testid="root-error"] button')
    expect(w.get('[data-testid="root-effective"]').text()).toBe('/env/flowgate')
    expect(writes()).toEqual([])
  })

  it.each([401, 403, 500])('a %s on the settings GET shows an error, never default 7 day / 3 editors', async (status) => {
    net.failSettingsGet = status
    const w = await open('/settings/system/general')
    expect(w.find('[data-testid="settings-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="scratch-value"]').exists()).toBe(false)
    expect(w.find('[data-testid="repeat-value"]').exists()).toBe(false)
    expect(writes()).toEqual([])
  })
})

describe('V12 Scratch 정상·검증', () => {
  it('7 day → 6 hour: one PATCH with exactly the two keys as strings, updated and the re-read agree, re-entry shows 6 hour', async () => {
    const w = await open('/settings/system/general')
    expect((w.get('[data-testid="scratch-value"]').element as HTMLInputElement).value).toBe('7')
    expect((w.get('[data-testid="scratch-unit"]').element as HTMLSelectElement).value).toBe('day')
    expect(w.findAll('[data-testid="scratch-unit"] option').map((o) => o.attributes('value'))).toEqual(['minute', 'hour', 'day'])
    expect(w.get('[data-testid="scratch-save"]').attributes('disabled')).toBeDefined() // unchanged
    await type(w, '[data-testid="scratch-value"]', '6')
    await w.get('[data-testid="scratch-unit"]').setValue('hour')
    await click(w, '[data-testid="scratch-save"]')
    expect(patches()).toEqual([{ updates: { scratch_ttl_value: '6', scratch_ttl_unit: 'hour' } }])
    expect(net.requests).toEqual([
      'GET /api/v1/system/storage-root', 'GET /api/v1/system/settings',
      'PATCH /api/v1/system/settings', 'GET /api/v1/system/settings',
    ])
    expect(w.find('[data-testid="scratch-saved"]').exists()).toBe(true)
    expect(w.find('[data-testid="scratch-error"]').exists()).toBe(false)
    expect([stored('scratch_ttl_value'), stored('scratch_ttl_unit')]).toEqual(['6', 'hour'])
    expect(w.get('[data-testid="scratch-save"]').attributes('disabled')).toBeDefined()

    await go('/settings/system/users')
    await go('/settings/system/general')
    expect((wrapper!.get('[data-testid="scratch-value"]').element as HTMLInputElement).value).toBe('6')
    expect((wrapper!.get('[data-testid="scratch-unit"]').element as HTMLSelectElement).value).toBe('hour')
  })

  it.each([['0'], ['-1'], ['1.5'], ['abc'], [''], ['999999999999999999']])('value %j (unit day) is refused on screen: no request is sent', async (value) => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', value)
    expect(w.find('[data-testid="scratch-invalid"]').exists()).toBe(true)
    expect(w.get('[data-testid="scratch-save"]').attributes('disabled')).toBeDefined()
    await click(w, '[data-testid="scratch-save"]')
    expect(patches()).toEqual([])
    expect([stored('scratch_ttl_value'), stored('scratch_ttl_unit')]).toEqual(['7', 'day'])
  })

  it('unit "week" cannot be chosen: the select only offers minute / hour / day', async () => {
    const w = await open('/settings/system/general')
    const select = w.get('[data-testid="scratch-unit"]').element as HTMLSelectElement
    expect([...select.options].map((o) => o.value)).not.toContain('week')
    select.value = 'week' // not an option → nothing selectable
    await w.get('[data-testid="scratch-unit"]').trigger('change')
    await click(w, '[data-testid="scratch-save"]')
    expect(patches()).toEqual([])
  })

  it('the largest pair the server accepts is sendable; one minute more is not', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '1440000000000')
    await w.get('[data-testid="scratch-unit"]').setValue('minute')
    expect(w.find('[data-testid="scratch-invalid"]').text()).toBe('값이 너무 큽니다.')
    await type(w, '[data-testid="scratch-value"]', '1439999999999')
    expect(w.find('[data-testid="scratch-invalid"]').exists()).toBe(false)
    await click(w, '[data-testid="scratch-save"]')
    expect(patches()).toEqual([{ updates: { scratch_ttl_value: '1439999999999', scratch_ttl_unit: 'minute' } }])
  })
})

describe('V13 Scratch 원자성 (화면 쪽)', () => {
  it('the store fails on the second key: 500, both keys unchanged, no success, input kept, no second/compensating PATCH', async () => {
    net.failSecondWrite = true
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    await w.get('[data-testid="scratch-unit"]').setValue('hour')
    await click(w, '[data-testid="scratch-save"]')
    expect(patches()).toEqual([{ updates: { scratch_ttl_value: '6', scratch_ttl_unit: 'hour' } }])
    expect(w.find('[data-testid="scratch-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="scratch-saved"]').exists()).toBe(false)
    expect((w.get('[data-testid="scratch-value"]').element as HTMLInputElement).value).toBe('6')
    expect((w.get('[data-testid="scratch-unit"]').element as HTMLSelectElement).value).toBe('hour')
    expect([stored('scratch_ttl_value'), stored('scratch_ttl_unit')]).toEqual(['7', 'day'])
    expect(writes()).toEqual(['PATCH /api/v1/system/settings'])
  })

  it('an answer without `updated` is not a completed save: error, re-read, input kept', async () => {
    net.dropUpdated = true
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    await w.get('[data-testid="scratch-unit"]').setValue('hour')
    await click(w, '[data-testid="scratch-save"]')
    expect(w.get('[data-testid="scratch-error"]').text()).toContain('저장 결과를 확인하지 못했습니다')
    expect(w.find('[data-testid="scratch-saved"]').exists()).toBe(false)
    expect(net.requests.slice(-2)).toEqual(['PATCH /api/v1/system/settings', 'GET /api/v1/system/settings'])
    expect(writes()).toEqual(['PATCH /api/v1/system/settings'])
  })

  it('a re-read that disagrees with the answer is not a completed save', async () => {
    net.storeOther = { scratch_ttl_value: '5' }
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    await w.get('[data-testid="scratch-unit"]').setValue('hour')
    await click(w, '[data-testid="scratch-save"]')
    expect(w.get('[data-testid="scratch-error"]').text()).toContain('다시 불러온 값이 저장하려던 값과 다릅니다')
    expect(w.find('[data-testid="scratch-saved"]').exists()).toBe(false)
    expect((w.get('[data-testid="scratch-value"]').element as HTMLInputElement).value).toBe('6')
  })

  it('the Scratch save path holds one PATCH with both keys (source check: no per-key save exists)', () => {
    const api = readFileSync(resolve(process.cwd(), 'src/features/settings/api.ts'), 'utf8')
    const view = readFileSync(resolve(process.cwd(), 'src/features/settings/views/SystemGeneralSettingsView.vue'), 'utf8')
    expect(api).toMatch(/updates: \{ scratch_ttl_value: value, scratch_ttl_unit: unit \}/)
    expect(view.match(/saveScratchRetention\(/g)?.length).toBe(1)
  })
})

describe('V14 반복 상한 하향', () => {
  it('10 → 3: the impact notice first; cancel sends nothing; confirm sends ai_repeat_count_max alone; re-read = 3; stored run picks untouched', async () => {
    const w = await open('/settings/system/general')
    expect((w.get('[data-testid="repeat-value"]').element as HTMLInputElement).value).toBe('10')
    await type(w, '[data-testid="repeat-value"]', '3')
    expect(w.get('[data-testid="repeat-lower-hint"]').text()).toContain('현재 상한으로 재평가됩니다')
    await click(w, '[data-testid="repeat-save"]')
    const modal = w.get('[data-testid="repeat-lower-confirm"]')
    expect(modal.text()).toContain('반복 상한 10 → 3')
    expect(modal.text()).toContain('저장·일시정지된 실행의 반복 선택은 이후 실행/재개 시 현재 상한으로 재평가됩니다. 상한을 넘은 기존 선택은 검수 없음 또는 기본 재시작 횟수로 처리될 수 있습니다.')
    expect(modal.text()).toContain('실행별 저장값을 바꾸거나 실행을 재개하지 않습니다')
    await click(w, '[data-testid="repeat-lower-cancel"]')
    expect(w.find('[data-testid="repeat-lower-confirm"]').exists()).toBe(false)
    expect(patches()).toEqual([])

    await click(w, '[data-testid="repeat-save"]')
    await click(w, '[data-testid="repeat-lower-ok"]')
    expect(patches()).toEqual([{ updates: { ai_repeat_count_max: '3' } }])
    expect(net.requests.slice(-2)).toEqual(['PATCH /api/v1/system/settings', 'GET /api/v1/system/settings'])
    expect(w.find('[data-testid="repeat-saved"]').exists()).toBe(true)
    expect(stored('ai_repeat_count_max')).toBe('3')
    expect(net.paused).toEqual({ review_count: 8, restart_count: 8 })
    expect(writes()).toEqual(['PATCH /api/v1/system/settings'])
    expect(net.requests.every((r) => r.includes('/api/v1/system/'))).toBe(true) // no run / resume call
    expect(w.text()).not.toMatch(/자동 보정|3회로 보정/)

    await go('/settings/system/users')
    await go('/settings/system/general')
    expect((wrapper!.get('[data-testid="repeat-value"]').element as HTMLInputElement).value).toBe('3')
  })

  it('raising (3 → 5) saves without the lowering notice', async () => {
    net.rows.ai_repeat_count_max.value = '3'
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '5')
    expect(w.find('[data-testid="repeat-lower-hint"]').exists()).toBe(false)
    await click(w, '[data-testid="repeat-save"]')
    expect(w.find('[data-testid="repeat-lower-confirm"]').exists()).toBe(false)
    expect(patches()).toEqual([{ updates: { ai_repeat_count_max: '5' } }])
    expect(stored('ai_repeat_count_max')).toBe('5')
  })
})

describe('V15 반복 경계 / 저장 오류', () => {
  it('1 and 30 are saved (1 after the lowering confirmation)', async () => {
    net.rows.ai_repeat_count_max.value = '3'
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '30')
    await click(w, '[data-testid="repeat-save"]')
    expect(stored('ai_repeat_count_max')).toBe('30')
    await type(w, '[data-testid="repeat-value"]', '1')
    await click(w, '[data-testid="repeat-save"]')
    await click(w, '[data-testid="repeat-lower-ok"]')
    expect(stored('ai_repeat_count_max')).toBe('1')
    expect(patches()).toEqual([{ updates: { ai_repeat_count_max: '30' } }, { updates: { ai_repeat_count_max: '1' } }])
  })

  it.each([['0'], ['31'], ['2.5'], ['-1'], ['']])('%j is refused on screen: no PATCH', async (value) => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', value)
    expect(w.get('[data-testid="repeat-invalid"]').text()).toBe('1~30 사이의 정수를 입력하세요.')
    expect(w.get('[data-testid="repeat-save"]').attributes('disabled')).toBeDefined()
    await click(w, '[data-testid="repeat-save"]')
    expect(patches()).toEqual([])
    expect(stored('ai_repeat_count_max')).toBe('10')
  })

  it('3 → 5 with a failing answer: input 5 and the error stay, no success notice, no retry', async () => {
    net.rows.ai_repeat_count_max.value = '3'
    net.failPatch = 500
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '5')
    await click(w, '[data-testid="repeat-save"]')
    expect(w.find('[data-testid="repeat-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="repeat-saved"]').exists()).toBe(false)
    expect((w.get('[data-testid="repeat-value"]').element as HTMLInputElement).value).toBe('5')
    expect(stored('ai_repeat_count_max')).toBe('3')
    expect(writes()).toEqual(['PATCH /api/v1/system/settings'])
  })

  it('a server 422 shows the existing ai_repeat_count_out_of_range message and keeps the input', async () => {
    // A value the screen accepts but the route refuses, answered the way the real route answers 0/31.
    net.rows.ai_repeat_count_max.value = '3'
    net.failPatchWith = { status: 422, data: { detail: { code: 'ai_repeat_count_out_of_range', params: { min: 1, max: 30 } } } }
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '5')
    await click(w, '[data-testid="repeat-save"]')
    expect(w.get('[data-testid="repeat-error"]').text()).toBe('반복 상한은 1~30 사이의 정수만 저장할 수 있습니다.')
    expect(w.find('[data-testid="repeat-saved"]').exists()).toBe(false)
    expect((w.get('[data-testid="repeat-value"]').element as HTMLInputElement).value).toBe('5')
    expect(stored('ai_repeat_count_max')).toBe('3')
  })

  it('a second click while saving sends no second PATCH (both editors)', async () => {
    net.rows.ai_repeat_count_max.value = '3'
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '5')
    net.holdNext = true
    await w.get('[data-testid="repeat-save"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="repeat-save"]').attributes('disabled')).toBeDefined()
    await w.get('[data-testid="repeat-save"]').trigger('click')
    await w.get('[data-testid="repeat-save"]').trigger('click')
    expect(patches()).toHaveLength(1)
    net.hold!()
    await flushPromises()
    expect(patches()).toEqual([{ updates: { ai_repeat_count_max: '5' } }])
    expect(w.find('[data-testid="repeat-saved"]').exists()).toBe(true)

    await type(w, '[data-testid="scratch-value"]', '2')
    net.holdNext = true
    await w.get('[data-testid="scratch-save"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="scratch-save"]').trigger('click')
    expect(patches()).toHaveLength(2)
    net.hold!()
    await flushPromises()
    expect(patches()[1]).toEqual({ updates: { scratch_ttl_value: '2', scratch_ttl_unit: 'day' } })
  })

  it('Scratch and 반복 상한 are separate saves: changing both sends two PATCHes, each with its own keys only', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    await type(w, '[data-testid="repeat-value"]', '12')
    await click(w, '[data-testid="repeat-save"]')
    expect(patches()).toEqual([{ updates: { ai_repeat_count_max: '12' } }])
    expect(stored('scratch_ttl_value')).toBe('7') // its own input is still pending
    await click(w, '[data-testid="scratch-save"]')
    expect(patches()).toEqual([{ updates: { ai_repeat_count_max: '12' } }, { updates: { scratch_ttl_value: '6', scratch_ttl_unit: 'day' } }])
    expect(stored('log_level')).toBe('INFO')
    expect(stored('source_mode')).toBe('remote')
    expect(stored('storage_root')).toBe('/data/stored-root')
  })
})

describe('V19 사용자 — Workspace UI gone, guards kept', () => {
  it('no Workspace tile / filter / column / drawer chips / create picker; no disabled "준비 중" row', async () => {
    const w = await open('/settings/system/users')
    expect(net.requests).toEqual(['GET /api/v1/system/users'])
    expect(w.find('[data-testid="tile-nows"]').exists()).toBe(false)
    expect(w.text()).not.toContain('워크스페이스')
    expect(w.text()).not.toContain('FlowGate Workspace')
    expect(w.find('a[href*="/settings/workspaces"]').exists()).toBe(false)
    await click(w, '[data-user="u2"]')
    expect(w.get('[data-testid="user-drawer"]').text()).not.toContain('워크스페이스')
    expect(w.get('[data-testid="user-drawer"]').text()).not.toContain('준비 중')
    expect(w.find('[data-testid="user-drawer"] button[disabled][title]').exists()).toBe(false)
    await click(w, '[data-testid="user-create"]')
    expect(w.find('[data-testid="new-user-ws"]').exists()).toBe(false)
  })

  it('create sends no workspace_id at all, then the list is re-read', async () => {
    const w = await open('/settings/system/users')
    await click(w, '[data-testid="user-create"]')
    await type(w, '[data-testid="new-user-name"]', 'Han Seojun')
    await type(w, '[data-testid="new-user-email"]', 'han@example.com')
    await click(w, '[data-testid="user-create-confirm"]')
    const body = net.bodies.find((b) => b.call === 'POST /api/v1/system/users')!.body as Record<string, unknown>
    expect(body).toEqual({ name: 'Han Seojun', email: 'han@example.com', start: 'invite', is_admin: false })
    expect('workspace_id' in body).toBe(false)
    expect(net.requests).toEqual(['GET /api/v1/system/users', 'POST /api/v1/system/users', 'GET /api/v1/system/users'])
    expect(w.get('[data-testid="credential-value"]').text()).toContain('/login?invite=tok0076')
  })

  it('the last administrator: admin switch and disable are locked in the drawer, no request', async () => {
    const w = await open('/settings/system/users')
    await click(w, '[data-user="u1"]')
    expect(w.get('[data-testid="drawer-admin"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="drawer-disable"]').attributes('disabled')).toBeDefined()
    expect(writes()).toEqual([])
  })

  it('disabling oneself (not the last admin) is refused by the server: the 409 message, no success, state unchanged', async () => {
    net.users.push(user('u4', 'Choi Yuna', { is_admin: true }))
    const w = await open('/settings/system/users')
    await click(w, '[data-user="u1"]')
    await click(w, '[data-testid="drawer-disable"]')
    await click(w, '[data-testid="confirm-ok"]')
    expect(writes()).toEqual(['POST /api/v1/system/users/u1/disable'])
    expect(w.text()).toContain('자신의 계정은 비활성화할 수 없습니다.')
    expect(w.text()).not.toContain('비활성화됨')
    expect(net.users.find((u) => u.user_id === 'u1')!.state).toBe('active')
    expect(w.get('[data-user="u1"]').text()).toContain('활성')
  })

  it('another user: disable after confirming, the re-read list shows it; enable again', async () => {
    const w = await open('/settings/system/users')
    await click(w, '[data-user="u2"]')
    await click(w, '[data-testid="drawer-disable"]')
    await click(w, '[data-testid="confirm-ok"]')
    expect(net.requests).toEqual(['GET /api/v1/system/users', 'POST /api/v1/system/users/u2/disable', 'GET /api/v1/system/users'])
    expect(w.get('[data-user="u2"]').classes()).toContain('dim')
    await click(w, '[data-testid="drawer-enable"]')
    expect(net.requests.slice(-2)).toEqual(['POST /api/v1/system/users/u2/enable', 'GET /api/v1/system/users'])
    expect(w.get('[data-user="u2"]').classes()).not.toContain('dim')
    // Memberships are the server's; the screen never touched them.
    expect(net.users.find((u) => u.user_id === 'u2')!.workspaces).toEqual([{ workspace_id: 'ws_default', name: 'FlowGate Workspace', role: 'member' }])
  })

  it('every users call goes to /api/v1/system/users…, never the legacy /users/{uid}', async () => {
    const w = await open('/settings/system/users')
    await click(w, '[data-user="u2"]')
    await click(w, '[data-testid="drawer-password"]')
    await click(w, '[data-testid="confirm-ok"]')
    await click(w, '[data-testid="drawer-admin"]')
    await click(w, '[data-testid="confirm-ok"]')
    expect(writes()).toEqual(['POST /api/v1/system/users/u2/password-reset', 'PUT /api/v1/system/users/u2/admin'])
    expect(net.requests.every((r) => r.includes('/api/v1/system/users'))).toBe(true)
  })
})

describe('저장 표시 — the success notice belongs to the saved value (D §3.2)', () => {
  it('Scratch: saved 6 hour, then typing 8 (or another unit) takes the notice away; the server keeps 6 hour', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    await w.get('[data-testid="scratch-unit"]').setValue('hour')
    await click(w, '[data-testid="scratch-save"]')
    expect(w.find('[data-testid="scratch-saved"]').exists()).toBe(true)
    await type(w, '[data-testid="scratch-value"]', '8')
    expect(w.find('[data-testid="scratch-saved"]').exists()).toBe(false)
    expect(w.get('[data-testid="scratch-save"]').attributes('disabled')).toBeUndefined() // 8 hour is unsaved
    await type(w, '[data-testid="scratch-value"]', '6')
    await w.get('[data-testid="scratch-unit"]').setValue('day')
    await flushPromises()
    expect(w.find('[data-testid="scratch-saved"]').exists()).toBe(false)
    expect(patches()).toEqual([{ updates: { scratch_ttl_value: '6', scratch_ttl_unit: 'hour' } }])
    expect([stored('scratch_ttl_value'), stored('scratch_ttl_unit')]).toEqual(['6', 'hour'])
  })

  it('반복 상한: saved 5, then typing 8 takes the notice away; the server keeps 5', async () => {
    net.rows.ai_repeat_count_max.value = '3'
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '5')
    await click(w, '[data-testid="repeat-save"]')
    expect(w.find('[data-testid="repeat-saved"]').exists()).toBe(true)
    await type(w, '[data-testid="repeat-value"]', '8')
    expect(w.find('[data-testid="repeat-saved"]').exists()).toBe(false)
    expect(patches()).toEqual([{ updates: { ai_repeat_count_max: '5' } }])
    expect(stored('ai_repeat_count_max')).toBe('5')
  })
})

describe('미저장 이탈 보호 (D §3.2) — 시스템 › 일반', () => {
  it('Scratch edited → 사용자: asks first; 계속 편집 keeps the screen and the input, sends nothing', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    await w.get('[data-testid="scratch-unit"]').setValue('hour')
    const before = [...net.requests]
    const move = await start('/settings/system/users')
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(true)
    expect(path()).toBe('/settings/system/general')
    await click(w, '[data-testid="leave-stay"]')
    await move.done
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
    expect(path()).toBe('/settings/system/general')
    expect((w.get('[data-testid="scratch-value"]').element as HTMLInputElement).value).toBe('6')
    expect((w.get('[data-testid="scratch-unit"]').element as HTMLSelectElement).value).toBe('hour')
    expect(net.requests).toEqual(before) // no request at all, no PATCH
    expect([stored('scratch_ttl_value'), stored('scratch_ttl_unit')]).toEqual(['7', 'day'])
  })

  it('반복 상한 edited → 개요: 저장하지 않고 나가기 leaves; nothing is saved', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '12')
    const move = await start('/settings')
    expect(w.get('[data-testid="leave-confirm"]').text()).toContain('저장하지 않은 변경이 있습니다')
    await click(w, '[data-testid="leave-discard"]')
    await move.done
    await flushPromises()
    expect(path()).toBe('/settings')
    expect(patches()).toEqual([])
    expect(stored('ai_repeat_count_max')).toBe('10')
  })

  it('after a confirmed save the move goes straight through (no question)', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    await click(w, '[data-testid="scratch-save"]')
    await type(w, '[data-testid="repeat-value"]', '12')
    await click(w, '[data-testid="repeat-save"]')
    expect(unloadHeld()).toBe(false)
    await go('/settings/system/users')
    expect(path()).toBe('/settings/system/users')
    expect(wrapper!.find('[data-testid="leave-confirm"]').exists()).toBe(false)
  })

  it('an untouched screen leaves without a question', async () => {
    await open('/settings/system/general')
    expect(unloadHeld()).toBe(false)
    await go('/settings')
    expect(path()).toBe('/settings')
  })

  it('refresh / tab close: the browser question is asked while unsaved, not once saved', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '12')
    expect(unloadHeld()).toBe(true)
    await click(w, '[data-testid="repeat-save"]')
    expect(unloadHeld()).toBe(false)
    await type(w, '[data-testid="scratch-value"]', '6')
    expect(unloadHeld()).toBe(true)
    w.unmount()
    wrapper = null
    expect(unloadHeld()).toBe(false) // the listener goes with the view
  })

  it('a save still out counts as unsaved: the question says it is not cancelled; after it settles the move is free', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    net.holdNext = true
    await w.get('[data-testid="scratch-save"]').trigger('click')
    await flushPromises()
    const move = await start('/settings/system/users')
    expect(w.find('[data-testid="leave-saving"]').exists()).toBe(true)
    await click(w, '[data-testid="leave-stay"]')
    await move.done
    net.hold!()
    await flushPromises()
    expect(w.find('[data-testid="scratch-saved"]').exists()).toBe(true)
    expect(patches()).toHaveLength(1)
    await go('/settings/system/users')
    expect(path()).toBe('/settings/system/users')
  })

  it('losing the administrator role is not held: the screen goes to the overview at once, no question', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    setCurrentUser({ ...ADMIN, isAdmin: false })
    await flushPromises()
    expect(path()).toBe('/settings')
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
    expect(w.find('[data-panel="system-general"]').exists()).toBe(false)
    expect(patches()).toEqual([])
  })

  it('a question already open gives way when the session ends (signed out): the screen is removed', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="repeat-value"]', '12')
    const move = await start('/settings/system/users')
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(true)
    setCurrentUser(null)
    await flushPromises()
    await move.done
    await flushPromises()
    expect(path()).toBe('/settings')
    expect(w.find('[data-panel="system-general"]').exists()).toBe(false)
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
    expect(unloadHeld()).toBe(false)
    expect(patches()).toEqual([])
  })

  it('without a token (signed out elsewhere) a refresh / close is not held', async () => {
    const w = await open('/settings/system/general')
    await type(w, '[data-testid="scratch-value"]', '6')
    expect(unloadHeld()).toBe(true)
    delete window.__accessToken__
    expect(unloadHeld()).toBe(false)
    expect(w.find('[data-testid="leave-confirm"]').exists()).toBe(false)
  })
})

describe('사용자 — a re-read that fails after a change is not a success', () => {
  it.each([401, 403, 500])('disable 200 then GET /system/users %s: error shown over the old list, no success, modal and drawer closed; retry is GET only', async (status) => {
    const w = await open('/settings/system/users')
    await click(w, '[data-user="u2"]')
    await click(w, '[data-testid="drawer-disable"]')
    net.failUsersGet = status
    await click(w, '[data-testid="confirm-ok"]')
    expect(net.requests).toEqual(['GET /api/v1/system/users', 'POST /api/v1/system/users/u2/disable', 'GET /api/v1/system/users'])
    expect(w.find('[data-testid="users-refresh-error"]').exists()).toBe(true)
    expect(w.text()).toContain('사용자 목록을 다시 불러오지 못했습니다')
    expect(w.text()).toContain('결과를 확인하지 못했습니다')
    expect(w.text()).not.toContain('비활성화됨')
    expect(w.find('[data-testid="confirm-ok"]').exists()).toBe(false) // the change cannot be sent again from here
    expect(w.get('[data-testid="user-drawer"]').classes()).not.toContain('on')

    net.failUsersGet = 0
    await click(w, '[data-testid="users-refresh-retry"]')
    expect(net.requests.slice(-1)).toEqual(['GET /api/v1/system/users'])
    expect(writes()).toEqual(['POST /api/v1/system/users/u2/disable']) // never re-sent
    expect(w.find('[data-testid="users-refresh-error"]').exists()).toBe(false)
    expect(w.get('[data-user="u2"]').classes()).toContain('dim')
    expect(w.text()).not.toContain('비활성화됨')
  })

  it.each([
    ['admin', 'drawer-admin', true, 'PUT /api/v1/system/users/u2/admin', '권한 변경됨'],
    ['tfa-reset', 'drawer-tfa-reset', true, 'POST /api/v1/system/users/u2/tfa-reset', '2단계 인증 초기화됨'],
    ['password-reset', 'drawer-password', true, 'POST /api/v1/system/users/u2/password-reset', ''],
    ['enable', 'drawer-enable', false, 'POST /api/v1/system/users/u2/enable', '다시 활성화됨'],
  ])('%s 200 then the re-read 500: no success notice, one write, the unconfirmed warning', async (_name, button, confirm, write, success) => {
    net.users[1] = { ...net.users[1], tfa_enabled: true, state: button === 'drawer-enable' ? 'disabled' : 'active' }
    const w = await open('/settings/system/users')
    await click(w, '[data-user="u2"]')
    if (!confirm) net.failUsersGet = 500 // 다시 활성화 runs at once, without a confirmation
    await click(w, `[data-testid="${button}"]`)
    net.failUsersGet = 500
    if (confirm) await click(w, '[data-testid="confirm-ok"]')
    expect(writes()).toEqual([write])
    expect(net.requests.slice(-1)).toEqual(['GET /api/v1/system/users'])
    expect(w.find('[data-testid="users-refresh-error"]').exists()).toBe(true)
    expect(w.text()).toContain('결과를 확인하지 못했습니다')
    if (success) expect(w.text()).not.toContain(success)
    expect(w.get('[data-testid="user-drawer"]').classes()).not.toContain('on')
  })

  it('create 201 then the re-read 500: the invite link (shown once) stays, no "생성됨", the error stays over the list', async () => {
    const w = await open('/settings/system/users')
    await click(w, '[data-testid="user-create"]')
    await type(w, '[data-testid="new-user-name"]', 'Han Seojun')
    await type(w, '[data-testid="new-user-email"]', 'han@example.com')
    net.failUsersGet = 500
    await click(w, '[data-testid="user-create-confirm"]')
    expect(writes()).toEqual(['POST /api/v1/system/users'])
    expect(w.get('[data-testid="credential-value"]').text()).toContain('/login?invite=tok0076')
    expect(w.text()).not.toContain('Han Seojun 생성됨')
    expect(w.find('[data-testid="users-refresh-error"]').exists()).toBe(true)
    expect(w.text()).toContain('결과를 확인하지 못했습니다')
  })

  it('the re-read succeeding is what lets the success notice show', async () => {
    const w = await open('/settings/system/users')
    await click(w, '[data-user="u2"]')
    await click(w, '[data-testid="drawer-disable"]')
    await click(w, '[data-testid="confirm-ok"]')
    expect(w.text()).toContain('비활성화됨')
    expect(w.find('[data-testid="users-refresh-error"]').exists()).toBe(false)
  })
})
