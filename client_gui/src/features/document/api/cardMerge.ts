// a card result's merge into its Flow's branch, handled inside the card (the Inspector's
// '병합 · 충돌' tab) instead of the Git panel. User JWT only, the Graph envelope.
//
//   state    GET  /api/v1/flows/{flow_id}/nodes/{node_key}/merge           -> 200 merge state (below)
//   action   POST /api/v1/flows/{flow_id}/nodes/{node_key}/merge/actions   { action, expected_attempt_id, ... }
//                                                                         -> 200 merge state + started_run_id
//   items    GET  /api/v1/merge-items                                      -> 200 { items: [item…] } (every readable Flow)
//
//   state = { flow_id, node_key, attempt_id, state: hidden | waiting | catching_up | needs_choice | failed,
//             integration_status, reason, blocked_by: { node_key, title } | null,
//             files: [{ path, resolved, kind: both | deleted_latest | deleted_card }],
//             session: { merge_id, kind, review_state } | null, resolving: { run_id } | null,
//             default_provider: { id, name } | null, actions: [ai_resolve | pick | merge | discard_rerun | retry…],
//             waiting_cards: [{ node_key, title }], auto_resolve: autoResolve | null }
//
//   autoResolve = the server's automatic conflict resolution (up to 3 tries) of this card's merge; null / absent = none ran:
//     { state: running | succeeded | exhausted | stopped_by_person | skipped_paused, attempt: 1..3, max_attempts: 3,
//       attempts: [{ no, mode: ai | rebase | rebase_ai | switch_ai, provider: { id, name } | null,
//                    result: running | resolved | conflicts_left | check_failed | ai_error | timeout | no_other_ai,
//                    reason, reason_summary?, started_at, ended_at }],
//       unresolved: [{ path, hunks: [{ start_line, end_line }] }], count? }
//     attempts[].result = ai_error for a failed AI execution (including a nonzero exit code); reason_summary is
//       a one-line display string (at most 1000 characters), also used for check_failed. reason keeps the diagnostic
//       text (at most 2000 characters); a check failure may carry a JSON verdict. Running / older attempts may omit
//       reason_summary. The decoder maps it to reasonSummary; the window prefers it and can unfold reason.
//     count = remaining conflicted files, a nonnegative integer in every state, including skipped_paused; the
//       decoder maps a positive count to fileCount, else null. Older servers may omit count.
//     exhausted unresolved[].hunks uses 1-based inclusive marker line ranges from the conflict session files;
//       an unreadable file or one without markers has []. The decoder maps start_line/end_line to startLine/endLine.
//   While automatic resolution runs the card state is `catching_up`; exhausted open conflicts are `needs_choice`
//     when no resolver is active. The Flow run status is separate: after resume, running automatic work wakes
//     idle(merge_conflict) to running; paused / stopped runs are not woken by settlement alone.
//
// Refusals are the Graph envelope `{ ok: false, error: { code, message, params } }` (cardMergeErrorOf): 409
// merge_state_changed (read the state again) / group_busy / conflict_markers_remain / git_busy, 422 invalid_request /
// pick_unavailable, 403 flow_access_denied.
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, postRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})

export const cardMergeStates = ['hidden', 'waiting', 'catching_up', 'needs_choice', 'failed'] as const
export type CardMergeStateKey = (typeof cardMergeStates)[number]
export const cardMergeActions = ['ai_resolve', 'pick', 'merge', 'discard_rerun', 'retry'] as const
export type CardMergeAction = (typeof cardMergeActions)[number]
/** One file's choice in a per-file pick: the AI, this card's result, or the latest Flow code. */
export type CardMergeSide = 'ai' | 'card' | 'latest'

/** How a conflicting file overlaps: both sides changed it, or one side (the latest code / this card) deleted it. */
export const cardMergeFileKinds = ['both', 'deleted_latest', 'deleted_card'] as const
export type CardMergeFileKind = (typeof cardMergeFileKinds)[number]

