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
import { getRequest } from '@shared/api'

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
