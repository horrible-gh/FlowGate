/**
 * flowgate.v02.0045 T0013 (S3+S4) — Designer Center screen editing a Graph draft with the real Vue
 * Flow: the draft bar (label, validate / confirm, issues), a Palette drop that becomes a draft
 * node, a connection between draft nodes (Vue Flow's connect event, as its handle drag would
 * emit it), Delete on the selected draft node, Ctrl+Z, and a v0.1 Flow that offers no editing.
 * flowgate.v02.0045 TR0020 rev2: the bar is only an edit note (no 초안 name / 되돌리기 / 검증 /
 * 확정); 검증 is the Toolbar's (the session's validateDraft), and 실행 confirms the draft first.
 * TR0020 rev3: no 편집 / 편집 마치기 — a Graph Flow's run view is edited right on the Canvas and a
 * stored draft is shown without asking.
 * TR0020 rev4 (rejection item 1): nothing is laid over the Canvas at all — no saving note, no
 * refused-save note, no 검증 result box; a problem only marks its card and names itself on hover.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow } from '@vue-flow/core'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent } from '@/features/designer/center/graph/flowGraphSource'
import { DEFAULT_PALETTE_ITEMS, paletteItemDefinition, paletteNodeSpec, readPaletteDragData, writePaletteDragData } from '@/features/designer/palette/paletteCatalog'

registerAppMessages(i18n)

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
})

afterAll(() => {
  vi.unstubAllGlobals()
  sizeProps.forEach((p, i) => {
    if (originalSize[i]) Object.defineProperty(HTMLElement.prototype, p, originalSize[i]!)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[p]
  })
})

const def = (nodeType: string, docType: string | null, label: string) => ({ nodeType, docType, paletteItemId: null, label, assignee: null })

function draftGraph(): FlowGraph {
  return {
    flowId: 'f1',
    ownership: 'unassigned',
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [
      { id: 'doc:r', kind: 'requirement', title: 'R 문서', state: 'done', x: 0, y: 0, movable: true },
      { id: 'gn_a', kind: 'requirement', title: '요구', state: 'draft', x: 0, y: 320, movable: true, structureEditable: true, definition: def('document', 'R', '요구') },
      { id: 'gn_b', kind: 'design', title: '설계', state: 'draft', x: 260, y: 320, movable: true, structureEditable: true, definition: def('document', 'D', '설계') },
    ],
    edges: [],
    draft: { version: 2, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    viewMode: 'draft',
  }
}

let center: DesignerCenterSession
let wrapper: VueWrapper<any> | null = null
let saved: FlowDraftContent[]
let confirms: number[]
let refuseConfirm: boolean
/** The fake source's draft save (a test may swap it to refuse). */
let saveDraft: (version: number, content: FlowDraftContent) => Promise<{ version: number; keyMap: Record<string, string> }>

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises()
}

async function mountWith(graph: FlowGraph | ((view?: string) => FlowGraph)) {
  center = createDesignerCenterSession({
    draftSaveDelayMs: 0,
    layoutSaveDelayMs: 0,
    readDrop: (transfer) => {
      const definition = readPaletteDragData(transfer)
      return definition ? paletteNodeSpec(definition) : null
    },
    source: {
      isSample: false,
      loadGraph: async (_p, _f, view?: string) => (typeof graph === 'function' ? graph(view) : graph),
      saveLayout: async () => ({ ignoredNodeIds: [] }),
      saveDraft: (_p, _f, version, content) => saveDraft(version, content),
      validateDraft: async () => ({
        confirmable: false,
        issues: [{ code: 'rule_undecided', structure: 'join', nodeIds: ['gn_b'], edgeIds: [], message: '합류 규칙 미정' }],
      }),
      confirmDraft: async (...args) => {
        confirms.push(args[2])
        if (refuseConfirm) {
          throw Object.assign(new Error('refused'), {
            code: 'graph_validation_failed',
            issues: [{ code: 'rule_undecided', structure: 'join', nodeIds: ['gn_b'], edgeIds: [], message: '합류 규칙 미정' }],
          })
        }
        return 1
      },
      loadRevision: async () => ({ revisionNo: 1, content: { nodes: [], edges: [] } }),
    },
  })
  center.setProject('p')
  wrapper = mount(DesignerCenter, { props: { center }, attachTo: document.body, global: { plugins: [i18n] } })
  center.openFlow({ projectId: 'p', flowId: 'f1', flowName: 'F1' })
  await settle()
  return wrapper
}

