/**
 * flowgate.v02.0059 T#2 (0011-CH contracts C / D) — on the real Shell (project store, Center session, server
 * Graph source, real Vue Flow Canvas; only `@shared/api` is mocked, as DesignerShellSelection.0059):
 * - 정렬 / 배치 ▾ (Toolbar) and 편집 › 정렬 / 배치 move the selected cards on the Canvas; items wait for enough
 *   selected cards; 자동 배치 lays the cards out by the server rules and spacing, whatever the density.
 * - 배치 밀도 (flowgate.v02.0059 0017-T: the card drag step, moveDensity.0059): choosing one moves no card and
 *   starts no 자동 배치; the Toolbar radios and the View menu show one value.
 * - every such action, a card drop of several cards included, is one 실행 취소 (Toolbar, Edit menu, Ctrl+Z —
 *   also on a Flow with no editable draft), and the cards' saved positions follow.
 * jsdom replays no d3 drag: a drop is the Vue Flow `nodeDragStop` event, as FlowCanvasLayout.0045 does.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow, type GraphNode } from '@vue-flow/core'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'

const getRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  putRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

// --- Canvas environment for Vue Flow under jsdom (this file only) ---------------------------
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

function n(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

const TREE = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
  n({ id: 'p.ui.0003', parent_id: 'module:p:ui', node_type: 'group', label: 'Legacy', number: '0003' }),
]

/** Three movable document cards where the user left them; p.ui.0003 is a v0.1 Flow (no editable draft). */
function canvasResponse(flowId: string) {
  const legacy = flowId === 'p.ui.0003'
  const card = (seq: number, type: string, kind: string, x: number, y: number) => ({
    node_key: `doc:${flowId}.000${seq}-${type}`, identity_source: 'document', origin: 'document', kind, doc_type: type,
    title: `${type} card`, state: 'done', position: { x, y, source: 'saved' }, editable: { move: true, structure: false },
    document: null, slot: null, graph_node: null, node_run: null,
  })
  return {
    ok: true,
    flow_id: flowId,
    project_id: 'p',
    ownership: { state: legacy ? 'v01' : 'unassigned', sequence_root_doc_id: null, graph_revision_no: null, assigned_at: null },
    projection: legacy ? 'sequence' : 'documents',
    view: null,
    graph: { draft: null, latest_revision_no: null, root_binding: null },
    nodes: [card(1, 'R', 'requirement', 0, 0), card(2, 'N', 'research', 300, 120), card(3, 'D', 'design', 90, 400)],
    edges: [],
    external_relations: [],
    viewport: null,
    run: null,
    identity: { sequence_tag: null, rematched: false, retired_node_keys: [] },
    collapsed: { applied: [], ignored_keys: [], hidden_counts: {} },
  }
}

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  putRequest.mockReset()
  putRequest.mockImplementation(async () => ({ data: { ok: true, saved_positions: 1, viewport_saved: false, ignored: [] } }))
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (url === '/api/v1/projects/p/groups/tree') return { data: { data: { nodes: TREE } } }
    if (/\/git\/status$/.test(url)) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes)$/.exec(url)
    if (group && group[2] === 'tree') return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: [] } } }
    if (group) return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: 'v0.2', changes: [] } } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    const canvas = /^\/api\/v1\/flows\/([^/]+)\/canvas$/.exec(url)
    if (canvas) return { data: canvasResponse(decodeURIComponent(canvas[1])) }
    throw new Error(`unexpected request ${url}`)
  })
})

let wrapper: VueWrapper<any> | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

async function settle() {
  for (let i = 0; i < 6; i += 1) await flushPromises()
}

async function mountShell() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project('p', 'Alpha'))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  return wrapper
}

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = w.findAll('[data-explorer-row]').find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
  if (!found) throw new Error(`row not rendered: ${label}`)
  return found.find('.explorer-row')
}

async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await flushPromises()
  }
  await settle()
}

async function openFlow(w: VueWrapper<any>, label: string) {
  if (!w.findAll('[data-explorer-row]').some((r) => r.find('[data-test="project-explorer-label"]').text() === label)) {
    await doubleClick(w, 'UI 개선')
  }
  await doubleClick(w, label)
}

const R = 'doc:p.ui.0002.0001-R'
const N = 'doc:p.ui.0002.0002-N'
const D = 'doc:p.ui.0002.0003-D'

/** Where the Canvas draws a card (its Vue Flow transform). */
function cardAt(w: VueWrapper<any>, id: string): [number, number] {
  const style = w.get(`.vue-flow__node[data-id="${id}"]`).attributes('style') ?? ''
  const match = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(style)
  if (!match) throw new Error(`no position for ${id}: ${style}`)
  return [Number(match[1]), Number(match[2])]
}
const cardsAt = (w: VueWrapper<any>, ids = [R, N, D]) => ids.map((id) => cardAt(w, id))

