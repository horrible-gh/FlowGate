import type { CardAiRoles, AiRole, AiRoleValue } from '@/features/document'
const AI_ROLES = ['instruction', 'execution', 'review'] as const
export function cardRoleRows(roles: CardAiRoles | null | undefined, instruction: unknown, pending: Readonly<Record<string, unknown>> = {}) {
  return AI_ROLES.flatMap(role => {
    const item = roles?.roles[role]
    if (!item || (role === 'instruction' && instruction === 'omit')) return []
    const field = `${role}_role` as `${AiRole}_role`
    return [{ role, field, ...item, selected: field in pending ? pending[field] as AiRoleValue : item.selected }]
  })
}
export const SETTINGS_FIELDS = { run: ['instruction'], test: ['instruction', 'result_cause', 'follow_scope'], plan: ['pre_instruction_auto'] } as const
export const AI_FIELDS = { run: ['review_count', 'review_basis', 'instruction_review_ai', 'instruction_review_count', 'time_limit', 'restart'], test: ['review_count'], plan: ['review_count', 'review_basis', 'time_limit', 'restart'] } as const
