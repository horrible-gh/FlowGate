// flowgate.v02.0048 T#11 — the test section's state (D#1 §7.2 / §7.3, §8, §10.2 T#11 row). The test view of
// the selected TS / TSR (runs, live run, Case tally, result summary) is read through useInspectorResource
// and read again on the server's test run events — started, phase, Case / stage finished, finished — of
// the TS the runs belong to (a TSR's runs are its TS's: the frames carry the TS doc_id), on a review state
// change of either document and on a reconnect. Starting and cancelling call the existing v0.1 API, then
// read the view and the guard again — success or refusal (D#1 §7.3, §8.1). Whether they are possible also
// reads the Flow's AI activity and the group's disposal (`group_disposed` of the document read). There is
// no permission list on the client (D#1 §8.2): a 403 of the server disables both with its reason for this
// document until another document is selected. ↗ 열기 opens the document in its window (openDocument).
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import {
  reviewActivityRead,
  reviewDocumentRead,
  testSpecErrorOf,
  testSpecRead,
  testSpecRunCancel,
  testSpecRunStart,
  type TestSpecView,
} from '@/features/document'
import { openDocument } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import {
  useInspectorResource,
  type InspectorEventMatch,
  type InspectorEventScope,
  type InspectorResource,
  type InspectorResourceStatus,
} from '../../useInspectorResource'
import {
  testSpecAvailability,
  testSpecCounts,
  testSpecDeniedBy,
  testSpecErrorKey,
  testSpecFailureKey,
  testSpecForm,
  testSpecHistory,
  testSpecLiveRun,
  testSpecRunState,
  testSpecTally,
  testSpecTargetDocId,
  type TestSpecAvailability,
  type TestSpecCountsFacts,
  type TestSpecForm,
  type TestSpecHistoryRow,
  type TestSpecRunState,
  type TestSpecTally,
} from './testSpecModel'

/** Server events after which the test view is read again (`services/test_run_service.py`, review state). */
export const TEST_SPEC_EVENTS = [
  'test_run_started',
  'test_run_phase',
  'test_case_finished',
  'test_stage_finished',
  'test_run_finished',
  'doc_review_status_changed',
  'document_explorer_refresh',
  'group_view_refresh',
] as const

/** Server events after which the change guard (AI activity, disposal) is read again. */
export const TEST_SPEC_GUARD_EVENTS = ['ai_invoke_started', 'ai_invoke_finished', 'document_explorer_refresh'] as const

/**
 * A frame about the view: the target document, the TS its runs belong to or the report the TS feeds
 * (known once the view is read), or a Flow-level frame (no doc_id) of the target Flow.
 */
export function testSpecEventMatches(view: () => Pick<TestSpecView, 'docId' | 'tsDocId' | 'tsrDocId'> | null): InspectorEventMatch {
  return (frame, scope) => {
    if (frame.docId === null) return frame.groupId === scope.flowId
    if (scope.docId !== null && frame.docId === scope.docId) return true
    const read = view()
    return !!read && read.docId === scope.docId && (frame.docId === read.tsDocId || frame.docId === read.tsrDocId)
  }
}

/** A guard event of any document of the target Flow, or of the target document itself. */
export const testSpecGuardMatches: InspectorEventMatch = (frame, scope) =>
  frame.groupId === scope.flowId || (scope.docId !== null && frame.docId === scope.docId)

function scopeOf(target: InspectorTarget | null): InspectorEventScope | null {
  return target ? { projectId: target.projectId, flowId: target.flowId, docId: testSpecTargetDocId(target) } : null
}

interface TestSpecGuard {
  readonly aiRunning: boolean
  readonly groupDisposed: boolean
}

export interface TestSpecFailure {
  readonly operation: 'run' | 'cancel'
  readonly key: string
}

export interface TestSpecState {
  /** The test view of the target document, or null before it is read. */
  readonly view: ComputedRef<TestSpecView | null>
  readonly form: ComputedRef<TestSpecForm>
  readonly tally: ComputedRef<TestSpecTally>
  readonly runState: ComputedRef<TestSpecRunState>
  readonly counts: ComputedRef<TestSpecCountsFacts | null>
  readonly history: ComputedRef<readonly TestSpecHistoryRow[]>
  readonly availability: ComputedRef<TestSpecAvailability>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  readonly stale: Readonly<Ref<boolean>>
  /** Message key of the failed read, or null. */
  readonly errorKey: ComputedRef<string | null>
  /** The start / cancel waiting for the server or for the read after it, or null. */
  readonly busy: Readonly<Ref<'run' | 'cancel' | null>>
  /** The last refused start / cancel on this document, or null. */
  readonly failure: Readonly<Ref<TestSpecFailure | null>>
  /** Starts a run of every automated Case of the TS; nothing is sent when starting is disabled. */
  run(): Promise<boolean>
  /** Cancels the live run; nothing is sent when cancelling is disabled. */
  cancel(): Promise<boolean>
  /** Opens a document (the target, its TS or its report) in its document window. */
  open(docId: string): void
  reload(): Promise<void>
}

