// 0660 T0004 §2 (RC2) — what the "create next document" dialog says when the server refuses.
//
// The dialog used to read only `response.data.detail`. A TR2 refusal has no `detail`: it is
// the flat TR2 envelope `{code, message, retryable, details: {reason}}`, and a refused
// workflow slot write is `{error: {code, ...}}`. Both fell through to the one generic line
// "빈 문서 생성 중 오류가 발생했습니다." — a worktree that will be ready in a moment and a
// project whose source directory does not exist looked identical. Only code, retryable and
// the public reason enum are read here; server text, paths and stack traces never reach
// the screen.

export type Translate = (key: string, params?: Record<string, unknown>) => string

export interface NextEmptyError {
  text: string
  code: string | null
  /** True only when the server says the same action can succeed later unchanged. */
  retryable: boolean
}

const KEY = 'main.next_empty_doc_modal'

function record(value: unknown): Record<string, any> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : {}
}

/** The TR2 envelope, a `{error: {...}}` envelope or a `{detail: {...}}` object, flattened. */
export function readNextEmptyError(data: unknown): { code: string | null; retryable: boolean; reason: string | null } {
  const root = record(data)
  const source = typeof root.code === 'string' ? root : (record(root.error).code ? record(root.error) : record(root.detail))
  const code = typeof source.code === 'string' && source.code ? source.code : null
  const details = record(source.details)
  const reason = typeof details.reason === 'string' ? details.reason : null
  return { code, retryable: source.retryable === true, reason }
}

/** The message for a refusal's code; null when the code is not one this dialog explains. */
export function nextEmptyCodeText(code: string | null, reason: string | null, t: Translate): string | null {
  switch (code) {
    case 'tr2_git_unavailable':
      return t(`${KEY}.error_tr2_worktree_retry`, {
        reason: t(`${KEY}.reason.${reason === 'git_busy' || reason === 'branch_merge_active' ? reason : 'worktree_provisioning'}`),
      })
    case 'tr2_source_root_missing':
      return t(reason === 'project_name_missing'
        ? `${KEY}.error_tr2_project_name_missing`
        : `${KEY}.error_tr2_source_missing`)
    case 'tr2_worktree_provision_failed':
      return t(`${KEY}.error_tr2_provision_failed`)
    case 'workflow_revert_pending':
      return t('main.git_errors.workflow_revert_pending')
    case 'workflow_slot_occupied':
    case 'workflow_document_already_linked':
      return t(`${KEY}.error_slot_conflict`)
    default:
      return null
  }
}

export function describeNextEmptyError(exc: unknown, t: Translate): NextEmptyError {
  const response = record(record(exc).response)
  const data = response.data
  const { code, retryable, reason } = readNextEmptyError(data)
  const known = nextEmptyCodeText(code, reason, t)
  if (known) return { text: known, code, retryable }
  if (code) return { text: t(`${KEY}.error_create_failed_code`, { code }), code, retryable }
  const detail = record(data).detail
  if (typeof detail === 'string' && detail) return { text: detail, code: null, retryable: false }
  if (Array.isArray(detail)) {
    return { text: detail.map((d: any) => d?.msg ?? d).join(', '), code: null, retryable: false }
  }
  return { text: t(`${KEY}.error_create_failed`), code: null, retryable: false }
}