const tool = (w: VueWrapper<any>, command: string) => w.get(`[data-test="designer-tool"][data-command="${command}"]`)

async function openArrange(w: VueWrapper<any>) {
  if (!w.find('[data-test="designer-arrange-menu"]').exists()) await w.get('[data-tool="arrange"]').trigger('click')
  return w.get('[data-test="designer-arrange-menu"]')
}

async function editMenu(w: VueWrapper<any>) {
  await w.get('[data-test="designer-menu"][data-menu="edit"]').trigger('click')
  return w.get('[data-test="designer-menu-pop"][data-menu="edit"]')
}

async function editCommand(w: VueWrapper<any>, command: string) {
  await (await editMenu(w)).get(`[data-test="designer-menu-item"][data-command="${command}"]`).trigger('click')
  await settle()
}

async function chooseDensity(w: VueWrapper<any>, level: string, from: 'toolbar' | 'menu') {
  if (from === 'toolbar') {
    await w.get(`[data-test="designer-tool-density"] [data-density="${level}"]`).trigger('click')
  } else {
    await w.get('[data-test="designer-menu"][data-menu="view"]').trigger('click')
    await w.get('[data-test="designer-menu-sub"][data-sub="density"]').trigger('click')
    await w.get(`[data-test="designer-menu-density"][data-density="${level}"]`).trigger('click')
  }
  await settle()
}

/** The density the Toolbar radios and the View menu each show. */
async function shownDensity(w: VueWrapper<any>) {
  const radio = w.get('[data-test="designer-tool-density"] [aria-checked="true"]').attributes('data-density')
  await w.get('[data-test="designer-menu"][data-menu="view"]').trigger('click')
  await w.get('[data-test="designer-menu-sub"][data-sub="density"]').trigger('click')
  const menu = w.get('[data-test="designer-menu-density"][aria-checked="true"]').attributes('data-density')
  await w.get('[data-test="designer-menu"][data-menu="view"]').trigger('click')
  return [radio, menu]
}

const disabled = (el: { attributes: (name: string) => string | undefined }) => el.attributes('disabled') !== undefined

/** The positions the layout saves stored for the open Flow, last one per card. */
function storedPositions() {
  const out: Record<string, [number, number]> = {}
  for (const [, body] of putRequest.mock.calls as Array<[string, { positions: Array<{ node_key: string; x: number; y: number }> }]>) {
    for (const p of body.positions) out[p.node_key] = [p.x, p.y]
  }
  return out
}
const waitSaves = () => new Promise((resolve) => setTimeout(resolve, 450))

