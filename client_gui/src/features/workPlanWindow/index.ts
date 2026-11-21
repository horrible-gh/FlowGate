// public surface of the v0.2 work plan edit window: one in-tab modeless window per work plan
// document (openWorkPlanEditWindow; the WP card connects to it in the earlier implementation), mounted once through DocumentWindows, and the
// Step window boundary the Step window feature registers with (the earlier implementation: setWorkPlanStepEditHandler — a row target and a
// session on the parent's editor).
export { default as WorkPlanEditWindows } from './WorkPlanEditWindows.vue'
export * from './workPlanEditWindows'
export type {
  WorkPlanEditor,
  WorkPlanEditorNotice,
  WorkPlanEditorPhase,
  WorkPlanStepApplyStatus,
  WorkPlanStepEditSession,
  WorkPlanStepEditTarget,
} from './useWorkPlanEditor'
export type { WorkPlanLockReason, WorkPlanPairRole, WorkPlanStepDraft } from './workPlanEditModel'
