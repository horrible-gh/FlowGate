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
import { getRequest, postFormRequest, postRequest, putRequest } from '@shared/api'

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

// 0072 T#2: upload reserves a file only. Its reference enters the plan on the parent's PUT;
// successful PUT cleans unreferenced reserved files. There is no per-file delete endpoint.
export async function workPlanUploadInstruction(docId: string, stepKey: string, file: File): Promise<Record<string, unknown>> {
  const form = new FormData()
  form.append('file', file)
  form.append('step_key', stepKey)
  const response = await postFormRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/work-plan/pre-instruction-attachments`, form)
  const reference = record(record(response.data).reference)
  if (reference.doc_id !== docId || typeof reference.filename !== 'string'
    || !reference.filename.startsWith('__wp_pre_instruction__')
    || typeof reference.original_filename !== 'string'
    || typeof reference.content_sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(reference.content_sha256)) {
    throw new Error('Invalid pre-instruction attachment reference')
  }
  return { ...reference }
}

// flowgate.v02.0074 T#5 — the WP→v0.2 card deploy (T#4 server `flowgate/work_plan_deploy/routes.py`, D#1 §5; contract
// 0014-TR §2), same transport and normalization as the reads above:
//   status   GET  /api/v1/documents/{doc_id}/work-plan/deploy          -> flow_id, wp_node_key, ownership, wp_revision_no,
//            deployable, reason, deployed / target / missing / orphan / mismatch _count, cards[], modes { first, add, cleanup }
//   preview  POST …/work-plan/deploy/preview { mode, wp_revision_no }   -> cards[], blockers[], writes, no_change, executable,
//            counts, baseline { wp_revision_no, plan_digest, draft_version, base_revision_no, remove_node_keys }
//   execute  POST …/work-plan/deploy { mode, ...baseline }              -> cards[], no_change, draft_version,
//            created_node_keys, removed_node_keys, key_map, counts
// The execution only takes a preview: its baseline goes back as the server sent it (a frozen copy kept here, never
// rebuilt from the normalized fields), with the preview's mode. A baseline the client made itself is refused before
// anything is sent. Refusals are `{ ok: false, code, message, ...params }` with the HTTP status (workPlanDeployFailureOf).

export type WorkPlanDeployMode = 'first' | 'add' | 'cleanup'

const keys = (value: unknown): string[] => list(value).flatMap((item) => (text(item) ? [item as string] : []))
const flag = (value: unknown): boolean => value === true
const nonNegative = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null)

/** A deployed card of the plan as the status read lists it. */
export interface WorkPlanDeployedCard {
  readonly cardId: string
  readonly nodeKey: string
  readonly docType: string | null
  /** Its node and document type match the plan's card, and it is the only card for that card id. */
  readonly valid: boolean
  /** The plan still has the card id. */
  readonly inPlan: boolean
}

export interface WorkPlanDeployCounts {
  readonly deployedCount: number
  readonly targetCount: number
  readonly missingCount: number
  readonly orphanCount: number
  readonly mismatchCount: number
}

/** `GET …/work-plan/deploy`: whether the plan can deploy now and its counts. */
export interface WorkPlanDeployStatus extends WorkPlanDeployCounts {
  readonly docId: string
  readonly flowId: string | null
  /** The plan's card on the Graph, or null (the plan is not on a card). */
  readonly wpNodeKey: string | null
  /** `unassigned` | `graph` | `v01` | `conflict` (open set), or null. */
  readonly ownership: string | null
  readonly wpRevisionNo: number
  readonly deployable: boolean
  /** The refusal code when not deployable (`wp_not_editable`, `flow_owned_by_v01`, `graph_run_locked`, …), else null. */
  readonly reason: string | null
  readonly cards: readonly WorkPlanDeployedCard[]
  readonly modes: Readonly<Record<WorkPlanDeployMode, boolean>>
}

function deployCounts(raw: unknown): WorkPlanDeployCounts {
  const data = record(raw)
  return {
    deployedCount: whole(data.deployed_count) ?? 0,
    targetCount: whole(data.target_count) ?? 0,
    missingCount: whole(data.missing_count) ?? 0,
    orphanCount: whole(data.orphan_count) ?? 0,
    mismatchCount: whole(data.mismatch_count) ?? 0,
  }
}

export function toWorkPlanDeployStatus(raw: unknown): WorkPlanDeployStatus {
  const data = record(raw)
  const modes = record(data.modes)
  const cards: WorkPlanDeployedCard[] = []
  for (const entry of list(data.cards)) {
    const row = record(entry)
    const cardId = text(row.card_id)
    const nodeKey = text(row.node_key)
    if (cardId === null || nodeKey === null) continue
    cards.push({ cardId, nodeKey, docType: text(row.doc_type), valid: flag(row.valid), inPlan: flag(row.in_plan) })
  }
  return {
    docId: text(data.doc_id) ?? '',
    flowId: text(data.flow_id),
    wpNodeKey: text(data.wp_node_key),
    ownership: text(data.ownership),
    wpRevisionNo: whole(data.wp_revision_no) ?? 0,
    deployable: flag(data.deployable),
    reason: flag(data.deployable) ? null : text(data.reason),
    ...deployCounts(data),
    cards,
    modes: { first: flag(modes.first), add: flag(modes.add), cleanup: flag(modes.cleanup) },
  }
}

/** Reads whether the plan can deploy and its counts (read permission). */
export async function workPlanDeployStatus(docId: string): Promise<WorkPlanDeployStatus> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/work-plan/deploy`)
  return toWorkPlanDeployStatus(response.data)
}

