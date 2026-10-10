// flowgate.v02.0048 T#3 — skeleton of the settings document API (D#1 §6.3). Owned by T#9 (settings), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `settings`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   the Node run settings the D#2 conclusion names (v0.1 sequence row; POST /api/v1/ai-invoke/start
//   continuation_restart_max_attempts / continuation_step_timeout_sec / document_review_loop)
//
// flowgate.v02.0048 T#9 — the Inspector settings tab's calls. D#2 item (1) concluded "no change": the run
// AI, the review count and the reviewer are already stored on the v0.1 sequence row and a continuous hop
// reads them itself, so every call is an existing endpoint (no new server API):
//   root      GET   /api/v1/flows/{flow_id}/graph -> { ok, ownership: { state, sequence_root_doc_id, … }, … }
//             The sequence is keyed by its root document (D#2 §2.4 rule 1). The ownership block is the
//             one the Canvas read carries; the graph read answers it without projecting the Canvas.
//   sequence  GET   /api/v1/workflow/sequence?doc_id=<root> -> { doc_id, decided, sequence_id, items: [{
//             item_id, item_seq, type, label, status, protected, note, source_doc_id, source_revision_no,
//             source_wp_card_id, provider_id, provider_display_name, provider_registered, review_count,
//             reviewer_provider_id, reviewer_provider_display_name, pre_instruction_text,
//             pre_instruction_attachment, … }] }. 400 sequence_not_decided before a decision.
//   save      PATCH /api/v1/workflow/sequence { doc_id: <root>, items } — replaces the rows not started
//             yet. Every row goes back with its item_id and the fields as read (D#2 §2.4 rule 2: a
//             field left out is a field the save deletes); started rows are echoes the server ignores,
//             a protected pending report row takes its settings in place, other pending rows are
//             written again under new ids. Refusals are flat `{ error: <code>, … }`: 409
//             group_disposed / flow_owned_by_graph / flow_ownership_conflict / sequence_item_stale /
//             protected_row_modified / protected_row_echo_ambiguous / sequence_changed, 400
//             sequence_not_decided, 422 note_too_long / invalid_sequence_item / corrupted_label; an AI
//             run owning the group answers the mutation policy's 423 / 409 `{ error: { code } }`.
//   choices   GET   /api/v1/ai-invoke/providers?project= -> { providers: [{ id, name, … }],
//             default_provider_id, execution_policy: { repeat_count_max, … } }. The run AI and reviewer
//             choices, and the review count choices: -1 (until it passes), 0 (no review), 1..max — the
//             set the server accepts (`repeat_count_choices(allow_zero=True)`). execution_policy is the
//             same payload as GET /projects/{pid}/ai-execution-policy, which is gated on
//             project.settings.read; this one is open to every document reader.
//   run       GET   /api/v1/ai-invoke/active?group_id= -> { active, status, mode, provider, hop_item_seq,
//             hop_review_count, hop_reviewer_provider_id, … }; POST /ai-invoke/start answers the same hop
//             fields plus selected_provider_source (`stored_sequence` = the row's run AI). Read by
//             settingsHopOf: what a running continuous hop took from the saved row.
// Not wrapped (D#2 §2.2): restart, step time limit and review criteria are run-request fields with no
// row to store them (Q 402), instruction use / after-review are not stored at all.
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, patchRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const integer = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) ? value : null)

/** The Flow's owner and, for a v0.1 Flow, the root document its sequence is keyed by. */
export interface SettingsRoot {
  readonly ownership: string
  readonly rootDocId: string | null
}

/** The graph read -> SettingsRoot (the sequence root only where a v0.1 sequence exists). */
export function toSettingsRoot(raw: unknown): SettingsRoot {
  const ownership = record(record(raw).ownership)
  return { ownership: text(ownership.state) ?? 'unassigned', rootDocId: text(ownership.sequence_root_doc_id) }
}

