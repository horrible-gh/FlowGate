// flowgate.v02.0048 T#13 — the WP summary's state (D#1 §7.2 / §7.3, §8, §10.2 T#13 row). The WP read view of
// the selected plan (revision, totals, Steps with their started cards, provider assignment) is read through
// useInspectorResource and read again when the plan changes on the server — a save, a restore or an AI fill
// of the plan sends document_explorer_refresh / group_view_refresh for the WP (`documents/routers/
// work_plan.py` _emit), a review sends doc_review_status_changed — when a document of the same Flow is added
// or updated (a card of the plan may have started), and on a reconnect. Nothing here changes the server:
// the plan opens in its document window by ↗ 열기 (openDocument).
import { computed, type ComputedRef, type Ref } from 'vue'
import { workPlanErrorOf, workPlanRead, type WorkPlanView } from '@/features/document'
import { openDocument } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorResource, type InspectorEventMatch, type InspectorResourceStatus } from '../../useInspectorResource'
import {
  workPlanAvailability,
  workPlanCards,
  workPlanEditKey,
  workPlanErrorKey,
  workPlanProgress,
  type WorkPlanAvailability,
  type WorkPlanCardRow,
  type WorkPlanProgress,
} from './workPlanModel'

/** Server events after which the plan is read again. */
export const WORK_PLAN_EVENTS = ['document_explorer_refresh', 'group_view_refresh', 'doc_review_status_changed'] as const

/** Flow-level refresh events: a document of the Flow was added or updated, so a card may have started. */
const FLOW_EVENTS: ReadonlySet<string> = new Set(['document_explorer_refresh', 'group_view_refresh'])

/** A frame of the plan itself, or a document refresh anywhere in the plan's Flow. */
export const workPlanEventMatches: InspectorEventMatch = (frame, scope) =>
  (scope.docId !== null && frame.docId === scope.docId) || (FLOW_EVENTS.has(frame.type) && frame.groupId === scope.flowId)

export interface WorkPlanState {
  /** The WP read view of the target document, or null before it is read. */
  readonly view: ComputedRef<WorkPlanView | null>
  readonly cards: ComputedRef<readonly WorkPlanCardRow[]>
  readonly progress: ComputedRef<WorkPlanProgress>
  /** Message key of where the plan is edited, or null before it is read. */
  readonly editKey: ComputedRef<string | null>
  readonly availability: ComputedRef<WorkPlanAvailability>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  readonly stale: Readonly<Ref<boolean>>
  /** Message key of the failed read, or null. */
  readonly errorKey: ComputedRef<string | null>
  /** The revision an unreadable plan answered with, or null. */
  readonly unreadableRevision: ComputedRef<number | null>
  /** Opens the plan in its document window in this tab. */
  open(): void
  reload(): Promise<void>
}

export function useWorkPlan(target: () => InspectorTarget | null): WorkPlanState {
  const docId = () => target()?.docId ?? null
  const read = useInspectorResource<WorkPlanView>({
    target,
    read: workPlanRead,
    events: WORK_PLAN_EVENTS,
    matches: workPlanEventMatches,
  })

  const view = computed(() => {
    const data = read.data.value
    const current = docId()
    return data && current && data.docId === current ? data : null
  })

  const error = computed(() => (read.status.value === 'error' ? workPlanErrorOf(read.error.value) : null))

  return {
    view,
    cards: computed(() => workPlanCards(view.value)),
    progress: computed(() => workPlanProgress(view.value)),
    editKey: computed(() => workPlanEditKey(view.value)),
    availability: computed(() => workPlanAvailability({ readOnly: target()?.readOnly ?? null, hasDocument: docId() !== null })),
    status: read.status,
    stale: read.stale,
    errorKey: computed(() => (error.value ? workPlanErrorKey(error.value.kind) : null)),
    unreadableRevision: computed(() => error.value?.revisionNo ?? null),
    open: () => {
      const id = docId()
      if (id !== null) openDocument(id)
    },
    reload: read.reload,
  }
}
