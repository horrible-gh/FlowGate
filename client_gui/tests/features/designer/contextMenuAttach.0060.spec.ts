/**
 * flowgate.v02.0060 T#2 — the Designer context menu attached (0006-D rev2 §9.3): the connector of the Shell
 * (contextMenu/attach) over a real Center session with an editable draft (fake source), the real DesignerCenter
 * (Flow tabs + Vue Flow Canvas), the real Palette and the real menu view. Checks the Canvas / tab / Palette entry
 * points, the first-step commands through the existing session paths, the all-disabled menus, the multi-selection
 * rule, the re-check before running, the invalidation and the right-button guard on the Canvas root.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, ref, type PropType, type Ref } from 'vue'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import FlowCanvas from '@/features/designer/center/canvas/FlowCanvas.vue'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowDraftContent, FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import DesignerContextMenu from '@/features/designer/contextMenu/DesignerContextMenu.vue'
import { useDesignerContextMenu } from '@/features/designer/contextMenu/attach/designerMenuConnector'
import PalettePanel from '@/features/designer/palette/PalettePanel.vue'
import { paletteNodeSpec, readPaletteDragData, type PaletteItemDefinition } from '@/features/designer/palette/paletteCatalog'

registerAppMessages(i18n)

// --- Canvas environment for Vue Flow under jsdom (as DesignerShellSelection.0059) ------------
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))
let paneStyle: HTMLStyleElement
beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
  paneStyle = document.createElement('style')
  paneStyle.textContent = '.vue-flow__pane { z-index: 1; }'
  document.head.appendChild(paneStyle)
})
afterAll(() => {
  vi.unstubAllGlobals()
  sizeProps.forEach((p, i) => {
    if (originalSize[i]) Object.defineProperty(HTMLElement.prototype, p, originalSize[i]!)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[p]
  })
  paneStyle.remove()
})
// -------------------------------------------------------------------------------------------

const definition = (label: string) => ({ nodeType: 'document', docType: 'T', paletteItemId: 'task', label, assignee: null })
/** gn_a (done) → gn_b (reached) is progressed; gn_b → gn_c is not. gn_lock cannot be removed. */
function draftGraph(flowId: string): FlowGraph {
  return {
    flowId,
    ownership: 'unassigned',
    nodes: [
      { id: 'gn_a', kind: 'task', title: 'A', x: 0, y: 0, movable: true, structureEditable: true, definition: definition('A'), state: 'done' },
      { id: 'gn_b', kind: 'task', title: 'B', x: 300, y: 0, movable: true, structureEditable: true, definition: definition('B'), state: 'in_progress' },
      { id: 'gn_c', kind: 'task', title: 'C', x: 600, y: 0, movable: true, structureEditable: true, definition: definition('C') },
      { id: 'gn_lock', kind: 'task', title: 'L', x: 0, y: 220, movable: true, structureEditable: false, definition: definition('L') },
    ],
    edges: [
      { id: 'ge_ab', source: 'gn_a', target: 'gn_b', edgeClass: 'execution', editable: true },
      { id: 'ge_bc', source: 'gn_b', target: 'gn_c', edgeClass: 'execution', editable: true },
    ],
    draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
    viewMode: 'draft',
  } as unknown as FlowGraph
}

