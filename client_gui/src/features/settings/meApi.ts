// 내 설정 API adapter (flowgate.v02.0076 T#2, D §2.1/§2.2). Only the existing self-service
// contracts are called — GET/PATCH /api/v1/me/ui-settings and the /auth session and password
// routes. The administrator routes (/users/{uid}, /system/users/{id}/tfa-reset) and
// /auth/totp/setup are never called from here.
//
// The /auth routes answer `{"detail": "<code>"}` (a bare string) or `{"detail": {"code"}}`; the
// ui-settings route answers `{"error": {"code"}}`. A known code is shown as
// gui.settings.self.errors.<code>; anything else falls back to the caller's message.

import { deleteRequest, getRequest, patchRequest, postRequest } from '@shared/api'
import {
  RETENTION_DOMAIN_MINUTES,
  RETENTION_FIELD,
  UI_SETTINGS_PATH,
  type UiSettingsResponse,
} from '@shared/aiFinishedCardRetention'
import i18n from '@shared/i18n'

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
    const message = key && i18n.global.te(key) ? String(i18n.global.t(key)) : String(i18n.global.t(`gui.settings.self.errors.${fallbackKey}`))
    throw new MeRequestError(message, code, status, violations)
  }
}

// ── 계정 · 화면: 완료 카드 유지 시간 ─────────────────────────────────────────────

export const getUiSettings = () => call(() => getRequest<UiSettingsResponse>(UI_SETTINGS_PATH), 'load_failed')

/** PATCH only the retention field; nothing else rides along. */
export const saveRetention = (minutes: number) =>
  call(() => patchRequest<UiSettingsResponse>(UI_SETTINGS_PATH, { [RETENTION_FIELD]: minutes }), 'save_failed')

/**
 * The choices to draw: the order the server ships (as AiRunMonitorSettingsView does), kept to
 * the members of the shared domain (-1, 0, 30 … 1440) so no other value — e.g. v7's 5 minutes —
 * can become a choice on this screen.
 */
export function retentionChoices(body: UiSettingsResponse | null | undefined): number[] {
  const allowed = RETENTION_DOMAIN_MINUTES as readonly number[]
  const shipped = body?.domain?.[RETENTION_FIELD]
  const usable = Array.isArray(shipped) ? shipped.filter((v): v is number => typeof v === 'number' && allowed.includes(v)) : []
  return usable.length > 0 ? usable : [...allowed]
}

/** The saved value in a GET/PATCH envelope, or null when the envelope does not carry a valid one. */
export function retentionIn(body: UiSettingsResponse | null | undefined): number | null {
  const value = body?.settings?.[RETENTION_FIELD]
  return typeof value === 'number' && (RETENTION_DOMAIN_MINUTES as readonly number[]).includes(value) ? value : null
}

// ── 보안 · 세션 ───────────────────────────────────────────────────────────────

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
