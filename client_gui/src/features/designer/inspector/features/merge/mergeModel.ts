// flowgate.v02test.0042 — the '병합 · 충돌' tab's pure rules (mockup v7, 0005-CH turns 38-40): when the tab shows, its
// colour, which screen it draws and how a reason reads. No API, no Vue.
import type { InspectorTarget } from '../../inspectorTarget'

/** Integration statuses with merge work a person may look at: the tab shows only then. */
export const MERGE_WORK_STATUSES: readonly string[] = ['pending', 'integrating', 'conflict', 'failed']

/** The window event every 'open this card's merge tab' goes through (Canvas, notifications, Git panel, this tab). */
export const OPEN_CARD_MERGE_EVENT = 'fg:open_card_merge'

export type MergeTone = 'info' | 'danger'

/** The card's node run shows merge work (the run / Canvas read's integration status). */
export function mergeWorkOf(target: Pick<InspectorTarget, 'nodeRun'> | null): boolean {
  return MERGE_WORK_STATUSES.includes(target?.nodeRun?.integrationStatus ?? '')
}

/** A server merge state key -> the tab colour: progress only is blue, a person's choice is red. */
export function mergeToneOfState(state: string | null): MergeTone | null {
  if (state === 'waiting' || state === 'catching_up') return 'info'
  if (state === 'needs_choice' || state === 'failed') return 'danger'
  return null
}

/** The node run's integration status -> the tab colour, before the merge state was read. */
export function mergeToneOfStatus(status: string | null | undefined): MergeTone | null {
  if (status === 'pending' || status === 'integrating') return 'info'
  if (status === 'conflict' || status === 'failed') return 'danger'
  return null
}

export type MergeScreen = 'loading' | 'waiting' | 'catching_up' | 'needs_choice' | 'pick' | 'failed' | 'merged' | 'none'

/**
 * Which screen the tab draws: the read state, or ④ Merge complete when work this tab showed went away while it was open
 * (`hadWork`), or the per-file pick (③) the person opened on a conflict.
 */
export function mergeScreenOf(state: string | null, hadWork: boolean, picking: boolean): MergeScreen {
  if (state === null) return 'loading'
  if (state === 'hidden') return hadWork ? 'merged' : 'none'
  if (state === 'needs_choice' && picking) return 'pick'
  if (state === 'waiting' || state === 'catching_up' || state === 'needs_choice' || state === 'failed') return state
  return 'none'
}

/** Message key (under `reasons.`) of an integration reason; `other` for one this tab has no sentence for. */
export function mergeReasonKey(reason: string | null): string {
  if (!reason) return 'other'
  if (reason.startsWith('merge_session:')) return 'merge_session'
  const known = ['conflict_deferred', 'session_busy', 'git_busy', 'gate_snapshot_reserved', 'resolve_requested',
    'verification_failed', 'conflict_aborted', 'approved', 'retry', 'recovered']
  return known.includes(reason) ? reason : 'other'
}

/** One try of the server's automatic resolution as the window draws it (the card merge API's attempt, read structurally). */
export interface MergeAutoAttempt {
  readonly no: number
  readonly mode: string | null
  readonly provider: { readonly name: string } | null
  readonly result: string | null
  readonly reason: string | null
  readonly reasonSummary?: string | null
}

/** The server's automatic resolution as the window draws it (the card merge API's `autoResolve`, read structurally). */
export interface MergeAuto {
  readonly state: string
  readonly attempt: number
  readonly maxAttempts: number
  readonly attempts: readonly MergeAutoAttempt[]
  readonly unresolved: readonly { readonly path: string; readonly hunks: readonly { readonly startLine: number; readonly endLine: number }[] }[]
  readonly fileCount?: number | null
}

/** The server is resolving the conflict by itself right now (the merge state stays `catching_up`). */
export function mergeAutoRunning(auto: Pick<MergeAuto, 'state'> | null | undefined): boolean {
  return auto?.state === 'running'
}

/** The try to show as current: the one still running, else the one the server counts, else the last one. */
export function mergeAutoCurrent<A extends Pick<MergeAutoAttempt, 'no' | 'result'>>(auto: { readonly attempt: number; readonly attempts: readonly A[] } | null | undefined): A | null {
  if (!auto || !auto.attempts.length) return null
  return auto.attempts.find((attempt) => attempt.result === 'running')
    ?? auto.attempts.find((attempt) => attempt.no === auto.attempt)
    ?? auto.attempts[auto.attempts.length - 1]
}

