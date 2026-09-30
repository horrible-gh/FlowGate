// TR2 display state (0565 T0028 §A). Pure mapping from the server read model — the
// server stays the authority for attempts, fingerprints and history; this only decides
// which of the MirageGlass TR2 screens the current facts correspond to.

export type Row = Record<string, any>

/** Server-decided: would approval admit this revision right now (T0030 §7). */
export interface Tr2Readiness {
  ready: boolean
  code: string | null
  loc: string | null
  reason: string | null
  edits?: Row[]
  worktree_clean?: boolean | null
}

export interface Tr2GateAdmissionCommand {
  index: number
  command: string
  state: 'registered' | 'candidate' | 'suppressed'
  registry_row_id: number | null
  origin: string | null
  verified_os: string | null
  shell_complex: boolean
}

export interface Tr2GateAdmission {
  fingerprint: string
  candidate_count: number
  all_candidate: boolean
  commands: Tr2GateAdmissionCommand[]
}

export interface Tr2View {
  document: { doc_id?: string; revision_no: number; doc_review_status?: string; editable?: boolean }
  readiness?: Tr2Readiness
  gate_admission?: Tr2GateAdmission
  mutation?: { allowed: boolean; reason: string | null }
  body: {
    tr2_version: number
    source_t2_doc_id: string
    edit_spec: { termination: string; edits: Row[]; deferred: Row[]; gate: { commands: string[]; apply?: boolean }; verify?: Row; notes?: string }
    baseline_fingerprint?: string
  }
  derived: {
    // exists/drift/live_fingerprint are null when the group worktree cannot be read; readiness
    // then carries the authoritative reason (tr2_git_unavailable).
    files: Array<{ path: string; kind: string; exists: boolean | null; edit_ids: string[] }>
    live_precheck: { source_available?: boolean; drift: boolean | null; baseline_fingerprint: string; live_fingerprint: string | null; anchors?: Row[] | null }
  }
  approval: { latest_attempt: Row | null; attempts: Row[]; retry?: Row }
  history: { source_history_state: string; ledger: Row[] }
}

export type Tr2StateKey =
  | 'ready' | 'not_ready' | 'needs_more_work' | 'drift' | 'applying' | 'rollback' | 'recovery_required'
  | 'validation_failed' | 'apply_failed' | 'commit_failed' | 'precheck_failed'
  | 'approved' | 'rejected' | 'restore_pending' | 'history_conflict' | 'history_invariant'

/** Visual family of a state: drives the pill/strip colour, never the wording. */
export type Tr2Tone = 'safe' | 'live' | 'warn' | 'danger' | 'blocked' | 'done' | 'past'

const FAILURE_BY_CODE: Record<string, Tr2StateKey> = {
  tr2_validation_failed: 'validation_failed',
  tr2_apply_failed: 'apply_failed',
  tr2_edit_not_applicable: 'apply_failed',
  tr2_commit_failed: 'commit_failed',
  tr2_source_drift: 'drift',
}

export function tr2State(view: Tr2View): Tr2StateKey {
  const attempt = view.approval.latest_attempt
  const history = view.history?.source_history_state
  const review = view.document.doc_review_status
  // recovery_required is terminal for the attempt and outranks everything: nothing may
  // move until a person resolves it. A rollback still in flight is a different screen.
  if (attempt?.state === 'recovery_required') return 'recovery_required'
  if (attempt?.state === 'in_progress') return attempt.phase === 'rollback' ? 'rollback' : 'applying'
  if (history === 'conflict') return 'history_conflict'
  if (history === 'invariant_error') return 'history_invariant'
  if (review === 'approved') return history === 'restore_pending' ? 'restore_pending' : 'approved'
  if (review === 'rejected') return 'rejected'
  // A failed attempt only describes the revision it ran against; a newer revision is a
  // fresh proposal and is judged by the live diagnostic instead.
  if (attempt?.state === 'failed' && Number(attempt.document_revision) === Number(view.document.revision_no)) {
    return FAILURE_BY_CODE[String(attempt.error_code ?? '')] ?? 'precheck_failed'
  }
  if (view.derived.live_precheck.drift) return 'drift'
  if (view.body.edit_spec.termination !== 'ready_to_apply') return 'needs_more_work'
  // Green only on the server's word: drift=false and ready_to_apply are not enough when an
  // anchor is missing, a new file already exists or the worktree is dirty (T0030 §7).
  return view.readiness?.ready === true ? 'ready' : 'not_ready'
}

