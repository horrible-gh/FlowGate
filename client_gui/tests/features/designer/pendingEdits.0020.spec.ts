import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import { pendingKey, writePending } from '@/features/designer/center/edit/pendingDraft'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
vi.mock('@/core/auth/currentUser', () => ({ useCurrentUser: () => ({ user: { value: { username: 'offline-user' } } }) }))
import { createDraftEditor } from '@/features/designer/center/edit/draftEditor'
import { rebaseDraft } from '@/features/designer/center/edit/pendingDraft'
import { createLayoutSaveQueue } from '@/features/designer/center/layoutSaveQueue'
import type { DraftModel, DraftNode } from '@/features/designer/center/edit/draftModel'

const node = (key: string, x = 0): DraftNode => ({ key, isNew: key.startsWith('tmp_'), nodeType: 'document',
  docType: 'T', paletteItemId: 'task', label: 'T', assignee: null, kind: 'task', x, y: 40 })
const empty: DraftModel = { nodes: [], edges: [] }
beforeEach(() => { vi.useFakeTimers(); localStorage.clear() })
afterEach(() => { vi.useRealTimers(); localStorage.clear() })

it('restores an offline new card and its latest position after the editor is recreated', async () => {
  const storageKey = 'draft-test'
  const first = createDraftEditor(empty, 0, { storageKey, save: vi.fn().mockRejectedValue(new Error('offline')),
    onKeys() {}, onChange() {} })
  first.apply({ type: 'add_node', node: node('tmp_' + 'a'.repeat(32), 50) })
  first.place([{ nodeId: 'tmp_' + 'a'.repeat(32), x: 900, y: 800 }])
  await vi.advanceTimersByTimeAsync(410)
  expect(first.model().nodes[0]).toMatchObject({ x: 900, y: 800 })
  first.dispose()
  await Promise.resolve()
  const save = vi.fn(async () => ({ version: 1, keyMap: { ['tmp_' + 'a'.repeat(32)]: 'gn_' + 'a'.repeat(32) } }))
  const second = createDraftEditor(empty, 0, { storageKey, save, onKeys() {}, onChange() {} })
  expect(second.model().nodes[0]).toMatchObject({ x: 900, y: 800 })
  await vi.advanceTimersByTimeAsync(410)
  expect(save).toHaveBeenCalledTimes(1)
  expect(second.status()).toBe('idle')
  expect(localStorage.getItem(storageKey)).toBeNull()
  second.dispose()
})

it('recognizes a committed temporary identity after a lost response and keeps remote additions', () => {
  const tmp = 'tmp_' + 'b'.repeat(32), key = 'gn_' + 'b'.repeat(32)
  const result = rebaseDraft(empty, { nodes: [node(tmp, 900)], edges: [] },
    { nodes: [node(key, 40), node('gn_other')], edges: [] })
  expect(result.keys[tmp]).toBe(key)
  expect(result.model.nodes.map(n => n.key).sort()).toEqual([key, 'gn_other'].sort())
  expect(result.model.nodes.find(n => n.key === key)?.x).toBe(900)
})

it('retries a failed position batch with newer coordinates and retains it across reload', async () => {
  let fail!: (error: Error) => void
  const send = vi.fn(() => new Promise((_, reject) => { fail = reject }))
  const queue = createLayoutSaveQueue(send, 0, undefined, 'layout-test')
  queue.queuePosition('f', 'gn_a', 100, 200)
  await vi.advanceTimersByTimeAsync(1)
  queue.queuePosition('f', 'gn_a', 300, 400)
  fail(new Error('offline'))
  await Promise.resolve()
  expect(queue.positions('f').get('gn_a')).toEqual({ x: 300, y: 400 })
  expect(queue.busy('f')).toBe(true)
  queue.dispose()
  const recovered = vi.fn(async () => {})
  const next = createLayoutSaveQueue(recovered, 0, undefined, 'layout-test')
  expect(next.positions('f').get('gn_a')).toEqual({ x: 300, y: 400 })
  await vi.advanceTimersByTimeAsync(1)
  expect(recovered).toHaveBeenCalledWith('f', { positions: [{ nodeId: 'gn_a', x: 300, y: 400 }], viewport: null })
  expect(next.busy('f')).toBe(false)
  expect(localStorage.getItem('layout-test')).toBeNull()
  next.dispose()
})

it('opens the cached canvas with an unsaved card and moved position while offline', async () => {
  const graph: FlowGraph = { flowId: 'f', ownership: 'unassigned', viewMode: null, nodes: [], edges: [],
    draft: { version: 0, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }
  writePending(pendingKey('p', 'f', 'canvas'), graph)
  writePending(pendingKey('p', 'f', 'draft'), { model: { nodes: [node('tmp_' + 'c'.repeat(32), 700)], edges: [] }, base: empty, version: 0 })
  const center = createDesignerCenterSession({ source: { isSample: false,
    loadGraph: vi.fn().mockRejectedValue({ network: true }), saveDraft: vi.fn().mockRejectedValue({ network: true }) } })
  center.setProject('p')
  center.openFlow({ projectId: 'p', flowId: 'f', flowName: 'F' })
  await vi.advanceTimersByTimeAsync(1)
  const load = center.state.graphs.f
  expect(load.status).toBe('ready')
  expect(load.status === 'ready' && load.graph.nodes[0]).toMatchObject({ x: 700, y: 40 })
  center.dispose()
})
