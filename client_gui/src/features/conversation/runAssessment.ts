// flowgate.v02.0147 T#2: stored body evidence and process completion are independent.
import type { AiRunState, ConversationTurn, StatusTone } from './types'
import type { ChatRunState } from './api'

export type TurnReadState = 'complete' | 'failed' | 'limited' | 'discarded'
export interface TurnReadResult { status: TurnReadState; nextAfterSeq: number | null }
export type RunBodyState = 'present' | 'absent' | 'unattributed' | 'unconfirmed'
export interface RunStatusLine { slot: string; tone: StatusTone; code: string; text: string | null }

export function runBelongsToChat(run: AiRunState, runId: string, chatId: string, listedLiveChatId: string | null = null): boolean {
  // Persisted detail omits action_scope in the current server contract. An exact CH doc_ref
  // still proves membership; an explicitly different action scope or group never does.
  // A running detail omits doc_ref. Only an exact server list binding may stand in
  // for it here; finished detail must carry its own CH. Never enrich the DTO itself.
  const docRef = run.docRef ?? (run.status === 'running' ? listedLiveChatId : null)
  return run.runId === runId && docRef === chatId &&
    (run.actionScope == null || run.actionScope === 'chat') &&
    (run.groupId == null || run.groupId === chatId.split('.').slice(0, 3).join('.'))
}

export function runBodyState(turns: readonly ConversationTurn[], runId: string, read: TurnReadState): RunBodyState {
  const saved = turns.filter((turn) => turn.seq > 0 && turn.speaker === 'ai' && turn.body.trim().length > 0)
  if (saved.some((turn) => turn.sourceRunId === runId)) return 'present'
  if (read !== 'complete') return 'unconfirmed'
  return saved.some((turn) => !turn.sourceRunId) ? 'unattributed' : 'absent'
}

const FAILURE_REASONS = new Set(['timeout', 'all_providers_failed', 'worker_error', 'spawn_failed', 'provider_start_failed', 'orphaned_by_restart'])
const REMOTE_CAUSES = new Set(['workdir_unset', 'permission_denied', 'workdir_create_failed', 'workdir_missing', 'path_invalid', 'agent_rejected', 'agent_failed'])

export function runStatusLines(run: ChatRunState, body: RunBodyState, read: TurnReadState): RunStatusLine[] {
  const lines: RunStatusLine[] = []
  const add = (slot: string, code: string, tone: StatusTone = 'warning', text: string | null = null) => lines.push({ slot, tone, code, text })
  // A stop stays a stop, including when the last read could not be confirmed.
  const commit = commitLine(run)
  if (run.endReason === 'cancelled') {
    add('result', 'run_cancelled', 'info')
    if (commit) lines.push(commit)
    return lines
  }
  const detail = [run.endReason, `outcome=${run.outcome ?? '?'}`, `exit_code=${run.exitCode ?? '?'}`].filter(Boolean).join(' · ')
  const failed = run.status === 'finished' && (FAILURE_REASONS.has(run.endReason ?? '') ||
    (typeof run.exitCode === 'number' && run.exitCode !== 0))
  const exited = run.status === 'finished' && run.endReason === 'exited' && run.exitCode === 0
  // The server judges API runs too (their exit code is null). Older servers keep
  // the original rule; an explicit false verdict never falls back to exit code 0.
  const complete = run.status === 'finished' && (run.succeeded == null
    ? exited && run.outcome === 'complete'
    : run.succeeded === true)
  const emptyExit = exited && run.outcome === 'none' && body === 'absent'
  if (failed) add('result', body === 'absent' ? 'run_no_reply' : 'run_failed', 'danger', run.failure?.detail ?? detail)
  else if (complete || emptyExit) {
    if (body === 'absent') add('result', 'run_no_reply', 'warning', detail)
  } else add('result', 'run_result_unconfirmed', 'warning', detail)

  if (read !== 'complete') add('query', 'run_query_unconfirmed')
  else if (body === 'unattributed') add('query', 'run_attribution_unconfirmed')

  if (failed && run.failure?.cause) {
    const { cause, workdir, message, detail: failureDetail } = run.failure
    const parts = workdir && message?.includes(workdir) ? [message] : [workdir, message]
    const known = REMOTE_CAUSES.has(cause)
    add('remote', known ? `remote_${cause}` : 'run_failed', 'danger',
      (known ? parts : [cause, ...parts, failureDetail]).filter(Boolean).join(' · ') || null)
  }
  if (commit) lines.push(commit)
  return lines
}

/**
 * flowgate.v02test.0023: the chat commit point's line — committed (short SHA · subject · files), left uncommitted for
 * want of a message (the next AI call asks for one), or skipped (e.g. `git_busy`; the next call asks for a message).
 */
export function commitLine(run: ChatRunState): RunStatusLine | null {
  const commit = run.chatCommit
  if (!commit) return null
  if (commit.committed) {
    return { slot: 'commit', tone: 'info', code: 'chat_committed',
      text: [commit.commit, commit.subject, `${commit.fileCount} files`].filter(Boolean).join(' · ') }
  }
  if (commit.skippedReason === 'message_missing') {
    const shown = commit.files.slice(0, 3).join(', ') + (commit.fileCount > 3 ? ` (+${commit.fileCount - 3})` : '')
    return { slot: 'commit', tone: 'warning', code: 'chat_commit_missing', text: shown || null }
  }
  return { slot: 'commit', tone: 'warning', code: 'chat_commit_skipped', text: commit.skippedReason }
}
