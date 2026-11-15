import { useCurrentUser } from '@/core/auth/currentUser'
import { remapDraft, type DraftModel } from './draftModel'

export function pendingKey(projectId: string, flowId: string, kind: string): string | undefined {
  const user = useCurrentUser().user.value?.username
  return user ? `flowgate.pending.${kind}:${JSON.stringify([user, projectId, flowId])}` : undefined
}
export function readPending<T>(key?: string): T | null {
  try { return key ? JSON.parse(globalThis.localStorage.getItem(key) ?? 'null') as T | null : null } catch { return null }
}
export function writePending(key: string | undefined, value: unknown): void {
  if (!key) return
  try {
    if (value === null) globalThis.localStorage.removeItem(key)
    else globalThis.localStorage.setItem(key, JSON.stringify(value))
  } catch { /* Keep the in-memory edit even when browser storage is unavailable. */ }
}
export interface PendingDraft { model: DraftModel; base: DraftModel; version: number }

/** Replay only local changes on the latest draft. Unrelated remote cards are retained. */
export function rebaseDraft(base: DraftModel, local: DraftModel, remote: DraftModel): { model: DraftModel; keys: Record<string, string> } {
  const keys: Record<string, string> = {}
  const remoteKeys = new Set([...remote.nodes, ...remote.edges].map(item => item.key))
  for (const [items, prefix] of [[local.nodes, 'gn_'], [local.edges, 'ge_']] as const) {
    for (const item of items) {
      if (/^tmp_[0-9a-f]{32}$/.test(item.key)) {
        const key = prefix + item.key.slice(4)
        if (remoteKeys.has(key)) keys[item.key] = key
      }
    }
  }
  const before = remapDraft(base, keys), ours = remapDraft(local, keys)
  function merge<T extends { key: string }>(old: readonly T[], changed: readonly T[], theirs: readonly T[]): T[] {
    const oldMap = new Map(old.map(item => [item.key, item]))
    const ourMap = new Map(changed.map(item => [item.key, item]))
    const result = new Map(theirs.filter(item => !oldMap.has(item.key) || ourMap.has(item.key)).map(item => [item.key, item]))
    for (const item of changed) {
      if (!oldMap.has(item.key) || JSON.stringify(item) !== JSON.stringify(oldMap.get(item.key))) result.set(item.key, item)
    }
    return [...result.values()]
  }
  const nodes = merge(before.nodes, ours.nodes, remote.nodes)
  const nodeKeys = new Set(nodes.map(node => node.key))
  const edges = merge(before.edges, ours.edges, remote.edges).filter(edge => nodeKeys.has(edge.source) && nodeKeys.has(edge.target))
  return { model: { nodes, edges }, keys }
}
