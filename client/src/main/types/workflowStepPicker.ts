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
  // 0399 T0018: the mention already saved on this sequence row (GET /workflow/sequence), and
  // which document it followed a plan in from — undefined/empty for a person-typed row.
  note?: string
  source_doc_id?: string | null
  source_revision_no?: number | null
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
  /**
   * The loaded sequence steps (empty while loading / pre-decision). Optional so existing
   * consumers that build this state as a literal need no change; the continuous dialog reads
   * it to list the distinct doc types for per-document-type provider assignment (0317 D0004).
   */
  steps?: WorkflowStepItem[]
}
