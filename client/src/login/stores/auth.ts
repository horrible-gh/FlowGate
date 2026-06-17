import { isAxiosError } from 'axios'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { postRequest, getRequest } from '@shared/api'

export type AuthState =
  | 'IDLE'
  | 'SUBMITTING'
  | 'TOTP_INPUT'
  | 'TOTP_SUBMITTING'
  | 'BACKUP_CODE_INPUT'
  | 'BACKUP_CODE_SUBMITTING'
  | 'PW_CHANGE_REQUIRED'
  | 'LOCKED'
  | 'SUCCESS'
  | 'ERROR'

interface LoginCredentials {
  username: string
  password: string
  remember_me: boolean
}

interface UserProfile {
  user_id: string
  username: string
  email: string
  first_login_required?: boolean
}

interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: UserProfile
}

interface LoginResponse {
  totp_required?: boolean
  temp_token?: string
  access_token?: string
  refresh_token?: string
  token_type?: string
  user?: UserProfile
  pw_change_required?: boolean
}

interface ErrorPayload {
  detail?: string
  locked_until?: string
}

const unwrapResponseData = <T>(payload: T | { data: T }): T => {
  if (payload && typeof payload === 'object' && 'data' in payload) {
    return payload.data
  }
  return payload
}

const getErrorPayload = (error: unknown): ErrorPayload | undefined => {
  if (!isAxiosError<ErrorPayload>(error)) {
    return undefined
  }
  return error.response?.data
}

const getErrorStatus = (error: unknown): number | undefined => {
  if (!isAxiosError(error)) {
    return undefined
  }
  return error.response?.status
}

const getErrorDetail = (error: unknown): string => {
  if (!isAxiosError<ErrorPayload>(error)) {
    return 'network'
  }
  return error.response?.data?.detail ?? 'network'
}

export const authRedirect = {
  toDashboard: () => window.location.replace('/main'),
}

