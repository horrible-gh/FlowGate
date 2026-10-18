// flowgate.v02.0048 T#3 — skeleton of the workPlan document API (D#1 §6.3). Owned by T#13 (workPlan), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `workPlan`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   read     GET  /api/v1/documents/{doc_id}/work-plan
//   history  GET  /api/v1/documents/{doc_id}/work-plan/applications
//
// flowgate.v02.0048 T#13 — the Inspector WP summary's one call, an existing v0.1 endpoint (NR0003 §2.4,
// `documents/routers/work_plan.py` get_work_plan / _read_view; no new server API):
//   read     GET  /api/v1/documents/{doc_id}/work-plan -> { ok, doc_id, title, group_id, status,
//            doc_review_status, editable, edit_locked_reason (final_approved | status | null), revision_no,
//            body { wp_version, quantities, steps[] { key, card_id, type, ordinal, pair_key, pair_role
//            (instruction | result | single), provider_id, provider_display_name, review_count, locked, … } },
//            step_execution_status[] { step_key, card_id, started }, assignment_summary[] { provider_id,
//            display_name, step_count }, unassigned_step_count, totals { design_sheets, work_sets, steps }, … }
// Only reading is wrapped. Editing (`PUT …/work-plan`), revision restore, suggestions, the apply history and
// apply / preview / sequence candidates (WP 전개) stay in the v0.1 WorkPlanEditor (↗ 열기, D#1 §10.2 T#13
// row, §8.4).
//
// Refusals keep the server's shape and are thrown as they are; workPlanErrorOf reads them. The route answers
// 403 (perm_document_read), 404 `detail` (no document), 422 `detail` (not a WP document) and 409
// `{ code: 'wp_unreadable', reason, revision_no, … }` at the top level of the body when the stored plan cannot
// be read as a table (the v0.1 editor offers its raw view and revision restore).
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, putRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const whole = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null)

/** One Step of the plan body (the parts the summary reads). */
export interface WorkPlanStep {
  /** `<type>#<ordinal>`, e.g. `T#1`. */
  readonly key: string
  /** The card the Step belongs to (an instruction / result pair shares one); falls back to the pair's key. */
  readonly cardId: string
  readonly type: string | null
  readonly ordinal: number | null
  /** The other half of an instruction / result pair, or null. */
  readonly pairKey: string | null
  /** `instruction` | `result` | `single` (open set). */
  readonly pairRole: string | null
  readonly providerId: string | null
  /** The provider's current name (registered list, then the plan's snapshot), or null when unassigned. */
  readonly providerName: string | null
  readonly reviewCount: number
  /** A server-assembled Step: nobody is assigned to it. */
  readonly locked: boolean
  /** The card already started in the workflow (the server's step_execution_status). */
  readonly started: boolean
}

/** Steps assigned to one provider, in the plan's order. */
export interface WorkPlanAssignment {
  readonly providerId: string
  readonly name: string
  readonly stepCount: number
}

/** The WP read view of one document (`GET /documents/{doc_id}/work-plan`). */
export interface WorkPlanView {
  readonly docId: string
  readonly title: string | null
  readonly groupId: string | null
  readonly revisionNo: number
  readonly docReviewStatus: string | null
  /** The server's one verdict on whether the plan can still be edited (in the v0.1 editor). */
  readonly editable: boolean
  /** `final_approved` | `status` | null (editable). */
  readonly editLockedReason: string | null
  readonly totals: { readonly designSheets: number; readonly workSets: number; readonly steps: number }
  readonly steps: readonly WorkPlanStep[]
  readonly assignments: readonly WorkPlanAssignment[]
  readonly unassignedSteps: number
}

/** The legacy card id of a Step without a stored one (server `legacy_card_id`). */
function legacyCardId(key: string, pairKey: string | null, pairRole: string | null): string {
  return pairRole === 'result' && pairKey ? pairKey : key
}