export interface CardMergeFile {
  readonly path: string
  readonly resolved: boolean
  readonly kind: CardMergeFileKind
}

/** How the automatic conflict resolution stands: running, or how it ended. */
export const cardMergeAutoStates = ['running', 'succeeded', 'exhausted', 'stopped_by_person', 'skipped_paused'] as const
export type CardMergeAutoStateKey = (typeof cardMergeAutoStates)[number]
/** What one try did: the AI as it is, the card's change re-applied on the Flow's tip (with the AI after it), or another AI. */
export const cardMergeAutoModes = ['ai', 'rebase', 'rebase_ai', 'switch_ai'] as const
export type CardMergeAutoMode = (typeof cardMergeAutoModes)[number]
/** How one try ended. */
export const cardMergeAutoResults = ['running', 'resolved', 'conflicts_left', 'check_failed', 'ai_error', 'timeout', 'no_other_ai'] as const
export type CardMergeAutoResult = (typeof cardMergeAutoResults)[number]

export interface CardMergeAutoAttempt {
  readonly no: number
  /** null = a mode this screen does not know (read as the plain AI try). */
  readonly mode: CardMergeAutoMode | null
  readonly provider: { readonly id: string; readonly name: string | null } | null
  readonly reasonCode?: string | null
  /** null = a result this screen does not know. */
  readonly result: CardMergeAutoResult | null
  readonly reason: string | null
  /** The server's one-line reason (`reason_summary`); null from an older server (the screen then reads `reason` itself). */
  readonly reasonSummary: string | null
  readonly startedAt: string | null
  readonly endedAt: string | null
}

export interface CardMergeAutoHunk {
  readonly startLine: number
  readonly endLine: number
}

export interface CardMergeAutoUnresolved {
  readonly path: string
  readonly hunks: readonly CardMergeAutoHunk[]
}

export interface CardMergeAutoResolve {
  readonly state: CardMergeAutoStateKey
  readonly phase?: string | null
  readonly waiting?: { readonly reason: 'run_in_progress'; readonly runId: string | null; readonly since: string | null } | null
  readonly attempt: number
  readonly maxAttempts: number
  readonly attempts: readonly CardMergeAutoAttempt[]
  readonly unresolved: readonly CardMergeAutoUnresolved[]
  /** The conflicted file count the server sent (`count`); null when absent or not a positive number. */
  readonly fileCount: number | null
}

export interface CardMergeCard {
  readonly nodeKey: string
  readonly title: string
  /** A card another one waits behind: holding the group with its open conflict, or merging. */
  readonly why?: 'conflict' | 'merging' | null
}

export interface CardMergeState {
  readonly flowId: string
  readonly nodeKey: string
  readonly attemptId: string | null
  readonly state: CardMergeStateKey
  readonly integrationStatus: string | null
  readonly reason: string | null
  readonly blockedBy: CardMergeCard | null
  readonly files: readonly CardMergeFile[]
  readonly session: { readonly mergeId: number; readonly kind: string | null; readonly reviewState: string | null } | null
  readonly resolving: { readonly runId: string | null } | null
  readonly defaultProvider: { readonly id: string; readonly name: string } | null
  readonly actions: readonly CardMergeAction[]
  /** The cards whose merge waits behind this one. */
  readonly waitingCards: readonly CardMergeCard[]
  /** An action's answer: the AI run it started, or null. */
  readonly startedRunId: string | null
  /** The server's automatic conflict resolution; null when the answer has none (the screen then stays as it was). */
  readonly autoResolve: CardMergeAutoResolve | null
}

function card(raw: unknown): CardMergeCard | null {
  const row = record(raw)
  const nodeKey = text(row.node_key)
  const why = row.why === 'conflict' || row.why === 'merging' ? row.why : null
  return nodeKey ? { nodeKey, title: text(row.title) ?? nodeKey, why } : null
}

