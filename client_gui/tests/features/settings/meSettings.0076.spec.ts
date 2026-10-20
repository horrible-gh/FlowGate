import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { RouterView } from 'vue-router'
import type { InternalAxiosRequestConfig } from 'axios'
import api from '@shared/api'
import i18n from '@shared/i18n'
import { RETENTION_FIELD, RETENTION_MIRROR_KEY } from '@shared/aiFinishedCardRetention'
import { registerAppMessages } from '@/app/i18n'
import router from '@/app/router/index'
import { setCurrentUser, useCurrentUser } from '@/core/auth/currentUser'

// flowgate.v02.0076 T#2 (D §2.1/§2.2, §5.2 V07–V10): 내 설정 › 계정 · 화면 and 보안 · 세션 on the
// real /app router (installAuthGuard + installSettingsAccessGuard + SettingsLayout) with the real
// views. Only the transport is replaced: @shared/api's request helpers talk to a small in-memory
// server that keeps the ui-settings row, the session list and the password, and every call is
// recorded with its body, so "요청 0건" and "재조회값" are read off the request list and the
// server state rather than off a mocked view. The axios instance itself (default export) stays
// real, so X-Locale is checked on its own request interceptor.

type Session = { session_id: string; device_label: string | null; ip_display: string | null; created_at: string; last_used_at: string; is_current: boolean }

const net = vi.hoisted(() => ({
  requests: [] as string[],
  bodies: [] as { call: string; body: unknown }[],
  ui: { value: 30, domain: [-1, 0, 30, 60, 120, 180, 360, 720, 1440] as number[], failPatch: 0, failGet: 0 },
  sessions: [] as Session[],
  failSessions: 0,
  password: '',
}))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const FIELD = 'ai_finished_card_retention_minutes'
  const fail = (status: number, data: unknown) => Object.assign(new Error(`HTTP ${status}`), { response: { status, data } })
  const envelope = () => ({
    data: { ok: true, settings: { [FIELD]: net.ui.value, updated_at: null }, is_default: false, domain: { [FIELD]: [...net.ui.domain] } },
  })
  const handle = async (method: string, path: string, body?: unknown) => {
    const call = `${method} ${path}`
    net.requests.push(call)
    if (body !== undefined) net.bodies.push({ call, body })
    if (call === 'GET /api/v1/me/ui-settings') {
      if (net.ui.failGet) throw fail(net.ui.failGet, { detail: 'boom' })
      return envelope()
    }
    if (call === 'PATCH /api/v1/me/ui-settings') {
      if (net.ui.failPatch) throw fail(net.ui.failPatch, { detail: 'Internal Server Error' })
      const value = (body as Record<string, unknown>)[FIELD]
      if (typeof value !== 'number' || !net.ui.domain.includes(value)) {
        throw fail(422, { ok: false, error: { code: 'invalid_request', field: FIELD } })
      }
      net.ui.value = value
      return envelope()
    }
    if (call === 'GET /auth/sessions') {
      if (net.failSessions) throw fail(net.failSessions, { detail: 'Invalid authentication credentials' })
      return { data: { sessions: net.sessions.map((s) => ({ ...s })) } }
    }
    const del = /^DELETE \/auth\/sessions\/(.+)$/.exec(call)
    if (del) {
      const sid = decodeURIComponent(del[1])
      const row = net.sessions.find((s) => s.session_id === sid)
      if (row?.is_current) throw fail(409, { detail: 'current_session_use_logout' })
      if (!row) throw fail(404, { detail: 'session_not_found' })
      net.sessions = net.sessions.filter((s) => s.session_id !== sid)
      return { data: { message: 'Session revoked', session_id: sid } }
    }
    if (call === 'POST /auth/sessions/revoke-others') {
      const before = net.sessions.length
      net.sessions = net.sessions.filter((s) => s.is_current)
      return { data: { message: 'Other sessions revoked', revoked_count: before - net.sessions.length } }
    }
    if (call === 'POST /auth/password/change') {
      const { current_password: current, new_password: next } = body as { current_password: string; new_password: string }
      if (current !== net.password) throw fail(401, { detail: 'current_password_incorrect' })
      if (next === current) throw fail(422, { detail: 'same_as_current' })
      if (next.length < 12) {
        throw fail(400, { detail: { code: 'invalid_password_policy', violations: ['Password must be at least 12 characters long.'] } })
      }
      net.password = next
      net.sessions = net.sessions.filter((s) => s.is_current)
      return { data: { message: 'Password changed successfully', first_login_required: false } }
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
    serverLogout: vi.fn(async () => {
      net.requests.push('POST /auth/logout')
    }),
  }
})