/** Reads the Flow's ownership and sequence root. */
export async function settingsRootRead(flowId: string): Promise<SettingsRoot> {
  const response = await getRequest<unknown>(`${API}/flows/${encodeURIComponent(flowId)}/graph`)
  return toSettingsRoot(response.data)
}

/** One v0.1 sequence row as the sequence read gives it. */
export interface SettingsRow {
  readonly itemId: number
  readonly itemSeq: number | null
  readonly type: string
  readonly label: string
  /** pending (not started) | in_progress | done — derived by the server from the result document. */
  readonly status: string
  /** Started, or the pending report right after a started instruction (saved in place). */
  readonly protected: boolean
  readonly note: string
  readonly sourceDocId: string | null
  readonly sourceRevisionNo: number | null
  readonly sourceWpCardId: string | null
  /** The run AI; null = the project default. */
  readonly providerId: string | null
  readonly providerDisplayName: string | null
  /** false: the stored run AI is no longer registered; null: unknown or none. */
  readonly providerRegistered: boolean | null
  /** -1 until it passes, 0 no review, n rounds. */
  readonly reviewCount: number
  /** The reviewer; null = the project default reviewer. */
  readonly reviewerProviderId: string | null
  readonly reviewerProviderDisplayName: string | null
  readonly preInstructionText: string | null
  /** Kept as read and sent back unchanged. */
  readonly preInstructionAttachment: unknown
}

/** A Flow's v0.1 sequence: its root document and every row in order. */
export interface SettingsSequence {
  readonly rootDocId: string
  readonly sequenceId: number | null
  readonly items: readonly SettingsRow[]
}

/** One sequence row; null without an item_id (it could not be sent back). */
function toSettingsRow(raw: unknown): SettingsRow | null {
  const row = record(raw)
  const itemId = integer(row.item_id) ?? integer(row.id)
  if (itemId === null) return null
  return {
    itemId,
    itemSeq: integer(row.item_seq),
    type: typeof row.type === 'string' ? row.type : '',
    label: typeof row.label === 'string' ? row.label : '',
    status: text(row.status) ?? 'pending',
    protected: row.protected === true,
    note: typeof row.note === 'string' ? row.note : '',
    sourceDocId: text(row.source_doc_id),
    sourceRevisionNo: integer(row.source_revision_no),
    sourceWpCardId: text(row.source_wp_card_id),
    providerId: text(row.provider_id),
    providerDisplayName: text(row.provider_display_name),
    providerRegistered: typeof row.provider_registered === 'boolean' ? row.provider_registered : null,
    reviewCount: integer(row.review_count) ?? 0,
    reviewerProviderId: text(row.reviewer_provider_id),
    reviewerProviderDisplayName: text(row.reviewer_provider_display_name),
    preInstructionText: typeof row.pre_instruction_text === 'string' ? row.pre_instruction_text : null,
    preInstructionAttachment: row.pre_instruction_attachment ?? null,
  }
}

/** The sequence read -> SettingsSequence. Throws when it names no document. */
export function toSettingsSequence(raw: unknown): SettingsSequence {
  const body = record(raw)
  const rootDocId = text(body.doc_id)
  if (rootDocId === null) throw new Error('workflow sequence without doc_id')
  return {
    rootDocId,
    sequenceId: integer(body.sequence_id),
    items: list(body.items).map(toSettingsRow).filter((row): row is SettingsRow => row !== null),
  }
}

/** Reads the sequence of a root document. Transport errors are thrown as they are. */
export async function settingsSequenceRead(rootDocId: string): Promise<SettingsSequence> {
  const response = await getRequest<unknown>(`${API}/workflow/sequence`, { doc_id: rootDocId })
  return toSettingsSequence(response.data)
}

/**
 * Reads a Flow's sequence: its root first, then the rows. A Flow with no v0.1 sequence (another owner)
 * has none to read; that is answered as the 400 sequence_not_decided a root without a decision gets.
 */
export async function settingsRead(flowId: string): Promise<SettingsSequence> {
  const root = await settingsRootRead(flowId)
  if (root.rootDocId === null) throw settingsNoSequence(flowId, root.ownership)
  return settingsSequenceRead(root.rootDocId)
}