const wait = () => new Promise((resolve) => setTimeout(resolve, 5))

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  saved = []
  confirms = []
  refuseConfirm = false
  saveDraft = async (version, content) => {
    saved.push(content)
    return { version: version + 1, keyMap: {} }
  }
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('FlowCanvas editing a Graph draft (flowgate.v02.0045 S3)', () => {
  it.each(['ko', 'en', 'ja'] as const)('shows a storage refusal in %s and keeps the unsaved connection', async (locale) => {
    const w = await mountWith(draftGraph())
    i18n.global.locale.value = locale
    saveDraft = async () => { throw { code: 'document_storage_unwritable' } }
    center.connectNodes('f1', 'gn_a', 'gn_b')
    await wait()
    await settle()
    expect(w.get('[data-test="designer-storage-error"]').text()).toBe(i18n.global.t('gui.designer.center.document_storage_unwritable'))
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.draft?.version).toBe(2)
    expect(load?.status === 'ready' && load.graph.edges).toHaveLength(1)
  })

  it('shows no draft name or draft buttons on the Canvas and marks draft nodes connectable', async () => {
    const w = await mountWith(draftGraph())
    // TR0020 rev4: nothing is laid over the Canvas — the canvas area holds the Canvas and nothing else.
    expect(w.find('[data-test="designer-draft-toolbar"]').exists()).toBe(false)
    expect([...w.get('.designer-center-canvas-area').element.children].map((el) => el.getAttribute('data-test'))).toEqual(['designer-canvas'])
    for (const gone of ['label', 'undo', 'validate', 'confirm', 'start', 'show-draft', 'show-revision']) {
      expect(w.find(`[data-test="designer-draft-${gone}"]`).exists(), gone).toBe(false)
    }
    expect(w.text()).not.toMatch(/초안|확정|개정/)
    expect(w.get('[data-test="designer-canvas"]').attributes('data-editable')).toBe('true')
    const cards = w.findAll('[data-test="designer-canvas-node"]')
    expect(cards.map((c) => [c.attributes('data-node-id'), c.attributes('data-connectable')])).toEqual([
      ['doc:r', 'false'], ['gn_a', 'true'], ['gn_b', 'true'],
    ])
  })

  it('a Palette card dropped on the Canvas becomes a draft node and is saved', async () => {
    const w = await mountWith(draftGraph())
    const store = new Map<string, string>()
    const transfer = { setData: (k: string, v: string) => store.set(k, v), getData: (k: string) => store.get(k) ?? '', effectAllowed: 'none', dropEffect: 'none' }
    const item = DEFAULT_PALETTE_ITEMS.find((i) => i.id === 'task')!
    writePaletteDragData(transfer as unknown as DataTransfer, paletteItemDefinition(item, '구현'))
    const canvas = w.get('[data-test="designer-canvas"]')
    const over = new Event('dragover', { cancelable: true }) as DragEvent
    Object.defineProperty(over, 'dataTransfer', { value: transfer })
    canvas.element.dispatchEvent(over)
    expect(over.defaultPrevented).toBe(true)
    const drop = new Event('drop', { cancelable: true }) as DragEvent
    Object.defineProperties(drop, { dataTransfer: { value: transfer }, clientX: { value: 300 }, clientY: { value: 200 } })
    canvas.element.dispatchEvent(drop)
    await settle()
    await wait()
    await settle()
    expect(saved).toHaveLength(1)
    expect(saved[0].nodes.map((n) => [n.docType, n.label, n.isNew])).toEqual([['R', '요구', false], ['D', '설계', false], ['T', '구현', true]])
  })

  it('a selected Palette card places through the existing draft drop path and Escape cancels', async () => {
    const w = await mountWith(draftGraph())
    await w.setProps({ placing: true })
    const pane = w.get('.vue-flow__pane')
    await pane.trigger('click', { clientX: 300, clientY: 200 })
    const placed = w.emitted('canvas-place')?.at(-1)
    expect(placed?.[0]).toBe('f1')
    expect(placed?.[1]).toBeTypeOf('number')
    expect(placed?.[2]).toBeTypeOf('number')

    const item = DEFAULT_PALETTE_ITEMS.find((i) => i.id === 'task')!
    const transfer = { getData: (type: string) => type === 'application/x-flowgate-palette-item'
      ? JSON.stringify(paletteItemDefinition(item, '구현')) : '' }
    expect(center.dropOnCanvas('f1', transfer, placed![1] as number, placed![2] as number)).toBe(true)
    await settle()
    await wait()
    await settle()
    expect(saved[0].nodes.at(-1)).toMatchObject({ docType: 'T', label: '구현', isNew: true })

    await w.get('[data-test="designer-canvas"]').trigger('keydown', { key: 'Escape' })
    expect(w.emitted('cancel-placement')).toHaveLength(1)
  })

  it('nothing appears while an edit is saved — no 저장 중 note, nothing over the Canvas (rev4)', async () => {
    const w = await mountWith(draftGraph())
    let release: () => void = () => {}
    saveDraft = (version) => new Promise((resolve) => {
      release = () => resolve({ version: version + 1, keyMap: {} })
    })
    expect(center.connectNodes('f1', 'gn_a', 'gn_b')).toBe(true)
    expect(center.state.edits.f1.status).toBe('pending')
    await wait()
    await settle()
    expect(center.state.edits.f1.status).toBe('saving')
    const area = w.get('.designer-center-canvas-area')
    expect([...area.element.children].map((el) => el.getAttribute('data-test'))).toEqual(['designer-canvas'])
    expect(area.text()).not.toMatch(/저장|Saving|保存/)
    release()
    await settle()
    expect(center.state.edits.f1.status).toBe('idle')
    expect([...area.element.children].map((el) => el.getAttribute('data-test'))).toEqual(['designer-canvas'])
  })

  it('a refused save shows nothing either: the Flow is read again as stored (rev4)', async () => {
    const loads: number[] = []
    const w = await mountWith(() => {
      loads.push(1)
      return draftGraph()
    })
    const stored = saveDraft
    saveDraft = async () => {
      throw Object.assign(new Error('refused'), { code: 'draft_version_conflict' })
    }
    const before = loads.length
    expect(center.connectNodes('f1', 'gn_a', 'gn_b')).toBe(true)
    await wait()
    await settle()
    saveDraft = stored
    expect(loads.length).toBe(before + 1)
    expect(center.state.edits.f1.status).toBe('idle')
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.edges).toEqual([])
    expect([...w.get('.designer-center-canvas-area').element.children].map((el) => el.getAttribute('data-test'))).toEqual(['designer-canvas'])
    expect(w.text()).not.toMatch(/저장|다시 불러오기/)
    expect(w.findAll('button').map((b) => b.text())).not.toContain('다시 불러오기')
  })

  it('connect, Delete and Ctrl+Z go through the draft commands', async () => {
    const w = await mountWith(draftGraph())
    const flow = useVueFlow('designer-canvas:f1')
    flow.emits.connect({ source: 'gn_a', target: 'gn_b', sourceHandle: null, targetHandle: null })
    flow.emits.connect({ source: 'doc:r', target: 'gn_b', sourceHandle: null, targetHandle: null })   // refused: not a draft node
    await settle()
    const load = center.state.graphs.f1
    expect(load?.status === 'ready' && load.graph.edges.map((e) => [e.source, e.target])).toEqual([['gn_a', 'gn_b']])
    center.selectNode('f1', 'gn_b')
    await settle()
    const canvas = w.get('[data-test="designer-canvas"]')
    await canvas.trigger('keydown', { key: 'Delete' })
    expect(center.state.graphs.f1?.status === 'ready' && (center.state.graphs.f1 as { graph: FlowGraph }).graph.nodes.map((n) => n.id)).toEqual(['doc:r', 'gn_a'])
    await canvas.trigger('keydown', { key: 'z', ctrlKey: true })
    expect((center.state.graphs.f1 as { graph: FlowGraph }).graph.nodes.map((n) => n.id).sort()).toEqual(['doc:r', 'gn_a', 'gn_b'])
  })

  it('validation issues (the Toolbar 검증 runs the session validateDraft) only mark their node, which names the problem on hover', async () => {
    const w = await mountWith(draftGraph())
    expect(center.activeCanValidate.value).toBe(true)
    await center.validateDraft('f1')
    await settle()
    // rev4: no result box over the Canvas.
    expect(w.find('[data-test="designer-draft-check"]').exists()).toBe(false)
    expect([...w.get('.designer-center-canvas-area').element.children].map((el) => el.getAttribute('data-test'))).toEqual(['designer-canvas'])
    const cards = w.findAll('[data-test="designer-canvas-node"]')
    const issueCard = cards.find((c) => c.attributes('data-node-id') === 'gn_b')!
    expect(issueCard.attributes('data-issue')).toBe('true')
    expect(issueCard.attributes('title')).toBe('합류 규칙 미정')
    expect(cards.find((c) => c.attributes('data-node-id') === 'gn_a')!.attributes('title')).toBeUndefined()
    // The next edit clears the mark.
    expect(center.connectNodes('f1', 'gn_a', 'gn_b')).toBe(true)
    await settle()
    expect(w.findAll('[data-test="designer-canvas-node"]').find((c) => c.attributes('data-node-id') === 'gn_b')!.attributes('data-issue')).toBe('false')
  })

  it('실행 confirms the edited draft first and lets the run start', async () => {
    await mountWith(draftGraph())
    const ready = center.prepareRun('f1')
    expect(ready).toBeInstanceOf(Promise)
    await expect(ready).resolves.toBe(true)
    expect(confirms).toEqual([2])
  })

  it('a refused confirmation stops the run and marks its issues on the Canvas, nothing more', async () => {
    const w = await mountWith(draftGraph())
    refuseConfirm = true
    await expect(center.prepareRun('f1')).resolves.toBe(false)
    await settle()
    expect(w.find('[data-test="designer-draft-check"]').exists()).toBe(false)
    const issueCard = w.findAll('[data-test="designer-canvas-node"]').find((c) => c.attributes('data-node-id') === 'gn_b')!
    expect([issueCard.attributes('data-issue'), issueCard.attributes('title')]).toEqual(['true', '합류 규칙 미정'])
    expect(w.text()).not.toMatch(/고친 뒤 실행하세요|문제 1건/)
  })

  it('nothing to confirm (v0.1 Flow, nothing drawn) lets the run start at once', async () => {
    await mountWith({ ...draftGraph(), ownership: 'v01', draft: null, viewMode: null })
    expect(center.prepareRun('f1')).toBe(true)
    expect(center.activeCanValidate.value).toBe(false)
    wrapper?.unmount()
    await mountWith({ ...draftGraph(), nodes: [], draft: { version: 0, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } })
    expect(center.prepareRun('f1')).toBe(true)
    expect(center.activeCanValidate.value).toBe(false)
    expect(confirms).toEqual([])
  })

  // TR0020 rev3: the run view of a Graph Flow as the server sends it (Graph nodes not editable).
  const runView = (): FlowGraph => ({
    ...draftGraph(),
    ownership: 'graph',
    nodes: draftGraph().nodes.map((node) => (node.definition ? { ...node, state: 'done', structureEditable: false } : node)),
    draft: null,
    viewMode: 'revision',
    latestRevisionNo: 1,
    viewRevisionNo: 1,
  })

  it('a Graph Flow showing its run has no 편집 button and is edited right on the Canvas', async () => {
    const w = await mountWith(runView())
    expect(w.find('[data-test="designer-draft-edit"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-draft-finish"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-draft-toolbar"]').exists()).toBe(false)
    expect(w.get('[data-test="designer-canvas"]').attributes('data-editable')).toBe('true')
    // The Graph nodes take connections now.
    const card = w.findAll('[data-test="designer-canvas-node"]').find((c) => c.attributes('data-node-id') === 'gn_a')!
    expect(card.attributes('data-connectable')).toBe('true')
    expect(center.connectNodes('f1', 'gn_a', 'gn_b')).toBe(true)
    await wait()
    await settle()
    // The first edit saves the whole revision (its keys kept) plus the edit as a new draft.
    expect(saved).toHaveLength(1)
    expect(saved[0].nodes.map((n) => [n.key, n.isNew])).toEqual([['gn_a', false], ['gn_b', false]])
    expect(saved[0].edges.map((e) => [e.source, e.target])).toEqual([['gn_a', 'gn_b']])
    expect(w.findAll('button').map((b) => b.text())).not.toContain('편집 마치기')
    // 실행 confirms it on top of the revision shown (no separate step).
    await expect(center.prepareRun('f1')).resolves.toBe(true)
    expect(confirms).toEqual([1])
  })

  it('a Graph Flow with a stored draft opens on the draft without a button', async () => {
    const views: Array<string | undefined> = []
    const w = await mountWith((view) => {
      views.push(view)
      return view === 'draft'
        ? { ...draftGraph(), ownership: 'graph', latestRevisionNo: 1, viewRevisionNo: null }
        : { ...runView(), draft: { version: 2, editable: false, confirmable: true, blockedReason: null, baseRevisionNo: 1 } }
    })
    expect(views).toEqual([undefined, 'draft'])
    expect(w.get('[data-test="designer-canvas"]').attributes('data-editable')).toBe('true')
    expect(w.find('[data-test="designer-draft-toolbar"]').exists()).toBe(false)
    expect(w.findAll('button').map((b) => b.text())).not.toContain('편집')
  })

  it('a v0.1 Flow offers no draft bar and no drop', async () => {
    const w = await mountWith({ ...draftGraph(), ownership: 'v01', draft: null, viewMode: null })
    expect(w.find('[data-test="designer-draft-toolbar"]').exists()).toBe(false)
    expect(w.get('[data-test="designer-canvas"]').attributes('data-editable')).toBe('false')
    const over = new Event('dragover', { cancelable: true })
    Object.defineProperty(over, 'dataTransfer', { value: { dropEffect: 'none' } })
    w.get('[data-test="designer-canvas"]').element.dispatchEvent(over)
    expect(over.defaultPrevented).toBe(false)
  })
})
