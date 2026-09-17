export type GitErrorScalar = string | number | boolean
export type GitErrorParams = Readonly<Record<string, GitErrorScalar | readonly string[]>>

export interface NormalizedGitError {
  readonly code: string | null
  readonly status: number | null
  readonly params: GitErrorParams
  readonly details: Readonly<Record<string, unknown>>
  readonly network: boolean
}

type Translate = (key: string, params?: Record<string, unknown>) => string

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

function cleanParams(value: unknown): GitErrorParams {
  if (!isRecord(value)) return Object.freeze({})
  const result: Record<string, GitErrorScalar | readonly string[]> = {}
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

function cleanDetails(value: unknown): Readonly<Record<string, unknown>> {
  return Object.freeze(isRecord(value) ? { ...value } : {})
}

export function normalizeGitError(input: unknown): NormalizedGitError {
  const root = isRecord(input) ? input : {}
  const response = isRecord(root.response) ? root.response : {}
  const responseData = isRecord(response.data) ? response.data : {}
  const data = Object.keys(responseData).length ? responseData : root
  const nested = isRecord(data.error) ? data.error : data
  const statusValue = response.status ?? data.status ?? nested.status
  const status = typeof statusValue === 'number' && Number.isInteger(statusValue) ? statusValue : null
  const code = typeof nested.code === 'string' && nested.code.trim() ? nested.code.trim() : null
  const hasResponse = Object.keys(response).length > 0
  return Object.freeze({
    code,
    status,
    params: cleanParams(nested.params),
    details: cleanDetails(nested.details),
    network: !!(root.isAxiosError && !hasResponse),
  })
}

const ERROR_KEYS: Readonly<Record<string, string>> = Object.freeze({
  base_dirty: 'base_dirty',
  base_untracked_conflict: 'base_untracked_conflict',
  dirty_worktree: 'dirty_worktree',
  git_busy: 'git_busy',
  git_unavailable: 'git_unavailable',
  git_error: 'generic',
  review_not_found: 'review_not_found',
  review_not_ready: 'review_not_ready',
  restoration_verification_failed: 'restoration_verification_failed',
  forbidden: 'forbidden',
  invalid_state: 'invalid_state',
  invalid_request: 'invalid_request',
})

export function resolveGitError(
  input: unknown,
  t: Translate,
  fallbackKey?: string,
): string {
  const error = normalizeGitError(input)
  if (error.network) return t('main.git_errors.network')
  const key = error.code ? ERROR_KEYS[error.code] : undefined
  if (key) {
    const params: Record<string, unknown> = { ...error.params }
    if (error.code === 'dirty_worktree' && typeof params.n !== 'number') {
      const files = error.details.files
      if (Array.isArray(files) && files.every((file) => typeof file === 'string')) params.n = files.length
    }
    return t(`main.git_errors.${key}`, params)
  }
  return t(fallbackKey || 'main.git_errors.generic')
}
