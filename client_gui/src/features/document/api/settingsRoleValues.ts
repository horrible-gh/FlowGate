export const AI_ROLES = ['instruction', 'execution', 'review'] as const
export type AiRole = (typeof AI_ROLES)[number]
export type AiRoleValue = { kind: 'assigned'; providerId: string } | { kind: 'not_used' }
export interface AiRoleOption { id: string; name: string }
export interface CardAiRole {
  state: string
  outcome: string
  source: string
  legacyFlag: boolean
  providerId: string | null
  displayName: string | null
  cause: string | null
  selected: AiRoleValue | null
}
export interface CardAiRoles {
  cardInitState: string | null
  hasLegacyRoles: boolean
  roles: Partial<Record<AiRole, CardAiRole>>
  preserved: Readonly<Record<string, unknown>>
  optionsState: string
  options: AiRoleOption[]
}

type RoleRaw = Record<string, any>
export function toAiRoleValue(raw: RoleRaw | null | undefined): AiRoleValue | null {
  if (raw?.kind === 'not_used') return { kind: 'not_used' }
  return raw?.kind === 'assigned' && typeof raw.provider_id === 'string'
    ? { kind: 'assigned', providerId: raw.provider_id } : null
}
export function aiRolePayload(value: AiRoleValue): Record<string, unknown> {
  return value.kind === 'assigned' ? { kind: 'assigned', provider_id: value.providerId } : { kind: 'not_used' }
}
export function toCardAiRoles(raw: RoleRaw): CardAiRoles {
  return {
    cardInitState: raw.card_init_state ?? null,
    hasLegacyRoles: raw.has_legacy_roles === true,
    roles: Object.fromEntries(AI_ROLES.filter(role => raw.roles?.[role]).map(role => {
      const item = raw.roles[role]
      return [role, { state: item.state, outcome: item.outcome, source: item.source, legacyFlag: item.legacy_flag === true,
        providerId: item.provider_id ?? null, displayName: item.display_name ?? null, cause: item.cause ?? null,
        selected: toAiRoleValue(item.selected) }]
    })),
    preserved: raw.preserved ?? {},
    optionsState: raw.options_state,
    options: (raw.options ?? []).map((item: RoleRaw) => ({ id: item.id, name: item.name })),
  }
}