export function tr2Tone(state: Tr2StateKey): Tr2Tone {
  switch (state) {
    case 'ready': return 'safe'
    case 'not_ready': return 'danger'
    case 'applying': case 'rollback': return 'live'
    case 'needs_more_work': case 'drift': case 'restore_pending': return 'warn'
    case 'recovery_required': case 'history_conflict': case 'history_invariant': return 'blocked'
    case 'approved': return 'done'
    case 'rejected': return 'past'
    default: return 'danger'
  }
}

/** Validation results recorded by the attempt that ran against this revision, if any. */
export function gateResults(view: Tr2View): { round: number; commands: Row[] } | null {
  const attempt = view.approval.latest_attempt
  if (!attempt?.validation_json || Number(attempt.document_revision) !== Number(view.document.revision_no)) return null
  try {
    const parsed = typeof attempt.validation_json === 'string' ? JSON.parse(attempt.validation_json) : attempt.validation_json
    return { round: Number(attempt.approval_round ?? 0), commands: Array.isArray(parsed?.commands) ? parsed.commands : [] }
  } catch {
    return null
  }
}

export type ItemCollection = 'edits' | 'deferred'
export type ItemOp = 'edit' | 'create_file' | 'deferred'

export function itemOp(collection: ItemCollection, item: Row): ItemOp {
  if (collection === 'deferred') return 'deferred'
  return item.kind === 'create_file' ? 'create_file' : 'edit'
}

/** Build the item the server validates from an editor draft; the draft may carry fields
 * of another kind (the user switched kinds), which the server would reject. */
export function itemFromDraft(op: ItemOp, draft: Row, original: Row = {}): { collection: ItemCollection; item: Row } {
  const base: Row = { ...original }
  for (const key of ['kind', 'anchor_old', 'replacement_new', 'content', 'confidence', 'reason', 'evidence', 'anchor_status']) delete base[key]
  const common = { id: String(draft.id ?? '').trim(), rationale: String(draft.rationale ?? '') }
  const file = String(draft.file ?? '').trim()
  if (op === 'deferred') {
    const item: Row = { ...base, ...common, reason: draft.reason }
    if (file) item.file = file; else delete item.file
    return { collection: 'deferred', item }
  }
  const shared = { ...base, ...common, file, confidence: draft.confidence }
  if (op === 'create_file') return { collection: 'edits', item: { ...shared, kind: 'create_file', content: String(draft.content ?? '') } }
  const item: Row = { ...shared, kind: 'edit', anchor_old: String(draft.anchor_old ?? ''), replacement_new: String(draft.replacement_new ?? '') }
  for (const key of ['evidence', 'anchor_status']) if (original[key] !== undefined && itemOp('edits', original) === 'edit') item[key] = original[key]
  return { collection: 'edits', item }
}

// ── Error meaning (T0030 §5) ─────────────────────────────────────────────────────
// Every failure is named by what it means for the user; server exception text, paths
// and stack traces never reach the screen. Unknown codes fall back to a generic line.

type Translate = (key: string, params?: Record<string, unknown>) => string
type Exists = (key: string) => boolean

export const BODY_ERROR_KEYS: Record<string, string> = {
  tr2_body_missing: 'body_missing',
  tr2_body_corrupt: 'body_corrupt',
  tr2_body_schema_invalid: 'body_schema_invalid',
  tr2_storage_mismatch: 'storage_mismatch',
}
const APPLY_CODES = new Set(['tr2_validation_failed', 'tr2_apply_failed', 'tr2_commit_failed', 'tr2_recovery_required', 'tr2_history_invariant_error'])
const PRECHECK_CODES = new Set([
  'tr2_source_drift', 'tr2_edit_not_applicable', 'tr2_worktree_dirty', 'tr2_source_locked', 'tr2_git_unavailable',
  'tr2_validation_command_unapproved', 'tr2_validation_command_os_mismatch', 'tr2_validation_command_unverified',
  'tr2_workflow_conflict', 'tr2_precheck_failed', 'tr2_history_revision_required', 'tr2_nested_transaction', 'tr2_principal_required',
])

