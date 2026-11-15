import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getRequest, postRequest } from '@shared/api'
import { authRedirect, useAuthStore } from '@/login/stores/auth'

// flowgate.v02gui.0024 (R0001 §4.2): the sign-in state machine moved from v0.1
// (client/src/login/stores/auth.ts) with the same steps and API calls. These cases carry over
// the v0.1 store specs (auth.spec.ts, authEnrolInvite.0020.spec.ts) and pin the new landing.
vi.mock('@shared/api', () => ({
  postRequest: vi.fn(),
  getRequest: vi.fn(),
}))

const mockedPost = vi.mocked(postRequest)
const mockedGet = vi.mocked(getRequest)
const toDashboard = vi.fn()

const tokenResponse = (n: number, firstLogin = false) => ({
  data: {
    data: {
      access_token: `access-${n}`,
      refresh_token: `refresh-${n}`,
      token_type: 'bearer',
      user: { user_id: '1', username: 'alice', email: 'alice@example.com', first_login_required: firstLogin },
    },
  },
})

beforeEach(() => {
  setActivePinia(createPinia())
  mockedPost.mockReset()
  mockedGet.mockReset()
  mockedGet.mockResolvedValue({ data: { is_admin: false, roles: [] } } as never)
  toDashboard.mockReset()
  localStorage.clear()
  sessionStorage.clear()
  delete window.__accessToken__
  vi.spyOn(authRedirect, 'toDashboard').mockImplementation(toDashboard)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('sign-in state machine (0024)', () => {
  it('moves to TOTP_INPUT when the server asks for a code', async () => {
    mockedPost.mockResolvedValue({ data: { data: { totp_required: true, temp_token: 'temp-123' } } } as never)
    const store = useAuthStore()
    await store.login({ username: 'alice', password: 'secret', remember_me: false })
    expect(mockedPost).toHaveBeenCalledWith('/auth/login', { username: 'alice', password: 'secret', remember_me: false })
    expect(store.state).toBe('TOTP_INPUT')
    expect(store.tempToken).toBe('temp-123')
  })

  it('stores tokens and leaves for the landing page on a direct sign-in', async () => {
    mockedPost.mockResolvedValue(tokenResponse(1) as never)
    const store = useAuthStore()
    await store.login({ username: 'alice', password: 'secret', remember_me: true })
    expect(store.state).toBe('SUCCESS')
    expect(window.__accessToken__).toBe('access-1')
    expect(sessionStorage.getItem('fg_access_token')).toBe('access-1')
    expect(localStorage.getItem('fg_refresh_token')).toBe('refresh-1')
    expect(mockedGet).toHaveBeenCalledWith('/auth/me')
    expect(toDashboard).toHaveBeenCalledTimes(1)
  })

  it('counts failed attempts on invalid credentials', async () => {
    mockedPost.mockRejectedValue({ isAxiosError: true, response: { status: 401, data: { detail: 'invalid_credentials' } } })
    const store = useAuthStore()
    await store.login({ username: 'alice', password: 'wrong', remember_me: false })
    await store.login({ username: 'alice', password: 'wrong', remember_me: false })
    expect(store.state).toBe('ERROR')
    expect(store.error).toBe('invalid_credentials')
    expect(store.failedCount).toBe(2)
  })

  it('moves to LOCKED on 423 with the unlock time', async () => {
    mockedPost.mockRejectedValue({ isAxiosError: true, response: { status: 423, data: { locked_until: '2030-01-01T00:00:00Z' } } })
    const store = useAuthStore()
    await store.login({ username: 'alice', password: 'wrong', remember_me: false })
    expect(store.state).toBe('LOCKED')
    expect(store.lockedUntil).toBe('2030-01-01T00:00:00Z')
  })

  it('signs in with a TOTP code', async () => {
    mockedPost.mockResolvedValue(tokenResponse(2) as never)
    const store = useAuthStore()
    store.tempToken = 'temp-123'
    await store.verifyTotp('123456')
    expect(mockedPost).toHaveBeenCalledWith('/auth/totp', { temp_token: 'temp-123', code: '123456' })
    expect(store.state).toBe('SUCCESS')
    expect(sessionStorage.getItem('fg_refresh_token')).toBe('refresh-2')
  })

  it('signs in with a backup code', async () => {
    mockedPost.mockResolvedValue(tokenResponse(3) as never)
    const store = useAuthStore()
    store.tempToken = 'temp-456'
    await store.verifyBackupCode('backup-code')
    expect(mockedPost).toHaveBeenCalledWith('/auth/totp/backup', { temp_token: 'temp-456', backup_code: 'backup-code' })
    expect(store.state).toBe('SUCCESS')
  })

  it('asks for a new password when the server requires it, then leaves after the change', async () => {
    mockedPost.mockResolvedValueOnce(tokenResponse(4, true) as never)
    const store = useAuthStore()
    await store.login({ username: 'alice', password: 'secret', remember_me: false })
    expect(store.state).toBe('PW_CHANGE_REQUIRED')
    expect(store.isInitialChange).toBe(true)
    expect(toDashboard).not.toHaveBeenCalled()

    mockedPost.mockResolvedValueOnce({ data: { message: 'ok', first_login_required: false } } as never)
    await store.changePassword({ new_password: 'Str0ng-Passw0rd!' })
    expect(mockedPost).toHaveBeenLastCalledWith('/auth/password/change', { new_password: 'Str0ng-Passw0rd!' })
    expect(store.state).toBe('SUCCESS')
    expect(toDashboard).toHaveBeenCalledTimes(1)
  })

  it('enrols TOTP when the policy requires it and signs in after the code', async () => {
    mockedPost.mockResolvedValueOnce({ data: { totp_setup_required: true, temp_token: 'tmp-1' } } as never)
    const store = useAuthStore()
    await store.login({ username: 'kim', password: 'pw', remember_me: false })
    expect(store.state).toBe('TOTP_SETUP')

    mockedPost.mockResolvedValueOnce({
      data: { secret: 'ABCDEFGH', qr_uri: 'otpauth://totp/x?secret=ABCDEFGH', backup_codes: ['A-B-C'], temp_token: 'tmp-enrol' },
    } as never)
    await store.startTotpSetup()
    expect(mockedPost).toHaveBeenLastCalledWith('/auth/totp/enroll', { temp_token: 'tmp-1' })
    expect(store.totpSetup?.secret).toBe('ABCDEFGH')

    mockedPost.mockResolvedValueOnce(tokenResponse(5) as never)
    await store.confirmTotpSetup('123456')
    expect(mockedPost).toHaveBeenLastCalledWith('/auth/totp', { temp_token: 'tmp-enrol', code: '123456' })
    expect(store.state).toBe('SUCCESS')
    expect(store.totpSetup).toBeNull()
  })

  it('returns to sign-in when an unsaved TOTP enrolment expires', async () => {
    const store = useAuthStore()
    store.tempToken = 'tmp-enrol'
    store.totpSetup = { secret: 'OLD', qr_uri: 'otpauth://x', backup_codes: [], temp_token: 'tmp-enrol' }
    store.state = 'TOTP_SETUP'
    mockedPost.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401, data: { detail: 'token_expired' } } })
    await store.confirmTotpSetup('123456')
    expect(store.state).toBe('IDLE')
    expect(store.error).toBe('code_expired')
    expect(store.totpSetup).toBeNull()
  })

  it('accepts an invitation and returns to sign-in with a notice', async () => {
    mockedGet.mockResolvedValueOnce({ data: { username: 'Han Seojun', email: 'han@x.io' } } as never)
    const store = useAuthStore()
    await store.openInvite('tok')
    expect(mockedGet).toHaveBeenCalledWith('/auth/invite', { token: 'tok' })
    expect(store.state).toBe('INVITE')
    expect(store.invite?.username).toBe('Han Seojun')

    mockedPost.mockResolvedValueOnce({ data: { username: 'Han Seojun' } } as never)
    await store.acceptInvite('Str0ng-Passw0rd!')
    expect(mockedPost).toHaveBeenCalledWith('/auth/invite/accept', { token: 'tok', password: 'Str0ng-Passw0rd!' })
    expect(store.state).toBe('IDLE')
    expect(store.notice).toBe('invite_accepted')
  })

  it('reports an invalid invitation link and a weak password', async () => {
    mockedGet.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404, data: { detail: 'invite_invalid' } } })
    const store = useAuthStore()
    await store.openInvite('bad')
    expect(store.error).toBe('invite_invalid')

    mockedPost.mockRejectedValueOnce({ isAxiosError: true, response: { status: 400, data: { detail: {} } } })
    await store.acceptInvite('short')
    expect(store.error).toBe('policy_violation')
    expect(store.state).toBe('INVITE')
  })

  it('backToIdle() clears the step and every stored token', () => {
    const store = useAuthStore()
    store.state = 'TOTP_INPUT'
    store.error = 'invalid_code'
    store.tempToken = 'temp-789'
    store.failedCount = 4
    window.__accessToken__ = 'access-4'
    sessionStorage.setItem('fg_refresh_token', 'refresh-4')

    store.backToIdle()

    expect(store.state).toBe('IDLE')
    expect(store.error).toBeNull()
    expect(store.tempToken).toBeNull()
    expect(store.failedCount).toBe(0)
    expect(window.__accessToken__).toBeUndefined()
    expect(sessionStorage.getItem('fg_refresh_token')).toBeNull()
  })
})