describe('Designer Shell — 정렬 / 배치, 배치 밀도 and their 실행 취소 (flowgate.v02.0059 T#2)', () => {
  it('정렬 / 배치 ▾ waits for enough selected cards, then aligns them on the Canvas; one 실행 취소 brings them back', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
    let menu = await openArrange(w)
    const items = () => menu.findAll('[data-test="designer-arrange-item"]')
    // No card selected: only 자동 배치 can be chosen.
    expect(items().filter((el) => !disabled(el)).map((el) => el.attributes('data-arrange'))).toEqual(['edit.auto-layout'])
    await w.get('[data-tool="arrange"]').trigger('click')
    expect(w.find('[data-test="designer-arrange-menu"]').exists()).toBe(false)

    await editCommand(w, 'edit.select-all')
    menu = await openArrange(w)
    expect(items().every((el) => !disabled(el))).toBe(true)
    expect(disabled(tool(w, 'edit.undo'))).toBe(true)
    await menu.get('[data-arrange="edit.align-top"]').trigger('click')
    await settle()
    expect(w.find('[data-test="designer-arrange-menu"]').exists()).toBe(false)
    expect(cardsAt(w)).toEqual([[0, 0], [300, 0], [90, 0]])
    expect(disabled(tool(w, 'edit.undo'))).toBe(false)

    await tool(w, 'edit.undo').trigger('click')
    await settle()
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
    expect(disabled(tool(w, 'edit.redo'))).toBe(false)
    await tool(w, 'edit.redo').trigger('click')
    await settle()
    expect(cardsAt(w)).toEqual([[0, 0], [300, 0], [90, 0]])
    await waitSaves()
    expect(storedPositions()).toEqual({ [N]: [300, 0], [D]: [90, 0] })
  })

  it('편집 › 정렬 / 배치 is the same: 균등 분배 and 가운데 정렬 act on the selection, each one 실행 취소', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const sub = async () => {
      await (await editMenu(w)).get('[data-test="designer-menu-sub"][data-sub="arrange"]').trigger('click')
      return w.get('[data-test="designer-menu-subpop"][data-sub="arrange"]')
    }
    expect((await sub()).findAll('[data-command]').every(disabled)).toBe(true)
    await w.get('[data-test="designer-menu"][data-menu="edit"]').trigger('click')
    await editCommand(w, 'edit.select-all')
    await (await sub()).get('[data-command="edit.distribute-horizontal"]').trigger('click')
    await settle()
    // Along x: R 0, D 90, N 300 -> D goes halfway.
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [150, 400]])
    await (await sub()).get('[data-command="edit.align-center"]').trigger('click')
    await settle()
    expect(cardsAt(w)).toEqual([[150, 0], [150, 120], [150, 400]])
    await editCommand(w, 'edit.undo')
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [150, 400]])
    await editCommand(w, 'edit.undo')
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
  })

  // flowgate.v02.0059 0017-T: the density is the step a card drag moves in (moveDensity.0059); choosing it lays
  // nothing out.
  it('배치 밀도 moves no card: no 자동 배치, no save, no 실행 취소, no zoom change; Toolbar and View menu show one value', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const left = [[0, 0], [300, 120], [90, 400]]
    const canvasReads = () => getRequest.mock.calls.filter(([url]) => /\/canvas$/.test(String(url))).length
    const reads = canvasReads()
    const zoom = w.get('[data-test="designer-tool-zoom"]').attributes('data-zoom')
    expect(await shownDensity(w)).toEqual(['normal', 'normal'])
    for (const [level, from] of [
      ['wide', 'toolbar'], ['normal', 'menu'], ['free', 'toolbar'], ['narrow', 'menu'], ['wide', 'menu'], ['free', 'menu'],
    ] as const) {
      await chooseDensity(w, level, from)
      expect(cardsAt(w)).toEqual(left)
      expect(await shownDensity(w)).toEqual([level, level])
      expect(disabled(tool(w, 'edit.undo'))).toBe(true)
      expect(w.get('[data-test="designer-tool-zoom"]').attributes('data-zoom')).toBe(zoom)
    }
    expect(canvasReads()).toBe(reads)
    await waitSaves()
    expect(storedPositions()).toEqual({})
  })

  it('자동 배치 (Toolbar ▾ and Edit menu) lays the cards out at the server spacing whatever the density', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    for (const [level, from] of [['wide', 'menu'], ['free', 'toolbar'], ['normal', 'menu'], ['narrow', 'toolbar']] as const) {
      await chooseDensity(w, level, from === 'menu' ? 'toolbar' : 'menu')
      if (from === 'menu') await editCommand(w, 'edit.auto-layout')
      else {
        await (await openArrange(w)).get('[data-arrange="edit.auto-layout"]').trigger('click')
        await settle()
      }
      expect(cardsAt(w)).toEqual([[0, 0], [260, 0], [520, 0]])
      expect(await shownDensity(w)).toEqual([level, level])
      await tool(w, 'edit.undo').trigger('click')
      await settle()
      expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
    }
  })

  it('a drop of several cards is one 실행 취소, also by Ctrl+Z on a Flow with no editable draft', async () => {
    const w = await mountShell()
    await openFlow(w, 'Legacy')
    const ids = ['doc:p.ui.0003.0001-R', 'doc:p.ui.0003.0002-N', 'doc:p.ui.0003.0003-D']
    expect(get(w).attributes('data-editable')).toBe('false')
    const flow = useVueFlow('designer-canvas:p.ui.0003')
    // Dropped by (100, 60): whole steps of the default density 보통 (20, 0018-TR rev2), so the drop lands as dragged.
    const dropped = ids.slice(0, 2).map((id) => {
      const node = flow.findNode(id)!
      return { ...node, position: { x: node.position.x + 100, y: node.position.y + 60 } } as GraphNode
    })
    flow.emits.nodeDragStop({ event: new MouseEvent('mouseup'), node: dropped[0], nodes: dropped, intersections: undefined } as never)
    await settle()
    expect(cardsAt(w, ids)).toEqual([[100, 60], [400, 180], [90, 400]])
    expect(disabled(tool(w, 'edit.undo'))).toBe(false)
    await get(w).trigger('keydown', { key: 'z', ctrlKey: true })
    await settle()
    expect(cardsAt(w, ids)).toEqual([[0, 0], [300, 120], [90, 400]])
    expect(disabled(tool(w, 'edit.undo'))).toBe(true)
    await editCommand(w, 'edit.redo')
    expect(cardsAt(w, ids)).toEqual([[100, 60], [400, 180], [90, 400]])
    await waitSaves()
    expect(storedPositions()).toEqual({ [ids[0]]: [100, 60], [ids[1]]: [400, 180] })
  })
})

function get(w: VueWrapper<any>) {
  return w.get('[data-test="designer-canvas"]')
}
