// flowgate.v02.0074 T#5 — the 작업계획 card's deploy (D#1 §6.1 · §3.6.1 · §3.6.4; T#4 API, 0014-TR §2). The status read
// (`GET …/work-plan/deploy`) is read through useInspectorResource with the plan's events (a save of the plan changes its
// targets) and again after every deploy call. A click — [전개] or a [재전개 ▾] item — always starts with a new preview of
// its mode at the status read's wp_revision_no:
// - blockers: nothing runs, the first blocker is the one line;
// - no change: nothing runs, 「변경 없음」;
// - cleanup: the preview waits in `pendingCleanup` for the one confirmation (its remove / keep counts); the execution then
//   sends that preview's baseline (and so its remove_node_keys). [전개] and 「추가분 전개」 run without asking.
// - otherwise the execution with the preview (its baseline as the server sent it, workPlanDeployExecute).
// A refused execution is one line (workPlanDeployFailureKey) and the status is read again; nothing runs again by itself —
// a baseline the server no longer matches (draft_version_conflict · deploy_preview_stale · wp_changed) is dropped and the
// next click starts from a new preview. One deploy at a time (`busy`, set before the first await); another card selected
// drops the answers of the previous one, and so does the header going away (another kind of card, no selection, the
// Designer closed): a preview answered after that runs nothing.
import { computed, onScopeDispose, ref, shallowRef, watch, type ComputedRef, type Ref, type ShallowRef } from 'vue'
import {
  workPlanDeployExecute,
  workPlanDeployFailureOf,
  workPlanDeployPreview,
  workPlanDeployStatus,
  type WorkPlanDeployFailure,
  type WorkPlanDeployPreview,
  type WorkPlanDeployStatus,
} from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorResource } from '../../useInspectorResource'
import { WORK_PLAN_EVENTS, workPlanEventMatches } from './useWorkPlan'
import {
  WORK_PLAN_DEPLOY_MODE_OF,
  workPlanDeployActions,
  workPlanDeployBlockerKey,
  workPlanDeployFailureKey,
  type WorkPlanDeployItem,
  type WorkPlanDeployOperation,
} from './workPlanModel'

export type WorkPlanDeployRunnable = 'deploy' | 'add' | 'cleanup'

/** The one line under the buttons: a refusal, a blocker, or how the last deploy went. Keys under gui.inspector.workPlan. */
export interface WorkPlanDeployLine {
  readonly kind: 'failure' | 'blocked' | 'outcome'
  readonly key: string
  readonly params?: Readonly<Record<string, string | number>>
  readonly tone: 'muted' | 'warning' | 'danger'
  /** failure: what was refused (code, stale baseline, blockers). */
  readonly failure?: WorkPlanDeployFailure
}

export interface WorkPlanCleanupCounts {
  readonly remove: number
  readonly keep: number
}

export interface WorkPlanDeployState {
  /** The status read of the target's plan, or null (none yet, or it failed). */
  readonly status: ComputedRef<WorkPlanDeployStatus | null>
  readonly actions: ComputedRef<Readonly<Record<WorkPlanDeployOperation, WorkPlanDeployItem>>>
  readonly busy: Readonly<Ref<boolean>>
  readonly line: Readonly<ShallowRef<WorkPlanDeployLine | null>>
  /** A cleanup preview waiting for its confirmation, or null. */
  readonly pendingCleanup: Readonly<ShallowRef<WorkPlanDeployPreview | null>>
  readonly cleanupCounts: ComputedRef<WorkPlanCleanupCounts | null>
  run(operation: WorkPlanDeployRunnable): Promise<void>
  confirmCleanup(): Promise<void>
  cancelCleanup(): void
}