/** The try number shown as `n/max`: the server's, kept inside 1..max. */
export function mergeAutoProgress(auto: Pick<MergeAuto, 'attempt' | 'maxAttempts'>): { n: number; max: number } {
  const max = Math.max(1, auto.maxAttempts)
  return { n: Math.min(Math.max(1, auto.attempt), max), max }
}

/** `3` / `3–9` for a conflict hunk's lines. */
export function mergeHunkRange(hunk: { startLine: number; endLine: number }): string {
  return hunk.endLine > hunk.startLine ? `${hunk.startLine}–${hunk.endLine}` : `${hunk.startLine}`
}

/** Result codes the window has a sentence for; any other (or none) reads as the plain `unknown` ('실패'), never the raw code. */
export const MERGE_AUTO_RESULT_KEYS: readonly string[] = ['running', 'resolved', 'conflicts_left', 'check_failed', 'ai_error', 'timeout', 'no_other_ai']

/** Message key (under `auto.results.`) of a try's result. */
export function mergeAutoResultKey(result: string | null | undefined): string {
  return result && MERGE_AUTO_RESULT_KEYS.includes(result) ? result : 'unknown'
}

/** How long a reason line may run before it is cut. */
export const MERGE_REASON_MAX = 120

const clip = (text: string): string => {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > MERGE_REASON_MAX ? `${flat.slice(0, MERGE_REASON_MAX).trimEnd()}…` : flat
}
const word = (value: unknown): string => (typeof value === 'string' ? value.trim() : typeof value === 'number' && Number.isFinite(value) ? String(value) : '')
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)

/** The JSON inside a reason (`{...}`, maybe after a `code: ` prefix); undefined when there is none or it does not parse. */
function reasonJson(reason: string): unknown {
  const start = reason.search(/[{[]/)
  if (start < 0) return undefined
  try { return JSON.parse(reason.slice(start)) } catch { return undefined }
}

/**
 * A check verdict (`result_verify.check`: `failures: [{ path, checker, line, message }]`, `failure_count`) or a like
 * object -> `checker · path:line · message (+n)`; null when nothing readable is in it.
 */
export function mergeVerdictLine(value: unknown): string | null {
  const row = isRecord(value) ? value : null
  const list: unknown[] = Array.isArray(value) ? value
    : row && Array.isArray(row.failures) ? row.failures
    : row && Array.isArray(row.errors) ? row.errors
    : row && Array.isArray(row.checks) ? row.checks
    : row ? [row] : []
  const first = list.find(isRecord)
  if (!first) return null
  const name = word(first.checker) || word(first.check) || word(first.name) || word(first.test)
  const file = word(first.path) || word(first.file)
  const line = word(first.line)
  const message = word(first.message) || word(first.error) || word(first.detail)
  const parts = [name, file && line ? `${file}:${line}` : file, message].filter(Boolean)
  if (!parts.length) return null
  const total = row && typeof row.failure_count === 'number' ? row.failure_count : list.filter(isRecord).length
  return parts.join(' · ') + (total > 1 ? ` (+${total - 1})` : '')
}

/**
 * What a try's reason shows: the server's summary when it sent one, else a line made from a JSON reason (older servers),
 * else the reason cut to its first part. `raw` is the full reason when the line is not it (shown only on unfolding).
 */
export function mergeAutoReason(attempt: Pick<MergeAutoAttempt, 'reason' | 'reasonSummary'>): { line: string; raw: string | null } {
  const reason = attempt.reason?.trim() ?? ''
  const summary = attempt.reasonSummary?.trim() ?? ''
  const json = !summary && reason ? reasonJson(reason) : undefined
  const parsed = json === undefined ? null : mergeVerdictLine(json)
  const line = summary ? clip(summary) : parsed ? clip(parsed) : clip(reason)
  return { line, raw: reason && reason !== line ? reason : null }
}

/** A `{count}` sentence: the counted key when the count is a positive number, else its `_nocount` sibling (no "( file(s))"). */
export function mergeCountKey(key: string, count: number | null | undefined): string {
  return typeof count === 'number' && count > 0 ? key : `${key}_nocount`
}

/** The selection key of a card. */
export function mergeCardKey(flowId: string, nodeId: string): string {
  return `${flowId}\u0000${nodeId}`
}
