import { isAxiosError } from 'axios'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getRequest, postRequest } from '@shared/api'
import { resolveSafeReturnPath } from '@shared/auth'

// flowgate.v02gui.0024 (R0001 §4.2): the sign-in state machine moved here from v0.1's
// client/src/login/stores/auth.ts with the same steps, API calls and token storage. The only
// change is where a successful sign-in lands: /app, or an allow-listed `next`.

export type AuthState =
  | 'IDLE'
  | 'SUBMITTING'
  | 'TOTP_INPUT'
  | 'TOTP_SUBMITTING'
  | 'TOTP_SETUP'
  | 'TOTP_SETUP_SUBMITTING'
  | 'INVITE'
  | 'INVITE_SUBMITTING'
  | 'BACKUP_CODE_INPUT'
  | 'BACKUP_CODE_SUBMITTING'
  | 'PW_CHANGE_REQUIRED'
  | 'LOCKED'
  | 'SUCCESS'
  | 'ERROR'

export interface LoginCredentials {
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
  /** flowgate.v02.0020: the 2FA policy covers this user and the grace period is over. */
  totp_setup_required?: boolean
  temp_token?: string
  access_token?: string
  refresh_token?: string
  token_type?: string
  user?: UserProfile
  pw_change_required?: boolean
}

export interface TotpSetupInfo {
  secret: string
  qr_uri: string
  backup_codes: string[]
  /** Carries the pending enrolment; the code is confirmed with this token. */
  temp_token: string
}

export interface InviteInfo {
  username: string
  email: string | null
}

interface ErrorPayload {
  detail?: string
  locked_until?: string
}

/** Sign-in attempts before the server locks the account (shown as "n/5"). */
export const MAX_LOGIN_ATTEMPTS = 5

/** Where a signed-in user lands when there is no allow-listed `next`. */
export const DEFAULT_LANDING = '/app'

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
  const detail = error.response?.data?.detail
  return typeof detail === 'string' && detail ? detail : 'network'
}