export const useAuthStore = defineStore('auth', () => {
  const state = ref<AuthState>('IDLE')
  const error = ref<string | null>(null)
  const tempToken = ref<string | null>(null)
  const lockedUntil = ref<string | null>(null)
  const user = ref<UserProfile | null>(null)
  const isInitialChange = ref(false)
  const rememberMe = ref(false)
  const failedCount = ref(0)
  const passwordChangeLoading = ref(false)

  const isBusy = computed(() =>
    ['SUBMITTING', 'TOTP_SUBMITTING', 'BACKUP_CODE_SUBMITTING'].includes(state.value),
  )

  function clearRefreshTokens() {
    localStorage.removeItem('fg_refresh_token')
    sessionStorage.removeItem('fg_refresh_token')
  }

  function setAccessToken(token: string) {
    window.__accessToken__ = token
    sessionStorage.setItem('fg_access_token', token)
  }

  function clearAccessToken() {
    sessionStorage.removeItem('fg_access_token')
    delete window.__accessToken__
  }

  function storeRefreshToken(token: string) {
    clearRefreshTokens()
    if (rememberMe.value) {
      localStorage.setItem('fg_refresh_token', token)
      return
    }
    sessionStorage.setItem('fg_refresh_token', token)
  }

  function handleTokenResponse(data: TokenResponse) {
    setAccessToken(data.access_token)
    storeRefreshToken(data.refresh_token)
    user.value = data.user
  }

  async function completeLogin(data: TokenResponse, requirePasswordChange?: boolean) {
    handleTokenResponse(data)
    
    // Load permission info after login (B005)
    try {
      const meResponse = await getRequest<{
        user_id: string
        username: string
        email: string
        is_admin: boolean
        first_login_required: boolean
        roles: string[]
      }>('/auth/me')
      const meData = unwrapResponseData(meResponse.data)
      // Store permission info in global so the settings app can access it
      window.__userPermissions__ = {
        is_admin: meData.is_admin,
        roles: meData.roles,
      }
    } catch {
      // If /auth/me call fails, keep permissions as empty
      window.__userPermissions__ = {
        is_admin: false,
        roles: [],
      }
    }
    
    if (requirePasswordChange || data.user.first_login_required) {
      isInitialChange.value = data.user.first_login_required ?? false
      state.value = 'PW_CHANGE_REQUIRED'
      return
    }
    state.value = 'SUCCESS'
    authRedirect.toDashboard()
  }

  async function login(credentials: LoginCredentials) {
    state.value = 'SUBMITTING'
    error.value = null
    rememberMe.value = credentials.remember_me

    try {
      const response = await postRequest<LoginResponse | { data: LoginResponse }>('/auth/login', credentials)
      const data = unwrapResponseData(response.data)

      if (data.totp_required && data.temp_token) {
        tempToken.value = data.temp_token
        state.value = 'TOTP_INPUT'
        return
      }

      if (data.access_token && data.refresh_token && data.user && data.token_type) {
        await completeLogin(
          {
            access_token: data.access_token,
            refresh_token: data.refresh_token,
            token_type: data.token_type,
            user: data.user,
          },
          data.pw_change_required,
        )
        return
      }

      throw new Error('Unexpected login response')
    } catch (caughtError: unknown) {
      const MAX_ATTEMPTS = 5
      failedCount.value = Math.min(failedCount.value + 1, MAX_ATTEMPTS)
      if (getErrorStatus(caughtError) === 423) {
        lockedUntil.value = getErrorPayload(caughtError)?.locked_until ?? null
        state.value = 'LOCKED'
        return
      }
      error.value = getErrorDetail(caughtError)
      state.value = 'ERROR'
    }
  }

  async function verifyTotp(code: string) {
    state.value = 'TOTP_SUBMITTING'
    error.value = null

    try {
      const response = await postRequest<TokenResponse | { data: TokenResponse }>('/auth/totp', {
        temp_token: tempToken.value,
        code,
      })
      await completeLogin(unwrapResponseData(response.data))
    } catch (caughtError: unknown) {
      if (getErrorStatus(caughtError) === 423) {
        lockedUntil.value = getErrorPayload(caughtError)?.locked_until ?? null
        state.value = 'LOCKED'
        return
      }

      if (getErrorDetail(caughtError) === 'token_expired') {
        tempToken.value = null
        error.value = 'code_expired'
        state.value = 'IDLE'
        return
      }

      error.value = getErrorDetail(caughtError)
      state.value = 'TOTP_INPUT'
    }
  }

  async function verifyBackupCode(backupCode: string) {
    state.value = 'BACKUP_CODE_SUBMITTING'
    error.value = null

    try {
      const response = await postRequest<TokenResponse | { data: TokenResponse }>('/auth/totp/backup', {
        temp_token: tempToken.value,
        backup_code: backupCode,
      })
      await completeLogin(unwrapResponseData(response.data))
    } catch (caughtError: unknown) {
      if (getErrorStatus(caughtError) === 423) {
        lockedUntil.value = getErrorPayload(caughtError)?.locked_until ?? null
        state.value = 'LOCKED'
        return
      }

      error.value = getErrorDetail(caughtError)
      state.value = 'BACKUP_CODE_INPUT'
    }
  }

  async function changePassword(payload: { current_password?: string; new_password: string }) {
    error.value = null
    passwordChangeLoading.value = true

    try {
      await postRequest<{ message: string; first_login_required: false } | { data: { message: string; first_login_required: false } }>('/auth/password/change', payload)
      state.value = 'SUCCESS'
      authRedirect.toDashboard()
    } catch (caughtError: unknown) {
      error.value = getErrorDetail(caughtError)
    } finally {
      passwordChangeLoading.value = false
    }
  }

  function goToBackup() {
    state.value = 'BACKUP_CODE_INPUT'
    error.value = null
  }

  function backToIdle() {
    state.value = 'IDLE'
    error.value = null
    tempToken.value = null
    lockedUntil.value = null
    user.value = null
    isInitialChange.value = false
    rememberMe.value = false
    failedCount.value = 0
    passwordChangeLoading.value = false
    clearAccessToken()
    clearRefreshTokens()
  }

  function backToTotp() {
    state.value = 'TOTP_INPUT'
    error.value = null
  }

  return {
    state,
    error,
    tempToken,
    lockedUntil,
    user,
    isInitialChange,
    rememberMe,
    failedCount,
    passwordChangeLoading,
    isBusy,
    login,
    verifyTotp,
    verifyBackupCode,
    changePassword,
    goToBackup,
    backToIdle,
    backToTotp,
  }
})
