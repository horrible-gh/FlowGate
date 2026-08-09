export interface WorkPlanWarning {
  code: string
  severity: 'info' | 'warning'
  count: number
  keys: string[]
  item_seqs: number[]
  message?: string
}

/** P0009 §7.7 — client-side numeric item_seq keys. */
export interface WorkPlanFillPreset {
  sourceDocId: string
  sourceRevisionNo: number
  instructionMode: 'auto_approved' | 'ai_direct'
  targetSeq: number
  providerOverrides: Record<number, string>
  messageOverrides: Record<number, string>
  defaultMessage: string
  filledSeqs: number[]
  warnings: WorkPlanWarning[]
}