/** One card of a preview or an execution. */
export interface WorkPlanDeployCardResult {
  readonly cardId: string
  readonly docType: string | null
  readonly nodeKey: string | null
  /** `create` | `already_deployed` | `kept_type_mismatch` | `kept_duplicate` | `remove` | `kept_orphan` | `skipped_type`. */
  readonly result: string
  readonly reason: string | null
  /** `instruction_write` | `instruction_kept` | null. */
  readonly instruction: string | null
  readonly instructionReason: string | null
  /** Settings the preview leaves out (the execution keeps their defaults). */
  readonly settingsSkipped: ReadonlyArray<{ readonly field: string; readonly reason: string }>
}

/**
 * Why a deploy would not run: `wp_card_has_successor` · `deploy_chain_nonlinear` · `deploy_position_blocked` ·
 * `deploy_draft_invalid`, a mode mismatch, or in a preview a refusal (ownership, locks, …) under its code.
 */
export interface WorkPlanDeployBlocker {
  readonly code: string
  readonly nodeKey: string | null
  readonly nodeKeys: readonly string[]
  readonly edgeIds: readonly string[]
  /** deploy_draft_invalid: the draft save's field errors. */
  readonly errors: ReadonlyArray<{ readonly field: string; readonly reason: string }>
  /** Every field the server sent with it. */
  readonly params: Readonly<Record<string, unknown>>
}

/** The preview's baseline (read only). Only a preview answer makes one; the execution sends the server's own copy. */
export interface WorkPlanDeployBaseline {
  readonly wpRevisionNo: number | null
  readonly planDigest: string | null
  readonly draftVersion: number | null
  readonly baseRevisionNo: number | null
  /** cleanup: the cards the preview showed for removal. */
  readonly removeNodeKeys: readonly string[]
}

export interface WorkPlanDeployPreview {
  readonly mode: WorkPlanDeployMode
  readonly cards: readonly WorkPlanDeployCardResult[]
  readonly blockers: readonly WorkPlanDeployBlocker[]
  readonly writes: boolean
  readonly noChange: boolean
  readonly executable: boolean
  readonly counts: WorkPlanDeployCounts
  readonly baseline: WorkPlanDeployBaseline
}

export interface WorkPlanDeployResult {
  readonly mode: WorkPlanDeployMode
  readonly cards: readonly WorkPlanDeployCardResult[]
  readonly noChange: boolean
  readonly draftVersion: number | null
  readonly createdNodeKeys: readonly string[]
  readonly removedNodeKeys: readonly string[]
  /** card id -> the node key it was placed as. */
  readonly keyMap: Readonly<Record<string, string>>
  readonly counts: WorkPlanDeployCounts
}

function pairs(value: unknown): Array<{ field: string; reason: string }> {
  return list(value).map((entry) => {
    const row = record(entry)
    return { field: typeof row.field === 'string' ? row.field : '', reason: typeof row.reason === 'string' ? row.reason : '' }
  })
}

