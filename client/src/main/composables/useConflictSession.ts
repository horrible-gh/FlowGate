/**
 * flowgate.default.0668 T0004 — one conflict lifecycle for every Git conflict surface.
 *
 * `GitStatusPanel`, `GitFinalizePanel` and `GitBranchMergeConflictHost` each carried their
 * own copy of the same four things: which server states mean "waiting for review", how a
 * conflict AI run is started, what the run line under it says, and when the run ending
 * should hand the operator to the review screen. The copies drifted (the header panel
 * handed over only while its resolver was open; a TR conflict re-opened an empty
 * resolver), so they live here once.
 *
 * The lifecycle is the server's, never the screen's:
 *
 *   conflict → AI resolving (or direct editing) → review pending → applying → completed
 *
 * `attempt_state`/`review_state` decide where a card is. A screen that was closed while the
 * AI worked reads the same state when it comes back, and the miniplayer can ask for the
 * review screen through {@link requestConflictReview} without any panel being mounted.
 */
import { ref, watch } from 'vue'
import { postRequest } from '@shared/api'
import { isScreenOwnedRun, useAiInvokeRunsStore, type AiInvokeRunEntry } from '../stores/aiInvokeRuns'
import { useAiProviderStore } from '../stores/aiProvider'

/** Server review states that belong to the review screen, not the resolver (0481 D0006 §6.4). */
export const REVIEW_PENDING_STATES: ReadonlySet<string> = new Set([
  'resolved_pending_review', 're_review', 'applying', 'reconciling',
])

/** A TR conflict session's own word for "resolved, waiting for review" (TR0019). */
export const TR_REVIEW_RESOLVED = 'resolved'

export function isReviewPendingState(reviewState: string | null | undefined): boolean {
  return !!reviewState && REVIEW_PENDING_STATES.has(reviewState)
}

/** i18n key suffix under `main.git_review.badge.*` for a review state. */
export function reviewBadgeKeyOf(reviewState: string | null | undefined): string {
  if (reviewState === 'reconciling') return 'reconciling'
  if (reviewState === 're_review') return 're_review'
  if (reviewState === 'applying') return 'applying'
  return 'pending'
}

export interface ConflictTarget {
  group_id: string
  merge_id: number | null
}

export function groupParts(groupId: string) {
  const [project, module = 'none', ...rest] = groupId.split('.')
  return { project, module, group: rest.join('.') }
}

/** The group's own conflict AI run while it is still working, else null. */
export function runningConflictRun(
  store: ReturnType<typeof useAiInvokeRunsStore>, groupId: string | null | undefined,
): AiInvokeRunEntry | null {
  if (!groupId) return null
  const entry = store.runsByGroup[groupId]
  if (!entry || !isScreenOwnedRun(entry)) return null
  return entry.phase === 'running' || entry.phase === 'pause_requested' ? entry : null
}

/** "m:ss" of a running group's run, for the run line every surface shows. */
export function conflictRunElapsed(
  store: ReturnType<typeof useAiInvokeRunsStore>, groupId: string,
): string {
  const total = Math.floor(store.elapsedMsFor(groupId) / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export interface ConflictAiStartOptions {
  /** Free text for the run (the resolver's instruction box). */
  message?: string
  /** The resolver's [자동] option — stamped server-side at this human request only. */
  auto?: boolean
}

/**
 * Start a `resolve_conflict` AI run for a group conflict session straight from wherever
 * the operator is — the conflict card or the resolver. The provider is the store's
 * current selection (the card's own selector writes it), so the choice made on the card
 * is the one the run uses and every later screen defaults to it.
 *
 * The start response is adopted as the run entry immediately (0481 T0010 rev5 반려 #1):
 * the card shows "AI 해결 중" without waiting for the worker's SSE frame. The request
 * body is exactly the one both panels sent before this composable existed.
 */
export function useConflictAiStarter(projectId: () => string) {
  const runs = useAiInvokeRunsStore()
  const providers = useAiProviderStore()
  /** group_id whose start request is in flight (the "call sent" window). */
  const starting = ref<string | null>(null)

  async function start(target: ConflictTarget, options: ConflictAiStartOptions = {}): Promise<void> {
    if (target.merge_id == null) return
    starting.value = target.group_id
    try {
      await providers.ensureLoaded(projectId())
      const body: Record<string, unknown> = {
        ...groupParts(target.group_id),
        action_scope: 'resolve_conflict',
        mode: 'single',
        merge_id: target.merge_id,
        // 0481 D0006 §3.2 — a no-op for a TR session (record_auto_authority ignores it).
        auto: !!options.auto,
      }
      if (providers.selectedProviderId) body.provider_id = providers.selectedProviderId
      if (options.message) body.messages = [options.message]
      const response = await postRequest<Record<string, unknown>>('/api/v1/ai-invoke/start', body)
      runs.trackStarted({
        ...response.data,
        group_id: target.group_id,
        action_scope: 'resolve_conflict',
      })
    } finally {
      starting.value = null
    }
  }

  return { starting, start }
}

/**
 * Call `onEnded(groupId)` when a group's own conflict AI run stops working — for every
 * group `groupIds()` names, whether or not any resolver is open. This is what replaces
 * the old "hand over only if the resolver was still on screen" watchers: the caller
 * re-reads server state and opens the review screen if the session reached review.
 */
export function watchConflictRunsEnded(
  groupIds: () => string[], onEnded: (groupId: string) => void | Promise<void>,
): void {
  const runs = useAiInvokeRunsStore()
  watch(
    () => groupIds().filter((groupId) => !!runningConflictRun(runs, groupId)).sort().join('\n'),
    (now, before) => {
      const running = new Set(now ? now.split('\n') : [])
      for (const groupId of before ? before.split('\n') : []) {
        if (!running.has(groupId)) void onEnded(groupId)
      }
    },
  )
}

/** Window event the global review host listens to (miniplayer, notifications). */
export const OPEN_CONFLICT_REVIEW_EVENT = 'fg:open_conflict_review'

export interface OpenConflictReviewDetail {
  group_id: string
  merge_id?: number | null
}

/** Ask the global host to open this group's review screen; it resolves the merge_id itself. */
export function requestConflictReview(groupId: string, mergeId: number | null = null): void {
  if (typeof window === 'undefined') return
  const detail: OpenConflictReviewDetail = { group_id: groupId, merge_id: mergeId }
  window.dispatchEvent(new CustomEvent(OPEN_CONFLICT_REVIEW_EVENT, { detail }))
}