const count = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const provider = (raw: unknown): { id: string; name: string } | null => {
  const row = record(raw)
  const id = text(row.id)
  return id ? { id, name: text(row.name) ?? id } : null
}

function autoProviderText(value: unknown): string | null {
  const name = text(value)?.trim()
  return name && !/^(none|null)$/i.test(name) ? name : null
}

/** `auto_resolve` -> CardMergeAutoResolve; null for a missing / null / unknown-state value (the screen then draws nothing extra). */
export function toCardMergeAutoResolve(raw: unknown): CardMergeAutoResolve | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const row = record(raw)
  const state = cardMergeAutoStates.find((key) => key === row.state)
  if (!state) return null
  const maxAttempts = count(row.max_attempts)
  const attempts = Array.isArray(row.attempts)
    ? row.attempts.map((item, index): CardMergeAutoAttempt => {
        const attempt = record(item)
        const ai = record(attempt.provider)
        const id = autoProviderText(ai.id)
        return {
          no: count(attempt.no) ?? index + 1,
          mode: cardMergeAutoModes.find((key) => key === attempt.mode) ?? null,
          provider: id ? { id, name: autoProviderText(ai.name) } : null,
          reasonCode: text(attempt.reason_code),
          result: cardMergeAutoResults.find((key) => key === attempt.result) ?? null,
          reason: text(attempt.reason),
          reasonSummary: text(attempt.reason_summary),
          startedAt: text(attempt.started_at),
          endedAt: text(attempt.ended_at),
        }
      })
    : []
  const unresolved = Array.isArray(row.unresolved)
    ? row.unresolved.flatMap((item) => {
        const file = record(item)
        const path = text(file.path)
        if (!path) return []
        const hunks = Array.isArray(file.hunks)
          ? file.hunks.flatMap((hunk) => {
              const range = record(hunk)
              const startLine = count(range.start_line)
              const endLine = count(range.end_line)
              return startLine === null ? [] : [{ startLine, endLine: endLine ?? startLine }]
            })
          : []
        return [{ path, hunks }]
      })
    : []
  const fileCount = count(row.count)
  const waiting = record(row.waiting)
  return {
    phase: text(row.phase),
    waiting: waiting.reason === 'run_in_progress'
      ? { reason: waiting.reason, runId: text(waiting.run_id), since: text(waiting.since) }
      : null,
    state, attempt: count(row.attempt) ?? attempts.length, maxAttempts: maxAttempts && maxAttempts > 0 ? maxAttempts : 3, attempts, unresolved,
    fileCount: fileCount !== null && fileCount > 0 ? Math.floor(fileCount) : null,
  }
}

/** A state answer -> CardMergeState (an unknown state reads as hidden, unknown actions are dropped). */
export function toCardMergeState(raw: unknown, flowId = '', nodeKey = ''): CardMergeState {
  const row = record(raw)
  const state = cardMergeStates.find((key) => key === row.state) ?? 'hidden'
  const session = record(row.session)
  const resolving = row.resolving && typeof row.resolving === 'object' ? record(row.resolving) : null
  const defaultProvider = provider(row.default_provider)
  return {
    flowId: text(row.flow_id) ?? flowId,
    nodeKey: text(row.node_key) ?? nodeKey,
    attemptId: text(row.attempt_id),
    state,
    integrationStatus: text(row.integration_status),
    reason: text(row.reason),
    blockedBy: card(row.blocked_by),
    files: Array.isArray(row.files)
      ? row.files.flatMap((item) => {
          const file = record(item)
          const path = text(file.path)
          const kind = cardMergeFileKinds.find((key) => key === file.kind) ?? 'both'
          return path ? [{ path, resolved: file.resolved === true, kind }] : []
        })
      : [],
    session: typeof session.merge_id === 'number' && Number.isFinite(session.merge_id)
      ? { mergeId: session.merge_id, kind: text(session.kind), reviewState: text(session.review_state) }
      : null,
    resolving: resolving ? { runId: text(resolving.run_id) } : null,
    defaultProvider,
    actions: Array.isArray(row.actions) ? cardMergeActions.filter((action) => (row.actions as unknown[]).includes(action)) : [],
    waitingCards: Array.isArray(row.waiting_cards) ? row.waiting_cards.flatMap((item) => card(item) ?? []) : [],
    startedRunId: text(row.started_run_id),
    autoResolve: toCardMergeAutoResolve(row.auto_resolve),
  }
}

