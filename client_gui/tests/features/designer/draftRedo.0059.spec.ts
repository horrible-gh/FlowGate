/**
 * flowgate.v02.0059 T#2 — Redo of the Graph structure edits (add / remove node, connect /
 * disconnect): the editor's redo stack (round trip, emptied by a new edit, `tmp_` key remap,
 * revived keys, conflict / reset), the Center session's `redoEdit` and the Toolbar / Edit menu
 * button state. Undo of moves / settings / Inspector edits stays out of scope.
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { createDraftEditor } from '@/features/designer/center/edit/draftEditor'
import type { DraftModel, DraftNode } from '@/features/designer/center/edit/draftModel'
import { applyCommandsWithUndo } from '@/features/designer/center/edit/graphEditCommands'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowDraftSaveResult, FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import { isMenuCommandEnabled } from '@/features/designer/top/designerTop'
import ko from '@/app/i18n/ko'

const node = (key: string, x = 0): DraftNode => ({
  key, isNew: key.startsWith('tmp_'), nodeType: 'document', docType: 'D', paletteItemId: null, label: 'D',
  assignee: null, kind: 'design', x, y: 320,
})
const model = (keys: string[]): DraftModel => ({ nodes: keys.map((k) => node(k)), edges: [] })
const keysOf = (m: DraftModel) => m.nodes.map((n) => n.key)

function setup(start = model(['gn_a']), version = 1) {
  const saves: Array<{ version: number; content: FlowDraftContent; resolve: (r: FlowDraftSaveResult) => void; reject: (e: unknown) => void }> = []
  const editor = createDraftEditor(start, version, {
    save: (v, content) => new Promise((resolve, reject) => saves.push({ version: v, content, resolve, reject })),
    onKeys: () => {},
    onChange: () => {},
    delayMs: 100,
  })
  return { editor, saves }
}

describe('applyCommandsWithUndo', () => {
  it('answers the commands that restore the start model, last command first', () => {
    const start = model(['gn_a'])
    const applied = applyCommandsWithUndo(start, [
      { type: 'add_node', node: node('tmp_b') },
      { type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_b' } },
    ])!
    expect(applied.undo.map((c) => c.type)).toEqual(['disconnect', 'remove_node'])
    expect(applyCommandsWithUndo(applied.model, applied.undo)!.model).toEqual(start)
    expect(applyCommandsWithUndo(start, [{ type: 'remove_node', key: 'nope' }])).toBeNull()
  })
})

describe('draft editor redo while a save deleting the entry is on the wire', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('a node saved as gn_b, undone and redone before the delete is answered, is saved again under a new tmp_ key', async () => {
    const { editor, saves } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    vi.advanceTimersByTime(100)
    saves[0].resolve({ version: 2, keyMap: { tmp_b: 'gn_b' } })
    await vi.runAllTimersAsync()
    expect(keysOf(editor.model())).toEqual(['gn_a', 'gn_b'])
    editor.undo()
    editor.flush()
    expect(saves[1].content.nodes.map((n) => n.key)).toEqual(['gn_a'])      // the delete is on the wire
    expect(editor.redo()).toBe(true)
    const restored = editor.model().nodes[1]
    expect(restored.key).toMatch(/^tmp_/)
    expect(restored.isNew).toBe(true)
    saves[1].resolve({ version: 3, keyMap: {} })
    await vi.advanceTimersByTimeAsync(100)
    expect(saves).toHaveLength(3)
    expect(saves[2].version).toBe(3)
    expect(saves[2].content.nodes.map((n) => [n.key === restored.key, n.isNew])).toEqual([[false, false], [true, true]])
    expect(JSON.stringify(saves[2].content)).not.toContain('gn_b')
    saves[2].resolve({ version: 4, keyMap: { [restored.key]: 'gn_c' } })
    await vi.runAllTimersAsync()
    expect(keysOf(editor.model())).toEqual(['gn_a', 'gn_c'])
    expect(editor.status()).toBe('idle')
    expect(editor.undo()).toBe(true)                        // the history follows the new key
    expect(keysOf(editor.model())).toEqual(['gn_a'])
  })

  it('an edge saved as ge_e, undone and redone before the delete is answered, is saved again under a new tmp_ key', async () => {
    const { editor, saves } = setup(model(['gn_a', 'gn_c']))
    editor.apply({ type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'gn_c' } })
    vi.advanceTimersByTime(100)
    saves[0].resolve({ version: 2, keyMap: { tmp_e: 'ge_e' } })
    await vi.runAllTimersAsync()
    editor.undo()
    editor.flush()
    expect(saves[1].content.edges).toEqual([])
    expect(editor.redo()).toBe(true)
    const restored = editor.model().edges[0]
    expect(restored.key).toMatch(/^tmp_/)
    expect(restored.isNew).toBe(true)
    saves[1].resolve({ version: 3, keyMap: {} })
    await vi.advanceTimersByTimeAsync(100)
    expect(saves[2].content.edges).toEqual([{ key: restored.key, isNew: true, source: 'gn_a', target: 'gn_c' }])
    saves[2].resolve({ version: 4, keyMap: { [restored.key]: 'ge_f' } })
    await vi.runAllTimersAsync()
    expect(editor.model().edges.map((e) => e.key)).toEqual(['ge_f'])
    expect(editor.status()).toBe('idle')
  })

  it('an entry whose save keeps its key (the sent model still holds it) comes back under that key', async () => {
    const { editor, saves } = setup(model(['gn_a', 'gn_b']))
    editor.apply({ type: 'remove_node', key: 'gn_b' })
    editor.undo()                                           // back in the model before anything is sent
    editor.flush()
    expect(saves[0].content.nodes.map((n) => n.key)).toEqual(['gn_a', 'gn_b'])
    editor.redo()
    editor.undo()
    expect(keysOf(editor.model())).toEqual(['gn_a', 'gn_b'])
    saves[0].resolve({ version: 2, keyMap: {} })
    await vi.runAllTimersAsync()
    expect(editor.model().nodes.every((n) => !n.isNew)).toBe(true)
  })
})

describe('draft editor redo', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('undo then redo goes back and forth; nothing to redo before an undo', () => {
    const { editor } = setup()
    expect(editor.canRedo()).toBe(false)
    expect(editor.redo()).toBe(false)
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    editor.apply({ type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_b' } })
    expect(editor.canRedo()).toBe(false)
    expect(editor.undo()).toBe(true)
    expect(editor.undo()).toBe(true)
    expect(keysOf(editor.model())).toEqual(['gn_a'])
    expect(editor.canUndo()).toBe(false)
    expect(editor.canRedo()).toBe(true)
    expect(editor.redo()).toBe(true)
    expect(keysOf(editor.model())).toEqual(['gn_a', 'tmp_b'])
    expect(editor.redo()).toBe(true)
    expect(editor.model().edges.map((e) => e.key)).toEqual(['tmp_e'])
    expect(editor.canRedo()).toBe(false)
    expect(editor.canUndo()).toBe(true)
    expect(editor.undo()).toBe(true)                      // the redone edit can be undone again
    expect(editor.model().edges).toEqual([])
  })

  it('a redo is saved like any edit (the same debounce and whole draft)', async () => {
    const { editor, saves } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    vi.advanceTimersByTime(100)
    saves[0].resolve({ version: 2, keyMap: {} })
    await vi.runAllTimersAsync()
    editor.undo()
    editor.redo()
    expect(editor.status()).toBe('pending')
    vi.advanceTimersByTime(100)
    expect(saves.at(-1)!.content.nodes.map((n) => n.key)).toEqual(['gn_a', 'tmp_b'])
  })

  it('a new structure edit empties the redo history', () => {
    const { editor } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    editor.undo()
    expect(editor.canRedo()).toBe(true)
    editor.apply({ type: 'add_node', node: node('tmp_c') })
    expect(editor.canRedo()).toBe(false)
    expect(editor.redo()).toBe(false)
    expect(keysOf(editor.model())).toEqual(['gn_a', 'tmp_c'])
  })

  it('a default node applied as not undoable offers neither undo nor redo', () => {
    const { editor } = setup(model([]))
    editor.apply({ type: 'add_node', node: node('tmp_d') }, false)
    expect(editor.canUndo()).toBe(false)
    expect(editor.canRedo()).toBe(false)
  })

  it('a save issuing keys renames the redo history too', async () => {
    const { editor, saves } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    editor.apply({ type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_b' } })
    vi.advanceTimersByTime(100)
    editor.undo()                                          // while the save runs: redo holds the tmp_ edge
    saves[0].resolve({ version: 2, keyMap: { tmp_b: 'gn_b', tmp_e: 'ge_e' } })
    await vi.runAllTimersAsync()
    expect(editor.redo()).toBe(true)
    // the edge is no longer in the draft, so it returns as a new one — between the renamed nodes
    expect(editor.model().edges.map((e) => [e.key.startsWith('tmp_'), e.isNew, e.source, e.target])).toEqual([[true, true, 'gn_a', 'gn_b']])
    expect(keysOf(editor.model())).toEqual(['gn_a', 'gn_b'])
  })

  it('a node removed on the server comes back under a new key and the rest of the history follows', async () => {
    const { editor, saves } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    editor.apply({ type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_b' } })
    vi.advanceTimersByTime(100)
    saves[0].resolve({ version: 2, keyMap: { tmp_b: 'gn_b', tmp_e: 'ge_e' } })
    await vi.runAllTimersAsync()
    editor.undo()
    editor.undo()
    vi.advanceTimersByTime(100)                            // the removal is stored
    saves[1].resolve({ version: 3, keyMap: {} })
    await vi.runAllTimersAsync()
    expect(editor.redo()).toBe(true)
    const revived = keysOf(editor.model()).find((k) => k !== 'gn_a')!
    expect(revived.startsWith('tmp_')).toBe(true)
    expect(revived).not.toBe('gn_b')
    expect(editor.redo()).toBe(true)
    const edge = editor.model().edges[0]
    expect([edge.source, edge.target, edge.isNew]).toEqual(['gn_a', revived, true])
    expect(editor.undo()).toBe(true)                       // and the history still undoes
    expect(editor.model().edges).toEqual([])
  })

  it('a conflict refuses redo; a fresh read empties both histories', async () => {
    const { editor, saves } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    editor.undo()
    expect(editor.canRedo()).toBe(true)
    vi.advanceTimersByTime(100)
    saves[0].reject(Object.assign(new Error('x'), { code: 'draft_version_conflict' }))
    await vi.runAllTimersAsync()
    expect(editor.status()).toBe('conflict')
    expect(editor.canRedo()).toBe(false)
    expect(editor.redo()).toBe(false)
    editor.reset(model(['gn_x']), 5)
    expect(editor.canRedo()).toBe(false)
    expect(editor.canUndo()).toBe(false)
  })
})

describe('Center session redo', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const base = (): FlowGraph => ({
    flowId: 'f1',
    ownership: 'unassigned',
    nodes: [
      { id: 'gn_a', kind: 'requirement', title: 'R', x: 0, y: 320, movable: true, structureEditable: true,
        definition: { nodeType: 'document', docType: 'R', paletteItemId: null, label: 'R', assignee: null } },
      { id: 'gn_b', kind: 'design', title: 'D', x: 260, y: 320, movable: true, structureEditable: true,
        definition: { nodeType: 'document', docType: 'D', paletteItemId: 'basic_design', label: 'D', assignee: null } },
    ],
    edges: [],
    draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    viewMode: 'draft',
  })

  function open() {
    const source: FlowGraphSource = {
      isSample: false,
      loadGraph: vi.fn(async () => base()),
      saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
      saveDraft: vi.fn(async (_p, _f, version) => ({ version: version + 1, keyMap: {} })),
      validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
      confirmDraft: vi.fn(async () => 1),
    }
    const center = createDesignerCenterSession({ source, layoutSaveDelayMs: 50, draftSaveDelayMs: 50 })
    center.setProject('p')
    center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
    return { center, source }
  }
  const shownGraph = (center: ReturnType<typeof open>['center']) => {
    const load = center.state.graphs.f1
    if (load?.status !== 'ready') throw new Error('not ready')
    return load.graph as FlowGraph
  }

  it('redoEdit takes an undone connect / remove back and mirrors canRedo', async () => {
    const { center, source } = open()
    await vi.runAllTimersAsync()
    expect(center.state.edits.f1.canRedo).toBe(false)
    expect(center.redoEdit('f1')).toBe(false)
    center.connectNodes('f1', 'gn_a', 'gn_b')
    center.removeNode('f1', 'gn_b')
    center.undoEdit('f1')
    center.undoEdit('f1')
    expect(shownGraph(center).edges).toEqual([])
    expect(center.state.edits.f1).toMatchObject({ canUndo: false, canRedo: true })
    expect(center.redoEdit('f1')).toBe(true)
    expect(shownGraph(center).edges).toHaveLength(1)
    expect(center.redoEdit('f1')).toBe(true)
    expect(shownGraph(center).nodes.map((n) => n.id)).toEqual(['gn_a'])
    expect(center.state.edits.f1).toMatchObject({ canUndo: true, canRedo: false })
    await vi.runAllTimersAsync()
    expect(source.saveDraft).toHaveBeenCalled()
  })

  it('a new connect after an undo empties redo; a refused save re-reads and empties it too', async () => {
    const { center, source } = open()
    await vi.runAllTimersAsync()
    center.connectNodes('f1', 'gn_a', 'gn_b')
    center.undoEdit('f1')
    expect(center.state.edits.f1.canRedo).toBe(true)
    center.connectNodes('f1', 'gn_b', 'gn_a')
    expect(center.state.edits.f1.canRedo).toBe(false)
    center.undoEdit('f1')
    expect(center.state.edits.f1.canRedo).toBe(true)
    source.saveDraft = vi.fn(async () => {
      throw Object.assign(new Error('refused'), { code: 'draft_version_conflict' })
    })
    center.connectNodes('f1', 'gn_a', 'gn_b')
    center.undoEdit('f1')
    await vi.runAllTimersAsync()
    expect(center.state.edits.f1).toMatchObject({ canUndo: false, canRedo: false, status: 'idle' })
    expect(center.redoEdit('f1')).toBe(false)
  })

  it('closing the Flow forgets its redo state', async () => {
    const { center } = open()
    await vi.runAllTimersAsync()
    center.connectNodes('f1', 'gn_a', 'gn_b')
    center.undoEdit('f1')
    center.closeFlow('f1')
    expect(center.redoEdit('f1')).toBe(false)
  })
})

describe('Toolbar and Edit menu state', () => {
  const i18n = createI18n({ legacy: false, locale: 'ko', messages: { ko } })
  const buttons = (props: Record<string, unknown>) => {
    const w = mount(DesignerToolbar, { props: { runMode: null, density: 'normal', zoom: 100, ...props }, global: { plugins: [i18n] } })
    const state = (command: string) => (w.find(`[data-command="${command}"]`).element as HTMLButtonElement).disabled
    return { w, state }
  }

  it('Undo / Redo follow canUndo / canRedo and emit the same commands as the menu', async () => {
    const { w, state } = buttons({ canUndo: true, canRedo: false })
    expect([state('edit.undo'), state('edit.redo')]).toEqual([false, true])
    await w.find('[data-command="edit.undo"]').trigger('click')
    await w.find('[data-command="edit.redo"]').trigger('click')       // disabled: no event
    expect(w.emitted('command')).toEqual([['edit.undo']])
    await w.setProps({ canUndo: false, canRedo: true })
    expect([state('edit.undo'), state('edit.redo')]).toEqual([true, false])
    await w.setProps({ disabled: true, canRedo: true })
    expect(state('edit.redo')).toBe(true)
  })

  it('without the flags both stay available as before; the Run menu rerun is a different command', () => {
    const { state } = buttons({})
    expect([state('edit.undo'), state('edit.redo')]).toEqual([false, false])
    const targets = { project: true, group: true, flow: true }
    expect(isMenuCommandEnabled('edit.undo', null, { ...targets, canUndo: false, canRedo: true })).toBe(false)
    expect(isMenuCommandEnabled('edit.redo', null, { ...targets, canUndo: false, canRedo: true })).toBe(true)
    expect(isMenuCommandEnabled('edit.redo', null, { ...targets, canRedo: false })).toBe(false)
    expect(isMenuCommandEnabled('edit.redo', null, { ...targets, flow: false, canRedo: true })).toBe(false)
    expect(isMenuCommandEnabled('run.rerun', null, { ...targets, canRedo: false })).toBe(true)
    expect(isMenuCommandEnabled('run.rerun', 'next', targets)).toBe(false)
  })
})
