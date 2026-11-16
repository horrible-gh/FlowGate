// Delayed deletion: no request is sent until the undo interval expires.
export function createUndoQueue<T extends { key: string }>(commit: (item: T) => Promise<void>, changed: () => void, delay = 5000) {
  const pending = new Map<string, { item: T; batch: number; timer: ReturnType<typeof setTimeout> }>()
  let batch = 0
  function undo(key?: string) {
    const last = Math.max(0, ...Array.from(pending.values(), p => p.batch))
    for (const [id, entry] of pending) {
      if (key ? id === key : entry.batch === last) { clearTimeout(entry.timer); pending.delete(id) }
    }
    changed()
  }
  function stage(items: readonly T[]) {
    const id = ++batch
    for (const item of items) {
      if (pending.has(item.key)) continue
      const timer = setTimeout(() => {
        pending.delete(item.key); changed()
        void commit(item)
      }, delay)
      pending.set(item.key, { item, batch: id, timer })
    }
    changed()
  }
  function clear() { for (const p of pending.values()) clearTimeout(p.timer); pending.clear(); changed() }
  return { pending, stage, undo, clear }
}