function makeSource(editable = true, hold = false, server = false) {
  const holds: Array<[string, boolean]> = []
  const drafts: FlowDraftContent[] = []
  const runs: string[] = []
  const source: FlowGraphSource = {
    isSample: false,
    loadGraph: vi.fn(async (_p: string, flowId: string) => {
      const graph = server ? ({ ...draftGraph(flowId), ownership: 'graph' } as FlowGraph) : draftGraph(flowId)
      return editable ? graph : ({ ...graph, draft: { ...graph.draft!, editable: false } } as FlowGraph)
    }),
    saveLayout: vi.fn(async () => ({ ignoredNodeIds: [] })),
    saveDraft: vi.fn(async (_p, _f, version, content) => {
      drafts.push(content)
      return { version: version + 1, keyMap: {} }
    }),
  }
  // flowgate.v02.0061 T#15: a source that keeps card Holds gives stored cards their run menu (Hold 설정 / 해제).
  if (hold || server) {
    source.writeCardHold = vi.fn(async (_p: string, _f: string, nodeKey: string, on: boolean) => {
      holds.push([nodeKey, on])
      return on
    })
  }
  // A server source with the run controls and the Graph Time Machine (0061 T#14 / T#15): an idle run, gn_a done.
  if (server) {
    const idle: FlowRunInfo = { runId: 'r1', status: 'idle', openNodeKeys: [], nodeRuns: [{ nodeKey: 'gn_a', status: 'done' }] }
    source.loadRun = vi.fn(async () => idle)
    source.startRun = vi.fn(async () => {
      runs.push('start')
      return idle
    })
    source.stopRun = vi.fn(async () => {
      runs.push('stop')
      return idle
    })
    source.resumeRun = vi.fn(async () => {
      runs.push('resume')
      return idle
    })
    source.timeMachine = vi.fn(async () => {
      runs.push('timeMachine')
      throw new Error('not expected')
    })
  }
  return { source, holds, drafts, runs }
}

interface Calls {
  selectAll: string[]
  groupCreate: number
  flowCreate: string[]
  palette: PaletteItemDefinition[]
}

const Harness = defineComponent({
  props: {
    center: { type: Object as PropType<DesignerCenterSession>, required: true },
    calls: { type: Object as PropType<Calls>, required: true },
    canSelectAll: { type: Function as PropType<() => boolean>, required: true },
    layout: { type: Object as PropType<Ref<string>>, required: true },
  },
  setup(props) {
    const menu = useDesignerContextMenu({
      center: props.center,
      projectId: () => props.center.state.projectId,
      canSelectAll: () => props.canSelectAll(),
      selectAll: (flowId) => {
        props.calls.selectAll.push(flowId)
        props.center.selectAll(flowId)
      },
      openGroupCreate: () => { props.calls.groupCreate += 1 },
      openFlowCreate: (groupId) => { props.calls.flowCreate.push(groupId) },
      selectPalette: (picked) => { props.calls.palette.push(picked) },
      layout: () => props.layout.value,
    })
    harnessMenu = menu
    return () => h('div', [
      h(DesignerCenter, { center: props.center }),
      h('div', { class: 'harness-palette' }, [h(PalettePanel)]),
      h(DesignerContextMenu, { host: menu.host, onRequest: menu.run }),
    ])
  },
})

let wrapper: VueWrapper<any> | null = null
/** The connector the mounted Harness made (the areas' entry points and the menu's `request`). */
let harnessMenu: ReturnType<typeof useDesignerContextMenu> | null = null
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  harnessMenu = null
  document.body.innerHTML = ''
})
beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

async function settle() {
  for (let i = 0; i < 6; i += 1) await flushPromises()
}

