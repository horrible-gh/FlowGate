// flowgate.v02test.0042 — the '병합 · 충돌' tab's pure rules (시안 v7, 0005-CH turns 38-40): when the tab shows, its
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
 * Which screen the tab draws: the read state, or ④ 병합 완료 when work this tab showed went away while it was open
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

/** The selection key of a card. */
export function mergeCardKey(flowId: string, nodeId: string): string {
  return `${flowId}\u0000${nodeId}`
}