export function toWorkPlanView(raw: unknown): WorkPlanView {
  const data = record(raw)
  const body = record(data.body)
  const started = new Map<string, boolean>()
  for (const entry of list(data.step_execution_status)) {
    const row = record(entry)
    const key = text(row.step_key)
    if (key !== null) started.set(key, row.started === true)
  }
  const assignments: WorkPlanAssignment[] = []
  for (const entry of list(data.assignment_summary)) {
    const row = record(entry)
    const providerId = text(row.provider_id)
    if (providerId === null) continue
    assignments.push({ providerId, name: text(row.display_name) ?? providerId, stepCount: whole(row.step_count) ?? 0 })
  }
  const currentName = new Map(assignments.map((entry) => [entry.providerId, entry.name]))
  const steps: WorkPlanStep[] = []
  for (const entry of list(body.steps)) {
    const row = record(entry)
    const key = text(row.key)
    if (key === null) continue
    const pairKey = text(row.pair_key)
    const pairRole = text(row.pair_role)
    const providerId = text(row.provider_id)
    steps.push({
      key,
      cardId: text(row.card_id) ?? legacyCardId(key, pairKey, pairRole),
      type: text(row.type),
      ordinal: whole(row.ordinal),
      pairKey,
      pairRole,
      providerId,
      providerName: providerId === null ? null : currentName.get(providerId) ?? text(row.provider_display_name) ?? providerId,
      reviewCount: whole(row.review_count) ?? 0,
      locked: row.locked === true,
      started: started.get(key) === true,
    })
  }
  const totals = record(data.totals)
  return {
    docId: text(data.doc_id) ?? '',
    title: text(data.title),
    groupId: text(data.group_id),
    revisionNo: whole(data.revision_no) ?? 0,
    docReviewStatus: text(data.doc_review_status),
    editable: data.editable === true,
    editLockedReason: data.editable === true ? null : text(data.edit_locked_reason),
    totals: {
      designSheets: whole(totals.design_sheets) ?? 0,
      workSets: whole(totals.work_sets) ?? 0,
      steps: whole(totals.steps) ?? steps.length,
    },
    steps,
    assignments,
    unassignedSteps: whole(data.unassigned_step_count) ?? 0,
  }
}

/** Reads the WP summary view of a work plan document. */
export async function workPlanRead(docId: string): Promise<WorkPlanView> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/work-plan`)
  return toWorkPlanView(response.data)
}

/**
 * unreadable: the stored plan is not a table (409 wp_unreadable); invalid: not a WP document (422);
 * forbidden / not_found / network / other as usual.
 */
export type WorkPlanErrorKind = 'unreadable' | 'invalid' | 'forbidden' | 'not_found' | 'network' | 'other'

export interface WorkPlanError {
  readonly kind: WorkPlanErrorKind
  readonly status: number | null
  readonly code: string | null
  /** The document's revision the unreadable answer names, or null. */
  readonly revisionNo: number | null
}

/** What a failed WP read threw, as a kind the section can show a reason for. */
export function workPlanErrorOf(error: unknown): WorkPlanError {
  const normalized = normalizeApiError(error)
  const data = record(record(record(error).response).data)
  // wp_unreadable is sent at the top level of the body, not in an error / detail envelope.
  const code = normalized.code ?? text(data.code)?.trim() ?? null
  const status = normalized.status
  let kind: WorkPlanErrorKind
  if (normalized.network) kind = 'network'
  else if (code === 'wp_unreadable') kind = 'unreadable'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 400 || status === 422) kind = 'invalid'
  else kind = 'other'
  return { kind, status, code: code || null, revisionNo: kind === 'unreadable' ? whole(data.revision_no) : null }
}

// flowgate.v02.0053 T#4 — the document window's work plan view (the v0.1 WorkPlanEditor's table, read only)
// needs more of the same answer than the Inspector summary: the quantity boxes and each Step's one-line note
// and whether it carries a pre-instruction. Same endpoint, same read; WorkPlanView is left as it is.

/** One quantity box of the plan (`body.quantities[type]`), in `body.counted_types` order. */
export interface WorkPlanQuantity {
  readonly type: string
  readonly count: number
  /** `sheet` (design sheets) | `set` (instruction / result sets) | null when the plan does not say. */
  readonly unit: 'sheet' | 'set' | null
}

/** What a Step carries beyond the summary row. */
export interface WorkPlanStepDetail {
  readonly note: string | null
  /** 0 none, n times, -1 until approved (the summary row reads -1 as 0). */
  readonly reviewCount: number
  /** A pre-instruction text or attachment is set. */
  readonly hasInstruction: boolean
}

/** The work plan as the document window shows it: the summary view plus quantities and Step details. */
export interface WorkPlanDocument {
  readonly view: WorkPlanView
  readonly quantities: readonly WorkPlanQuantity[]
  /** Step key -> its details. */
  readonly details: Readonly<Record<string, WorkPlanStepDetail>>
}

export function toWorkPlanDocument(raw: unknown): WorkPlanDocument {
  const body = record(record(raw).body)
  const quantities = record(body.quantities)
  const order = list(body.counted_types).flatMap((code) => (text(code) ? [code as string] : []))
  for (const code of Object.keys(quantities)) if (!order.includes(code)) order.push(code)
  const boxes: WorkPlanQuantity[] = order.map((type) => {
    const box = record(quantities[type])
    const unit = box.unit === 'sheet' || box.unit === 'set' ? box.unit : null
    return { type, count: whole(box.count) ?? 0, unit }
  })
  const details: Record<string, WorkPlanStepDetail> = {}
  for (const entry of list(body.steps)) {
    const row = record(entry)
    const key = text(row.key)
    if (key === null) continue
    const instruction = typeof row.pre_instruction_text === 'string' ? row.pre_instruction_text.trim() : ''
    const review = row.review_count
    details[key] = {
      note: text(typeof row.note === 'string' ? row.note.trim() : null),
      reviewCount: typeof review === 'number' && Number.isInteger(review) && review >= -1 ? review : 0,
      hasInstruction: instruction !== '' || Object.keys(record(row.pre_instruction_attachment)).length > 0,
    }
  }
  return { view: toWorkPlanView(raw), quantities: boxes, details }
}

/** Reads a work plan for the document window (`GET /documents/{doc_id}/work-plan`, as workPlanRead). */
export async function workPlanDocumentRead(docId: string): Promise<WorkPlanDocument> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/work-plan`)
  return toWorkPlanDocument(response.data)
}

