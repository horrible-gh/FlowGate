export type ApiErrorScalar = string | number | boolean
export type ApiErrorParams = Readonly<Record<string, ApiErrorScalar | readonly string[]>>

export type Translate = (key: string, params?: Record<string, unknown>) => string

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

export function cleanParams(value: unknown): ApiErrorParams {
  if (!isRecord(value)) return Object.freeze({})
  const result: Record<string, ApiErrorScalar | readonly string[]> = {}
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === 'string' || typeof item === 'boolean'
      || (typeof item === 'number' && Number.isFinite(item))) {
      result[key] = item
    } else if (Array.isArray(item) && item.every((entry) => typeof entry === 'string')) {
      result[key] = Object.freeze([...item])
    }
  }
  return Object.freeze(result)
}

export function cleanDetails(value: unknown): Readonly<Record<string, unknown>> {
  return Object.freeze(isRecord(value) ? { ...value } : {})
}

export interface NormalizedApiInput {
  readonly data: Readonly<Record<string, unknown>>
  readonly response: Readonly<Record<string, unknown>>
  readonly network: boolean
}

/**
 * Axios exception/response.data shape unification and network determination, shared by
 * gitErrors.ts's normalizeGitError and this module's normalizeApiError so both resolvers
 * agree on what counts as "no response reached the client" (D0005 §3.5).
 */
export function normalizeApiInput(input: unknown): NormalizedApiInput {
  const root = isRecord(input) ? input : {}
  const response = isRecord(root.response) ? root.response : {}
  const responseData = isRecord(response.data) ? response.data : {}
  const data = Object.keys(responseData).length ? responseData : root
  const hasResponse = Object.keys(response).length > 0
  return Object.freeze({
    data: Object.freeze({ ...data }),
    response: Object.freeze({ ...response }),
    network: !!(root.isAxiosError && !hasResponse),
  })
}

export interface NormalizedApiError {
  readonly code: string | null
  readonly status: number | null
  readonly params: ApiErrorParams
  readonly source: Readonly<Record<string, unknown>>
  readonly network: boolean
}

/**
 * Finds a registered code across the two shapes T0010 §2.1 documents: the Git-style
 * `response.data.error.code` envelope and a `response.data.detail.code` object. A string or
 * array `detail` (FastAPI RequestValidationError, plain HTTPException(detail=str)) has no code.
 * `params` folds in either an explicit `.params` object (ai_repeat_count_out_of_range) or the
 * matched object's own scalar fields (GROUP_AI_RUN_LOCKED's flat `run_live`), whichever exists.
 */
export function normalizeApiError(input: unknown): NormalizedApiError {
  const { data, response, network } = normalizeApiInput(input)
  const errorObj = isRecord(data.error) ? data.error : null
  const detailObj = isRecord(data.detail) ? data.detail : null
  const source = errorObj ?? detailObj ?? {}
  const code = typeof source.code === 'string' && source.code.trim() ? source.code.trim() : null
  const statusValue = response.status ?? data.status
  const status = typeof statusValue === 'number' && Number.isInteger(statusValue) ? statusValue : null
  const paramsSource = isRecord(source.params) ? source.params : source
  return Object.freeze({
    code,
    status,
    params: cleanParams(paramsSource),
    source: Object.freeze({ ...source }),
    network,
  })
}

const VALIDATION_FAILED_KEYS: Readonly<Record<string, string>> = Object.freeze({
  'kind:unsupported_kind': 'main.api_errors.validation_failed.kind_unsupported_kind',
  'model_name:invalid_model_name': 'main.api_errors.validation_failed.model_name_invalid_model_name',
})

function resolveValidationFailedKey(errors: unknown): string {
  if (Array.isArray(errors)) {
    for (const entry of errors) {
      if (!isRecord(entry) || typeof entry.index !== 'undefined') continue
      const field = typeof entry.field === 'string' ? entry.field : ''
      const reason = typeof entry.reason === 'string' ? entry.reason : ''
      const key = VALIDATION_FAILED_KEYS[`${field}:${reason}`]
      if (key) return key
    }
  }
  return 'main.api_errors.validation_failed.generic'
}

/**
 * Renders a registered code's i18n message, or null when the input carries no code this
 * module knows (unregistered code, no code, or malformed params for a known code) and the
 * caller should fall back to its own screen text instead. Network failures always resolve
 * here since every call site treats them the same way.
 */
function resolveRegisteredApiErrorMessage(input: unknown, t: Translate): string | null {
  const error = normalizeApiError(input)
  if (error.network) return t('error.network')

  if (error.code === 'validation_failed') {
    return t(resolveValidationFailedKey(error.source.errors))
  }

  if (error.code === 'ai_repeat_count_out_of_range') {
    const { min, max } = error.params
    if (typeof min === 'number' && Number.isFinite(min) && typeof max === 'number' && Number.isFinite(max)) {
      return t('main.api_errors.ai_repeat_count_out_of_range', { ...error.params })
    }
    return null
  }

  if (error.code === 'GROUP_AI_RUN_LOCKED') {
    return t(error.params.run_live === false
      ? 'main.review_action_bar.ai_lease_orphaned_hint'
      : 'main.review_action_bar.ai_running_hint')
  }

  return null
}

/**
 * Common resolver for non-Git API errors (T0010 §2.2). Priority: registered code with valid
 * params -> unregistered/malformed -> caller's screen fallback key -> common guidance. Never
 * reads response.data.detail/error.message text -- only code + validated params ever reach
 * the returned string.
 */
export function resolveApiError(
  input: unknown,
  t: Translate,
  fallbackKey?: string,
): string {
  return resolveRegisteredApiErrorMessage(input, t) ?? t(fallbackKey || 'error.server')
}

/**
 * Same registered-code priority as resolveApiError, but for callers (extractApiErrorMessage)
 * whose fallback is already a resolved display string rather than a translation key.
 */
export function resolveApiErrorWithFallbackText(
  input: unknown,
  t: Translate,
  fallback: string,
): string {
  return resolveRegisteredApiErrorMessage(input, t) ?? fallback
}