function deployCards(value: unknown): WorkPlanDeployCardResult[] {
  const out: WorkPlanDeployCardResult[] = []
  for (const entry of list(value)) {
    const row = record(entry)
    const cardId = text(row.card_id)
    if (cardId === null) continue
    out.push({
      cardId,
      docType: text(row.doc_type),
      nodeKey: text(row.node_key),
      result: text(row.result) ?? '',
      reason: text(row.reason),
      instruction: text(row.instruction),
      instructionReason: text(row.instruction_reason),
      settingsSkipped: pairs(row.settings_skipped),
    })
  }
  return out
}

export function toWorkPlanDeployBlockers(value: unknown): WorkPlanDeployBlocker[] {
  const out: WorkPlanDeployBlocker[] = []
  for (const entry of list(value)) {
    const row = record(entry)
    const code = text(row.code)
    if (code === null) continue
    out.push({ code, nodeKey: text(row.node_key), nodeKeys: keys(row.node_keys), edgeIds: keys(row.edge_ids), errors: pairs(row.errors), params: { ...row } })
  }
  return out
}

/** The server's own baseline of each preview (a frozen copy), by the baseline object handed out. */
const SERVER_BASELINES = new WeakMap<WorkPlanDeployBaseline, Readonly<Record<string, unknown>>>()

function issueBaseline(raw: unknown): WorkPlanDeployBaseline {
  const sent = JSON.parse(JSON.stringify(record(raw))) as Record<string, unknown>
  if (Array.isArray(sent.remove_node_keys)) Object.freeze(sent.remove_node_keys)
  const baseline: WorkPlanDeployBaseline = Object.freeze({
    wpRevisionNo: nonNegative(sent.wp_revision_no),
    planDigest: typeof sent.plan_digest === 'string' ? sent.plan_digest : null,
    draftVersion: nonNegative(sent.draft_version),
    baseRevisionNo: nonNegative(sent.base_revision_no),
    removeNodeKeys: Object.freeze(keys(sent.remove_node_keys)),
  })
  SERVER_BASELINES.set(baseline, Object.freeze(sent))
  return baseline
}

export function toWorkPlanDeployPreview(raw: unknown, mode: WorkPlanDeployMode): WorkPlanDeployPreview {
  const data = record(raw)
  return Object.freeze({
    mode,
    cards: deployCards(data.cards),
    blockers: toWorkPlanDeployBlockers(data.blockers),
    writes: flag(data.writes),
    noChange: flag(data.no_change),
    executable: flag(data.executable),
    counts: deployCounts(data.counts),
    baseline: issueBaseline(data.baseline),
  })
}

export function toWorkPlanDeployResult(raw: unknown, mode: WorkPlanDeployMode): WorkPlanDeployResult {
  const data = record(raw)
  const keyMap: Record<string, string> = {}
  for (const [cardId, nodeKey] of Object.entries(record(data.key_map))) if (text(nodeKey)) keyMap[cardId] = nodeKey as string
  return {
    mode,
    cards: deployCards(data.cards),
    noChange: flag(data.no_change),
    draftVersion: nonNegative(data.draft_version),
    createdNodeKeys: keys(data.created_node_keys),
    removedNodeKeys: keys(data.removed_node_keys),
    keyMap,
    counts: deployCounts(data.counts),
  }
}

/** What the deploy of `mode` would do now, and the baseline its execution must carry (write permission). */
export async function workPlanDeployPreview(docId: string, mode: WorkPlanDeployMode, wpRevisionNo: number): Promise<WorkPlanDeployPreview> {
  const response = await postRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/work-plan/deploy/preview`, { mode, wp_revision_no: wpRevisionNo })
  return toWorkPlanDeployPreview(response.data, mode)
}

/** Runs the deploy a preview showed: its mode and its baseline exactly as the server sent them. */
export async function workPlanDeployExecute(docId: string, preview: Pick<WorkPlanDeployPreview, 'mode' | 'baseline'>): Promise<WorkPlanDeployResult> {
  const baseline = SERVER_BASELINES.get(preview.baseline)
  if (!baseline) throw new Error('A work plan deploy runs only with the baseline of its preview.')
  const response = await postRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/work-plan/deploy`, { mode: preview.mode, ...baseline })
  return toWorkPlanDeployResult(response.data, preview.mode)
}