async function mountHarness(options: { editable?: boolean; flows?: string[]; canSelectAll?: boolean; hold?: boolean; server?: boolean; saveDelay?: number } = {}) {
  const { source, holds, drafts, runs } = makeSource(options.editable ?? true, options.hold ?? false, options.server ?? false)
  const center = createDesignerCenterSession({
    source,
    // Saves wait unless a test lets them run (a save answered between two steps would re-key a restored card).
    draftSaveDelayMs: options.saveDelay ?? 60_000,
    layoutSaveDelayMs: options.saveDelay ?? 60_000,
    readDrop: (transfer) => {
      const read = readPaletteDragData(transfer)
      return read ? paletteNodeSpec(read) : null
    },
  })
  center.setProject('p')
  for (const flowId of options.flows ?? ['f1']) center.openFlow({ projectId: 'p', flowId, flowName: flowId.toUpperCase() })
  const calls: Calls = { selectAll: [], groupCreate: 0, flowCreate: [], palette: [] }
  const layout = ref('left:open')
  wrapper = mount(Harness, {
    props: { center, calls, canSelectAll: () => options.canSelectAll ?? true, layout },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await settle()
  return { w: wrapper, center, calls, holds, drafts, runs, source, layout }
}

const menuEl = () => document.querySelector<HTMLElement>('[data-designer-context-menu]')
const items = (level = 0) =>
  Array.from(document.querySelectorAll<HTMLElement>(`[data-designer-context-menu] [data-level="${level}"] [role="menuitem"]`))
/** Icon, label and › of each item, as the menu shows them in their own cells. */
const itemTexts = (level = 0) =>
  items(level).map((entry) => Array.from(entry.querySelectorAll('span')).map((cell) => cell.textContent!.trim()).join(' '))
const item = (key: string, level = 0) => items(level).find((entry) => entry.dataset.key === key)!
const graphOf = (center: DesignerCenterSession, flowId = 'f1') => {
  const load = center.state.graphs[flowId]
  if (load?.status !== 'ready') throw new Error('graph not ready')
  return load.graph as FlowGraph
}

function rightClick(element: Element, x = 40, y = 50) {
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 2 })
  element.dispatchEvent(event)
  return event
}
const card = (w: VueWrapper<any>, id: string) => w.get(`.vue-flow__node[data-id="${id}"]`).element
const line = (w: VueWrapper<any>, id: string) => w.get(`[data-test="designer-canvas-edge"][data-edge-id="${id}"]`).element
const pane = (w: VueWrapper<any>) => w.get('.vue-flow__pane').element
const tabEl = (w: VueWrapper<any>, flowId: string) => w.get(`[data-test="designer-flow-tab"][data-flow-id="${flowId}"]`).element

/** What a right click / an opened menu must never change (§3.5). */
function invariants(center: DesignerCenterSession, flowId = 'f1') {
  const view = center.state.views[flowId]
  const edit = center.state.edits[flowId]
  return JSON.stringify({
    active: center.state.activeFlowId,
    tabs: center.state.tabs.map((tab) => tab.flowId),
    selected: view?.selectedNodeIds,
    single: view?.selectedNodeId,
    mode: center.selectMode.value,
    viewport: view?.viewport,
    positions: graphOf(center, flowId).nodes.map((node) => [node.id, node.x, node.y]),
    undo: edit?.canUndo ?? null,
    redo: edit?.canRedo ?? null,
    status: edit?.status ?? null,
  })
}

