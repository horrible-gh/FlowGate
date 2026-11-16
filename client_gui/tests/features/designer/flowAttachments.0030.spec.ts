import { afterEach, describe, expect, it, vi } from 'vitest'
import { createUndoQueue } from '@/features/designer/flowAttachments/undo'

afterEach(() => vi.useRealTimers())
describe('Flow attachment undo', () => {
  it('sends nothing before five seconds and a row undo preserves the file', async () => {
    vi.useFakeTimers()
    const commit = vi.fn(async () => {})
    const queue = createUndoQueue(commit, () => {})
    queue.stage([{ key: 'a' }, { key: 'b' }])
    await vi.advanceTimersByTimeAsync(4999)
    expect(commit).not.toHaveBeenCalled()
    queue.undo('a')
    await vi.advanceTimersByTimeAsync(1)
    expect(commit.mock.calls).toEqual([[{ key: 'b' }]])
  })
  it('Ctrl+Z undoes the latest batch, and close cancels remaining delayed work', async () => {
    vi.useFakeTimers()
    const commit = vi.fn(async () => {})
    const queue = createUndoQueue(commit, () => {})
    queue.stage([{ key: 'a' }]); queue.stage([{ key: 'b' }, { key: 'c' }])
    queue.undo()
    expect([...queue.pending.keys()]).toEqual(['a'])
    queue.clear()
    await vi.advanceTimersByTimeAsync(5000)
    expect(commit).not.toHaveBeenCalled()
  })
  it('separates Flow windows and prevents duplicate requests for staged rows', async () => {
    vi.useFakeTimers()
    const first = vi.fn(async () => {}), second = vi.fn(async () => {})
    const a = createUndoQueue(first, () => {}), b = createUndoQueue(second, () => {})
    a.stage([{ key: 'same' }]); b.stage([{ key: 'same' }]); b.stage([{ key: 'same' }])
    a.undo('same')
    await vi.advanceTimersByTimeAsync(5000)
    expect(first).not.toHaveBeenCalled(); expect(second).toHaveBeenCalledTimes(1)
  })
})
