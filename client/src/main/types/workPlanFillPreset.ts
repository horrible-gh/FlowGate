export interface WorkPlanWarning {
  code: string
  severity: 'info' | 'warning'
  count: number
  keys: string[]
  item_seqs: number[]
  message?: string
}

/** 연속 실행의 N/T 작성 주체. 0406 T0022 작업 2: 이 값을 넘기지 않은 진입점이
 *  서버에서 조용히 auto_approved 로 접히던 것이 이번 결함의 절반이라, 타입에서
 *  선택 사항이 아니게 만들어 빠뜨리면 타입 검사에서 잡히게 한다. */
export type ContinuationInstructionMode = 'auto_approved' | 'ai_direct'

/** 0406 T0022 작업 1: 새 연속 실행의 기본값. 화면·프리셋·재설정이 모두 이 한 값을 읽는다. */
export const DEFAULT_INSTRUCTION_MODE: ContinuationInstructionMode = 'ai_direct'

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