/** The refusal settingsRead throws for a Flow without a v0.1 sequence (read by settingsErrorOf). */
export function settingsNoSequence(flowId: string, ownership: string): unknown {
  return { response: { status: 400, data: { error: 'sequence_not_decided', flow_id: flowId, ownership } } }
}

/** The settings one row is saved with (display names are the chosen AI's names, null = default). */
export interface SettingsValues {
  readonly providerId: string | null
  readonly providerDisplayName: string | null
  readonly reviewCount: number
  readonly reviewerProviderId: string | null
  readonly reviewerProviderDisplayName: string | null
}

/** The PATCH items: every row with its item_id and its fields as read; `changes` replaces one row's settings. */
export function settingsSavePayload(
  items: readonly SettingsRow[],
  changes: { readonly itemId: number; readonly values: SettingsValues } | null = null,
): Raw[] {
  return items.map((row) => {
    const values = changes && changes.itemId === row.itemId ? changes.values : row
    return {
      item_id: row.itemId,
      type: row.type,
      label: row.label,
      note: row.note,
      source_doc_id: row.sourceDocId,
      source_revision_no: row.sourceRevisionNo,
      source_wp_card_id: row.sourceWpCardId,
      provider_id: values.providerId,
      provider_display_name: values.providerId === null ? null : values.providerDisplayName,
      review_count: values.reviewCount,
      reviewer_provider_id: values.reviewerProviderId,
      reviewer_provider_display_name: values.reviewerProviderId === null ? null : values.reviewerProviderDisplayName,
      pre_instruction_text: row.preInstructionText,
      pre_instruction_attachment: row.preInstructionAttachment,
    }
  })
}

/** What a save answered (the existing response, kept for the caller to read). */
export interface SettingsSaveResult {
  readonly rootDocId: string
  readonly raw: Readonly<Raw>
}

/** Saves the sequence of a root document with one row's settings changed. */
export async function settingsSave(
  rootDocId: string,
  items: readonly SettingsRow[],
  changes: { readonly itemId: number; readonly values: SettingsValues },
): Promise<SettingsSaveResult> {
  const response = await patchRequest<unknown>(`${API}/workflow/sequence`, {
    doc_id: rootDocId,
    items: settingsSavePayload(items, changes),
  })
  return { rootDocId, raw: record(response.data) }
}

/** One AI the run AI / reviewer may be set to. */
export interface SettingsProvider {
  readonly id: string
  readonly name: string
}

/** What the settings may be set to. */
export interface SettingsChoices {
  readonly providers: readonly SettingsProvider[]
  readonly defaultProviderId: string | null
  /** -1 (until it passes), 0 (no review), then 1..repeat_count_max. */
  readonly reviewCounts: readonly number[]
}

/** The server's fallback ceiling when the policy is missing (REPEAT_COUNT_DEFAULT_MAX). */
export const SETTINGS_REPEAT_COUNT_DEFAULT_MAX = 3

/** The providers read -> SettingsChoices. */
export function toSettingsChoices(raw: unknown): SettingsChoices {
  const body = record(raw)
  const providers = list(body.providers).flatMap((entry) => {
    const row = record(entry)
    const id = text(row.id)
    return id === null ? [] : [{ id, name: text(row.name) ?? id }]
  })
  const max = integer(record(body.execution_policy).repeat_count_max)
  const ceiling = max !== null && max >= 1 ? max : SETTINGS_REPEAT_COUNT_DEFAULT_MAX
  return {
    providers,
    defaultProviderId: text(body.default_provider_id),
    reviewCounts: [-1, 0, ...Array.from({ length: ceiling }, (_, i) => i + 1)],
  }
}

/** Reads the run AI / reviewer choices and the review count choices of a project. */
export async function settingsChoicesRead(projectId: string): Promise<SettingsChoices> {
  const response = await getRequest<unknown>(`${API}/ai-invoke/providers`, { project: projectId })
  return toSettingsChoices(response.data)
}