registerAppMessages(i18n)

const Host = defineComponent({ render: () => h(RouterView) })
const USER = { username: 'member', email: 'member@flowgate.local', isAdmin: false }
const DOMAIN = [-1, 0, 30, 60, 120, 180, 360, 720, 1440]
const OLD_PASSWORD = 'OldP@ssw0rd123'
const NEW_PASSWORD = 'NewSecureP@ss123'

function session(id: string, current: boolean): Session {
  return { session_id: id, device_label: current ? 'Chrome on Windows' : 'Firefox on Linux', ip_display: '10.0.0.1', created_at: '2026-10-20T00:00:00+00:00', last_used_at: '2026-10-20T01:00:00+00:00', is_current: current }
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

/** Every request that changes something on the server. */
const writes = () => net.requests.filter((r) => !r.startsWith('GET '))

beforeEach(async () => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  sessionStorage.clear()
  window.__accessToken__ = 'token-me-0076'
  sessionStorage.setItem('fg_access_token', 'token-me-0076')
  net.ui.value = 30
  net.ui.domain = [...DOMAIN]
  net.ui.failPatch = 0
  net.ui.failGet = 0
  net.sessions = [session('s1', true), session('s2', false)]
  net.failSessions = 0
  net.password = OLD_PASSWORD
  setCurrentUser(USER)
  window.history.replaceState({}, '', '/app/')
  await router.replace('/')
  net.requests.length = 0
  net.bodies.length = 0
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setCurrentUser(null)
  delete window.__accessToken__
  i18n.global.locale.value = 'ko'
})

describe('V01 (내 설정 부분) — the hub and the 내 설정 sub-navigation', () => {
  it('a non-administrator gets the 내 설정 card with exactly its two screens, nothing else', async () => {
    const hub = await open('/settings')
    expect(hub.findAll('[data-scope-card]').map((c) => c.attributes('data-scope-card'))).toEqual(['me'])
    expect(hub.get('[data-scope-card="me"]').findAll('.tag').map((t) => t.text())).toEqual(['계정 · 화면', '보안 · 세션'])
    expect(hub.get('[data-scope-card="me"] a').attributes('href')).toBe('/app/settings/me/account')
    expect(net.requests).toEqual([])
    await go('/settings/me/account')
    expect(hub.findAll('.subnav [data-subnav]').map((i) => i.attributes('data-subnav'))).toEqual(['account', 'security'])
    expect(hub.findAll('[data-scope]').map((t) => t.attributes('data-scope'))).toEqual(['me'])
  })
})

describe('V07 개인 정보 / 언어', () => {
  it('shows /auth/me username/email as text with no input and no profile write', async () => {
    const w = await open('/settings/me/account')
    const profile = w.get('[data-card="profile"]')
    expect(w.get('[data-testid="me-username"]').text()).toBe('member')
    expect(w.get('[data-testid="me-email"]').text()).toBe('member@flowgate.local')
    expect(profile.findAll('input, select, textarea, [contenteditable], button').length).toBe(0)
    // The only request of the screen is the ui-settings GET (username/email come from bootstrap's /auth/me).
    expect(net.requests).toEqual(['GET /api/v1/me/ui-settings'])
  })

  it('ko→ja→en: the rendered language, preferred_locale and the next request\'s X-Locale agree; re-entry keeps it', async () => {
    const w = await open('/settings/me/account')
    const seen: string[] = []
    const original = api.defaults.adapter
    api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
      seen.push(String(config.headers.get('X-Locale')))
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    }
    try {
      expect(w.get('[data-locale="ko"]').attributes('aria-pressed')).toBe('true')
      for (const [code, title] of [['ja', 'アカウント · 画面'], ['en', 'Account · display']] as const) {
        await w.get(`[data-locale="${code}"]`).trigger('click')
        await flushPromises()
        expect(i18n.global.locale.value).toBe(code)
        expect(localStorage.getItem('preferred_locale')).toBe(code)
        expect(w.get('.fgs-phead h2').text()).toBe(title)
        expect(w.get(`[data-locale="${code}"]`).attributes('aria-pressed')).toBe('true')
        await api.get('/locale-probe')
      }
    } finally {
      api.defaults.adapter = original
    }
    expect(seen).toEqual(['ja', 'en'])
    // 재진입: leave and come back — still English. A reload starts from preferred_locale, which the
    // shared i18n reads at start-up (client/tests/shared/api.locale.0589 pins that part).
    await go('/settings/me/security')
    await go('/settings/me/account')
    expect(w.get('[data-locale="en"]').attributes('aria-pressed')).toBe('true')
    expect(w.get('.fgs-phead h2').text()).toBe('Account · display')
    expect(localStorage.getItem('preferred_locale')).toBe('en')
    // Switching the language wrote nothing to the server.
    expect(writes()).toEqual([])
  })
})

