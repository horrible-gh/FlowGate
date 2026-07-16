// Shared contract for WorkflowStepPicker.vue (0242 NR0003 권고 1).
//
// The picker owns "how far should this continuous run go?" for BOTH continuous-run entry
// points (the action-bar ContinuousWorkDialog and the AI-invoke dialog), so the payload it
// reports lives here rather than being re-declared per consumer.

export interface WorkflowStepItem {
  id: number
  item_seq: number
  type: string
  label: string
  status: string // pending | in_progress | done
}

/** A runnable target the user picked. Absent (null) whenever nothing is runnable. */
export interface WorkflowStepSelection {
  /** item_seq of the stop point, or -1 (run-to-end sentinel) for a pre-decision run. */
  targetSeq: number
  targetType: string
  targetLabel: string
  /** Steps from the head through the target, inclusive. 0 for a pre-decision run. */
  stepCount: number
  /** True ⇒ the workflow is not decided yet; the run starts FROM the decision step. */
  fromDecision: boolean
}

export interface WorkflowStepPickerState {
  loading: boolean
  /** i18n key of the load error, or null. */
  errorKey: string | null
  /** Every step is already done — nothing left to run (the list stays visible, read-only). */
  allDone: boolean
  fromDecision: boolean
  /** null unless a target is actually runnable — consumers gate their confirm button on this. */
  selection: WorkflowStepSelection | null
}
