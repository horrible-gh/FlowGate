import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { authRedirect, useAuthStore } from '@login/stores/auth'
import { getRequest, postRequest } from '@shared/api'

// flowgate.v02.0020: 2FA enrolment required by 시스템 › 보안, and invitation acceptance from
// 시스템 › 사용자.
vi.mock('@shared/api', () => ({
  postRequest: vi.fn(),
  getRequest: vi.fn(),
}))

const mockedPost = vi.mocked(postRequest)
const mockedGet = vi.mocked(getRequest)

beforeEach(() => {
  setActivePinia(createPinia())
  mockedPost.mockReset()
  mockedGet.mockReset()
  sessionStorage.clear()
  localStorage.clear()
  vi.spyOn(authRedirect, 'toDashboard').mockImplementation(() => undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('2FA enrolment at sign-in (0020)', () => {
  it('moves to TOTP_SETUP, enrols with the temp token and signs in after the code', async () => {
    mockedPost.mockResolvedValueOnce({ data: { totp_setup_required: true, temp_token: 'tmp-1' } } as never)
    const store = useAuthStore()
    await store.login({ username: 'kim', password: 'pw', remember_me: false })
    expect(store.state).toBe('TOTP_SETUP')
    expect(store.tempToken).toBe('tmp-1')

    mockedPost.mockResolvedValueOnce({
      data: { secret: 'ABCDEFGH', qr_uri: 'otpauth://totp/x?secret=ABCDEFGH', backup_codes: ['A-B-C'], temp_token: 'tmp-enrol' },
    } as never)
    await store.startTotpSetup()
    expect(mockedPost).toHaveBeenLastCalledWith('/auth/totp/enroll', { temp_token: 'tmp-1' })
    expect(store.totpSetup?.secret).toBe('ABCDEFGH')
    expect(store.tempToken).toBe('tmp-enrol')
    expect(store.state).toBe('TOTP_SETUP')

    mockedGet.mockResolvedValueOnce({ data: { is_admin: false, roles: [] } } as never)
    mockedPost.mockResolvedValueOnce({
      data: {
        access_token: 'a', refresh_token: 'r', token_type: 'bearer',
        user: { user_id: 'u', username: 'kim', email: 'k@x', first_login_required: false },
      },
    } as never)
    await store.confirmTotpSetup('123456')
    expect(mockedPost).toHaveBeenLastCalledWith('/auth/totp', { temp_token: 'tmp-enrol', code: '123456' })
    expect(store.state).toBe('SUCCESS')
    expect(store.totpSetup).toBeNull()
  })

  it('stays on the setup step with the error when the code is wrong', async () => {
    const store = useAuthStore()
    store.tempToken = 'tmp-2'
    mockedPost.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401, data: { detail: 'invalid_code' } } })
    await store.confirmTotpSetup('000000')
    expect(store.state).toBe('TOTP_SETUP')
    expect(store.error).toBe('invalid_code')
  })

  it('returns to sign-in when the unsaved enrolment expires, and restarts on the next sign-in', async () => {
    const store = useAuthStore()
    store.tempToken = 'tmp-enrol'
    store.totpSetup = { secret: 'OLD', qr_uri: 'otpauth://x', backup_codes: [], temp_token: 'tmp-enrol' }
    store.state = 'TOTP_SETUP'
    mockedPost.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401, data: { detail: 'token_expired' } } })
    await store.confirmTotpSetup('123456')
    expect(store.state).toBe('IDLE')
    expect(store.error).toBe('code_expired')
    expect(store.tempToken).toBeNull()
    expect(store.totpSetup).toBeNull()

    mockedPost.mockResolvedValueOnce({ data: { totp_setup_required: true, temp_token: 'tmp-2' } } as never)
    await store.login({ username: 'kim', password: 'pw', remember_me: false })
    expect(store.state).toBe('TOTP_SETUP')
    expect(store.totpSetup).toBeNull()
    expect(store.tempToken).toBe('tmp-2')
  })
})

describe('invitation acceptance (0020)', () => {
  it('loads the invitation, sets the password and returns to sign-in with a notice', async () => {
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

  it('reports an invalid link and a weak password', async () => {
    mockedGet.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404, data: { detail: 'invite_invalid' } } })
    const store = useAuthStore()
    await store.openInvite('bad')
    expect(store.state).toBe('INVITE')
    expect(store.error).toBe('invite_invalid')

    mockedPost.mockRejectedValueOnce({ isAxiosError: true, response: { status: 400, data: { detail: {} } } })
    await store.acceptInvite('short')
    expect(store.error).toBe('policy_violation')
    expect(store.state).toBe('INVITE')
  })
})
