import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { authRedirect, useAuthStore } from '@login/stores/auth'
import { postRequest } from '@shared/api'

vi.mock('@shared/api', () => ({
  postRequest: vi.fn(),
}))

const mockedPostRequest = vi.mocked(postRequest)

const mockReplace = vi.fn()

const createSuccessResponse = <T,>(data: T) => ({
  data: {
    data,
  },
})

beforeEach(() => {
  setActivePinia(createPinia())
  mockedPostRequest.mockReset()
  mockReplace.mockReset()
  localStorage.clear()
  sessionStorage.clear()
  delete window.__accessToken__
  vi.spyOn(authRedirect, 'toDashboard').mockImplementation(mockReplace)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAuthStore', () => {
  it('transitions login() to TOTP_INPUT', async () => {
    mockedPostRequest.mockResolvedValue(
      createSuccessResponse({
        totp_required: true,
        temp_token: 'temp-123',
      }) as never,
    )

    const store = useAuthStore()
    await store.login({ username: 'alice', password: 'secret', remember_me: false })

    expect(store.state).toBe('TOTP_INPUT')
    expect(store.tempToken).toBe('temp-123')
  })

  it('transitions login() to SUCCESS and stores tokens', async () => {
    mockedPostRequest.mockResolvedValue(
      createSuccessResponse({
        access_token: 'access-1',
        refresh_token: 'refresh-1',
        token_type: 'bearer',
        user: {
          user_id: '1',
          username: 'alice',
          email: 'alice@example.com',
          first_login_required: false,
        },
      }) as never,
    )

    const store = useAuthStore()
    await store.login({ username: 'alice', password: 'secret', remember_me: true })

    expect(store.state).toBe('SUCCESS')
    expect(window.__accessToken__).toBe('access-1')
    expect(localStorage.getItem('fg_refresh_token')).toBe('refresh-1')
    expect(mockReplace).toHaveBeenCalledTimes(1)
  })

  it('transitions login() to LOCKED', async () => {
    mockedPostRequest.mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 423,
        data: {
          locked_until: '2030-01-01T00:00:00Z',
        },
      },
    })

    const store = useAuthStore()
    await store.login({ username: 'alice', password: 'wrong', remember_me: false })

    expect(store.state).toBe('LOCKED')
    expect(store.lockedUntil).toBe('2030-01-01T00:00:00Z')
  })

  it('transitions verifyTotp() to SUCCESS', async () => {
    mockedPostRequest.mockResolvedValue(
      createSuccessResponse({
        access_token: 'access-2',
        refresh_token: 'refresh-2',
        token_type: 'bearer',
        user: {
          user_id: '1',
          username: 'alice',
          email: 'alice@example.com',
          first_login_required: false,
        },
      }) as never,
    )

    const store = useAuthStore()
    store.tempToken = 'temp-123'
    await store.verifyTotp('123456')

    expect(store.state).toBe('SUCCESS')
    expect(window.__accessToken__).toBe('access-2')
    expect(sessionStorage.getItem('fg_refresh_token')).toBe('refresh-2')
  })

  it('transitions verifyBackupCode() to SUCCESS', async () => {
    mockedPostRequest.mockResolvedValue(
      createSuccessResponse({
        access_token: 'access-3',
        refresh_token: 'refresh-3',
        token_type: 'bearer',
        user: {
          user_id: '1',
          username: 'alice',
          email: 'alice@example.com',
          first_login_required: false,
        },
      }) as never,
    )

    const store = useAuthStore()
    store.tempToken = 'temp-456'
    await store.verifyBackupCode('backup-code')

    expect(store.state).toBe('SUCCESS')
    expect(window.__accessToken__).toBe('access-3')
    expect(sessionStorage.getItem('fg_refresh_token')).toBe('refresh-3')
  })

  it('returns backToIdle() to IDLE and clears auth state', () => {
    const store = useAuthStore()
    store.state = 'TOTP_INPUT'
    store.error = 'invalid_code'
    store.tempToken = 'temp-789'
    store.lockedUntil = '2030-01-01T00:00:00Z'
    store.failedCount = 4
    window.__accessToken__ = 'access-4'
    sessionStorage.setItem('fg_refresh_token', 'refresh-4')

    store.backToIdle()

    expect(store.state).toBe('IDLE')
    expect(store.error).toBeNull()
    expect(store.tempToken).toBeNull()
    expect(store.lockedUntil).toBeNull()
    expect(store.failedCount).toBe(0)
    expect(window.__accessToken__).toBeUndefined()
    expect(sessionStorage.getItem('fg_refresh_token')).toBeNull()
  })
})