describe('Designer context menu — Canvas (0006-D §9.3 ④)', () => {
  it('blank Canvas: 카드 추가 › (9 built-in cards) and 전체 선택 only; opening changes nothing', async () => {
    const { w, center } = await mountHarness()
    const before = invariants(center)
    const event = rightClick(pane(w), 120, 90)
    await settle()
    expect(event.defaultPrevented).toBe(true)
    expect(itemTexts()).toEqual(['＋ 카드 추가 ›', '▢ 전체 선택'])
    expect(menuEl()!.querySelectorAll('[data-level="0"] [role="separator"]')).toHaveLength(1)
    item('canvas.addCard').dispatchEvent(new MouseEvent('mouseenter'))
    await settle()
    expect(itemTexts(1)).toEqual(['기본 설계', '프로토콜', '로직', 'DB설계', '조사', '작업계획', '작업', '테스트', '확인'].map((label) => `＋ ${label}`))
    expect(invariants(center)).toBe(before)
  })

  it('카드 추가 adds the chosen card at the right-clicked point through the drop path (save, undo, redo)', async () => {
    const { w, center, drafts } = await mountHarness({ saveDelay: 10 })
    rightClick(pane(w), 120, 90)
    await settle()
    const at = (wrapperMenuTarget() as { canvas: { x: number; y: number } }).canvas
    item('canvas.addCard').dispatchEvent(new MouseEvent('mouseenter'))
    await settle()
    item('canvas.addCard:task', 1).click()
    await settle()
    expect(menuEl()).toBeNull()
    const added = graphOf(center).nodes.filter((node) => node.id.startsWith('tmp_'))
    expect(added).toHaveLength(1)
    expect([added[0].x, added[0].y, added[0].kind]).toEqual([at.x, at.y, 'task'])
    await new Promise((resolve) => setTimeout(resolve, 30))
    await settle()
    expect(drafts.length).toBeGreaterThan(0)
    expect(center.undoEdit('f1')).toBe(true)
    expect(graphOf(center).nodes.filter((node) => node.x === at.x && node.y === at.y)).toHaveLength(0)
    expect(center.redoEdit('f1')).toBe(true)
    expect(graphOf(center).nodes.filter((node) => node.x === at.x && node.y === at.y)).toHaveLength(1)
  })

  it('전체 선택 runs the Edit menu path: select mode on, every card selected, nothing else changed', async () => {
    const { w, center, calls } = await mountHarness()
    const positions = graphOf(center).nodes.map((node) => [node.id, node.x, node.y])
    rightClick(pane(w))
    await settle()
    item('canvas.selectAll').click()
    await settle()
    expect(calls.selectAll).toEqual(['f1'])
    expect(center.selectMode.value).toBe(true)
    expect(center.state.views.f1.selectedNodeIds).toEqual(['gn_a', 'gn_b', 'gn_c', 'gn_lock'])
    expect(graphOf(center).nodes.map((node) => [node.id, node.x, node.y])).toEqual(positions)
    expect(center.state.edits.f1?.canUndo ?? false).toBe(false)
  })

  it('전체 선택 is disabled while the Edit menu would refuse it', async () => {
    const { w } = await mountHarness({ canSelectAll: false })
    rightClick(pane(w))
    await settle()
    expect(item('canvas.selectAll').getAttribute('aria-disabled')).toBe('true')
    item('canvas.selectAll').click()
    await settle()
    expect(menuEl()).not.toBeNull()
  })

  it('a card: 삭제 removes that card through the Delete key path (undo restores it)', async () => {
    const { w, center } = await mountHarness()
    const before = invariants(center)
    rightClick(card(w, 'gn_c'))
    await settle()
    expect(invariants(center)).toBe(before)
    expect(itemTexts()).toEqual(['× 삭제'])
    expect(item('node.delete').classList.contains('danger')).toBe(true)
    item('node.delete').click()
    await settle()
    expect(graphOf(center).nodes.map((node) => node.id).sort()).toEqual(['gn_a', 'gn_b', 'gn_lock'])
    expect(graphOf(center).edges.map((edge) => edge.id)).toEqual(['ge_ab'])
    expect(center.undoEdit('f1')).toBe(true)
    expect(graphOf(center).nodes.map((node) => node.title).sort()).toEqual(['A', 'B', 'C', 'L'])
  })

  it('a locked card / a progressed line open an all-disabled menu: no command, Escape closes and returns focus', async () => {
    const { w, center } = await mountHarness()
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    for (const [element, key] of [[card(w, 'gn_lock'), 'node.delete'], [line(w, 'ge_ab'), 'edge.disconnect']] as const) {
      trigger.focus()
      const before = invariants(center)
      const event = rightClick(element)
      await settle()
      expect(event.defaultPrevented).toBe(true)
      expect(items().map((entry) => [entry.dataset.key, entry.getAttribute('aria-disabled')])).toEqual([[key, 'true']])
      expect(document.activeElement).toBe(item(key))
      item(key).click()
      item(key).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      item(key).dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }))
      await settle()
      expect(menuEl()).not.toBeNull()
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
      await settle()
      expect(menuEl()).toBeNull()
      expect(document.activeElement).toBe(trigger)
      expect(invariants(center)).toBe(before)
      expect(graphOf(center).nodes).toHaveLength(4)
      expect(graphOf(center).edges).toHaveLength(2)
    }
  })

  it('a line not gone through: 연결 해제 removes it', async () => {
    const { w, center } = await mountHarness()
    rightClick(line(w, 'ge_bc'))
    await settle()
    expect(itemTexts()).toEqual(['⌁ 연결 해제'])
    item('edge.disconnect').click()
    await settle()
    expect(graphOf(center).edges.map((edge) => edge.id)).toEqual(['ge_ab'])
  })

  it('a not editable Canvas: 카드 추가 shows disabled and runs nothing', async () => {
    const { w, center } = await mountHarness({ editable: false })
    rightClick(pane(w))
    await settle()
    expect(item('canvas.addCard').getAttribute('aria-disabled')).toBe('true')
    item('canvas.addCard').click()
    await settle()
    expect(document.querySelector('[data-designer-context-menu] [data-level="1"]')).toBeNull()
    expect(graphOf(center).nodes).toHaveLength(4)
  })

  it('multi-selection: a selected card opens no menu and keeps the selection; a card outside is the single card menu', async () => {
    const { w, center } = await mountHarness()
    center.setSelectMode(true)
    center.selectNodes('f1', ['gn_a', 'gn_b'])
    await settle()
    const before = invariants(center)
    const event = rightClick(card(w, 'gn_b'))
    await settle()
    expect(menuEl()).toBeNull()
    expect(event.defaultPrevented).toBe(false)
    expect(invariants(center)).toBe(before)
    expect(graphOf(center).nodes).toHaveLength(4)

    rightClick(card(w, 'gn_c'))
    await settle()
    expect(itemTexts()).toEqual(['× 삭제'])
    item('node.delete').click()
    await settle()
    expect(graphOf(center).nodes.map((node) => node.id).sort()).toEqual(['gn_a', 'gn_b', 'gn_lock'])
    expect(center.state.views.f1.selectedNodeIds).toEqual(['gn_a', 'gn_b'])
    expect(center.selectMode.value).toBe(true)
  })

  it('a selection or select mode change while the menu is open closes it; nothing runs', async () => {
    const { w, center } = await mountHarness()
    rightClick(card(w, 'gn_c'))
    await settle()
    const stale = item('node.delete')
    center.selectNode('f1', 'gn_a')
    await settle()
    expect(menuEl()).toBeNull()
    stale.click()
    await settle()
    expect(graphOf(center).nodes).toHaveLength(4)

    rightClick(pane(w))
    await settle()
    center.setSelectMode(true)
    await settle()
    expect(menuEl()).toBeNull()
  })

  it('re-checks before running: a card turned not removable after opening is not removed', async () => {
    const { w, center, source } = await mountHarness()
    rightClick(card(w, 'gn_c'))
    await settle()
    // A background re-read answers the same cards on a draft that is not editable any more; the menu stays open.
    vi.mocked(source.loadGraph).mockImplementation(async (_p: string, flowId: string) =>
      ({ ...draftGraph(flowId), draft: { ...draftGraph(flowId).draft!, editable: false } }) as FlowGraph)
    center.refreshFlow('f1')
    await settle()
    expect(graphOf(center).draft?.editable).toBe(false)
    expect(menuEl()).not.toBeNull()
    item('node.delete').click()
    await settle()
    expect(graphOf(center).nodes.map((node) => node.id)).toContain('gn_c')
  })

  it('an area hidden (Dock collapsed, mode / tool switched), another active Flow or Project closes the menu', async () => {
    const { w, center, layout } = await mountHarness({ flows: ['f1', 'f2'] })
    rightClick(pane(w))
    await settle()
    expect(menuEl()).not.toBeNull()
    layout.value = 'left:collapsed'
    await settle()
    expect(menuEl()).toBeNull()
    rightClick(tabEl(w, 'f1'))
    await settle()
    expect(menuEl()).not.toBeNull()
    center.activateFlow('f1')
    await settle()
    expect(menuEl()).toBeNull()
    rightClick(tabEl(w, 'f1'))
    await settle()
    center.setProject('q')
    await settle()
    expect(menuEl()).toBeNull()
  })

  it('a graph change that removes the target closes the menu', async () => {
    const { w, center } = await mountHarness()
    rightClick(card(w, 'gn_c'))
    await settle()
    center.removeNode('f1', 'gn_c')
    await settle()
    expect(menuEl()).toBeNull()
  })

  it('carries the card\'s existing run menu (Hold 설정) into the card menu, run through the session', async () => {
    const { w, holds } = await mountHarness({ hold: true })
    rightClick(card(w, 'gn_c'))
    await settle()
    const keys = items().map((entry) => entry.dataset.key)
    expect(keys).toEqual(['node.delete', 'node.run.holdSet'])
    expect(menuEl()!.querySelectorAll('[data-level="0"] [role="separator"]')).toHaveLength(1)
    expect(item('node.run.holdSet').textContent).toContain(i18n.global.t('gui.designer.center.card_menu.hold_set'))
    item('node.run.holdSet').click()
    await settle()
    expect(holds).toEqual([['gn_c', true]])
  })

  it('multi-selection on a server source (Hold, Time Machine): a selected card opens no menu, runs nothing (§3.6)', async () => {
    const { w, center, holds, runs, source } = await mountHarness({ server: true })
    // The source does offer the run menu: a single card shows it (gn_a is done, 되돌리기 can run).
    rightClick(card(w, 'gn_a'))
    await settle()
    expect(items().map((entry) => entry.dataset.key)).toEqual(['node.delete', 'node.run.holdSet', 'node.run.timeMachine'])
    expect(item('node.run.timeMachine').getAttribute('aria-disabled')).not.toBe('true')
    rightClick(card(w, 'gn_c'))
    await settle()
    expect(items().map((entry) => entry.dataset.key)).toEqual(['node.delete', 'node.run.holdSet'])
    expect(item('node.run.holdSet').getAttribute('aria-disabled')).not.toBe('true')

    center.setSelectMode(true)
    center.selectNodes('f1', ['gn_a', 'gn_c'])
    await settle()
    expect(menuEl()).toBeNull()
    const before = invariants(center)
    for (const id of ['gn_a', 'gn_c']) {
      const event = rightClick(card(w, id))
      await settle()
      expect(menuEl()).toBeNull()
      expect(event.defaultPrevented).toBe(false)
    }
    expect(invariants(center)).toBe(before)
    expect(graphOf(center).nodes).toHaveLength(4)
    expect(holds).toEqual([])
    expect(runs).toEqual([])
    expect(source.writeCardHold).not.toHaveBeenCalled()
    expect(source.timeMachine).not.toHaveBeenCalled()
    expect(center.timeMachine.value).toBeNull()
  })
})

