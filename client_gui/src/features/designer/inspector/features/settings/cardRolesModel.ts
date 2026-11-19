import type { CardAiRoles, AiRole, AiRoleValue } from '@/features/document'
import { REVIEW_COUNT_HIDDEN } from './reviewCountModel'
const AI_ROLES = ['instruction', 'execution', 'review'] as const
/**
 * The AI tab's role rows, in 지시 · 실행 · 검수 order, for the roles the server gives the card (a WP card has no 지시 AI).
 * flowgate.v02.0113 T#4 (D 개정 §6.3): a card that omits its instruction keeps its 지시 AI row, `held` (off, its value kept).
 * flowgate.v02gui.0032 (decision 7): a card whose 지시서 already has a body never calls its 지시 AI (the written
 * instruction is reused at run time), so the row shows 사용하지 않음, `held` and `written`; the stored value is not
 * changed and comes back once the 지시서 is empty again.
 */
export function cardRoleRows(roles: CardAiRoles | null | undefined, instruction: unknown, pending: Readonly<Record<string, unknown>> = {}, instructionWritten = false) {
  return AI_ROLES.flatMap(role => {
    const item = roles?.roles[role]
    if (!item) return []
    const field = `${role}_role` as `${AiRole}_role`
    const written = role === 'instruction' && instructionWritten
    const selected: AiRoleValue | null = written ? { kind: 'not_used' } : field in pending ? pending[field] as AiRoleValue : item.selected
    return [{ role, field, ...item, held: (role === 'instruction' && instruction === 'omit') || written, written, selected }]
  })
}

/** flowgate.v02gui.0032: whether the card's 지시서 line has a written body (the Canvas line's `has_body`). */
export function instructionWritten(lines: readonly { readonly slot: string; readonly docId: string | null; readonly hasBody?: boolean }[] | null | undefined): boolean {
  return !!lines?.some(line => line.slot === 'instruction' && line.docId !== null && line.hasBody === true)
}
export const SETTINGS_FIELDS = { run: ['instruction'], test: ['instruction', 'result_cause', 'follow_scope'], plan: ['pre_instruction_auto'] } as const
export const AI_FIELDS = { run: ['review_count', 'review_basis', 'after_review_exhausted', 'instruction_review_ai', 'instruction_review_count', 'time_limit', 'restart', 'verify_max_runs', 'verify_run_minutes'], test: ['review_count'], plan: ['review_count', 'review_basis', 'after_review_exhausted', 'time_limit', 'restart'] } as const
/** flowgate.v02.0113 T#4: the AI tab's [고급 ▾] — 실행 설정 (재시작 · 실행 제한 시간 · 검수 기준), then 지시서 검수. */
export const AI_ADVANCED_FIELDS = { run: ['restart', 'time_limit', 'verify_max_runs', 'verify_run_minutes', 'review_basis', 'after_review_exhausted'], instructionReview: ['instruction_review_ai', 'instruction_review_count'] } as const
/**
 * flowgate.v02gui.0029 (0003-CH C안): the AI tab's ⚙ of each AI row opens that AI's own advanced settings —
 * 지시 AI: 지시서 검수 AI · 지시서 검수 횟수, 실행 AI: 재시작 · 실행 제한 시간 · 검증 최대 횟수 · 검증 1회 최대 시간 (flowgate.v02test.0053
 * 0003-T: the two 검증 options, a work card only), 검수 AI: 검수 기준 · 검수 횟수 소진 시.
 */
export const AI_GEAR_FIELDS = { instruction: ['instruction_review_ai', 'instruction_review_count'], execution: ['restart', 'time_limit', 'verify_max_runs', 'verify_run_minutes'], review: ['review_basis', 'after_review_exhausted'] } as const
/**
 * flowgate.v02.0113 T#4 (0002-D §3.7, 0029-TR 시안) offered 안 함 · 1회 · 2회 · 3회 only. flowgate.v02gui.0007 B: the AI
 * tab's review counts are the server's (0 · 1 … the effective maximum, ai_repeat_count_max), so a maximum of 5 offers
 * 4회 and 5회; only "until it passes" (-1) stays off the tab, as in the 시안.
 */
export const AI_REVIEW_COUNT_HIDDEN: readonly number[] = REVIEW_COUNT_HIDDEN