const mergePath = (flowId: string, nodeKey: string) => `${API}/flows/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeKey)}/merge`

/** Reads a card's merge state. */
export async function cardMergeRead(flowId: string, nodeKey: string): Promise<CardMergeState> {
  const response = await getRequest<unknown>(mergePath(flowId, nodeKey))
  return toCardMergeState(response.data, flowId, nodeKey)
}

/** What a merge action sends (the attempt the screen showed, so a moved attempt is refused). */
export type CardMergeRequest =
  | { readonly action: 'ai_resolve'; readonly expectedAttemptId: string; readonly providerId?: string | null }
  | { readonly action: 'pick'; readonly expectedAttemptId: string; readonly files: readonly { readonly path: string; readonly side: CardMergeSide }[]; readonly providerId?: string | null }
  | { readonly action: 'merge' | 'discard_rerun' | 'retry'; readonly expectedAttemptId: string }

/** The request body in the server's names. */
export function cardMergePayload(request: CardMergeRequest): Raw {
  const body: Raw = { action: request.action, expected_attempt_id: request.expectedAttemptId }
  if ((request.action === 'ai_resolve' || request.action === 'pick') && request.providerId) body.provider_id = request.providerId
  if (request.action === 'pick') body.files = request.files.map((file) => ({ path: file.path, side: file.side }))
  return body
}

/** Presses one merge action; answers the state after it. */
export async function cardMergeAct(flowId: string, nodeKey: string, request: CardMergeRequest): Promise<CardMergeState> {
  const response = await postRequest<unknown>(`${mergePath(flowId, nodeKey)}/actions`, cardMergePayload(request))
  return toCardMergeState(response.data, flowId, nodeKey)
}

/** One merge waiting somewhere (the notification list). */
export interface CardMergeItem {
  readonly flowId: string
  readonly flowTitle: string | null
  readonly nodeKey: string
  readonly title: string
  readonly state: Exclude<CardMergeStateKey, 'hidden'>
  readonly blockedBy: CardMergeCard | null
}

/** The items answer -> the shown items (hidden or malformed rows dropped). */
export function toCardMergeItems(raw: unknown): CardMergeItem[] {
  const items = record(raw).items
  if (!Array.isArray(items)) return []
  return items.flatMap((item) => {
    const row = record(item)
    const flowId = text(row.flow_id)
    const nodeKey = text(row.node_key)
    const state = cardMergeStates.find((key) => key === row.state)
    if (!flowId || !nodeKey || !state || state === 'hidden') return []
    return [{ flowId, flowTitle: text(row.flow_title), nodeKey, title: text(row.title) ?? nodeKey, state, blockedBy: card(row.blocked_by) }]
  })
}

/** Reads the merges waiting in every Flow the user may read. */
export async function cardMergeItems(): Promise<CardMergeItem[]> {
  const response = await getRequest<unknown>(`${API}/merge-items`)
  return toCardMergeItems(response.data)
}

/** A refused merge call as the screen reads it. */
export interface CardMergeError {
  readonly code: string | null
  readonly status: number | null
  readonly network: boolean
  readonly message: string
}

export function cardMergeErrorOf(error: unknown): CardMergeError {
  const normalized = normalizeApiError(error)
  const source = normalized.source as Raw
  return {
    code: normalized.code,
    status: normalized.status,
    network: normalized.network,
    message: typeof source?.message === 'string' ? source.message.trim() : '',
  }
}
