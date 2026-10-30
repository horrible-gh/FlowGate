import type { AiRole, AiRoleValue, AiRoleOption } from '@/features/document'
export { AI_ROLES } from '@/features/document'
export type { AiRole, AiRoleValue, AiRoleOption, CardAiRole, CardAiRoles } from '@/features/document'
export interface AiAccountRole {
  state: string
  value: AiRoleValue | null
  displayName: string | null
  cause: string | null
}
export interface MyAiRoles {
  setupCompleted: boolean
  selectableCount: number
  roles: Record<AiRole, AiAccountRole>
  options: AiRoleOption[]
}
export type AiRoleValues = Record<AiRole, AiRoleValue>