describe('Designer context menu — ① project tree row against the tree (0006-D §3.3, §3.4)', () => {
  it('새 플로우 runs only while the Explorer tree holds the Group: checked on open, while open and right before running', async () => {
    const { calls } = await mountHarness()
    const row = document.createElement('div')
    document.body.appendChild(row)
    const inTree = ref(true)
    const open = () => {
      let opened = false
      row.addEventListener('contextmenu', (event) => {
        opened = harnessMenu!.api.project(event, { kind: 'group', projectId: 'p', groupId: 'module:p:ui' }, row, () => inTree.value)
      }, { once: true })
      rightClick(row)
      return opened
    }
    expect(open()).toBe(true)
    await settle()
    expect(items().map((entry) => entry.dataset.key)).toEqual(['group.newFlow'])
    // Gone from the tree and chosen in the same tick (before the open menu's check reacts): nothing runs.
    inTree.value = false
    item('group.newFlow').click()
    await settle()
    expect(menuEl()).toBeNull()
    expect(calls.flowCreate).toEqual([])

    // Gone while the menu is open: it closes; a stale item runs nothing.
    inTree.value = true
    expect(open()).toBe(true)
    await settle()
    const stale = item('group.newFlow')
    inTree.value = false
    await settle()
    expect(menuEl()).toBeNull()
    stale.click()
    await settle()
    expect(calls.flowCreate).toEqual([])

    // A row not in the tree opens no menu; with the Group there 새 플로우 runs on it.
    expect(open()).toBe(false)
    expect(menuEl()).toBeNull()
    inTree.value = true
    expect(open()).toBe(true)
    await settle()
    item('group.newFlow').click()
    await settle()
    expect(calls.flowCreate).toEqual(['module:p:ui'])
  })
})