/**
 * forbidden 403; not_found 404 wp_not_found; not_a_work_plan 422; not_editable 422 wp_not_editable (`reason`); invalid 422
 * (the request); ai_running 423 GROUP_AI_RUN_LOCKED (the middleware's too); 409: owned_by_v01 · ownership_conflict ·
 * run_locked (graph_run_locked) · not_on_card · disposed (flow_disposed) · unreadable (wp_unreadable) · wp_changed ·
 * attachment_unreadable · already_deployed · not_deployed · draft_conflict (draft_version_conflict) · blocked
 * (deploy_blocked, its blockers kept) · preview_stale · target_changed (instruction_target_changed); 500
 * restore_incomplete (deploy_restore_incomplete, its doc_ids kept); network / other.
 */
export type WorkPlanDeployFailureKind =
  | 'forbidden'
  | 'not_found'
  | 'not_a_work_plan'
  | 'not_editable'
  | 'invalid'
  | 'ai_running'
  | 'owned_by_v01'
  | 'ownership_conflict'
  | 'run_locked'
  | 'not_on_card'
  | 'disposed'
  | 'unreadable'
  | 'wp_changed'
  | 'attachment_unreadable'
  | 'already_deployed'
  | 'not_deployed'
  | 'draft_conflict'
  | 'blocked'
  | 'preview_stale'
  | 'target_changed'
  | 'restore_incomplete'
  | 'network'
  | 'other'

export interface WorkPlanDeployFailure {
  readonly kind: WorkPlanDeployFailureKind
  readonly status: number | null
  readonly code: string | null
  /** The server's own words, or null. */
  readonly message: string | null
  /** not_editable: final_approved | status …; wp_changed: plan_digest when the plan file changed. */
  readonly reason: string | null
  /** blocked: the blockers as sent. */
  readonly blockers: readonly WorkPlanDeployBlocker[]
  /** restore_incomplete: the documents left with their kept original. */
  readonly docIds: readonly string[]
  /** attachment_unreadable: the card whose pre-instruction file failed. */
  readonly cardId: string | null
  /**
   * The baseline no longer matches the server (draft_version_conflict · deploy_preview_stale · wp_changed): a new
   * preview is needed — the old baseline is never sent again.
   */
  readonly stale: boolean
}

const DEPLOY_CONFLICTS: Readonly<Record<string, WorkPlanDeployFailureKind>> = {
  flow_owned_by_v01: 'owned_by_v01',
  flow_ownership_conflict: 'ownership_conflict',
  graph_run_locked: 'run_locked',
  wp_not_on_card: 'not_on_card',
  flow_disposed: 'disposed',
  wp_unreadable: 'unreadable',
  wp_changed: 'wp_changed',
  wp_attachment_unreadable: 'attachment_unreadable',
  deploy_already_deployed: 'already_deployed',
  deploy_not_deployed: 'not_deployed',
  draft_version_conflict: 'draft_conflict',
  deploy_blocked: 'blocked',
  deploy_preview_stale: 'preview_stale',
  instruction_target_changed: 'target_changed',
}

const DEPLOY_STALE: ReadonlySet<WorkPlanDeployFailureKind> = new Set(['draft_conflict', 'preview_stale', 'wp_changed'])

/** What a failed deploy call (status, preview or execution) threw, as a kind the caller says in one line. */
export function workPlanDeployFailureOf(error: unknown): WorkPlanDeployFailure {
  const normalized = normalizeApiError(error)
  const data = record(record(record(error).response).data)
  const status = normalized.status
  const code = text(data.code)?.trim() ?? normalized.code
  let kind: WorkPlanDeployFailureKind
  if (normalized.network) kind = 'network'
  else if (status === 423 || code === 'GROUP_AI_RUN_LOCKED') kind = 'ai_running'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 422) kind = code === 'not_a_work_plan' ? 'not_a_work_plan' : code === 'wp_not_editable' ? 'not_editable' : 'invalid'
  else if (status === 409 && code !== null && DEPLOY_CONFLICTS[code]) kind = DEPLOY_CONFLICTS[code]
  else if (status === 500 && code === 'deploy_restore_incomplete') kind = 'restore_incomplete'
  else kind = 'other'
  return {
    kind,
    status,
    code: code || null,
    message: text(data.message) ?? text(data.detail),
    reason: text(data.reason),
    blockers: kind === 'blocked' ? toWorkPlanDeployBlockers(data.blockers) : [],
    docIds: kind === 'restore_incomplete' ? keys(data.doc_ids) : [],
    cardId: kind === 'attachment_unreadable' ? text(data.card_id) : null,
    stale: DEPLOY_STALE.has(kind),
  }
}
