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
// Review-count select keys: 'inherit' (type row) / 'unset' (common row) stand for a null value, `count:n` for a number.
export function reviewCountKey(value: number | null, nullKey: 'inherit' | 'unset'): string {
  return value === null ? nullKey : `count:${value}`
}
export function reviewCountValue(key: string): number | null {
  if (key === 'inherit' || key === 'unset') return null
  const count = key.startsWith('count:') ? Number(key.slice(6)) : NaN
  if (!Number.isInteger(count)) throw new Error('Invalid review count')
  return count
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