describe('Designer context menu — Canvas right-button guard (0006-D §3.1 (가), §9.3 ④)', () => {
  function spyOn(element: Element) {
    const seen: string[] = []
    for (const type of ['pointerdown', 'pointerup', 'mousedown', 'mousemove', 'mouseup']) {
      element.addEventListener(type, (event) => seen.push(`${type}:${(event as MouseEvent).button}`))
    }
    return seen
  }
  const press = (element: Element, type: string, button: number, buttons: number) =>
    element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, button, buttons, clientX: 30, clientY: 30 }))

  it('a right press, its held move and release never reach Vue Flow; the press is relayed once to the document', async () => {
    const { w, center } = await mountHarness()
    const before = invariants(center)
    const onCard = spyOn(card(w, 'gn_a'))
    const onPane = spyOn(pane(w))
    const relayed: string[] = []
    const relay = (event: Event) => relayed.push(event.type)
    document.addEventListener('mousedown', relay)
    for (const target of [card(w, 'gn_a'), pane(w)]) {
      press(target, 'pointerdown', 2, 2)
      press(target, 'mousedown', 2, 2)
      press(target, 'mousemove', 0, 2)
      press(target, 'mouseup', 2, 0)
    }
    document.removeEventListener('mousedown', relay)
    expect(onCard).toEqual([])
    expect(onPane).toEqual([])
    expect(relayed).toEqual(['mousedown', 'mousedown'])
    // The left button still reaches the card and the pane (pointer events: Vue Flow's own d3 handlers listen to the
    // mouse events, which a synthetic jsdom event without a view cannot drive).
    press(card(w, 'gn_a'), 'pointerdown', 0, 1)
    press(card(w, 'gn_a'), 'pointerup', 0, 0)
    expect(onCard).toEqual(['pointerdown:0', 'pointerup:0'])
    expect(onPane).toEqual(['pointerdown:0', 'pointerup:0'])
    await settle()
    expect(invariants(center)).toBe(before)
  })

  it('a Canvas mounted outside the Designer (no menu provided) has no guard and no menu', async () => {
    const graph = draftGraph('f1')
    const w = mount(FlowCanvas, {
      props: { flowId: 'f1', flowName: 'F1', graph, viewport: null, selectedNodeId: null, sample: false },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await settle()
    try {
      const onCard = spyOn(w.get('.vue-flow__node[data-id="gn_a"]').element)
      press(w.get('.vue-flow__node[data-id="gn_a"]').element, 'pointerdown', 2, 2)
      expect(onCard).toEqual(['pointerdown:2'])
      const event = rightClick(w.get('.vue-flow__pane').element)
      expect(event.defaultPrevented).toBe(false)
      expect(menuEl()).toBeNull()
    } finally {
      w.unmount()
    }
  })
})

