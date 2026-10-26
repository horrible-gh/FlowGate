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

// flowgate.v02.0053 T#5 — the card (Node) settings T#2 stores (server/flowgate/card_settings), read by the
// documents tab for the one value it shows: a card whose instruction is omitted has no 지시서 line (v7).
//   node      GET   /api/v1/flows/{flow_id}/nodes/{node_key}/settings -> { ok, flow_id, node_key, card_kind,
//             locked, fields: { <name>: { value, stored, default, choices } } } — `value` is the stored
//             value, else the default; `card_kind` is null for a card without settings. 404 node_not_found
//             for a node the server does not keep.

/** One card setting as the server answers it. */
export interface SettingsNodeField {
  /** The stored value, else the default. */
  readonly value: unknown
  readonly stored: unknown
  readonly default: unknown
  /** The values to choose from; null for a free value (an AI, a branch, a number). */
  readonly choices: readonly unknown[] | null
}

/** Read-only judgement returned with check card settings (0098 T#1). */
export interface SettingsMergeTargetEntry {
  readonly branch: string | null
  readonly checked: boolean
  readonly error: string | null
  readonly unmerge_supported: boolean | null
  readonly warnings: readonly string[]
  readonly inherited?: boolean
}
export interface SettingsMergeTargetState {
  readonly status: string
  readonly code: string | null
  readonly reason: string | null
  readonly current: SettingsMergeTargetEntry | null
  readonly default: SettingsMergeTargetEntry | null
}

/** A card's settings (T#2). */
export interface SettingsNode {
  readonly mergeTargetOptions?: readonly string[]
  readonly defaultTargetBranch?: string | null
  readonly mergeTargetState?: SettingsMergeTargetState | null
  readonly pushRemoteCheckRequired?: boolean
  readonly settingsVersion?: number
  readonly lockReason?: string | null
  readonly cardKind: string | null
  /** A running card: its settings cannot change now. */
  readonly locked: boolean
  readonly fields: Readonly<Record<string, SettingsNodeField>>
}

/** The card settings read -> SettingsNode (unknown field shapes dropped). */
export function toSettingsNode(raw: unknown): SettingsNode {
  const data = record(raw)
  const fields: Record<string, SettingsNodeField> = {}
  for (const [name, value] of Object.entries(record(data.fields))) {
    const field = record(value)
    fields[name] = {
      value: field.value ?? null,
      stored: field.stored ?? null,
      default: field.default ?? null,
      choices: Array.isArray(field.choices) ? [...field.choices] : null,
    }
  }
  return { cardKind: text(data.card_kind), locked: data.locked === true, fields,
    ...('merge_target_options' in data ? { mergeTargetOptions: list(data.merge_target_options).filter((v): v is string => typeof v === 'string') } : {}),
    ...('default_target_branch' in data ? { defaultTargetBranch: text(data.default_target_branch) } : {}),
    ...('merge_target_state' in data ? { mergeTargetState: data.merge_target_state && typeof data.merge_target_state === 'object' ? data.merge_target_state as SettingsMergeTargetState : null } : {}),
    ...('push_remote_check_required' in data ? { pushRemoteCheckRequired: data.push_remote_check_required === true } : {}),
    ...(typeof data.settings_version === 'number' ? { settingsVersion: data.settings_version } : {}),
    ...('lock_reason' in data ? { lockReason: text(data.lock_reason) } : {}) }
}

/** Reads a card's settings. */
export async function settingsNodeRead(flowId: string, nodeKey: string, refreshMergeTargets = false): Promise<SettingsNode> {
  const url = `${API}/flows/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeKey)}/settings`
  const response = refreshMergeTargets
    ? await getRequest<unknown>(url, { refresh_merge_targets: true })
    : await getRequest<unknown>(url)
  return toSettingsNode(response.data)
}

/** Whether the card omits its instruction (`instruction` = omit): its 지시서 line is not shown. */
export function settingsInstructionOmitted(node: SettingsNode | null | undefined): boolean {
  return node?.fields.instruction?.value === 'omit'
}

// flowgate.v02.0053 T#7 — the settings tab changes the card's settings one value at a time (no save button):
//   change    PATCH /api/v1/flows/{flow_id}/nodes/{node_key}/settings { field, value } -> the card's settings
//             as they are now (the GET shape). `value` null takes the default back. Refusals are the Graph
//             envelope `{ ok: false, error: { code, message, params } }`: 409 node_settings_locked (the card
//             runs) / flow_disposed / card_settings_unavailable, 422 invalid_request (params.errors[].reason),
//             404 flow_not_found / node_not_found, 401 / 403.
//   teams     GET   /api/v1/ai-groups -> { items: [{ ai_group_id, name, enabled, supervisor: { provider_name } | null }] }
//             The AI Team choices of a requirement card (the server takes the ids of this table only).

