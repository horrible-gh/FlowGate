/**
 * F104-2 (flowgate.v02test.0104.0001-R, A: R1~R4): a new Flow's default 요구사항 card is placed and saved whichever of
 * the canvas read and the archive read answers first. While the archive state is unknown the Flow is read only, so
 * the seed waits for it instead of being dropped; an archived Flow gets no card; a card that could not be placed or
 * saved is told on the Canvas. The earlier seed tests ran without `readArchive` and could not see this race.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { createDesignerCenterSession, type CanvasNodeSpec, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import DraftActionNotice from '@/features/designer/center/edit/DraftActionNotice.vue'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'

registerAppMessages(i18n)

const REQUIREMENT: CanvasNodeSpec = { nodeType: 'document', docType: 'R', paletteItemId: null, label: '요구사항', kind: 'requirement' }

const empty = (extra: Partial<FlowGraph> = {}): FlowGraph => ({
  flowId: 'f1',
  ownership: 'unassigned',
  nodes: [],
  edges: [],
  draft: { version: 0, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
  viewMode: null,
  ...extra,
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function setup(options: { graph?: FlowGraph; saveFails?: boolean | 'once'; laterRead?: () => Promise<FlowGraph> } = {}) {
  const canvas = deferred<FlowGraph>()
  const archive = deferred<{ archived: boolean }>()
  const drafts: Array<{ version: number; content: FlowDraftContent }> = []
  let reads = 0
  let saveFailures = 0
  const source: FlowGraphSource = {
    isSample: false,
    // The first read waits for the test; later reads (after a save) answer what was saved.
    loadGraph: vi.fn(() => (reads++ === 0
      ? canvas.promise
      : options.laterRead ? options.laterRead() : Promise.resolve(options.graph ?? empty()))),
    saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
    saveDraft: vi.fn(async (_p, _f, version, content) => {
      if (options.saveFails === true || (options.saveFails === 'once' && saveFailures++ === 0)) throw Object.assign(new Error('save failed'), { code: 'document_storage_unwritable' })
      drafts.push({ version, content })
      const keyMap: Record<string, string> = {}
      content.nodes.filter((n) => n.isNew).forEach((n, i) => (keyMap[n.key] = `gn_seed${i}`))
      return { version: version + 1, keyMap }
    }),
    validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
    confirmDraft: vi.fn(async () => 1),
  }
  const session = createDesignerCenterSession({
    source, layoutSaveDelayMs: 10, draftSaveDelayMs: 10, readArchive: () => archive.promise,
  })
  session.setProject('p')
  session.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' }, { seedNode: REQUIREMENT })
  return { session, canvas, archive, drafts, graph: options.graph ?? empty() }
}

const settle = async () => {
  await flushPromises()
  await vi.advanceTimersByTimeAsync(100)
  await flushPromises()
}

const shownNodes = (session: DesignerCenterSession) => {
  const load = session.state.graphs.f1
  return load?.status === 'ready' ? load.graph.nodes : []
}

const savedRequirement = (drafts: Array<{ version: number; content: FlowDraftContent }>) =>
  drafts.map((d) => [d.version, d.content.nodes.map((n) => [n.nodeType, n.docType, n.label, n.isNew])])

describe('F104-2 new Flow default 요구사항 card vs the archive read', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('canvas answers first, archive (not archived) later: the card is kept and placed once the Flow is writable', async () => {
    const { session, canvas, archive, drafts, graph } = setup()
    canvas.resolve(graph)
    await settle()
    // The Flow is still read only here: nothing placed yet, nothing dropped.
    expect(session.isFlowReadOnly('f1')).toBe(true)
    expect(drafts).toEqual([])
    archive.resolve({ archived: false })
    await settle()
    expect(savedRequirement(drafts)).toEqual([[0, [['document', 'R', '요구사항', true]]]])
    expect(shownNodes(session).map((n) => [n.id, n.kind, n.title])).toEqual([['gn_seed0', 'requirement', '요구사항']])
    expect(session.state.edits.f1?.notice ?? null).toBeNull()
    session.dispose()
  })

  it('archive (not archived) answers first, canvas later: one card placed and saved', async () => {
    const { session, canvas, archive, drafts, graph } = setup()
    archive.resolve({ archived: false })
    await settle()
    canvas.resolve(graph)
    await settle()
    expect(savedRequirement(drafts)).toEqual([[0, [['document', 'R', '요구사항', true]]]])
    expect(shownNodes(session)).toHaveLength(1)
    session.dispose()
  })

  it('an archived Flow gets no card and no notice, whichever answers first', async () => {
    for (const archiveFirst of [true, false]) {
      const { session, canvas, archive, drafts, graph } = setup()
      if (archiveFirst) archive.resolve({ archived: true })
      else canvas.resolve(graph)
      await settle()
      if (archiveFirst) canvas.resolve(graph)
      else archive.resolve({ archived: true })
      await settle()
      expect(drafts).toEqual([])
      expect(shownNodes(session)).toEqual([])
      expect(session.state.edits.f1?.notice ?? null).toBeNull()
      session.dispose()
    }
  })

  it('keeps the never-a-second-card rule: a Flow with nodes or a saved draft gets nothing', async () => {
    const graphs = [
      empty({ nodes: [{ id: 'doc:r', kind: 'requirement', title: 'R', state: 'done', x: 0, y: 0, movable: true }] }),
      empty({ draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }),
    ]
    for (const graph of graphs) {
      const { session, canvas, archive, drafts } = setup({ graph })
      canvas.resolve(graph)
      await settle()
      archive.resolve({ archived: false })
      await settle()
      expect(drafts).toEqual([])
      expect(session.state.edits.f1?.notice ?? null).toBeNull()
      session.dispose()
    }
  })

  it('tells the Canvas when the archive read fails (the card cannot be placed)', async () => {
    const { session, canvas, archive, drafts, graph } = setup()
    canvas.resolve(graph)
    await settle()
    archive.reject(new Error('network'))
    await settle()
    expect(drafts).toEqual([])
    expect(session.state.edits.f1?.notice).toEqual({ kind: 'error', code: 'default_card_archive_unknown' })
    session.dispose()
  })

  it('closing the tab before the archive answer and opening it again still places and saves the card', async () => {
    // archive answers while the tab is closed / after the tab is opened again
    for (const answerWhileClosed of [true, false]) {
      const { session, canvas, archive, drafts, graph } = setup()
      canvas.resolve(graph)
      await settle()
      expect(drafts).toEqual([])
      session.closeFlow('f1')
      if (answerWhileClosed) {
        archive.resolve({ archived: false })
        await settle()
        expect(drafts).toEqual([])
      }
      // the reopened tab carries no seedNode (only the create path passes it)
      session.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
      await settle()
      if (!answerWhileClosed) {
        archive.resolve({ archived: false })
        await settle()
      }
      expect(savedRequirement(drafts)).toEqual([[0, [['document', 'R', '요구사항', true]]]])
      expect(shownNodes(session).map((n) => [n.id, n.kind])).toEqual([['gn_seed0', 'requirement']])
      expect(session.state.edits.f1?.notice ?? null).toBeNull()
      session.dispose()
    }
  })

  it('a reopened tab never gets a second card: the seed already placed is gone', async () => {
    const { session, canvas, archive, drafts, graph } = setup()
    archive.resolve({ archived: false })
    canvas.resolve(graph)
    await settle()
    expect(drafts).toHaveLength(1)
    session.closeFlow('f1')
    session.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
    await settle()
    expect(drafts).toHaveLength(1)
    session.dispose()
  })

  it('the background read the archive answer left the card to fails: the card is still placed on the shown Graph', async () => {
    const reread = deferred<FlowGraph>()
    const { session, canvas, archive, drafts, graph } = setup({ laterRead: () => reread.promise })
    canvas.resolve(graph)
    await settle()
    // a change elsewhere reads the Flow again in the background (the empty Graph stays shown)
    session.refreshFlow('f1')
    await flushPromises()
    archive.resolve({ archived: false })
    await settle()
    expect(drafts).toEqual([]) // left to the read under way
    reread.reject(new Error('network'))
    await settle()
    expect(savedRequirement(drafts)).toEqual([[0, [['document', 'R', '요구사항', true]]]])
    expect(shownNodes(session).map((n) => [n.id, n.kind])).toEqual([['gn_seed0', 'requirement']])
    expect(session.state.edits.f1?.notice ?? null).toBeNull()
    session.dispose()
  })

  it('a failed first save is told as not saved yet, and the notice goes once the retry is stored', async () => {
    const { session, canvas, archive, drafts, graph } = setup({ saveFails: 'once' })
    archive.resolve({ archived: false })
    canvas.resolve(graph)
    await settle()
    expect(session.state.edits.f1?.notice).toEqual({ kind: 'error', code: 'default_card_save_failed' })
    expect(shownNodes(session)).toHaveLength(1) // the card is on the Canvas
    await vi.advanceTimersByTimeAsync(2000) // the editor's retry (1 s back-off)
    await flushPromises()
    expect(savedRequirement(drafts)).toEqual([[0, [['document', 'R', '요구사항', true]]]])
    expect(shownNodes(session).map((n) => n.id)).toEqual(['gn_seed0'])
    expect(session.state.edits.f1?.notice ?? null).toBeNull()
    session.dispose()
  })

  it('tells the Canvas when the card save fails', async () => {
    const { session, canvas, archive, graph } = setup({ saveFails: true })
    archive.resolve({ archived: false })
    canvas.resolve(graph)
    await settle()
    expect(session.state.edits.f1?.notice).toEqual({ kind: 'error', code: 'default_card_save_failed' })
    session.dispose()
  })

  it('shows the default card notices in their own words: not placed (drag it) apart from not saved yet (retried)', () => {
    i18n.global.locale.value = 'ko'
    const text = (code: string) => {
      const w = mount(DraftActionNotice, { props: { notice: { kind: 'error', code } }, global: { plugins: [i18n] } })
      const alert = w.get('[data-test="designer-draft-notice"]')
      expect(alert.attributes('data-code')).toBe(code)
      const shown = alert.text()
      w.unmount()
      return shown
    }
    for (const code of ['default_card_not_placed', 'default_card_archive_unknown']) {
      expect(text(code)).toContain('기본 요구사항 카드를 놓지 못했습니다')
      expect(text(code)).toContain('팔레트')
    }
    const saving = text('default_card_save_failed')
    expect(saving).toContain('아직 저장하지 못했습니다')
    expect(saving).not.toContain('팔레트')
  })
})