describe('V08 완료 카드 유지 시간', () => {
  const options = (w: VueWrapper) => w.findAll('[data-testid="me-retention"] option').map((o) => Number(o.attributes('value')))

  it('lists exactly the server domain -1, 0, 30 … 1440 (no 5 minutes), labelled', async () => {
    const w = await open('/settings/me/account')
    expect(options(w)).toEqual(DOMAIN)
    expect(options(w)).not.toContain(5)
    const labels = w.findAll('[data-testid="me-retention"] option').map((o) => o.text())
    expect(labels[0]).toBe('직접 정리할 때까지 유지')
    expect(labels[1]).toBe('표시 안 함')
    expect(labels).toEqual(['직접 정리할 때까지 유지', '표시 안 함', '30분', '1시간', '2시간', '3시간', '6시간', '12시간', '24시간'])
  })

  it('a value outside the shared domain shipped by a server never becomes a choice', async () => {
    net.ui.domain = [5, ...DOMAIN]
    const w = await open('/settings/me/account')
    expect(options(w)).toEqual(DOMAIN)
  })

  it('30→60 saves (PATCH carries only the field), re-entry reads 60; then a failed 120 puts 60 back with an error', async () => {
    const w = await open('/settings/me/account')
    expect((w.get('[data-testid="me-retention"]').element as HTMLSelectElement).value).toBe('30')

    await w.get('[data-testid="me-retention"]').setValue('60')
    await flushPromises()
    expect(net.bodies).toEqual([{ call: 'PATCH /api/v1/me/ui-settings', body: { [RETENTION_FIELD]: 60 } }])
    expect(net.ui.value).toBe(60)
    expect((w.get('[data-testid="me-retention"]').element as HTMLSelectElement).value).toBe('60')
    expect(w.find('[data-testid="me-retention-saved"]').exists()).toBe(true)
    expect(localStorage.getItem(RETENTION_MIRROR_KEY)).toBe('60')

    // 재진입: a new GET answers the stored 60.
    await go('/settings/me/security')
    await go('/settings/me/account')
    expect(net.requests.filter((r) => r === 'GET /api/v1/me/ui-settings')).toHaveLength(2)
    expect((w.get('[data-testid="me-retention"]').element as HTMLSelectElement).value).toBe('60')

    net.ui.failPatch = 500
    await w.get('[data-testid="me-retention"]').setValue('120')
    await flushPromises()
    expect(net.bodies.at(-1)).toEqual({ call: 'PATCH /api/v1/me/ui-settings', body: { [RETENTION_FIELD]: 120 } })
    expect(net.ui.value).toBe(60)
    expect((w.get('[data-testid="me-retention"]').element as HTMLSelectElement).value).toBe('60')
    expect(w.get('[data-testid="me-retention-error"]').attributes('role')).toBe('alert')
    expect(w.get('[data-testid="me-retention-error"]').text()).toBe('저장하지 못했습니다. 이전 값으로 되돌렸습니다.')
    expect(w.find('[data-testid="me-retention-saved"]').exists()).toBe(false)
    expect(localStorage.getItem(RETENTION_MIRROR_KEY)).toBe('60') // the mirror only follows confirmed values
    // A PATCH never carries anything but the retention field.
    for (const { body } of net.bodies) expect(Object.keys(body as object)).toEqual([RETENTION_FIELD])
  })

  it.each([401, 403, 500])('a GET answered %s is an error with a retry — not a default value, no select, no save', async (status) => {
    net.ui.failGet = status
    const w = await open('/settings/me/account')
    expect(w.find('[data-testid="me-retention-load-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="me-retention"]').exists()).toBe(false)
    expect(writes()).toEqual([])
    net.ui.failGet = 0
    await w.get('[data-testid="me-retention-load-error"] button').trigger('click')
    await flushPromises()
    expect((w.get('[data-testid="me-retention"]').element as HTMLSelectElement).value).toBe('30')
  })
})