/** What one continuous hop runs with — from the start answer or a run status. */
export interface SettingsHop {
  readonly mode: string | null
  readonly itemSeq: number | null
  readonly providerId: string | null
  readonly providerName: string | null
  /** stored_sequence = the row's run AI; other values: a per-step or fixed pick of the request, the default. */
  readonly providerSource: string | null
  readonly reviewCount: number | null
  readonly reviewerProviderId: string | null
  readonly reviewerProviderName: string | null
}

/** A start answer or a run status -> SettingsHop. */
export function settingsHopOf(raw: unknown): SettingsHop {
  const body = record(raw)
  const provider = record(body.provider)
  return {
    mode: text(body.mode),
    itemSeq: integer(body.hop_item_seq),
    providerId: text(provider.id),
    providerName: text(provider.name),
    providerSource: text(body.selected_provider_source),
    reviewCount: integer(body.hop_review_count),
    reviewerProviderId: text(body.hop_reviewer_provider_id),
    reviewerProviderName: text(body.hop_reviewer_provider_name),
  }
}

/** The group's live AI run, if any, with its hop. */
export interface SettingsRun {
  readonly active: boolean
  readonly hop: SettingsHop | null
}

/** The /ai-invoke/active answer -> SettingsRun (a finished run is not active). */
export function toSettingsRun(raw: unknown): SettingsRun {
  const body = record(raw)
  const active = body.active === true && text(body.status) !== 'finished'
  return { active, hop: active ? settingsHopOf(body) : null }
}

/** Reads whether an AI run owns the group now, and the hop it runs. */
export async function settingsRunRead(groupId: string): Promise<SettingsRun> {
  const response = await getRequest<unknown>(`${API}/ai-invoke/active`, { group_id: groupId })
  return toSettingsRun(response.data)
}

/**
 * Why a settings call was refused:
 * disposed = the group was disposed; running = an AI run owns the group (423, or the policy's 409);
 * owner = the Flow is Graph-owned or in an ownership conflict; stale = the rows moved since the read
 * (another save, a row started); not_decided = no decided sequence; invalid = 400 / 422 (a note too
 * long, a malformed row); forbidden 403; not_found 404; network = no answer.
 */
export type SettingsErrorKind =
  | 'disposed'
  | 'running'
  | 'owner'
  | 'stale'
  | 'not_decided'
  | 'invalid'
  | 'forbidden'
  | 'not_found'
  | 'network'
  | 'other'

export interface SettingsError {
  readonly kind: SettingsErrorKind
  readonly status: number | null
  readonly code: string | null
}

const RUNNING_CODES = new Set(['GROUP_AI_RUN_LOCKED', 'run_in_progress', 'run_still_live', 'group_ai_running'])
const OWNER_CODES = new Set(['flow_owned_by_graph', 'flow_ownership_conflict'])
const STALE_CODES = new Set(['sequence_item_stale', 'protected_row_modified', 'protected_row_echo_ambiguous', 'sequence_changed'])

/** What a failed settings call threw, as kind / status / code. Only codes and status are read. */
export function settingsErrorOf(error: unknown): SettingsError {
  const normalized = normalizeApiError(error)
  const data = record(record(record(error).response).data)
  // The sequence routes answer a flat `{ error: <code> }`; the mutation policy an `error` object.
  const code = normalized.code ?? text(data.error)?.trim() ?? text(data.code)?.trim() ?? null
  const status = normalized.status
  let kind: SettingsErrorKind
  if (normalized.network) kind = 'network'
  else if (code === 'group_disposed') kind = 'disposed'
  else if (status === 423 || (code !== null && RUNNING_CODES.has(code))) kind = 'running'
  else if (code !== null && OWNER_CODES.has(code)) kind = 'owner'
  else if (code !== null && STALE_CODES.has(code)) kind = 'stale'
  else if (code === 'sequence_not_decided') kind = 'not_decided'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 400 || status === 422) kind = 'invalid'
  else kind = 'other'
  return { kind, status, code }
}