/** Changes one setting; gate callers pass the confirmed version for the server's concurrency check. */
export async function settingsNodeWrite(flowId: string, nodeKey: string, field: string, value: unknown, expectedSettingsVersion?: number): Promise<SettingsNode> {
  const response = await patchRequest<unknown>(`${API}/flows/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeKey)}/settings`, {
    field,
    value: value ?? null,
    ...(expectedSettingsVersion === undefined ? {} : { expected_settings_version: expectedSettingsVersion }),
  })
  return toSettingsNode(response.data)
}

// flowgate.v02.0061 T#15 — a card's Hold (T#11) is the card setting `hold` of the same call: true = held, false / null =
// released (the default). The Designer's card context menu sets and releases it; the server judges it by the Flow's run
// (409 graph_run_locked while the run is running / paused / stopping, 409 graph_hold_done_node for a done card) and a
// release only changes the setting — nothing starts before the next run command.

/** Whether the card's settings say it is held. */
export function settingsNodeHold(node: SettingsNode | null | undefined): boolean {
  return node?.fields.hold?.value === true
}

/** A refused card settings call as an Error with the server's `code`, `status` and message (x-locale) as sent. */
export function settingsNodeRefusal(error: unknown): Error & { readonly code: string | null; readonly status: number | null } {
  const normalized = normalizeApiError(error)
  const message = typeof normalized.source.message === 'string' ? normalized.source.message.trim() : ''
  return Object.assign(new Error(message), { code: normalized.network ? null : normalized.code, status: normalized.status })
}

/** Sets (true) or releases (false) the card's Hold; answers whether it is held now. A refusal rejects with settingsNodeRefusal's Error. */
export async function settingsNodeHoldWrite(flowId: string, nodeKey: string, on: boolean): Promise<boolean> {
  try {
    return settingsNodeHold(await settingsNodeWrite(flowId, nodeKey, 'hold', on))
  } catch (error) {
    throw settingsNodeRefusal(error)
  }
}

/**
 * Why a card settings call was refused: locked = the card runs (its AI already took the settings);
 * disposed = the Flow's group was disposed; unavailable = the server has no card settings yet; invalid =
 * 400 / 422 (a value the card does not take); forbidden 401 / 403; not_found 404; network = no answer.
 */
export type SettingsNodeErrorKind = 'locked' | 'disposed' | 'unavailable' | 'invalid' | 'forbidden' | 'not_found' | 'network' | 'other'

/** What a failed card settings call threw. Only the code and the status are read. */
export function settingsNodeErrorOf(error: unknown): SettingsNodeErrorKind {
  const normalized = normalizeApiError(error)
  const code = normalized.code
  const status = normalized.status
  if (normalized.network) return 'network'
  if (code === 'node_settings_locked' || code === 'gate_settings_locked') return 'locked'
  if (code === 'flow_disposed' || code === 'group_disposed') return 'disposed'
  if (code === 'card_settings_unavailable') return 'unavailable'
  if (status === 400 || status === 422) return 'invalid'
  if (status === 401 || status === 403) return 'forbidden'
  if (status === 404) return 'not_found'
  return 'other'
}

/**
 * flowgate.v02.0056 F2-1 — the reason a 422 card settings change names (`error.params.errors[0].reason`: unknown_ai,
 * not_a_text, invalid_text, not_a_setting_of_this_card …), or null.
 */
export function settingsNodeErrorReason(error: unknown): string | null {
  const normalized = normalizeApiError(error)
  if (normalized.status !== 422 && normalized.status !== 400) return null
  const params = normalized.source.params
  const errors = params && typeof params === 'object' ? (params as Record<string, unknown>).errors : undefined
  const first = Array.isArray(errors) ? errors.find((entry) => entry && typeof entry === 'object') : undefined
  const reason = first ? (first as Record<string, unknown>).reason : undefined
  return typeof reason === 'string' && reason ? reason : null
}

/**
 * flowgate.v02.0056 F2-1 — the server's own message of a refused card settings change, as it sent it (x-locale),
 * or null (no answer, no message, or only the code repeated).
 */
export function settingsNodeErrorMessage(error: unknown): string | null {
  const normalized = normalizeApiError(error)
  const message = typeof normalized.source.message === 'string' ? normalized.source.message.trim() : ''
  return message && message !== normalized.code ? message : null
}

/** One AI Team a requirement card may name. */
export interface SettingsTeam {
  readonly id: string
  readonly name: string
  /** Its Supervisor's AI name, or null (a team without a Supervisor). */
  readonly supervisorName: string | null
  readonly enabled: boolean
}

/** The AI Team list -> SettingsTeam[] (rows without an id dropped). */
export function toSettingsTeams(raw: unknown): SettingsTeam[] {
  return list(record(raw).items).flatMap((entry) => {
    const row = record(entry)
    const id = text(row.ai_group_id)
    if (id === null) return []
    const supervisor = record(row.supervisor)
    return [{ id, name: text(row.name) ?? id, supervisorName: text(supervisor.provider_name) ?? text(supervisor.provider_id), enabled: row.enabled !== false }]
  })
}

/** Reads the AI Teams a requirement card may name. */
export async function settingsTeamsRead(): Promise<SettingsTeam[]> {
  const response = await getRequest<unknown>(`${API}/ai-groups`)
  return toSettingsTeams(response.data)
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
