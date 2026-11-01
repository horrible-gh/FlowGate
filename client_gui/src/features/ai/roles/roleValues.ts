import { AI_ROLES, type MyAiRoles } from './types'
import { toAiRoleValue } from '@/features/document'
export { toAiRoleValue, aiRolePayload, toCardAiRoles } from '@/features/document'
type RoleRaw = Record<string, any>
export function toMyAiRoles(raw: RoleRaw): MyAiRoles {
  return {
    setupCompleted: raw.setup_completed === true,
    selectableCount: raw.selectable_count,
    roles: Object.fromEntries(AI_ROLES.map(role => {
      const item = raw.roles?.[role] ?? {}
      return [role, { state: item.state, value: toAiRoleValue(item.value), displayName: item.display_name ?? null, cause: item.cause ?? null }]
    })) as MyAiRoles['roles'],
    options: (raw.options ?? []).map((item: RoleRaw) => ({ id: item.id, name: item.name })),
  }
}