export interface Tr2ErrorInfo { text: string; code: string | null; stale: boolean; recovery: boolean; revision: number | null }

function codeText(code: string, t: Translate, te: Exists): string {
  const key = `main.tr2_body.readiness.code.${code}`
  return te(key) ? t(key) : t('main.tr2_body.readiness.unknown', { code })
}

export function describeTr2Error(exc: any, t: Translate, te: Exists): Tr2ErrorInfo {
  const response = exc?.response
  const data = response?.data ?? {}
  const code: string | null = data.code ?? data.error?.code ?? null
  const details = data.details ?? data.error?.details ?? {}
  const info = (text: string, extra: Partial<Tr2ErrorInfo> = {}): Tr2ErrorInfo =>
    ({ text, code, stale: false, recovery: false, revision: null, ...extra })
  if (!response) return info(t('main.tr2_body.errors.network'))
  if (code && BODY_ERROR_KEYS[code]) return info(t(`main.tr2_body.errors.${BODY_ERROR_KEYS[code]}`), { recovery: true })
  switch (code) {
    case 'tr2_spec_changed': {
      const revision = typeof details.current_revision_no === 'number' ? details.current_revision_no : null
      return info(t('main.tr2_body.errors.stale', { revision: revision ?? '?' }), { stale: true, revision })
    }
    case 'tr2_spec_immutable': {
      const reasonKey = `main.tr2_body.errors.immutable_reason.${details.reason ?? 'unknown'}`
      return info(t('main.tr2_body.errors.immutable', { reason: t(te(reasonKey) ? reasonKey : 'main.tr2_body.errors.immutable_reason.unknown') }))
    }
    case 'tr2_in_progress': return info(t('main.tr2_body.errors.in_progress'))
    case 'tr2_spec_invalid': return info(t('main.tr2_body.errors.invalid', { loc: details.loc ?? '', reason: details.reason ?? '' }))
    case 'tr2_item_not_found': return info(t('main.tr2_body.errors.not_found'))
    case 'tr2_path_unsafe': return info(t('main.tr2_body.errors.path_unsafe', { loc: details.loc ?? '' }))
    case 'tr2_revision_not_found': return info(t('main.tr2_body.errors.revision_not_found'))
    case 'tr2_revision_unusable': return info(t('main.tr2_body.errors.revision_unusable'))
    case 'tr2_internal_error': return info(t('main.tr2_body.errors.internal'))
  }
  if (code && PRECHECK_CODES.has(code)) return info(t('main.tr2_body.errors.precheck', { detail: codeText(code, t, te) }))
  if (code && APPLY_CODES.has(code)) return info(t('main.tr2_body.errors.apply', { detail: codeText(code, t, te) }))
  if (Number(response.status) >= 500) return info(t('main.tr2_body.errors.internal'))
  if (Number(response.status) === 422 && !code) return info(t('main.tr2_body.errors.not_editable'))
  return info(t('main.tr2_body.errors.unknown', { code: code ?? response.status ?? '?' }))
}

/** One line for why the server would refuse approval now. */
export function readinessText(readiness: Tr2Readiness | undefined, t: Translate, te: Exists): string {
  if (!readiness) return t('main.tr2_body.readiness.unavailable')
  const reasonKey = `main.tr2_body.readiness.reason.${readiness.reason ?? ''}`
  if (readiness.reason && te(reasonKey)) return t(reasonKey, { id: readiness.loc ?? '' })
  if (readiness.code) return codeText(readiness.code, t, te)
  return t('main.tr2_body.readiness.unavailable')
}
