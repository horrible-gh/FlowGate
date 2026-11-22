// "내 설정" API adapter (flowgate.v02.0076 T#2, D §2.1/§2.2). Only the existing self-service
// contracts are called — GET/PATCH /api/v1/me/ui-settings and the /auth session and password
// routes. The administrator routes (/users/{uid}, /system/users/{id}/tfa-reset) and
// /auth/totp/setup are never called from here.
//
// The /auth routes answer `{"detail": "<code>"}` (a bare string) or `{"detail": {"code"}}`; the
// ui-settings route answers `{"error": {"code"}}`. A known code is shown as
// gui.settings.self.errors.<code>; anything else falls back to the caller's message.

import { deleteRequest, getRequest, patchRequest, postRequest } from '@shared/api'
import { UI_SETTINGS_PATH, type UiSettingsResponse } from '@shared/aiFinishedCardRetention'
import i18n from '@shared/i18n'
import { TASKBAR_STYLE_FIELD, type TaskbarStyle } from '@/core/overlay/modelessRegistry'

export interface AuthSession {
  session_id: string
  device_label: string | null
  ip_display: string | null
  created_at: string
  last_used_at: string
  is_current: boolean
}

export class MeRequestError extends Error {
  constructor(
    message: string,
    readonly code: string | null,
    readonly status: number | null,
    readonly violations: string[] = [],
    /**
     * the gui.settings.self.errors.* key `message` was made from, so a screen that keeps the
     * error on display can show it again in the current language.
     */
    readonly messageKey: string | null = null,
  ) {
    super(message)
    this.name = 'MeRequestError'
  }
}

interface ErrorBody {
  detail?: unknown
  error?: unknown
}

function describe(error: unknown): { code: string | null; status: number | null; violations: string[] } {
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response
  const status = typeof response?.status === 'number' ? response.status : null
  const body = (response?.data && typeof response.data === 'object' ? response.data : {}) as ErrorBody
  const detail = body.detail
  const nested = (detail && typeof detail === 'object' ? detail : body.error && typeof body.error === 'object' ? body.error : null) as
    | { code?: unknown; violations?: unknown }
    | null
  const code = typeof detail === 'string' && /^[a-z_]+$/.test(detail) ? detail : typeof nested?.code === 'string' ? nested.code : null
  const violations = Array.isArray(nested?.violations) ? nested!.violations.filter((v): v is string => typeof v === 'string') : []
  return { code, status, violations }
}

async function call<T>(request: () => Promise<{ data: T }>, fallbackKey: string): Promise<T> {
  try {
    return (await request()).data
  } catch (error) {
    const { code, status, violations } = describe(error)
    const key = code ? `gui.settings.self.errors.${code}` : ''
    const messageKey = key && i18n.global.te(key) ? key : `gui.settings.self.errors.${fallbackKey}`
    throw new MeRequestError(String(i18n.global.t(messageKey)), code, status, violations, messageKey)
  }
}

// ── Display settings (GET/PATCH /me/ui-settings) ───────────────────────────────────────────────────

export const getUiSettings = () => call(() => getRequest<UiSettingsResponse>(UI_SETTINGS_PATH), 'load_failed')

/** Theme writes carry only their own field; the other stored fields remain untouched. */
export const saveTaskbarStyle = (style: TaskbarStyle) =>
  call(() => patchRequest<UiSettingsResponse>(UI_SETTINGS_PATH, { [TASKBAR_STYLE_FIELD]: style }), 'save_failed')

// ── "보안" · "세션" ───────────────────────────────────────────────────────────────

/** A body without a session list is a failure, not an empty list. */
export async function listSessions(): Promise<AuthSession[]> {
  const data = await call(() => getRequest<{ sessions?: AuthSession[] }>('/auth/sessions'), 'sessions_failed')
  if (!Array.isArray(data?.sessions)) {
    throw new MeRequestError(String(i18n.global.t('gui.settings.self.errors.sessions_failed')), null, null)
  }
  return data.sessions
}

/** Another session only; the server refuses the current one with 409. */
export const revokeSession = (sessionId: string) =>
  call(() => deleteRequest<{ session_id: string }>(`/auth/sessions/${encodeURIComponent(sessionId)}`), 'revoke_failed')

export const revokeOtherSessions = () =>
  call(() => postRequest<{ revoked_count?: number }>('/auth/sessions/revoke-others', {}), 'revoke_failed')

export const changePassword = (currentPassword: string, newPassword: string) =>
  call(
    () => postRequest<{ message?: string }>('/auth/password/change', { current_password: currentPassword, new_password: newPassword }),
    'password_failed',
  )
