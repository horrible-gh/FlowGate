import type { RoleProfileValue } from '../types'

export const PROFILE_ROLES = ['instruction', 'execution', 'review'] as const

// A prefix keeps provider ids distinct from the reserved kinds.
export function profileValueKey(value: RoleProfileValue): string {
  return value.kind === 'assigned' ? `provider:${value.provider_id}` : value.kind
}
export function profileValue(key: string): RoleProfileValue {
  if (key.startsWith('provider:')) return { kind: 'assigned', provider_id: key.slice(9) }
  if (key === 'inherit' || key === 'unset' || key === 'not_used') return { kind: key }
  throw new Error('Invalid role profile value')
}
// Shared across component instances: leaving and returning cannot overlap writes to a group.
const requests = new Map<string, Promise<unknown>>()
export function inProfileQueue<T>(groupId: string, action: () => Promise<T>): Promise<T> {
  const previous = requests.get(groupId) ?? Promise.resolve()
  const next = previous.catch(() => undefined).then(action)
  requests.set(groupId, next)
  const clear = () => { if (requests.get(groupId) === next) requests.delete(groupId) }
  void next.then(clear, clear)
  return next
}