export function useTestSpec(target: () => InspectorTarget | null): TestSpecState {
  const scope = () => scopeOf(target())
  const docId = () => scope()?.docId ?? null
  // The matcher reads the view this resource holds, so a TSR also hears its TS's run frames.
  let resource: InspectorResource<TestSpecView> | null = null
  const resourceView = () => resource?.data.value ?? null
  resource = useInspectorResource<TestSpecView>({
    target: scope,
    read: testSpecRead,
    events: TEST_SPEC_EVENTS,
    matches: testSpecEventMatches(resourceView),
  })
  const read = resource
  const guard = useInspectorResource<TestSpecGuard>({
    target: scope,
    read: async (id) => {
      const flowId = target()?.flowId
      if (!flowId) throw new Error('no target Flow')
      const [activity, document] = await Promise.all([reviewActivityRead(flowId), reviewDocumentRead(id)])
      return { aiRunning: activity.active, groupDisposed: document.groupDisposed }
    },
    events: TEST_SPEC_GUARD_EVENTS,
    matches: testSpecGuardMatches,
  })

  const busy = ref<'run' | 'cancel' | null>(null)
  const failure = ref<TestSpecFailure | null>(null)
  const denied = ref(false)

  // A refusal and what it blocks belong to the document they were refused on.
  watch(docId, () => {
    failure.value = null
    denied.value = false
  })

  const view = computed(() => {
    const data = read.data.value
    const current = docId()
    return data && current && data.docId === current ? data : null
  })

  const availability = computed(() =>
    testSpecAvailability({
      readOnly: target()?.readOnly ?? null,
      // An unanswered guard read does not block: the server refuses on the same facts.
      groupDisposed: guard.data.value?.groupDisposed === true,
      aiRunning: guard.data.value?.aiRunning === true,
      view: view.value,
      denied: denied.value,
      busy: busy.value !== null,
    }),
  )

  const errorKey = computed(() => (read.status.value === 'error' ? testSpecErrorKey(testSpecErrorOf(read.error.value).kind) : null))

  /** Runs one call; success or refusal, the view and the guard are read again before it ends. */
  async function change(operation: 'run' | 'cancel', call: () => Promise<unknown>): Promise<boolean> {
    const id = docId()
    if (id === null) return false
    busy.value = operation
    failure.value = null
    let ok = false
    try {
      try {
        await call()
        ok = true
      } catch (error) {
        if (docId() === id) {
          const kind = testSpecErrorOf(error).kind
          failure.value = { operation, key: testSpecFailureKey(operation, kind) }
          if (testSpecDeniedBy(kind)) denied.value = true
        }
      }
      // The buttons stay closed until the read is back, so the old view cannot send the same call twice.
      if (docId() === id) await Promise.all([read.reload(), guard.reload()])
    } finally {
      busy.value = null
    }
    return ok
  }

  async function run(): Promise<boolean> {
    const tsDocId = view.value?.tsDocId ?? null
    if (tsDocId === null || !availability.value.run.visible || !availability.value.run.enabled) return false
    return change('run', () => testSpecRunStart(tsDocId))
  }

  async function cancel(): Promise<boolean> {
    const runId = testSpecLiveRun(view.value)?.runId ?? null
    if (runId === null || !availability.value.cancel.visible || !availability.value.cancel.enabled) return false
    return change('cancel', () => testSpecRunCancel(runId))
  }

  return {
    view,
    form: computed(() => testSpecForm(view.value)),
    tally: computed(() => testSpecTally(view.value)),
    runState: computed(() => testSpecRunState(view.value)),
    counts: computed(() => testSpecCounts(view.value)),
    history: computed(() => testSpecHistory(view.value)),
    availability,
    status: read.status,
    stale: read.stale,
    errorKey,
    busy,
    failure,
    run,
    cancel,
    open: (id) => openDocument(id),
    reload: async () => {
      await Promise.all([read.reload(), guard.reload()])
    },
  }
}
