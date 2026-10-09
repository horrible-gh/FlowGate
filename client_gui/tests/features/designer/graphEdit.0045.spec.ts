/**
 * flowgate.v02.0045 T0013 (S3) — pure Graph draft editing: edit commands and their undo, the
 * connection pre-check (server validator criteria, L0009 §2.5), the draft taken from / put back
 * into the shown Graph, and the draft editor's saves (P0008 S3-1 whole replacement, one save on
 * the wire per Flow, key remapping, S3-2 conflict). No DOM, no Vue.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { checkConnection } from '@/features/designer/center/edit/connectionRules'
import { createDraftEditor } from '@/features/designer/center/edit/draftEditor'
import {
  draftContent,
  draftFromGraph,
  remapDraft,
  remapGraph,
  withDraft,
  type DraftModel,
  type DraftNode,
} from '@/features/designer/center/edit/draftModel'
import { applyCommand, applyCommands, reviveCommands } from '@/features/designer/center/edit/graphEditCommands'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowDraftSaveResult } from '@/features/designer/center/graph/flowGraphSource'

const node = (key: string, docType = 'D', x = 0): DraftNode => ({
  key, isNew: key.startsWith('tmp_'), nodeType: 'document', docType, paletteItemId: null, label: docType,
  assignee: null, kind: 'design', x, y: 320,
})

const model = (keys: string[], edges: Array<[string, string]> = []): DraftModel => ({
  nodes: keys.map((k) => node(k)),
  edges: edges.map(([s, t], i) => ({ key: `ge_${i}`, isNew: false, source: s, target: t })),
})

describe('edit commands', () => {
  it('add / connect / disconnect each answer their undo', () => {
    const added = applyCommand(model(['gn_a']), { type: 'add_node', node: node('tmp_b') })!
    expect(added.model.nodes.map((n) => n.key)).toEqual(['gn_a', 'tmp_b'])
    expect(added.undo).toEqual([{ type: 'remove_node', key: 'tmp_b' }])
    const linked = applyCommand(added.model, { type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_b' } })!
    expect(applyCommands(linked.model, linked.undo)).toEqual(added.model)
    expect(applyCommand(linked.model, { type: 'disconnect', key: 'nope' })).toBeNull()
    expect(applyCommand(added.model, { type: 'connect', edge: { key: 'x', isNew: true, source: 'gn_a', target: 'missing' } })).toBeNull()
  })

  it('removing a node takes its edges and its undo brings both back', () => {
    const start = model(['gn_a', 'gn_b', 'gn_c'], [['gn_a', 'gn_b'], ['gn_b', 'gn_c']])
    const removed = applyCommand(start, { type: 'remove_node', key: 'gn_b' })!
    expect(removed.model.edges).toEqual([])
    expect(applyCommands(removed.model, removed.undo)).toEqual({ nodes: [start.nodes[0], start.nodes[2], start.nodes[1]], edges: start.edges })
  })

  it('an undo re-adding nodes whose removal was saved brings them back as new nodes', () => {
    let n = 0
    const revived = reviveCommands(
      [{ type: 'add_node', node: node('gn_b') }, { type: 'connect', edge: { key: 'ge_1', isNew: false, source: 'gn_a', target: 'gn_b' } }],
      new Set(['gn_a']),
      () => `tmp_r${++n}`,
    )
    expect(revived).toEqual([
      { type: 'add_node', node: { ...node('gn_b'), key: 'tmp_r1', isNew: true } },
      { type: 'connect', edge: { key: 'tmp_r2', isNew: true, source: 'gn_a', target: 'tmp_r1' } },
    ])
  })
})

describe('connection pre-check', () => {
  it('refuses non-draft ends and duplicates, warns about undecided structures', () => {
    const m = model(['gn_a', 'gn_b', 'gn_c'], [['gn_a', 'gn_b']])
    expect(checkConnection(m, 'doc:x', 'gn_a')).toEqual({ ok: false, reason: 'not_editable' })
    expect(checkConnection(m, 'gn_a', 'gn_b')).toEqual({ ok: false, reason: 'duplicate' })
    expect(checkConnection(m, 'gn_b', 'gn_c')).toEqual({ ok: true, warnings: [] })
    expect(checkConnection(m, 'gn_a', 'gn_c')).toEqual({ ok: true, warnings: ['branch'] })
    expect(checkConnection(m, 'gn_c', 'gn_b')).toEqual({ ok: true, warnings: ['join'] })
    expect(checkConnection(m, 'gn_b', 'gn_a')).toEqual({ ok: true, warnings: ['cycle'] })
    expect(checkConnection(m, 'gn_c', 'gn_c')).toEqual({ ok: true, warnings: ['cycle'] })
  })
})

describe('draft model', () => {
  const graph: FlowGraph = {
    flowId: 'f',
    nodes: [
      { id: 'doc:r', kind: 'requirement', title: 'R', x: 0, y: 0, movable: true },
      { id: 'gn_a', kind: 'design', title: '설계', x: 0, y: 320, movable: true, structureEditable: true,
        definition: { nodeType: 'document', docType: 'D', paletteItemId: 'basic_design', label: '설계', assignee: null } },
    ],
    edges: [{ id: 'ref:1', source: 'doc:r', target: 'doc:r', edgeClass: 'reference' }],
  }

  it('takes only editable draft nodes and puts an edited draft back', () => {
    const draft = draftFromGraph(graph)
    expect(draft.nodes.map((n) => n.key)).toEqual(['gn_a'])
    const edited = applyCommand(draft, { type: 'add_node', node: { ...node('tmp_t', 'T', 260), kind: 'task', label: '구현' } })!.model
    const linked = applyCommand(edited, { type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_t' } })!.model
    const shown = withDraft(graph, linked)
    expect(shown.nodes.map((n) => [n.id, n.x, n.y, n.structureEditable === true])).toEqual([
      ['doc:r', 0, 0, false], ['gn_a', 0, 320, true], ['tmp_t', 260, 320, true],
    ])
    expect(shown.edges.map((e) => [e.id, e.editable === true])).toEqual([['ref:1', false], ['tmp_e', true]])
    expect(draftContent(linked)).toEqual({
      nodes: [
        { key: 'gn_a', isNew: false, nodeType: 'document', docType: 'D', paletteItemId: 'basic_design', label: '설계', assignee: null },
        { key: 'tmp_t', isNew: true, nodeType: 'document', docType: 'T', paletteItemId: null, label: '구현', assignee: null },
      ],
      edges: [{ key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_t' }],
    })
    const keyMap = { tmp_t: 'gn_t', tmp_e: 'ge_e' }
    expect(remapDraft(linked, keyMap).edges).toEqual([{ key: 'ge_e', isNew: false, source: 'gn_a', target: 'gn_t' }])
    expect(remapGraph(shown, keyMap).nodes.map((n) => n.id)).toEqual(['doc:r', 'gn_a', 'gn_t'])
  })
})

describe('draft editor', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  function setup(start = model(['gn_a']), version = 1) {
    const saves: Array<{ version: number; content: FlowDraftContent; resolve: (r: FlowDraftSaveResult) => void; reject: (e: unknown) => void }> = []
    const keys: Array<Record<string, string>> = []
    const editor = createDraftEditor(start, version, {
      save: (v, content) => new Promise((resolve, reject) => saves.push({ version: v, content, resolve, reject })),
      onKeys: (k) => keys.push({ ...k }),
      onChange: () => {},
      delayMs: 100,
    })
    return { editor, saves, keys }
  }

  it('batches edits, keeps one save on the wire and sends the next with the answered version', async () => {
    const { editor, saves, keys } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    editor.apply({ type: 'connect', edge: { key: 'tmp_e', isNew: true, source: 'gn_a', target: 'tmp_b' } })
    expect(editor.status()).toBe('pending')
    expect(editor.busy()).toBe(true)
    vi.advanceTimersByTime(100)
    expect(saves).toHaveLength(1)
    expect(saves[0].version).toBe(1)
    expect(saves[0].content.nodes.map((n) => n.key)).toEqual(['gn_a', 'tmp_b'])
    editor.apply({ type: 'add_node', node: node('tmp_c') })
    vi.advanceTimersByTime(100)
    expect(saves).toHaveLength(1)                         // still one on the wire
    saves[0].resolve({ version: 2, keyMap: { tmp_b: 'gn_b', tmp_e: 'ge_e' } })
    await vi.runAllTimersAsync()
    expect(keys).toEqual([{ tmp_b: 'gn_b', tmp_e: 'ge_e' }])
    expect(saves).toHaveLength(2)
    expect(saves[1].version).toBe(2)
    expect(saves[1].content.nodes.map((n) => [n.key, n.isNew])).toEqual([['gn_a', false], ['gn_b', false], ['tmp_c', true]])
    expect(saves[1].content.edges).toEqual([{ key: 'ge_e', isNew: false, source: 'gn_a', target: 'gn_b' }])
    saves[1].resolve({ version: 3, keyMap: { tmp_c: 'gn_c' } })
    await vi.runAllTimersAsync()
    expect(editor.status()).toBe('idle')
    expect(editor.version()).toBe(3)
    expect(editor.busy()).toBe(false)
  })

  it('a conflict stops saving and refuses edits until a fresh read resets it', async () => {
    const { editor, saves } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    const settled = editor.settled()
    saves[0].reject(Object.assign(new Error('x'), { code: 'draft_version_conflict' }))
    expect(await settled).toBe(false)
    expect(editor.status()).toBe('conflict')
    expect(editor.errorCode()).toBe('draft_version_conflict')
    expect(editor.apply({ type: 'add_node', node: node('tmp_c') })).toBe(false)
    expect(editor.canUndo()).toBe(false)
    editor.reset(model(['gn_x']), 5)
    expect(editor.status()).toBe('idle')
    expect(editor.apply({ type: 'remove_node', key: 'gn_x' })).toBe(true)
    vi.advanceTimersByTime(100)
    expect(saves[1].version).toBe(5)
  })

  it('a failed save is retried by the next edit; undo goes back step by step', async () => {
    const { editor, saves } = setup()
    editor.apply({ type: 'add_node', node: node('tmp_b') })
    vi.advanceTimersByTime(100)
    saves[0].reject(new Error('network'))
    await vi.runAllTimersAsync()
    expect(editor.status()).toBe('error')
    expect(editor.errorCode()).toBe('save_failed')
    expect(editor.undo()).toBe(true)
    expect(editor.model().nodes.map((n) => n.key)).toEqual(['gn_a'])
    vi.advanceTimersByTime(100)
    expect(saves).toHaveLength(2)
    expect(saves[1].content.nodes.map((n) => n.key)).toEqual(['gn_a'])
  })
})
