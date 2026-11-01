import type { CardAiRoles, AiRole, AiRoleValue } from '@/features/document'
const AI_ROLES = ['instruction', 'execution', 'review'] as const
/**
 * The AI tab's role rows, in 지시 · 실행 · 검수 order, for the roles the server gives the card (a WP card has no 지시 AI).
 * flowgate.v02.0113 T#4 (D 개정 §6.3): a card that omits its instruction keeps its 지시 AI row, `held` (off, its value kept).
 */
export function cardRoleRows(roles: CardAiRoles | null | undefined, instruction: unknown, pending: Readonly<Record<string, unknown>> = {}) {
  return AI_ROLES.flatMap(role => {
    const item = roles?.roles[role]
    if (!item) return []
    const field = `${role}_role` as `${AiRole}_role`
    return [{ role, field, ...item, held: role === 'instruction' && instruction === 'omit', selected: field in pending ? pending[field] as AiRoleValue : item.selected }]
  })
}
export const SETTINGS_FIELDS = { run: ['instruction'], test: ['instruction', 'result_cause', 'follow_scope'], plan: ['pre_instruction_auto'] } as const
export const AI_FIELDS = { run: ['review_count', 'review_basis', 'instruction_review_ai', 'instruction_review_count', 'time_limit', 'restart'], test: ['review_count'], plan: ['review_count', 'review_basis', 'time_limit', 'restart'] } as const
/** flowgate.v02.0113 T#4: the AI tab's [고급 ▾] — 실행 설정 (재시작 · 실행 제한 시간 · 검수 기준), then 지시서 검수. */
export const AI_ADVANCED_FIELDS = { run: ['restart', 'time_limit', 'review_basis'], instructionReview: ['instruction_review_ai', 'instruction_review_count'] } as const
/** flowgate.v02.0113 T#4 (0002-D §3.7, 0029-TR 시안): the AI tab's review counts are 안 함 · 1회 · 2회 · 3회. */
export const AI_REVIEW_COUNTS: readonly number[] = [0, 1, 2, 3]