describe('Designer context menu — Flow tabs (0006-D §9.3 ③)', () => {
  it('닫기 on an inactive tab closes that tab only and keeps the active one', async () => {
    const { w, center } = await mountHarness({ flows: ['f1', 'f2', 'f3'] })
    center.activateFlow('f1')
    await settle()
    rightClick(tabEl(w, 'f2'))
    await settle()
    expect(center.state.activeFlowId).toBe('f1')
    expect(itemTexts()).toEqual(['× 닫기', '⊟ 다른 탭 닫기', '☒ 전체 닫기'])
    item('tab.close').click()
    await settle()
    expect(center.state.tabs.map((tab) => tab.flowId)).toEqual(['f1', 'f3'])
    expect(center.state.activeFlowId).toBe('f1')
  })

  it('다른 탭 닫기 / 전체 닫기 close through the session one tab at a time', async () => {
    const { w, center } = await mountHarness({ flows: ['f1', 'f2', 'f3'] })
    const closeFlow = vi.spyOn(center, 'closeFlow')
    rightClick(tabEl(w, 'f2'))
    await settle()
    item('tab.closeOthers').click()
    await settle()
    expect(closeFlow.mock.calls.map(([id]) => id)).toEqual(['f1', 'f3'])
    expect(center.state.tabs.map((tab) => tab.flowId)).toEqual(['f2'])
    rightClick(tabEl(w, 'f2'))
    await settle()
    expect(item('tab.closeOthers').getAttribute('aria-disabled')).toBe('true')
    item('tab.closeAll').click()
    await settle()
    expect(center.state.tabs).toEqual([])
    expect(center.state.activeFlowId).toBeNull()
  })

  it('the blank strip has 전체 닫기 only enabled; a closed target tab closes the menu', async () => {
    const { w, center } = await mountHarness({ flows: ['f1', 'f2'] })
    rightClick(w.get('[data-test="designer-flow-tabs"]').element)
    await settle()
    expect(items().map((entry) => [entry.dataset.key, entry.getAttribute('aria-disabled')])).toEqual([
      ['tab.close', 'true'], ['tab.closeOthers', 'true'], ['tab.closeAll', 'false'],
    ])
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await settle()
    rightClick(tabEl(w, 'f1'))
    await settle()
    center.closeFlow('f1')
    await settle()
    expect(menuEl()).toBeNull()
  })
})

