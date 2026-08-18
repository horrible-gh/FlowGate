export interface WorkPlanWarning {
  code: string
  severity: 'info' | 'warning'
  count: number
  keys: string[]
  item_seqs: number[]
  message?: string
}

/** The N/T authoring party for a continuous run. 0406 T0022 task 2: an entry point that
 *  didn't pass this value used to be silently folded to auto_approved on the server — that
 *  was half of this defect — so it's made non-optional in the type, catching an omission
 *  at type-check time instead. */
export type ContinuationInstructionMode = 'auto_approved' | 'ai_direct'

/** Default for a new continuous run. The screen, presets, and resets all read this single
 *  value.
 *  0409 B0001 rejection: 0406 T0022 task 1 flipped this value to ai_direct, but the user
 *  instructed reverting it, saying "원래 [자동승인] 이 기본 선택이였는데 왜 지시서 작성으로
 *  선택되어있는거야" [it used to default to [Auto-approve] — why is instruction-authoring
 *  selected now?], so it was restored to auto_approved. ai_direct is used only when the
 *  user explicitly picks it via radio button. */
export const DEFAULT_INSTRUCTION_MODE: ContinuationInstructionMode = 'auto_approved'

/** P0009 §7.7 — client-side numeric item_seq keys. */
export interface WorkPlanFillPreset {
  sourceDocId: string
  sourceRevisionNo: number
  instructionMode: ContinuationInstructionMode
  targetSeq: number
  providerOverrides: Record<number, string>
  messageOverrides: Record<number, string>
  defaultMessage: string
  filledSeqs: number[]
  warnings: WorkPlanWarning[]
}
