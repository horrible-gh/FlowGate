/** the marks a Flow tab keeps for a run / chat that ended while another tab was active. */
import { nextTick, reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import {
  flowTabMarksKey, readFlowTabMarks, runEndOf, useFlowTabMarks, writeFlowTabMarks,
  type FlowTabMarkStorage, type FlowTabRunFacts,
} from '@/features/designer/center/flowTabMarks'

function memoryStorage(): FlowTabMarkStorage & { data: Map<string, string> } {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => { data.set(key, value) },
    removeItem: (key) => { data.delete(key) },
  }
}

function setup(storage = memoryStorage(), projectId = 'p1') {
  const state = reactive({
    projectId: projectId as string | null,
    tabs: ['a', 'b', 'c'] as string[],
    active: 'a' as string | null,
    runs: {} as Record<string, FlowTabRunFacts>,
    chats: {} as Record<string, boolean>,
  })
  const chatEnds: Record<string, 'done' | 'failed'> = {}
  const { marks, stop } = useFlowTabMarks({
    projectId: () => state.projectId,
    tabs: () => state.tabs,
    activeFlowId: () => state.active,
    runs: () => state.runs,
    chats: () => state.chats,
    chatEnd: (id) => chatEnds[id] ?? null,
    storage: () => storage,
  })
  const run = (id: string, status: string | null, idleReason: string | null = null, known = true) => {
    state.runs = { ...state.runs, [id]: { known, status, idleReason } }
  }
  return { state, marks, stop, storage, run, chatEnds }
}

describe('runEndOf (run band classification)', () => {
  it.each([
    ['completed', 'success'],
    ['needs_intervention', 'warning'], ['waiting_approval', 'warning'], ['hold', 'warning'],
    ['error', 'danger'], ['blocked_rejected', 'danger'], ['merge_conflict', 'danger'],
    ['stopped', 'neutral'], ['step_done', 'neutral'], [null, 'neutral'],
  ])('idle with %s → %s', (reason, tone) => {
    expect(runEndOf('running', { status: 'idle', idleReason: reason })).toEqual({ tone, reason })
  })

  it('needs a locked status before and an idle one after', () => {
    expect(runEndOf(null, { status: 'idle', idleReason: 'completed' })).toBeNull()
    expect(runEndOf('idle', { status: 'idle', idleReason: 'completed' })).toBeNull()
    expect(runEndOf('running', { status: 'paused' })).toBeNull()
    expect(runEndOf('stopping', { status: 'idle', idleReason: 'stopped' })).toEqual({ tone: 'neutral', reason: 'stopped' })
    expect(runEndOf('running', null)).toBeNull()
  })
})

describe('useFlowTabMarks', () => {
  it('marks a run that ends on an inactive tab, not on the active one', async () => {
    const t = setup()
    t.run('a', 'running'); t.run('b', 'running'); await nextTick()
    t.run('a', 'idle', 'completed'); t.run('b', 'idle', 'error'); await nextTick()
    expect(t.marks.a).toBeUndefined()
    expect(t.marks.b).toEqual({ run: { tone: 'danger', reason: 'error' } })
  })

  it('ignores a run status not confirmed by a read', async () => {
    const t = setup()
    t.run('b', 'running'); await nextTick()
    t.run('b', null, null, false); await nextTick()
    expect(t.marks.b).toBeUndefined()
    t.run('b', 'idle', 'completed'); await nextTick()
    expect(t.marks.b).toEqual({ run: { tone: 'success', reason: 'completed' } })
  })

  it('marks a chat answer that ends on an inactive tab: done, or failed when the finish said so', async () => {
    const t = setup()
    t.state.chats = { a: true, b: true, c: true }; await nextTick()
    t.chatEnds.c = 'failed'
    t.state.chats = { a: false, b: false, c: false }; await nextTick()
    expect(t.marks.a).toBeUndefined()
    expect(t.marks.b).toEqual({ chat: 'done' })
    expect(t.marks.c).toEqual({ chat: 'failed' })
  })

  it('clears a tab\'s marks when it is activated, and drops them when it closes', async () => {
    const t = setup()
    t.run('b', 'running'); t.run('c', 'running'); await nextTick()
    t.run('b', 'idle', 'completed'); t.run('c', 'idle', 'hold'); await nextTick()
    expect(Object.keys(t.marks).sort()).toEqual(['b', 'c'])
    t.state.active = 'b'; await nextTick()
    expect(t.marks.b).toBeUndefined()
    t.state.tabs = ['a', 'b']; await nextTick()
    expect(t.marks.c).toBeUndefined()
    expect(t.storage.data.has(flowTabMarksKey('p1'))).toBe(false)
  })

  it('keeps unseen marks in storage per Project across a reload', async () => {
    const storage = memoryStorage()
    const first = setup(storage)
    first.run('b', 'running'); await nextTick()
    first.run('b', 'idle', 'merge_conflict'); await nextTick()
    first.state.chats = { c: true }; await nextTick()
    first.state.chats = { c: false }; await nextTick()
    first.stop()
    expect(JSON.parse(storage.data.get(flowTabMarksKey('p1'))!)).toEqual({
      b: { run: { tone: 'danger', reason: 'merge_conflict' } }, c: { chat: 'done' },
    })

    const again = setup(storage)
    expect(again.marks.b).toEqual({ run: { tone: 'danger', reason: 'merge_conflict' } })
    expect(again.marks.c).toEqual({ chat: 'done' })
    // Another Project has its own marks; switching does not drop the first Project's.
    again.state.projectId = 'p2'; again.state.tabs = []; await nextTick()
    expect(Object.keys(again.marks)).toEqual([])
    expect(storage.data.has(flowTabMarksKey('p1'))).toBe(true)
  })

  it('drops the active tab\'s stored marks when it is shown', () => {
    const storage = memoryStorage()
    writeFlowTabMarks(storage, 'p1', { a: { chat: 'done' }, b: { chat: 'failed' } })
    const t = setup(storage)
    expect(t.marks.a).toBeUndefined()
    expect(t.marks.b).toEqual({ chat: 'failed' })
  })

  it('reads nothing from a broken or foreign record', () => {
    const storage = memoryStorage()
    storage.setItem(flowTabMarksKey('p1'), '{not json')
    expect(readFlowTabMarks(storage, 'p1')).toEqual({})
    storage.setItem(flowTabMarksKey('p1'), JSON.stringify({ a: { run: { tone: 'info' } }, b: { chat: 'x' }, c: { chat: 'done' } }))
    expect(readFlowTabMarks(storage, 'p1')).toEqual({ c: { chat: 'done' } })
    expect(readFlowTabMarks(null, 'p1')).toEqual({})
  })
})