// flowgate.v02.0072 T#1 — the v0.2 work plan edit window (features/workPlanWindow) edits the plan through the same
// v0.1 endpoints, no new server API (documents/routers/work_plan.py):
//   read  GET /api/v1/documents/{doc_id}/work-plan -> the read view above; the editor keeps the answer whole, because
//         the body goes back as it came (wp_version 2, every field and top-level extension the server keeps).
//   save  PUT /api/v1/documents/{doc_id}/work-plan { base_revision_no, body, capability_warning_acks? } -> { revision_no,
//         body (card ids assigned), title, totals, assignment_summary, unassigned_step_count, warnings }.
// The save's refusals (save_work_plan): 409 `wp_revision_conflict` (top level: updated_by, updated_at); 409 `detail`
// (the Flow was disposed); 422 `validation_failed` with `errors[] { loc, key, code, params, msg }`; 422
// `provider_capability_confirmation_required` with `findings[]` (sent again with every finding's step_key acknowledged);
// 422 `detail` (not editable: final approval / status); 423 (the Flow's AI run holds it); 403 / 404 as usual.

/** The whole read answer of `GET …/work-plan` (an object; the editor reads it with toWorkPlanEditView). */
export async function workPlanEditRead(docId: string): Promise<Record<string, unknown>> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/work-plan`)
  return record(response.data)
}

export interface WorkPlanSaveRequest {
  readonly baseRevisionNo: number
  /** The plan body as it is sent (wp_version 2). */
  readonly body: unknown
  /** The step keys of the capability findings the person confirmed; left out when empty. */
  readonly capabilityWarningAcks?: readonly string[]
}

/** Saves the whole plan body (`PUT …/work-plan`, optimistic on base_revision_no); the answer as it is. */
export async function workPlanSave(docId: string, request: WorkPlanSaveRequest): Promise<Record<string, unknown>> {
  const payload: Record<string, unknown> = { base_revision_no: request.baseRevisionNo, body: request.body }
  if (request.capabilityWarningAcks?.length) payload.capability_warning_acks = [...request.capabilityWarningAcks]
  const response = await putRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/work-plan`, payload)
  return record(response.data)
}