describe('landing after sign-in (0024)', () => {
  const originalLocation = window.location

  const setSearch = (search: string) => {
    delete (window as unknown as { location?: unknown }).location
    ;(window as unknown as { location: { origin: string; search: string; replace: (url: string) => void } }).location = {
      origin: 'http://localhost',
      search,
      replace: vi.fn(),
    }
  }

  afterEach(() => {
    ;(window as unknown as { location: Location }).location = originalLocation
  })

  it('goes to /app when there is no next', () => {
    vi.mocked(authRedirect.toDashboard).mockRestore()
    setSearch('')
    authRedirect.toDashboard()
    expect(window.location.replace).toHaveBeenCalledWith('/app')
  })

  it('goes to an allow-listed /app next', () => {
    setSearch(`?next=${encodeURIComponent('/app/projects?tab=x')}`)
    expect(authRedirect.target()).toBe('/app/projects?tab=x')
  })

  it('ignores external, protocol-relative and removed v0.1 next values', () => {
    for (const next of ['//evil.example.com/app', '/\\evil.example.com/app', 'https://evil.example.com/app', '/main', '/settings/users']) {
      setSearch(`?next=${encodeURIComponent(next)}`)
      expect(authRedirect.target(), next).toBe('/app')
    }
  })

  it('never reads the removed fg_ui GUI-generation choice', () => {
    localStorage.setItem('fg_ui', 'legacy')
    setSearch('')
    expect(authRedirect.target()).toBe('/app')
  })
})
