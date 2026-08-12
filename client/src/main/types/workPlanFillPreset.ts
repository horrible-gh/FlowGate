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

/** 새 연속 실행의 기본값. 화면·프리셋·재설정이 모두 이 한 값을 읽는다.
 *  0409 B0001 반려: 0406 T0022 작업 1 이 이 값을 ai_direct 로 뒤집었으나 사용자가
 *  "원래 [자동승인] 이 기본 선택이였는데 왜 지시서 작성으로 선택되어있는거야" 로 되돌리라고
 *  지시해 auto_approved 로 복구했다. ai_direct 는 사용자가 라디오로 직접 고를 때만 쓰인다. */
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