/**
 * One field error of a refused save, as the server sends it (`work_plan_service.render_errors`: loc, the Step key or
 * null, code, params, msg) — the same shape `@shared/workPlanErrors` renders (this layer imports the transport only).
 */
export interface WorkPlanFieldError {
  readonly loc: string
  readonly key: string | null
  readonly code: string
  readonly params: Readonly<Record<string, unknown>>
  readonly msg: string
}

/** One T/TR Step whose AI cannot modify source or run tests (the save waits for a confirmation). */
export interface WorkPlanCapabilityFinding {
  readonly stepKey: string
  readonly stepType: string | null
  readonly providerId: string | null
  readonly providerName: string | null
  readonly missing: readonly string[]
}

/**
 * conflict: someone saved first (409 wp_revision_conflict); validation: field errors (422 errors[]); capability: the
 * findings wait for a confirmation (422); not_editable: final approval or status (422 detail); disposed: the Flow was
 * discarded (409 detail); ai_running: the Flow's AI run holds it (423); forbidden / not_found / network / other.
 */
export type WorkPlanSaveFailureKind =
  | 'conflict'
  | 'validation'
  | 'capability'
  | 'not_editable'
  | 'disposed'
  | 'ai_running'
  | 'forbidden'
  | 'not_found'
  | 'network'
  | 'other'

export interface WorkPlanSaveFailure {
  readonly kind: WorkPlanSaveFailureKind
  readonly status: number | null
  /** The server's own words (`message`, or a string `detail`), or null. */
  readonly message: string | null
  /** validation: the field errors as the server sent them (rendered with renderWorkPlanFieldError). */
  readonly errors: readonly WorkPlanFieldError[]
  /** capability: the findings to confirm. */
  readonly findings: readonly WorkPlanCapabilityFinding[]
  /** conflict: who saved and when, as the server says. */
  readonly updatedBy: string | null
  readonly updatedAt: string | null
}

function fieldErrors(value: unknown): WorkPlanFieldError[] {
  return list(value).map((entry) => {
    const row = record(entry)
    return {
      loc: typeof row.loc === 'string' ? row.loc : '',
      key: text(row.key),
      code: typeof row.code === 'string' ? row.code : '',
      params: record(row.params),
      msg: typeof row.msg === 'string' ? row.msg : '',
    }
  })
}

function capabilityFindings(value: unknown): WorkPlanCapabilityFinding[] {
  const out: WorkPlanCapabilityFinding[] = []
  for (const entry of list(value)) {
    const row = record(entry)
    const stepKey = text(row.step_key)
    if (stepKey === null) continue
    out.push({
      stepKey,
      stepType: text(row.step_type),
      providerId: text(row.provider_id),
      providerName: text(row.provider_name),
      missing: list(row.missing_capabilities).flatMap((item) => (text(item) ? [item as string] : [])),
    })
  }
  return out
}

/** What a failed save threw, as a kind the window can act on. */
export function workPlanSaveFailureOf(error: unknown): WorkPlanSaveFailure {
  const normalized = normalizeApiError(error)
  const data = record(record(record(error).response).data)
  const status = normalized.status
  const code = text(data.code) ?? normalized.code
  const message = text(data.message) ?? text(data.detail)
  const base = { status, message, errors: [] as WorkPlanFieldError[], findings: [] as WorkPlanCapabilityFinding[], updatedBy: null, updatedAt: null }
  if (normalized.network) return { ...base, kind: 'network' }
  if (status === 409 && code === 'wp_revision_conflict') {
    return { ...base, kind: 'conflict', updatedBy: text(data.updated_by), updatedAt: text(data.updated_at) }
  }
  if (status === 422 && code === 'provider_capability_confirmation_required') {
    return { ...base, kind: 'capability', findings: capabilityFindings(data.findings) }
  }
  if (status === 422 && Array.isArray(data.errors)) return { ...base, kind: 'validation', errors: fieldErrors(data.errors) }
  if (status === 422) return { ...base, kind: 'not_editable' }
  if (status === 409) return { ...base, kind: 'disposed' }
  if (status === 423) return { ...base, kind: 'ai_running' }
  if (status === 403) return { ...base, kind: 'forbidden' }
  if (status === 404) return { ...base, kind: 'not_found' }
  return { ...base, kind: 'other' }
}
