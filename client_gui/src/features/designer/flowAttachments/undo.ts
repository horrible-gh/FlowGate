// Delete at once, undo for five seconds (final mockup). One 🗑 press — a row or the selection — is one group;
// no request is sent until the group's five seconds run out. A row's ↶ takes back that row only, the
// numbered ↶ and Ctrl+Z the latest group.
export const UNDO_MS = 5000

export interface UndoGroup<T> { id: number; items: T[]; start: number; timer: ReturnType<typeof setTimeout> }

export function createUndoQueue<T extends { key: string }>(commit: (items: T[]) => void | Promise<void>, changed: () => void, delay = UNDO_MS) {
  const groups: UndoGroup<T>[] = []
  let serial = 0
  function find(key: string) { return groups.find(g => g.items.some(i => i.key === key)) }
  function drop(group: UndoGroup<T>) { clearTimeout(group.timer); groups.splice(groups.indexOf(group), 1) }
  function stage(items: readonly T[]) {
    const fresh = items.filter(i => !find(i.key))
    if (!fresh.length) return
    const group: UndoGroup<T> = { id: ++serial, items: [...fresh], start: Date.now(), timer: 0 as unknown as ReturnType<typeof setTimeout> }
    group.timer = setTimeout(() => {
      const index = groups.indexOf(group)
      if (index < 0) return
      groups.splice(index, 1); changed()
      if (group.items.length) void commit(group.items)
    }, delay)
    groups.push(group); changed()
  }
  /** One row back; the rest of its group keeps its countdown. */
  function undoItem(key: string) {
    const group = find(key)
    if (!group) return
    group.items = group.items.filter(i => i.key !== key)
    if (!group.items.length) drop(group)
    changed()
  }
  function undoGroup(group: UndoGroup<T> | undefined) {
    if (!group || !groups.includes(group)) return false
    drop(group); changed()
    return true
  }
  /** Ctrl+Z: the latest group. */
  function undoLast() { return undoGroup(groups.at(-1)) }
  /** The numbered ↶ shows the latest group that took more than one file. */
  function bulk() { return [...groups].reverse().find(g => g.items.length > 1) }
  function pending(key: string) { return find(key) }
  function clear() { for (const g of groups) clearTimeout(g.timer); groups.length = 0; changed() }
  return { groups, stage, undoItem, undoGroup, undoLast, bulk, pending, clear }
}
