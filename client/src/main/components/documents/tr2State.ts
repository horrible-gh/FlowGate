// TR2 display state (0565 T0028 §A). Pure mapping from the server read model — the
// server stays the authority for attempts, fingerprints and history; this only decides
// which of the MirageGlass TR2 screens the current facts correspond to.

export type Row = Record<string, any>

export interface Tr2View {
  document: { doc_id?: string; revision_no: number; doc_review_status?: string; editable?: boolean }
  mutation?: { allowed: boolean; reason: string | null }
  body: {
    tr2_version: number
    source_t2_doc_id: string
    edit_spec: { termination: string; edits: Row[]; deferred: Row[]; gate: { commands: string[]; apply?: boolean }; verify?: Row; notes?: string }
    baseline_fingerprint?: string
  }
  derived: {
    files: Array<{ path: string; kind: string; exists: boolean; edit_ids: string[] }>
    live_precheck: { drift: boolean; baseline_fingerprint: string; live_fingerprint: string; anchors?: Row[] | null }
  }
  approval: { latest_attempt: Row | null; attempts: Row[]; retry?: Row }
  history: { source_history_state: string; ledger: Row[] }
}

export type Tr2StateKey =
  | 'ready' | 'needs_more_work' | 'drift' | 'applying' | 'rollback' | 'recovery_required'
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
  return 'ready'
}

export function tr2Tone(state: Tr2StateKey): Tr2Tone {
  switch (state) {
    case 'ready': return 'safe'
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
