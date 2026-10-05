import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { isScreenOwnedRun, useAiInvokeRunsStore, type AiInvokeRunEntry } from '../stores/aiInvokeRuns'

/**
 * flowgate.default.0674 T0004 §2-1 (D1) — how a screen-owned conflict AI run ended, for the
 * two conflict screens that start it (GitFinalizePanel, GitStatusPanel).
 *
 * Both used to react to the end of the run by re-reading the status and the conflict list
 * only, so a run that failed (0668: provider at capacity, exit 1, outcome none) left the
 * same conflict list behind with nothing said — while the running line had promised "this
 * screen updates with the result when it finishes". The verdict is judged here, once, from
 * the run's own finished entry: anything but a `complete` landing is a failure, and the
 * reason is the server's canonical `stop_reason` (or the run's last message). Whether the
 * conflict is still open is the host's call — it is the one holding the finalize state.
 */
export interface ConflictAiRunFailure {
  runId: string
  outcome: AiInvokeRunEntry['outcome']
  lost: boolean
  reason: string | null
}

const REASON_MAX_CHARS = 300

function clip(text: string): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > REASON_MAX_CHARS ? `${flat.slice(0, REASON_MAX_CHARS - 1)}…` : flat
}

/**
 * The verdict for one ended run. `finished` is the run-keyed history entry, which is absent
 * when the finished-card retention is 0 ("disappears immediately"): an unknown ending is
 * judged a failure too, and the host only shows it while the conflict is still open.
 */
export function judgeConflictAiRun(
  runId: string,
  finished: AiInvokeRunEntry | null | undefined,
): ConflictAiRunFailure | null {
  if (finished && finished.phase === 'finished' && finished.outcome === 'complete') return null
  const raw = finished?.stopReason || finished?.lastMessage || ''
  return {
    runId,
    outcome: finished?.outcome ?? null,
    lost: finished?.phase === 'lost',
    reason: raw.trim() ? clip(raw) : null,
  }
}

function isLive(entry: AiInvokeRunEntry): boolean {
  return entry.phase === 'running' || entry.phase === 'pause_requested'
}

/**
 * Watches every group's screen-owned conflict run. When one ends, `onSettled(groupId,
 * failure)` runs first (the host refreshes its state there) and only then is the failure
 * recorded, so a run that did resolve never flashes a failure line before the refresh lands.
 * A new run of the same group clears the group's failure.
 */
export function useConflictAiRunFailures(
  onSettled?: (groupId: string, failure: ConflictAiRunFailure | null) => Promise<void> | void,
) {
  const store = useAiInvokeRunsStore()
  const { t } = useI18n()
  const failures = reactive<Record<string, ConflictAiRunFailure>>({})

  const liveRuns = computed(() => {
    const out: Record<string, string> = {}
    for (const [groupId, entry] of Object.entries(store.runsByGroup)) {
      if (entry.runId && isScreenOwnedRun(entry) && isLive(entry)) out[groupId] = entry.runId
    }
    return out
  })

  watch(liveRuns, (now, before) => {
    for (const [groupId, runId] of Object.entries(now)) {
      if (before?.[groupId] !== runId) delete failures[groupId]
    }
    for (const [groupId, runId] of Object.entries(before ?? {})) {
      if (now[groupId] === runId) continue
      // Still in the group slot but no longer live (a pause): not an ending.
      if (store.runsByGroup[groupId]?.runId === runId) continue
      const failure = judgeConflictAiRun(runId, store.finishedByRun[runId])
      void (async () => {
        try {
          await onSettled?.(groupId, failure)
        } finally {
          // A newer run may have started while the host was refreshing.
          if (!liveRuns.value[groupId]) {
            if (failure) failures[groupId] = failure
            else delete failures[groupId]
          }
        }
      })()
    }
  })

  function failureText(groupId: string | null | undefined): string {
    const failure = groupId ? failures[groupId] : undefined
    if (!failure) return ''
    const head = failure.reason
      ? t('main.git_finalize.conflict_ai_failed', { reason: failure.reason })
      : t('main.git_finalize.conflict_ai_failed_no_reason')
    return `${head} ${t('main.git_finalize.conflict_ai_failed_next')}`
  }

  function clear(groupId: string | null | undefined): void {
    if (groupId) delete failures[groupId]
  }

  return { failures, failureText, clear }
}