describe('Designer context menu — Palette (0006-D §9.3 ⑤)', () => {
  const paletteCard = (w: VueWrapper<any>, id: string) => w.get(`[data-test="designer-palette-item"][data-item-id="${id}"]`).element

  it('카드 추가 selects the card for the Canvas click placement only when chosen', async () => {
    const { w, calls } = await mountHarness()
    rightClick(paletteCard(w, 'protocol'))
    await settle()
    expect(calls.palette).toEqual([])
    expect(itemTexts()).toEqual(['＋ 카드 추가'])
    item('palette.addCard').click()
    await settle()
    expect(calls.palette.map((picked) => picked.itemId)).toEqual(['protocol'])
  })

  it('cannot place on a not editable Canvas: the menu opens with 카드 추가 disabled', async () => {
    const { w, calls } = await mountHarness({ editable: false })
    rightClick(paletteCard(w, 'task'))
    await settle()
    expect(item('palette.addCard').getAttribute('aria-disabled')).toBe('true')
    item('palette.addCard').click()
    await settle()
    expect(calls.palette).toEqual([])
  })

  it('the blank Palette is no target: no menu, the browser menu stays, an open menu closes', async () => {
    const { w } = await mountHarness()
    rightClick(pane(w))
    await settle()
    expect(menuEl()).not.toBeNull()
    const event = rightClick(w.get('[data-test="designer-palette"]').element)
    await settle()
    expect(event.defaultPrevented).toBe(false)
    expect(menuEl()).toBeNull()
  })
})

function wrapperMenuTarget() {
  // The view shows the menu the host owns; the snapshot is read back from the harness's connector through the DOM-less
  // route: the menu panel position is the screen point and the Canvas point is what the session receives on add.
  const harness = wrapper!.findComponent(DesignerContextMenu)
  return (harness.props('host') as ReturnType<typeof useDesignerContextMenu>['host']).current.value!.model.target
}