describe('V09 보안 / 세션 (s1 = 현재, s2 = 다른 세션)', () => {
  const row = (w: VueWrapper, sid: string) => w.find(`[data-session="${sid}"]`)

  it('s2 ends with its DELETE and leaves the list; s1 is never DELETEd and offers logout instead', async () => {
    const w = await open('/settings/me/security')
    expect(net.requests).toEqual(['GET /auth/sessions'])
    expect(row(w, 's1').find('[data-testid="session-current"]').exists()).toBe(true)
    expect(row(w, 's1').find('[data-testid="revoke-session"]').exists()).toBe(false)
    expect(row(w, 's1').find('[data-testid="logout-current"]').exists()).toBe(true)

    await row(w, 's2').get('[data-testid="revoke-session"]').trigger('click')
    await flushPromises()
    expect(net.requests).toEqual(['GET /auth/sessions', 'DELETE /auth/sessions/s2', 'GET /auth/sessions'])
    expect(row(w, 's2').exists()).toBe(false)
    expect(net.sessions.map((s) => s.session_id)).toEqual(['s1'])
    expect(w.get('[data-testid="sessions-notice"]').text()).toBe('세션을 로그아웃했습니다.')
    expect(net.requests).not.toContain('DELETE /auth/sessions/s1')
  })

  it('a session that is already gone (404) is reported as such and the list is re-read', async () => {
    const w = await open('/settings/me/security')
    net.sessions = net.sessions.filter((s) => s.session_id !== 's2') // ended elsewhere meanwhile
    await row(w, 's2').get('[data-testid="revoke-session"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="sessions-notice"]').text()).toBe('이미 종료된 세션입니다. 목록을 다시 불러왔습니다.')
    expect(w.find('[data-testid="sessions-error"]').exists()).toBe(false)
    expect(row(w, 's2').exists()).toBe(false)
  })

  it('revoke-others: cancel sends nothing; confirm sends the one POST and the re-read list keeps only s1', async () => {
    net.sessions.push(session('s3', false))
    const w = await open('/settings/me/security')
    await w.get('[data-testid="revoke-others"]').trigger('click')
    await w.get('[data-testid="confirm-cancel"]').trigger('click')
    await flushPromises()
    expect(net.requests).toEqual(['GET /auth/sessions'])

    await w.get('[data-testid="revoke-others"]').trigger('click')
    await w.get('[data-testid="confirm-ok"]').trigger('click')
    await flushPromises()
    expect(net.requests).toEqual(['GET /auth/sessions', 'POST /auth/sessions/revoke-others', 'GET /auth/sessions'])
    expect(w.findAll('[data-session]').map((r) => r.attributes('data-session'))).toEqual(['s1'])
    expect(w.get('[data-testid="sessions-notice"]').text()).toBe('다른 세션 2개를 로그아웃했습니다.')
    expect(w.find('[data-testid="revoke-others"]').exists()).toBe(false) // nothing left to sign out
  })

  it('wrong current password: no success, old password still valid; right one: success, other sessions gone', async () => {
    net.sessions.push(session('s3', false))
    const w = await open('/settings/me/security')
    const fill = async (current: string, next: string, confirm = next) => {
      await w.get('[data-testid="current-password"]').setValue(current)
      await w.get('[data-testid="new-password"]').setValue(next)
      await w.get('[data-testid="confirm-password"]').setValue(confirm)
      await w.get('form[data-card="password"]').trigger('submit')
      await flushPromises()
    }

    await fill('WrongP@ssw0rd1', NEW_PASSWORD)
    expect(net.bodies.at(-1)).toEqual({ call: 'POST /auth/password/change', body: { current_password: 'WrongP@ssw0rd1', new_password: NEW_PASSWORD } })
    expect(w.get('[data-testid="password-error"]').text()).toContain('현재 비밀번호가 올바르지 않습니다.')
    expect(w.find('[data-testid="password-done"]').exists()).toBe(false)
    expect(net.password).toBe(OLD_PASSWORD)
    expect(net.sessions.map((s) => s.session_id)).toEqual(['s1', 's2', 's3'])

    // A confirmation that does not match never reaches the server.
    const before = net.requests.length
    await fill(OLD_PASSWORD, NEW_PASSWORD, 'Different1!x')
    expect(net.requests.length).toBe(before)
    expect(w.get('[data-testid="password-error"]').text()).toBe('새 비밀번호와 확인 값이 일치하지 않습니다.')

    // The server's policy answer is shown with its violations, not as a success.
    await fill(OLD_PASSWORD, 'Sh0rt!')
    expect(w.get('[data-testid="password-error"]').text()).toContain('새 비밀번호가 비밀번호 규칙에 맞지 않습니다.')
    expect(w.get('[data-testid="password-error"]').text()).toContain('Password must be at least 12 characters long.')
    expect(net.password).toBe(OLD_PASSWORD)

    await fill(OLD_PASSWORD, NEW_PASSWORD)
    expect(w.find('[data-testid="password-error"]').exists()).toBe(false)
    expect(w.get('[data-testid="password-done"]').text()).toBe('비밀번호를 변경했습니다. 다른 세션은 로그아웃되었습니다.')
    expect(net.password).toBe(NEW_PASSWORD)
    expect(net.requests.slice(-2)).toEqual(['POST /auth/password/change', 'GET /auth/sessions'])
    expect(w.findAll('[data-session]').map((r) => r.attributes('data-session'))).toEqual(['s1'])
    expect((w.get('[data-testid="current-password"]').element as HTMLInputElement).value).toBe('')
  })

  it('s1 logout: the existing POST /auth/logout, then the tokens and the signed-in user are gone, /login', async () => {
    localStorage.setItem('fg_refresh_token', 'refresh-me')
    localStorage.setItem('flowgate.user.member.recent', 'x')
    localStorage.setItem('flowgate.user.other.recent', 'keep')
    const w = await open('/settings/me/security')
    const realLocation = window.location
    let href = ''
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...realLocation,
        pathname: realLocation.pathname,
        search: realLocation.search,
        hash: realLocation.hash,
        get href() {
          return href || realLocation.href
        },
        set href(value: string) {
          href = value
        },
      },
    })
    try {
      await row(w, 's1').get('[data-testid="logout-current"]').trigger('click')
      await flushPromises()
      expect(net.requests).toEqual(['GET /auth/sessions', 'POST /auth/logout'])
      expect(net.requests.some((r) => r.startsWith('DELETE'))).toBe(false)
      expect(href).toBe('/login')
      expect(window.__accessToken__).toBeUndefined()
      expect(sessionStorage.getItem('fg_access_token')).toBeNull()
      expect(localStorage.getItem('fg_refresh_token')).toBeNull()
      expect(localStorage.getItem('flowgate.user.member.recent')).toBeNull()
      expect(localStorage.getItem('flowgate.user.other.recent')).toBe('keep')
      expect(useCurrentUser().user.value).toBeNull()
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: realLocation })
    }
  })

  it.each([401, 403, 500])('a session list answered %s is an error with a retry, never an empty list', async (status) => {
    net.failSessions = status
    const w = await open('/settings/me/security')
    expect(w.find('[data-testid="sessions-load-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="sessions-empty"]').exists()).toBe(false)
    expect(w.findAll('[data-session]')).toHaveLength(0)
    net.failSessions = 0
    await w.get('[data-testid="sessions-load-error"] button').trigger('click')
    await flushPromises()
    expect(w.findAll('[data-session]').map((r) => r.attributes('data-session'))).toEqual(['s1', 's2'])
  })
})