export const authRedirect = {
  /** Landing after sign-in: an allow-listed `next` return path, otherwise /app. */
  target: (): string => {
    const next =
      typeof window !== 'undefined'
        ? resolveSafeReturnPath(new URLSearchParams(window.location.search).get('next'))
        : null
    return next ?? DEFAULT_LANDING
  },
  toDashboard: () => {
    window.location.replace(authRedirect.target())
  },
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
  const totpSetup = ref<TotpSetupInfo | null>(null)
  const invite = ref<InviteInfo | null>(null)
  const inviteToken = ref<string | null>(null)
  const notice = ref<string | null>(null)

  const isBusy = computed(() =>
    ['SUBMITTING', 'TOTP_SUBMITTING', 'BACKUP_CODE_SUBMITTING', 'TOTP_SETUP_SUBMITTING', 'INVITE_SUBMITTING'].includes(
      state.value,
    ),
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

  function enterLocked(caughtError: unknown) {
    lockedUntil.value = getErrorPayload(caughtError)?.locked_until ?? null
    state.value = 'LOCKED'
  }

  async function completeLogin(data: TokenResponse, requirePasswordChange?: boolean) {
    handleTokenResponse(data)

    // Permission info for the first screen after sign-in (B005).
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
      window.__userPermissions__ = {
        is_admin: meData.is_admin,
        roles: meData.roles,
      }
    } catch {
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
    notice.value = null
    rememberMe.value = credentials.remember_me

    try {
      const response = await postRequest<LoginResponse | { data: LoginResponse }>('/auth/login', credentials)
      const data = unwrapResponseData(response.data)

      if (data.totp_required && data.temp_token) {
        tempToken.value = data.temp_token
        state.value = 'TOTP_INPUT'
        return
      }

      if (data.totp_setup_required && data.temp_token) {
        tempToken.value = data.temp_token
        totpSetup.value = null
        state.value = 'TOTP_SETUP'
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
      failedCount.value = Math.min(failedCount.value + 1, MAX_LOGIN_ATTEMPTS)
      if (getErrorStatus(caughtError) === 423) {
        enterLocked(caughtError)
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
        enterLocked(caughtError)
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
        enterLocked(caughtError)
        return
      }
      if (getErrorDetail(caughtError) === 'token_expired') {
        tempToken.value = null
        error.value = 'code_expired'
        state.value = 'IDLE'
        return
      }
      error.value = getErrorDetail(caughtError)
      state.value = 'BACKUP_CODE_INPUT'
    }
  }

  // flowgate.v02.0020 ("시스템" › "보안"): enrolment the 2FA policy requires before sign-in.
  async function startTotpSetup() {
    state.value = 'TOTP_SETUP_SUBMITTING'
    error.value = null
    try {
      const response = await postRequest<TotpSetupInfo | { data: TotpSetupInfo }>('/auth/totp/enroll', {
        temp_token: tempToken.value,
      })
      const setup = unwrapResponseData(response.data)
      // Nothing is saved until the code is confirmed: leaving this step leaves the user
      // unenrolled, and the next sign-in starts the enrolment again with a new key.
      tempToken.value = setup.temp_token
      totpSetup.value = setup
      state.value = 'TOTP_SETUP'
    } catch (caughtError: unknown) {
      if (getErrorDetail(caughtError) === 'token_expired') {
        tempToken.value = null
        error.value = 'code_expired'
        state.value = 'IDLE'
        return
      }
      error.value = getErrorDetail(caughtError)
      state.value = 'TOTP_SETUP'
    }
  }

  async function confirmTotpSetup(code: string) {
    state.value = 'TOTP_SETUP_SUBMITTING'
    error.value = null
    try {
      const response = await postRequest<TokenResponse | { data: TokenResponse }>('/auth/totp', {
        temp_token: tempToken.value,
        code,
      })
      totpSetup.value = null
      await completeLogin(unwrapResponseData(response.data))
    } catch (caughtError: unknown) {
      if (getErrorStatus(caughtError) === 423) {
        enterLocked(caughtError)
        return
      }
      if (getErrorDetail(caughtError) === 'token_expired') {
        // The pending enrolment expired unsaved; signing in again starts over.
        tempToken.value = null
        totpSetup.value = null
        error.value = 'code_expired'
        state.value = 'IDLE'
        return
      }
      error.value = getErrorDetail(caughtError)
      state.value = 'TOTP_SETUP'
    }
  }

  // flowgate.v02.0020 ("시스템" › "사용자" › "초대"): /login?invite=<token>.
  async function openInvite(token: string) {
    inviteToken.value = token
    invite.value = null
    error.value = null
    state.value = 'INVITE_SUBMITTING'
    try {
      const response = await getRequest<InviteInfo | { data: InviteInfo }>('/auth/invite', { token })
      invite.value = unwrapResponseData(response.data)
    } catch (caughtError: unknown) {
      error.value = getErrorStatus(caughtError) === 404 ? 'invite_invalid' : getErrorDetail(caughtError)
    }
    state.value = 'INVITE'
  }

  async function acceptInvite(password: string) {
    state.value = 'INVITE_SUBMITTING'
    error.value = null
    try {
      await postRequest('/auth/invite/accept', { token: inviteToken.value, password })
      inviteToken.value = null
      invite.value = null
      notice.value = 'invite_accepted'
      state.value = 'IDLE'
    } catch (caughtError: unknown) {
      const status = getErrorStatus(caughtError)
      error.value = status === 400 ? 'policy_violation' : status === 404 ? 'invite_invalid' : getErrorDetail(caughtError)
      state.value = 'INVITE'
    }
  }

  async function changePassword(payload: { current_password?: string; new_password: string }) {
    error.value = null
    passwordChangeLoading.value = true

    try {
      await postRequest('/auth/password/change', payload)
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

  function backToTotp() {
    state.value = 'TOTP_INPUT'
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
    totpSetup.value = null
    invite.value = null
    inviteToken.value = null
    clearAccessToken()
    clearRefreshTokens()
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
    totpSetup,
    invite,
    notice,
    isBusy,
    login,
    verifyTotp,
    startTotpSetup,
    confirmTotpSetup,
    openInvite,
    acceptInvite,
    verifyBackupCode,
    changePassword,
    goToBackup,
    backToIdle,
    backToTotp,
  }
})