export function useWorkPlanDeploy(target: () => InspectorTarget | null): WorkPlanDeployState {
  const docId = () => target()?.docId ?? null
  const read = useInspectorResource<WorkPlanDeployStatus>({
    target,
    read: workPlanDeployStatus,
    events: WORK_PLAN_EVENTS,
    matches: workPlanEventMatches,
  })

  const busy = ref(false)
  const line = shallowRef<WorkPlanDeployLine | null>(null)
  const pendingCleanup = shallowRef<WorkPlanDeployPreview | null>(null)
  // Request order: an answer counts only for the deploy (and the card) it was sent for.
  let generation = 0

  const status = computed(() => {
    const data = read.data.value
    const current = docId()
    return data && current && data.docId === current ? data : null
  })

  const actions = computed(() =>
    workPlanDeployActions({ target: target(), status: status.value, statusFailed: read.status.value === 'error', busy: busy.value }),
  )

  const cleanupCounts = computed<WorkPlanCleanupCounts | null>(() => {
    const preview = pendingCleanup.value
    if (!preview) return null
    return {
      remove: preview.cards.filter((card) => card.result === 'remove').length,
      keep: preview.cards.filter((card) => card.result === 'kept_orphan').length,
    }
  })

  function fail(error: unknown): void {
    const failure = workPlanDeployFailureOf(error)
    line.value = { kind: 'failure', key: workPlanDeployFailureKey(failure.kind), tone: 'danger', failure }
    // The status is read again (the counts or the lock may have moved); the deploy itself is not sent again.
    void read.reload()
  }

  async function execute(id: string, preview: WorkPlanDeployPreview, seq: number): Promise<void> {
    const result = await workPlanDeployExecute(id, preview)
    if (seq !== generation) return
    line.value = result.noChange
      ? { kind: 'outcome', key: 'outcome.no_change', tone: 'muted' }
      : {
          kind: 'outcome',
          key: 'outcome.deployed',
          tone: 'muted',
          params: { created: result.createdNodeKeys.length, removed: result.removedNodeKeys.length },
        }
    // The Canvas follows the draft's own change event; the counts are read here.
    void read.reload()
  }

  async function run(operation: WorkPlanDeployRunnable): Promise<void> {
    const id = docId()
    const current = status.value
    if (busy.value || !actions.value[operation].enabled || id === null || current === null) return
    const mode = WORK_PLAN_DEPLOY_MODE_OF[operation]
    const seq = ++generation
    busy.value = true
    line.value = null
    pendingCleanup.value = null
    try {
      const preview = await workPlanDeployPreview(id, mode, current.wpRevisionNo)
      if (seq !== generation) return
      if (preview.blockers.length > 0) {
        line.value = { kind: 'blocked', key: workPlanDeployBlockerKey(preview.blockers[0].code), tone: 'warning' }
        return
      }
      if (preview.noChange || !preview.executable) {
        line.value = { kind: 'outcome', key: 'outcome.no_change', tone: 'muted' }
        return
      }
      if (mode === 'cleanup') {
        pendingCleanup.value = preview
        return
      }
      await execute(id, preview, seq)
    } catch (error) {
      if (seq === generation) fail(error)
    } finally {
      if (seq === generation) busy.value = false
    }
  }

  /** Runs the cleanup the dialog shows: that preview's baseline and remove list, nothing read anew. */
  async function confirmCleanup(): Promise<void> {
    const preview = pendingCleanup.value
    const id = docId()
    if (busy.value || preview === null || id === null) return
    const seq = ++generation
    busy.value = true
    try {
      await execute(id, preview, seq)
    } catch (error) {
      if (seq === generation) fail(error)
    } finally {
      if (seq === generation) {
        busy.value = false
        pendingCleanup.value = null
      }
    }
  }

  function cancelCleanup(): void {
    if (!busy.value) pendingCleanup.value = null
  }

  // Another card: its deploy starts clean; a late answer of the previous one is dropped.
  function drop(): void {
    generation += 1
    busy.value = false
    line.value = null
    pendingCleanup.value = null
  }
  watch(docId, drop)
  // The header unmounted: no answer of its deploy may run anything after it.
  onScopeDispose(drop)

  return { status, actions, busy, line, pendingCleanup, cleanupCounts, run, confirmCleanup, cancelCleanup }
}