describe('V10 TOTP — no re-enrolment, no setup call, no administrator API', () => {
  it('the security screen offers no TOTP setup/re-enrolment control and sends no /totp or admin request', async () => {
    const w = await open('/settings/me/security')
    await go('/settings/me/account')
    await go('/settings/me/security')
    expect(w.text()).not.toMatch(/TOTP|2단계|재등록|OTP/)
    expect(w.findAll('button').map((b) => b.attributes('data-testid'))).toEqual(
      expect.not.arrayContaining(['totp-setup', 'totp-reenrol', 'tfa-reset']),
    )
    expect(net.requests.filter((r) => /\/totp|tfa-reset|\/users\//.test(r))).toEqual([])
  })

  it('no 내 설정 source calls /auth/totp/setup, /users/{uid} or /system/users/{id}/tfa-reset', () => {
    const files = [
      '../../../src/features/settings/views/MeAccountSettingsView.vue',
      '../../../src/features/settings/views/MeSecuritySettingsView.vue',
      '../../../src/features/settings/meApi.ts',
      '../../../src/core/auth/logout.ts',
    ]
    for (const file of files) {
      const code = readFileSync(fileURLToPath(new URL(file, import.meta.url)), 'utf-8')
        .split('\n')
        .filter((line) => !/^\s*(\/\/|\*|<!--)/.test(line))
        .join('\n')
      expect(code).not.toMatch(/totp\/setup|tfa-reset|\/users\/|patchRequest\([^)]*users/)
    }
  })
})
