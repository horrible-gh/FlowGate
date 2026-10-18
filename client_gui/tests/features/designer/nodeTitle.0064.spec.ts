/**
 * flowgate.v02.0064 T#4 (0062 D0005 §3.2 / §3.5 / §6 세트 2, 0062 L0006 2.1 / 2.8, 0064 P0006 §4.2.7 / §6.5, V-P-25 /
 * V-P-27 client part) — a card's own title, without the DOM of the card:
 *
 * - graph/nodeTitle.ts: the server's normalization and 30 code point count, the version order (accept_title_state) of
 *   save answers and reads, and which cards open for editing (title state, ownership, past revision, running lock — the
 *   server's `node_running` of one node run bundle, the run read's else the Canvas's, never the approval state);
 * - the Center session: a saved title goes onto the shared Graph at once (the Canvas card and the Inspector header),
 *   never through the draft — no draft save, no undo / redo entry, the draft version as it was; a late answer or an old
 *   read never takes a newer title back; an edit, an undo and a redo of the structure keep the title; a refusal names the
 *   server's code and changes nothing; a reread after the save keeps it (새로고침 유지);
 * - the Inspector header shows the shared title — the same new title after a save or another tab's save read again;
 * - flowgate.v02.0070 T#1 (B1): the title is edited in the Inspector header, not on the card — the Canvas mapping carries
 *   no title edit state any more; the session's selection says whether the selected card's title opens now
 *   (`titleEdit`, the same rule with the run read's node run) and the Inspector target passes it on (`canEditTitle`,
 *   `titleLock`); without the host's saver the header shows the title only.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { NO_RUN_MARKS } from '@/features/designer/center/run/flowRunLock'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode, FlowGraphTitleState } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowGraphSource, FlowRunNodeInfo } from '@/features/designer/center/graph/flowGraphSource'
import {
  NODE_TITLE_MAX_CHARS,
  acceptsTitleState,
  applyTitleState,
  checkNodeTitle,
  nodeRunsNow,
  nodeTitleEdit,
  nodeTitleLength,
  normalizeNodeTitle,
  takeTitleStates,
} from '@/features/designer/center/graph/nodeTitle'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()

const F = 'test2.default.0052'
const ts = (title: string | null, version: number, titleSource: string | null = title === null ? null : 'human'): FlowGraphTitleState => ({ title, titleSource, version })

function card(id: string, fields: Partial<FlowGraphNode> = {}, nodeType = 'document'): FlowGraphNode {
  const custom = nodeType === 'custom'
  return {
    id,
    kind: custom ? 'custom' : 'requirement',
    title: custom ? '외부 승인' : '요구사항',
    state: 'draft',
    docType: custom ? null : 'R',
    x: 0,
    y: 0,
    movable: true,
    structureEditable: true,
    definition: { nodeType, docType: custom ? null : 'R', paletteItemId: custom ? null : 'requirement', label: custom ? '외부 승인' : '요구사항', assignee: null },
    titleState: ts(null, 0),
    ...fields,
  }
}

const graphOf = (nodes: FlowGraphNode[], extra: Partial<FlowGraph> = {}): FlowGraph => ({
  flowId: F,
  ownership: 'graph',
  nodes,
  edges: [],
  draft: { version: 4, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 1 },
  viewMode: 'draft',
  latestRevisionNo: 1,
  ...extra,
})

// --- graph/nodeTitle.ts ---------------------------------------------------------------------------------------

describe('the title text rules (L0006 2.1, the server’s)', () => {
  it('normalizes like the server: NFC, control / separators to one space, no zero width space or BOM, trimmed', () => {
    expect(normalizeNodeTitle('  로그인   개선 요구 정리 ')).toBe('로그인 개선 요구 정리')
    expect(normalizeNodeTitle('a\tb\nc d e f　g')).toBe('a b c d e f g')
    expect(normalizeNodeTitle('​A﻿B​')).toBe('AB')
    // NFD Hangul jamo become one syllable each.
    expect(normalizeNodeTitle('한')).toBe('한')
  })

  it('counts NFC code points: 30 Korean syllables pass, 31 do not; spaces only are empty; a raw text over 200 is refused as is', () => {
    const thirty = '가'.repeat(30)
    expect(checkNodeTitle(thirty)).toEqual({ value: thirty, length: 30, refusal: null })
    expect(checkNodeTitle(`${thirty}나`).refusal).toBe('title_too_long')
    expect(checkNodeTitle('   \t ').refusal).toBe('title_empty')
    expect(checkNodeTitle('​​').refusal).toBe('title_empty')
    expect(checkNodeTitle(` ${'가'.repeat(29)} `)).toMatchObject({ length: 29, refusal: null })
    expect(checkNodeTitle(`${'a'.repeat(10)}${' '.repeat(191)}`)).toMatchObject({ length: 201, refusal: 'title_too_long' })
    expect(checkNodeTitle(`${'a'.repeat(10)}${' '.repeat(190)}`)).toMatchObject({ length: 10, refusal: null })
    expect(nodeTitleLength('한'.repeat(30))).toBe(NODE_TITLE_MAX_CHARS)
  })
})

describe('the version order (L0006 2.8 accept_title_state)', () => {
  it('takes a bundle only above the shown version', () => {
    expect(acceptsTitleState(undefined, ts(null, 0))).toBe(true)
    expect(acceptsTitleState(ts('A', 2), ts('B', 3))).toBe(true)
    expect(acceptsTitleState(ts('B', 3), ts('A', 2))).toBe(false)
    expect(acceptsTitleState(ts('B', 3), ts('B', 3))).toBe(false)
  })

  it('a read older than the shown title keeps it (its title too); a newer or the same read is shown as read', () => {
    const known = new Map([['n_a', ts('B 제목', 3)], ['n_b', ts('예전', 1)]])
    const read = graphOf([
      card('n_a', { title: 'A 제목', titleState: ts('A 제목', 2) }),
      card('n_b', { title: '새 제목', titleState: ts('새 제목', 2, 'ai') }),
      card('n_c'),
      card('doc:free', { titleState: null, title: '자유 문서' }),
    ])
    const shown = takeTitleStates(read, known)
    expect(shown.nodes.map((n) => [n.id, n.title, n.titleState?.version ?? null])).toEqual([
      ['n_a', 'B 제목', 3],
      ['n_b', '새 제목', 2],
      ['n_c', '요구사항', 0],
      ['doc:free', '자유 문서', null],
    ])
    expect([...known.entries()].map(([k, v]) => [k, v.version])).toEqual([['n_a', 3], ['n_b', 2], ['n_c', 0]])
    // Nothing kept: the same Graph.
    expect(takeTitleStates(read, new Map())).toBe(read)
  })

  it('a save answer goes onto its node only when newer (title, source and version together)', () => {
    const known = new Map<string, FlowGraphTitleState>([['n_a', ts(null, 0)]])
    const graph = graphOf([card('n_a'), card('n_b')])
    const next = applyTitleState(graph, 'n_a', ts('로그인 개선', 1), known)!
    expect(next.nodes[0]).toMatchObject({ title: '로그인 개선', titleState: ts('로그인 개선', 1) })
    expect(next.nodes[1]).toBe(graph.nodes[1])
    expect(applyTitleState(next, 'n_a', ts('늦은 응답', 1), known)).toBeNull()
    // A node not shown: nothing to draw, the known version still moves on.
    expect(applyTitleState(next, 'n_gone', ts('X', 5), known)).toBeNull()
    expect(known.get('n_gone')?.version).toBe(5)
  })
})

describe('which card titles open (D0005 §3.2 잠금 표, P0006 §6.5)', () => {
  const open = { editable: true, lock: null }
  const closed = { editable: false, lock: null }
  const running = { editable: false, lock: 'running' }

  it('document, custom and gate Graph cards with a title state open; a card without one never does', () => {
    const graph = graphOf([])
    expect(nodeTitleEdit(graph, card('n_a'))).toEqual(open)
    expect(nodeTitleEdit(graph, card('n_c', {}, 'custom'))).toEqual(open)
    expect(nodeTitleEdit(graph, card('n_g', {}, 'gate'))).toEqual(open)
    expect(nodeTitleEdit(graph, card('doc:free', { titleState: null, definition: null }))).toEqual(closed)
    // An older server sends none.
    const { titleState: _none, ...older } = card('n_old')
    expect(nodeTitleEdit(graph, older)).toEqual(closed)
  })

  it('a v0.1 or conflict Flow and a past revision view never open; the latest revision (run view) and an unassigned draft do', () => {
    expect(nodeTitleEdit(graphOf([], { ownership: 'v01' }), card('n_a'))).toEqual(closed)
    expect(nodeTitleEdit(graphOf([], { ownership: 'conflict' }), card('n_a'))).toEqual(closed)
    expect(nodeTitleEdit(graphOf([], { viewMode: 'revision', latestRevisionNo: 3, viewRevisionNo: 2, draft: null }), card('n_a'))).toEqual(closed)
    expect(nodeTitleEdit(graphOf([], { viewMode: 'revision', latestRevisionNo: 3, viewRevisionNo: 3, draft: null }), card('n_a'))).toEqual(open)
    expect(nodeTitleEdit(graphOf([], { ownership: 'unassigned', latestRevisionNo: null }), card('n_a'))).toEqual(open)
  })

  type Facts = Partial<NonNullable<FlowGraphNode['nodeRun']>>
  const nodeRun = (facts: Facts) => ({ openedAt: null, finishedAt: null, resultDocId: 'R-1', status: null, ...facts })
  const runCard = (facts: Facts) => card('n_a', { nodeRun: nodeRun(facts) })

  it('the lock is the server’s node_running (the card settings’ lock a title save is refused by), never the approval state', () => {
    const graph = graphOf([])
    expect(nodeTitleEdit(graph, runCard({ status: 'open', awaitingApproval: false, nodeRunning: true }))).toEqual(running)
    // In review while the card’s AI review works (server: result_doc_id and review_loop_active).
    expect(nodeTitleEdit(graph, runCard({ status: 'review', awaitingApproval: false, nodeRunning: true }))).toEqual(running)
    // In review waiting for a person’s approval.
    expect(nodeTitleEdit(graph, runCard({ status: 'review', awaitingApproval: true, nodeRunning: false }))).toEqual(open)
    // In review with no AI review, waiting only for its result integration (server: integration_waiting) — it awaits no
    // approval and still is not running: the server takes its title, so the screen opens it.
    expect(nodeTitleEdit(graph, runCard({ status: 'review', awaitingApproval: false, integrationStatus: 'pending', nodeRunning: false }))).toEqual(open)
    expect(nodeTitleEdit(graph, runCard({ status: 'review', awaitingApproval: false, integrationStatus: 'conflict', nodeRunning: false }))).toEqual(open)
    expect(nodeTitleEdit(graph, runCard({ status: 'done', awaitingApproval: false, nodeRunning: false }))).toEqual(open)
    expect(nodeTitleEdit(graph, runCard({ status: 'pending', awaitingApproval: false, nodeRunning: false }))).toEqual(open)
    // An older server without node_running: only an open node is locked on screen (the server still judges the save).
    expect(nodeTitleEdit(graph, runCard({ status: 'open' }))).toEqual(running)
    expect(nodeTitleEdit(graph, runCard({ status: 'review', awaitingApproval: false }))).toEqual(open)
    expect(nodeRunsNow(null)).toBe(false)
  })

  it('the run read’s node run (the latest) decides as one bundle; the Canvas node run only when the run read has none', () => {
    const graph = graphOf([])
    // A draft view: its nodes carry no node run; the run read says the card waits for its approval — it opens.
    expect(nodeTitleEdit(graph, card('n_a'), { status: 'review', nodeRunning: false })).toEqual(open)
    // The Canvas said awaiting approval; the run read says the AI review started — locked, nothing taken from the Canvas.
    const awaiting = runCard({ status: 'review', awaitingApproval: true, nodeRunning: false })
    expect(nodeTitleEdit(graph, awaiting, { status: 'review', nodeRunning: true })).toEqual(running)
    // And the other way round: the Canvas said running, the run read says it is done.
    expect(nodeTitleEdit(graph, runCard({ status: 'open', nodeRunning: true }), { status: 'done', nodeRunning: false })).toEqual(open)
    // No run read node run: the Canvas node run as a whole.
    expect(nodeTitleEdit(graph, awaiting, undefined)).toEqual(open)
    expect(nodeTitleEdit(graph, runCard({ status: 'review', nodeRunning: true }), null)).toEqual(running)
  })

  it('through the Inspector target: a draft card waiting for approval in the run read opens; one whose AI review started is locked (0070 T#1)', () => {
    const graph = graphOf([card('n_a'), card('n_b'), card('n_c', { nodeRun: nodeRun({ status: 'review', awaitingApproval: true, nodeRunning: false }) })])
    const target = (id: string, runNode?: Facts & { nodeKey?: string }) => {
      const node = graph.nodes.find((n) => n.id === id)!
      const { canEditTitle, titleLock, titleState } = toInspectorTarget({ projectId: 'p', graph, node, titleEdit: nodeTitleEdit(graph, node, runNode) })!
      return [id, canEditTitle, titleLock, titleState]
    }
    expect(target('n_a', { status: 'review', awaitingApproval: true, nodeRunning: false })).toEqual(['n_a', true, null, ts(null, 0)])
    expect(target('n_b', { status: 'review', awaitingApproval: false, integrationStatus: 'pending', nodeRunning: false })).toEqual(['n_b', true, null, ts(null, 0)])
    expect(target('n_a', { status: 'review', awaitingApproval: false, nodeRunning: true })).toEqual(['n_a', false, 'running', ts(null, 0)])
    expect(target('n_c', { status: 'review', awaitingApproval: false, nodeRunning: true })).toEqual(['n_c', false, 'running', ts(null, 0)])
    // A selection without `titleEdit` (a source that saves no titles) never opens.
    const node = graph.nodes[0]
    expect(toInspectorTarget({ projectId: 'p', graph, node })).toMatchObject({ canEditTitle: false, titleLock: null })
  })

  it('the Canvas mapping carries no title edit state any more: a card only shows its title (0070 T#1)', () => {
    const graph = graphOf([card('n_a'), card('n_b', { nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null, nodeRunning: true } }), card('doc:free', { titleState: null })])
    const marks = { ...NO_RUN_MARKS, nodeRuns: new Map([['n_a', { nodeKey: 'n_a', status: 'review', nodeRunning: false } as FlowRunNodeInfo]]) }
    for (const n of toVueFlowNodes(graph, null, new Map(), { marks })) {
      expect(n.data, n.id).not.toHaveProperty('titleEditable')
      expect(n.data, n.id).not.toHaveProperty('titleLock')
    }
  })
})

// --- the Center session -----------------------------------------------------------------------------------------

function setup(first: FlowGraph) {
  const reads: FlowGraph[] = [first]
  const drafts: Array<{ version: number; content: FlowDraftContent }> = []
  const saves: Array<{ flowId: string; nodeKey: string; title: string }> = []
  const answers: Array<(state: FlowGraphTitleState) => void> = []
  const refusals: Array<(error: unknown) => void> = []
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: vi.fn(async () => reads.length > 1 ? reads.shift()! : reads[0]),
    saveDraft: vi.fn(async (_p, _f, version, content) => {
      drafts.push({ version, content })
      return { version: version + 1, keyMap: {} }
    }),
    validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
    confirmDraft: vi.fn(async () => 2),
    saveNodeTitle: vi.fn((_p: string, flowId: string, nodeKey: string, title: string) => {
      saves.push({ flowId, nodeKey, title })
      return new Promise<FlowGraphTitleState>((resolve, reject) => {
        answers.push(resolve)
        refusals.push(reject)
      })
    }),
  }
  const center = createDesignerCenterSession({ source, draftSaveDelayMs: 10, layoutSaveDelayMs: 10 })
  center.setProject('p')
  center.openFlow({ projectId: 'p', flowId: F, flowName: 'F' })
  const shown = () => {
    const load = center.state.graphs[F]
    if (load?.status !== 'ready') throw new Error('not ready')
    return load.graph as FlowGraph
  }
  const nodeOf = (id: string) => shown().nodes.find((n) => n.id === id)!
  return { center, source, reads, drafts, saves, answers, refusals, shown, nodeOf }
}

const TWO = () => graphOf([
  card('n_2e7f', { x: 0 }),
  card('n_c0f2', { x: 300 }, 'custom'),
], { edges: [{ id: 'ge_1', source: 'n_2e7f', target: 'n_c0f2', edgeClass: 'execution', editable: true }] })

describe('the Center session saves a card title on its own (D0005 §3.2 / §3.5)', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('the answer goes onto the shared Graph at once; no draft save, no undo entry, the draft version as it was', async () => {
    const { center, source, saves, answers, nodeOf, shown } = setup(TWO())
    await flushPromises()
    expect(center.canEditNodeTitles).toBe(true)
    const before = { ...center.state.edits[F] }
    const pending = center.saveNodeTitle(F, 'n_c0f2', '  외부 승인 대기 현황 ')
    expect(saves).toEqual([{ flowId: F, nodeKey: 'n_c0f2', title: '  외부 승인 대기 현황 ' }])
    // No optimistic title: nothing changes before the answer.
    expect(nodeOf('n_c0f2').title).toBe('외부 승인')
    answers[0](ts('외부 승인 대기 현황', 1))
    expect(await pending).toEqual({ ok: true, titleState: ts('외부 승인 대기 현황', 1) })
    expect(nodeOf('n_c0f2')).toMatchObject({ title: '외부 승인 대기 현황', titleState: ts('외부 승인 대기 현황', 1) })
    // The custom card's name (label), the draft version and the history did not move.
    expect(nodeOf('n_c0f2').definition?.label).toBe('외부 승인')
    expect(shown().draft?.version).toBe(4)
    expect(center.state.edits[F]).toEqual(before)
    expect(center.state.edits[F].canUndo).toBe(false)
    expect(source.saveDraft).not.toHaveBeenCalled()
    // No read is held back or added by the save.
    expect(source.loadGraph).toHaveBeenCalledTimes(1)
  })

  it('the Inspector header shows the shared title and follows the save (V-P-27, V-P-25 client part)', async () => {
    const { center, answers } = setup(TWO())
    await flushPromises()
    for (const [nodeId, before, after] of [['n_2e7f', '요구사항', '로그인 개선 요구 정리'], ['n_c0f2', '외부 승인', '외부 승인 대기 현황']] as const) {
      center.selectNode(F, nodeId)
      const header = mount(InspectorHeader, { props: { target: toInspectorTarget(center.activeSelection.value)! }, global: { plugins: [i18n] } })
      const shownBefore = header.find('[data-test="designer-inspector-title"]')
      if (shownBefore.exists()) expect(shownBefore.text()).toBe(before)
      const pending = center.saveNodeTitle(F, nodeId, after)
      answers[answers.length - 1](ts(after, 1))
      await pending
      await header.setProps({ target: toInspectorTarget(center.activeSelection.value)! })
      expect(header.get('[data-test="designer-inspector-title"]').text()).toBe(after)
      // 0070 T#1: the selection says the title opens, but without the host's saver the header shows it only.
      expect(toInspectorTarget(center.activeSelection.value)?.canEditTitle).toBe(true)
      await header.get('[data-test="designer-inspector-title"]').trigger('click')
      expect(header.findAll('input, textarea, [contenteditable]')).toHaveLength(0)
      expect(header.find('[data-test^="designer-canvas-node-title"]').exists()).toBe(false)
      header.unmount()
    }
  })

  it('another tab’s save read again shows in the Inspector selection too (SSE reason title -> read -> version order)', async () => {
    const { center, reads, nodeOf } = setup(TWO())
    await flushPromises()
    center.selectNode(F, 'n_2e7f')
    reads.push(graphOf([card('n_2e7f', { title: 'B 제목', titleState: ts('B 제목', 3) }), card('n_c0f2', {}, 'custom')]))
    reads.shift()
    center.refreshFlow(F)
    await flushPromises()
    expect(nodeOf('n_2e7f').title).toBe('B 제목')
    expect(toInspectorTarget(center.activeSelection.value)?.title).toBe('B 제목')
  })

  it('two tabs: a late answer (v2) never takes back the newer title a read showed (v3), nor an old read a newer answer (V-24)', async () => {
    const { center, reads, answers, nodeOf } = setup(TWO())
    await flushPromises()
    // Tab 1 saves A (committed as v2, answer late); tab 2's B (v3) arrives through a read.
    const late = center.saveNodeTitle(F, 'n_2e7f', 'A 제목')
    reads.push(graphOf([card('n_2e7f', { title: 'B 제목', titleState: ts('B 제목', 3) }), card('n_c0f2', {}, 'custom')]))
    reads.shift()
    center.refreshFlow(F)
    await flushPromises()
    expect(nodeOf('n_2e7f').title).toBe('B 제목')
    answers[0](ts('A 제목', 2))
    expect(await late).toMatchObject({ ok: true })
    expect(nodeOf('n_2e7f')).toMatchObject({ title: 'B 제목', titleState: ts('B 제목', 3) })

    // The other way round: this tab's answer (v5) first, then a read taken at v4.
    const mine = center.saveNodeTitle(F, 'n_2e7f', 'C 제목')
    answers[1](ts('C 제목', 5))
    await mine
    reads.push(graphOf([card('n_2e7f', { title: 'D 제목', titleState: ts('D 제목', 4) }), card('n_c0f2', {}, 'custom')]))
    reads.shift()
    center.refreshFlow(F)
    await flushPromises()
    expect(nodeOf('n_2e7f')).toMatchObject({ title: 'C 제목', titleState: ts('C 제목', 5) })
    // The same version read again is the same title.
    reads.push(graphOf([card('n_2e7f', { title: 'C 제목', titleState: ts('C 제목', 5) }), card('n_c0f2', {}, 'custom')]))
    reads.shift()
    center.refreshFlow(F)
    await flushPromises()
    expect(nodeOf('n_2e7f').title).toBe('C 제목')
  })

  it('a refusal names the server’s code and changes nothing', async () => {
    const { center, refusals, nodeOf } = setup(TWO())
    await flushPromises()
    const pending = center.saveNodeTitle(F, 'n_2e7f', '수동 정리')
    refusals[0](Object.assign(new Error('실행 중인 카드의 제목은 바꿀 수 없습니다.'), { code: 'node_title_locked', status: 409 }))
    expect(await pending).toEqual({ ok: false, code: 'node_title_locked', message: '실행 중인 카드의 제목은 바꿀 수 없습니다.' })
    expect(nodeOf('n_2e7f')).toMatchObject({ title: '요구사항', titleState: ts(null, 0) })
  })

  it('structure edits, their undo and redo keep the title; the draft content never carries it', async () => {
    vi.useFakeTimers()
    const { center, drafts, answers, nodeOf, shown } = setup(TWO())
    await vi.runAllTimersAsync()
    const pending = center.saveNodeTitle(F, 'n_2e7f', '로그인 개선')
    answers[0](ts('로그인 개선', 1))
    await pending
    expect(center.removeEdge(F, 'ge_1')).toBe(true)
    expect(nodeOf('n_2e7f')).toMatchObject({ title: '로그인 개선', titleState: ts('로그인 개선', 1) })
    expect(center.undoEdit(F)).toBe(true)
    expect(shown().edges.map((e) => e.id)).toEqual(['ge_1'])
    expect(nodeOf('n_2e7f')).toMatchObject({ title: '로그인 개선', titleState: ts('로그인 개선', 1) })
    expect(center.redoEdit(F)).toBe(true)
    expect(nodeOf('n_2e7f').title).toBe('로그인 개선')
    // The undo history held the structure edit only: one more undo has nothing of the title to take back.
    expect(center.undoEdit(F)).toBe(true)
    expect(center.undoEdit(F)).toBe(false)
    expect(nodeOf('n_2e7f').title).toBe('로그인 개선')
    await vi.runAllTimersAsync()
    for (const save of drafts) {
      for (const node of save.content.nodes) {
        expect(Object.keys(node).sort()).toEqual(['assignee', 'docType', 'isNew', 'key', 'label', 'nodeType', 'paletteItemId'])
        expect(node.label).not.toBe('로그인 개선')
      }
    }
  })

  it('a reread after the save shows the stored title (새로고침 유지); another project or a closed tab drops a late answer', async () => {
    const { center, reads, answers, nodeOf } = setup(TWO())
    await flushPromises()
    const pending = center.saveNodeTitle(F, 'n_2e7f', '로그인 개선')
    answers[0](ts('로그인 개선', 1))
    await pending
    reads.push(graphOf([card('n_2e7f', { title: '로그인 개선', titleState: ts('로그인 개선', 1) }), card('n_c0f2', {}, 'custom')]))
    reads.shift()
    center.retryGraph(F)
    await flushPromises()
    expect(nodeOf('n_2e7f').title).toBe('로그인 개선')

    const late = center.saveNodeTitle(F, 'n_2e7f', '늦은 저장')
    center.closeFlow(F)
    answers[1](ts('늦은 저장', 2))
    expect(await late).toMatchObject({ ok: true })
    expect(center.state.graphs[F]).toBeUndefined()
    // A closed Flow saves nothing more.
    expect(await center.saveNodeTitle(F, 'n_2e7f', 'x')).toEqual({ ok: false, code: null, message: '' })
  })

  it('a source without titles (the sample source) opens no title', () => {
    const center = createDesignerCenterSession()
    expect(center.canEditNodeTitles).toBe(false)
  })

  it('the selection says whether the selected card’s title opens now, by the Canvas rule (0070 T#1)', async () => {
    const running = card('n_c0f2', { x: 300, nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null, nodeRunning: true } }, 'custom')
    const { center } = setup(graphOf([card('n_2e7f'), running]))
    await flushPromises()
    center.selectNode(F, 'n_2e7f')
    expect(center.activeSelection.value?.titleEdit).toEqual({ editable: true, lock: null })
    center.selectNode(F, 'n_c0f2')
    expect(center.activeSelection.value?.titleEdit).toEqual({ editable: false, lock: 'running' })
    expect(toInspectorTarget(center.activeSelection.value)).toMatchObject({ canEditTitle: false, titleLock: 'running' })
  })
})
