/**
 * flowgate.v02.0059 0017-T — 배치 밀도 is the step a card drag moves in (넓게 / 보통 / 좁게 coarse to fine,
 * 자율형 none), not a layout:
 * - pure: DENSITY_MOVE_STEP (one place, on the server auto placement's 20 lattice), stepGroupMove (the dragged
 *   card on its nearest step point, one offset for every dragged card, so a group keeps its layout; none without a
 *   step), clampGroupToCanvas in whole steps and canvasDotGrid (0018-TR rework: the dots are the step lattice).
 * - on the real Shell (project store, Center session, server Graph source, real Vue Flow Canvas; only
 *   `@shared/api` is mocked, and its layout saves are kept and read back like the server's): the four densities
 *   move a dragged card in visibly different steps while it is dragged and where it is dropped, from the Toolbar
 *   radios and the View menu alike; a dragged selection moves by the same step and keeps its layout; choosing a
 *   density changes no card, Edge, MiniMap, saved position or zoom; a drop is one 실행 취소 / 다시 실행; the saved
 *   positions come back after the Designer is opened again.
 * - 0018-TR rework (rejection: the dots did not match the cards and did not change with the density): the Canvas
 *   dot grid is the density's step (40 / 20 / 10, none for 자율형) and follows pan / zoom, and a dropped card sits
 *   on a dot — checked through the drawn CSS background against Vue Flow's own transform, zoomed and panned.
 * jsdom replays no d3 drag: a drag is Vue Flow's own `nodeDragStart` / `nodeDrag` / `nodeDragStop` with the cards
 * where Vue Flow puts them (start + pointer offset), as DesignerShellArrange.0059 does for a drop.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useVueFlow, type GraphNode } from '@vue-flow/core'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import {
  AUTO_STACK_DY,
  AUTO_X_STEP,
  COLLISION_DY,
  DRAFT_ROW_Y_DOCUMENTS,
  FREE_DOC_ROW_GAP_GRAPH,
  FREE_DOC_ROW_Y_SEQUENCE,
} from '@/features/designer/center/arrange/autoLayout'
import {
  CANVAS_GRID_MIN_SCREEN_GAP,
  CANVAS_MAX_ZOOM,
  CANVAS_MIN_ZOOM,
  canvasDotGrid,
  clampGroupToCanvas,
  stepGroupMove,
} from '@/features/designer/center/canvasView'
import { DENSITY_MOVE_STEP, DESIGNER_DENSITIES } from '@/features/designer/top/designerTop'
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

describe('move step of the density (pure)', () => {
  it('is one constant: 넓게 40, 보통 20, 좁게 10 (coarse to fine) and none for 자율형', () => {
    expect(Object.keys(DENSITY_MOVE_STEP)).toEqual([...DESIGNER_DENSITIES])
    expect(DENSITY_MOVE_STEP).toEqual({ wide: 40, normal: 20, narrow: 10, free: null })
  })

  it('keeps a card the server placed on the server placement lattice (20)', () => {
    for (const spacing of [AUTO_X_STEP, AUTO_STACK_DY, FREE_DOC_ROW_Y_SEQUENCE, DRAFT_ROW_Y_DOCUMENTS, FREE_DOC_ROW_GAP_GRAPH, COLLISION_DY]) {
      expect(spacing % 20).toBe(0)
    }
    for (const step of [DENSITY_MOVE_STEP.wide!, DENSITY_MOVE_STEP.normal!]) expect(step % 20).toBe(0)
    expect(20 % DENSITY_MOVE_STEP.narrow!).toBe(0)
  })

  const card = (nodeId: string, x: number, y: number) => ({ nodeId, x, y })

  it('puts the dragged card on the nearest step point (the dot lattice), the same each way', () => {
    const start = (nodeId: string) => (nodeId === 'a' ? { x: 0, y: 0 } : null)
    expect(stepGroupMove([card('a', 29, -31)], start, 20)).toEqual([card('a', 20, -40)])
    expect(stepGroupMove([card('a', -29, 31)], start, 20)).toEqual([card('a', -20, 40)])
    expect(stepGroupMove([card('a', 10, -10)], start, 20)).toEqual([card('a', 20, -20)])
    expect(stepGroupMove([card('a', 9, -9)], start, 20)).toEqual([card('a', 0, 0)])
    expect(stepGroupMove([card('a', 47, 23)], start, 40)).toEqual([card('a', 40, 40)])
    expect(stepGroupMove([card('a', 47, 23)], start, 10)).toEqual([card('a', 50, 20)])
  })

  it('takes a card that started off the lattice onto it (0018-TR rework: the place is stepped, not the offset)', () => {
    const start = () => ({ x: 13, y: 7 })
    // Dragged by (29, -31) to (42, -24): on the 20 lattice at (40, -20) — an offset in whole steps would leave (33, -33).
    expect(stepGroupMove([card('a', 42, -24)], start, 20)).toEqual([card('a', 40, -20)])
    expect(stepGroupMove([card('a', 42, -24)], start, 40)).toEqual([card('a', 40, -40)])
    expect(stepGroupMove([card('a', 42, -24)], start, 10)).toEqual([card('a', 40, -20)])
    // Not dragged far enough to reach another point: the nearest one, also when that is not where it started.
    expect(stepGroupMove([card('a', 14, 8)], start, 20)).toEqual([card('a', 20, 0)])
  })

  it('moves a dragged group by one offset (the anchor card\'s), so the cards keep their relative layout', () => {
    const starts: Record<string, { x: number; y: number }> = { a: { x: 13, y: 7 }, b: { x: 108, y: 44 }, c: { x: 5, y: 301 } }
    const moved = [card('a', 13 + 27, 7 + 12), card('b', 108 + 27, 44 + 12), card('c', 5 + 27, 301 + 12)]
    const stepped = stepGroupMove(moved, (id) => starts[id], 20, 'b')
    // b (under the pointer) at (135, 56) goes to the step point (140, 60); a and c move by its offset (32, 16).
    expect(stepped).toEqual([card('a', 45, 23), card('b', 140, 60), card('c', 37, 317)])
    // Relative layout as before the drag; the cards are not snapped onto a grid one by one.
    expect([stepped[1].x - stepped[0].x, stepped[1].y - stepped[0].y]).toEqual([95, 37])
    expect([stepped[2].x - stepped[0].x, stepped[2].y - stepped[0].y]).toEqual([-8, 294])
  })

  it('pushes a group back inside the Canvas origin in whole steps, so a card on the lattice stays on it', () => {
    const at = (x: number, y: number) => ({ x, y })
    expect(clampGroupToCanvas([at(-40, 20), at(-7, 60)], 20)).toEqual([at(0, 20), at(33, 60)])
    // The left-most card is off the lattice: the push is 20, not 7, so the first card stays on the 20 lattice.
    expect(clampGroupToCanvas([at(20, 0), at(-7, -3)], 20)).toEqual([at(40, 20), at(13, 17)])
    expect(clampGroupToCanvas([at(-7, -3)], 40)).toEqual([at(33, 37)])
    // Inside the origin: unchanged; without a step: the plain push as before.
    expect(clampGroupToCanvas([at(20, 0), at(7, 3)], 20)).toEqual([at(20, 0), at(7, 3)])
    expect(clampGroupToCanvas([at(20, 0), at(-7, -3)], null)).toEqual([at(27, 3), at(0, 0)])
  })

  it('leaves every position as dragged without a step (자율형) or without a known start', () => {
    const moved = [card('a', 12.5, 7.25), card('b', 300.75, 41)]
    const starts: Record<string, { x: number; y: number }> = { a: { x: 0, y: 0 }, b: { x: 288, y: 34 } }
    expect(stepGroupMove(moved, (id) => starts[id], null)).toEqual(moved)
    expect(stepGroupMove(moved, (id) => starts[id], undefined)).toEqual(moved)
    expect(stepGroupMove(moved, (id) => (id === 'a' ? starts.a : null), 20)).toEqual(moved)
    expect(stepGroupMove([], (id) => starts[id], 20)).toEqual([])
  })
})

describe('Canvas dot grid of the move step (pure, 0018-TR rework)', () => {
  it('draws the step itself: 넓게 40, 보통 20, 좁게 10 apart; 자율형 (no step) draws no grid', () => {
    const view = { x: 37, y: -12, zoom: 1 }
    expect(canvasDotGrid(view, DENSITY_MOVE_STEP.wide)).toEqual({ gap: 40, size: 40, x: 37, y: -12 })
    expect(canvasDotGrid(view, DENSITY_MOVE_STEP.normal)).toEqual({ gap: 20, size: 20, x: 37, y: -12 })
    expect(canvasDotGrid(view, DENSITY_MOVE_STEP.narrow)).toEqual({ gap: 10, size: 10, x: 37, y: -12 })
    expect(canvasDotGrid(view, DENSITY_MOVE_STEP.free)).toBeNull()
    expect(canvasDotGrid(view, undefined)).toBeNull()
  })

  it('scales with the zoom and moves with the pan: the Graph origin is a dot at the Viewport translation', () => {
    expect(canvasDotGrid({ x: 120.5, y: 64, zoom: 1.15 }, 20)).toEqual({ gap: 20, size: 23, x: 120.5, y: 64 })
    expect(canvasDotGrid({ x: -300, y: 41, zoom: 2 }, 40)).toEqual({ gap: 40, size: 80, x: -300, y: 41 })
    expect(canvasDotGrid({ x: 0, y: 0, zoom: 0.5 }, 10)).toEqual({ gap: 10, size: 5, x: 0, y: 0 })
  })

  it('far zoomed out draws every second step only while the step would be closer than the screen minimum', () => {
    expect(CANVAS_GRID_MIN_SCREEN_GAP).toBe(4)
    expect(canvasDotGrid({ x: 0, y: 0, zoom: CANVAS_MIN_ZOOM }, 10)).toEqual({ gap: 20, size: 5, x: 0, y: 0 })
    expect(canvasDotGrid({ x: 0, y: 0, zoom: CANVAS_MIN_ZOOM }, 20)).toEqual({ gap: 20, size: 5, x: 0, y: 0 })
    expect(canvasDotGrid({ x: 0, y: 0, zoom: CANVAS_MIN_ZOOM }, 40)).toEqual({ gap: 40, size: 10, x: 0, y: 0 })
    // Every zoom of the Canvas range: the dots are lattice points of the step, never closer than the minimum.
    for (let zoom = CANVAS_MIN_ZOOM; zoom <= CANVAS_MAX_ZOOM; zoom += 0.05) {
      for (const step of [40, 20, 10]) {
        const grid = canvasDotGrid({ x: 0, y: 0, zoom }, step)!
        expect(grid.gap % step).toBe(0)
        expect(grid.size).toBeGreaterThanOrEqual(CANVAS_GRID_MIN_SCREEN_GAP)
        if (step * zoom >= CANVAS_GRID_MIN_SCREEN_GAP) expect(grid.gap).toBe(step)
      }
    }
  })
})

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
]

const FLOW = 'p.ui.0002'
const R = `doc:${FLOW}.0001-R`
const N = `doc:${FLOW}.0002-N`
const D = `doc:${FLOW}.0003-D`
const ALL = [R, N, D]
const LEFT: Record<string, [number, number]> = { [R]: [0, 0], [N]: [300, 120], [D]: [90, 400] }

/** The server's saved layout of the Flow: what PUT /canvas/layout stored, read back by the next Canvas read. */
let serverSaved: Record<string, [number, number]> = {}

/** Three movable document cards (R → N joined by an execution Edge), at their saved positions if any. */
function canvasResponse(flowId: string) {
  const card = (key: string, type: string, kind: string) => {
    const [x, y] = serverSaved[key] ?? LEFT[key]
    return {
      node_key: key, identity_source: 'document', origin: 'document', kind, doc_type: type,
      title: `${type} card`, state: 'done', position: { x, y, source: 'saved' }, editable: { move: true, structure: false },
      document: null, slot: null, graph_node: null, node_run: null,
    }
  }
  return {
    ok: true,
    flow_id: flowId,
    project_id: 'p',
    ownership: { state: 'unassigned', sequence_root_doc_id: null, graph_revision_no: null, assigned_at: null },
    projection: 'documents',
    view: null,
    graph: { draft: null, latest_revision_no: null, root_binding: null },
    nodes: [card(R, 'R', 'requirement'), card(N, 'N', 'research'), card(D, 'D', 'design')],
    edges: [{
      edge_id: `seq:${R}>${N}`, edge_class: 'execution', origin: 'sequence',
      source: R, target: N, relation: null, condition: null, editable: false,
    }],
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
  serverSaved = {}
  getRequest.mockReset()
  putRequest.mockReset()
  putRequest.mockImplementation(async (url: string, body: { positions?: Array<{ node_key: string; x: number; y: number }> }) => {
    for (const p of body.positions ?? []) serverSaved[p.node_key] = [p.x, p.y]
    return { data: { ok: true, saved_positions: body.positions?.length ?? 0, viewport_saved: false, ignored: [] } }
  })
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

async function openCanvasFlow(w: VueWrapper<any>) {
  await doubleClick(w, 'UI 개선')
  await doubleClick(w, 'Canvas')
}

/** Where the Canvas draws a card (its Vue Flow transform). */
function cardAt(w: VueWrapper<any>, id: string): [number, number] {
  const style = w.get(`.vue-flow__node[data-id="${id}"]`).attributes('style') ?? ''
  const match = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(style)
  if (!match) throw new Error(`no position for ${id}: ${style}`)
  return [Number(match[1]), Number(match[2])]
}
const cardsAt = (w: VueWrapper<any>, ids = ALL) => Object.fromEntries(ids.map((id) => [id, cardAt(w, id)]))
const moved = (by: [number, number], ids = ALL) =>
  Object.fromEntries(ids.map((id) => [id, [LEFT[id][0] + by[0], LEFT[id][1] + by[1]]]))

/** The MiniMap's card boxes and Edge lines, as drawn. */
function miniMap(w: VueWrapper<any>) {
  const nodes = Object.fromEntries(
    w.findAll('[data-test="designer-minimap-node"]').map((el) => [el.attributes('data-node-id'), [Number(el.attributes('x')), Number(el.attributes('y'))]]),
  )
  const edges = w.findAll('[data-test="designer-minimap-edge"]').map((el) => ['x1', 'y1', 'x2', 'y2'].map((a) => Number(el.attributes(a))))
  return { nodes, edges }
}

const tool = (w: VueWrapper<any>, command: string) => w.get(`[data-test="designer-tool"][data-command="${command}"]`)
const disabled = (el: { attributes: (name: string) => string | undefined }) => el.attributes('disabled') !== undefined
const canvas = (w: VueWrapper<any>) => w.get('[data-test="designer-canvas"]')
const shownZoom = (w: VueWrapper<any>) => w.get('[data-test="designer-tool-zoom"]').attributes('data-zoom')

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

async function editCommand(w: VueWrapper<any>, command: string) {
  await w.get('[data-test="designer-menu"][data-menu="edit"]').trigger('click')
  await w.get(`[data-test="designer-menu-pop"][data-menu="edit"] [data-test="designer-menu-item"][data-command="${command}"]`).trigger('click')
  await settle()
}

/**
 * Drags `ids` (the first under the pointer) by a pointer offset the way Vue Flow does: drag start, then each
 * move puts every dragged card at its start + the pointer offset so far and reports the drag; the drop follows.
 * Returns the cards as shown halfway and at the end of the drag (before the drop).
 */
async function drag(w: VueWrapper<any>, ids: readonly string[], by: [number, number]) {
  const flow = useVueFlow(`designer-canvas:${FLOW}`)
  const nodes = ids.map((id) => flow.findNode(id)!) as GraphNode[]
  const starts = nodes.map((node) => ({ ...node.position }))
  const params = (event: MouseEvent) => ({ event, node: nodes[0], nodes, intersections: undefined }) as never
  flow.emits.nodeDragStart(params(new MouseEvent('mousedown')))
  const shown: Array<Record<string, [number, number]>> = []
  for (const part of [0.5, 1]) {
    nodes.forEach((node, index) => {
      node.position = { x: starts[index].x + by[0] * part, y: starts[index].y + by[1] * part }
    })
    flow.emits.nodeDrag(params(new MouseEvent('mousemove')))
    await settle()
    shown.push(cardsAt(w, ids))
  }
  flow.emits.nodeDragStop(params(new MouseEvent('mouseup')))
  await settle()
  return { halfway: shown[0], end: shown[1] }
}

const waitSaves = () => new Promise((resolve) => setTimeout(resolve, 450))

/** The Canvas dot grid as drawn (null = none): its numbers and the CSS background that places the dots. */
function gridOf(w: VueWrapper<any>) {
  const el = w.find('[data-test="designer-canvas-grid"]')
  if (!el.exists()) return null
  const style = (el.element as HTMLElement).style
  const [x, y] = el.attributes('data-grid-origin')!.split(' ').map(Number)
  return {
    gap: Number(el.attributes('data-grid-gap')),
    size: Number(el.attributes('data-grid-size')),
    x,
    y,
    backgroundSize: style.backgroundSize,
    backgroundPosition: style.backgroundPosition,
  }
}

/** Vue Flow's own transform of the Canvas — what the cards are drawn with. */
const viewOf = () => ({ ...useVueFlow(`designer-canvas:${FLOW}`).viewport.value })

/**
 * Whether a Graph point (a card's top-left corner) is drawn on a dot: the screen point Vue Flow draws it at
 * (translation + point × zoom) against the dot centres the CSS background draws (its position + half a tile,
 * then every tile).
 */
function onDot(w: VueWrapper<any>, point: readonly [number, number]): boolean {
  const grid = gridOf(w)
  if (!grid) return false
  const view = viewOf()
  const [left, top] = grid.backgroundPosition.split(' ').map((v) => Number.parseFloat(v))
  const tile = Number.parseFloat(grid.backgroundSize.split(' ')[0])
  return [
    [view.x + point[0] * view.zoom, left],
    [view.y + point[1] * view.zoom, top],
  ].every(([screen, start]) => {
    const tiles = (screen - (start + tile / 2)) / tile
    return Math.abs(tiles - Math.round(tiles)) < 1e-6
  })
}

async function zoomTo(w: VueWrapper<any>, percent: string) {
  await w.get('[data-test="designer-tool-zoom"]').setValue(percent)
  await settle()
}

describe('Designer Shell — 배치 밀도 is the card drag step (flowgate.v02.0059 0017-T)', () => {
  // A drag of R by (47, 23), halfway at (23.5, 11.5): each density's step, from the Toolbar or the View menu.
  const CASES = [
    ['wide', 'toolbar', '40', [40, 0], [40, 40]],
    ['normal', 'menu', '20', [20, 20], [40, 20]],
    ['narrow', 'toolbar', '10', [20, 10], [50, 20]],
    ['free', 'menu', '', [23.5, 11.5], [47, 23]],
  ] as const

  it.each(CASES)('%s (%s): a dragged card moves in its step while dragged and where dropped', async (level, from, step, halfway, end) => {
    const w = await mountShell()
    await openCanvasFlow(w)
    expect(cardsAt(w)).toEqual(LEFT)
    await chooseDensity(w, level, from)
    expect(canvas(w).attributes('data-move-step')).toBe(step)
    expect(cardsAt(w)).toEqual(LEFT)
    const edgesBefore = miniMap(w).edges
    expect(edgesBefore).toHaveLength(1)

    const shown = await drag(w, [R], [47, 23])
    expect(shown.halfway[R]).toEqual(halfway)
    expect(shown.end[R]).toEqual(end)
    expect(cardsAt(w)).toEqual({ ...LEFT, [R]: end })
    expect(miniMap(w).nodes[R]).toEqual(end)
    // The Edge R → N follows R on the MiniMap (its R end moves by R's offset).
    expect(miniMap(w).edges).not.toEqual(edgesBefore)

    // One drop is one 실행 취소 / 다시 실행.
    await tool(w, 'edit.undo').trigger('click')
    await settle()
    expect(cardsAt(w)).toEqual(LEFT)
    await tool(w, 'edit.redo').trigger('click')
    await settle()
    expect(cardsAt(w)).toEqual({ ...LEFT, [R]: end })
    await waitSaves()
    expect(serverSaved).toEqual({ [R]: end })
  })

  it('the four densities move the same drag by visibly different steps; 자율형 forces none', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    const ends: Record<string, [number, number]> = {}
    for (const [level, from] of [['wide', 'menu'], ['normal', 'toolbar'], ['narrow', 'menu'], ['free', 'toolbar']] as const) {
      await chooseDensity(w, level, from)
      ends[level] = (await drag(w, [N], [-47, 23])).end[N]
      await tool(w, 'edit.undo').trigger('click')
      await settle()
      expect(cardsAt(w)).toEqual(LEFT)
    }
    // N from (300, 120) dragged to (253, 143): the nearest point of each lattice (40: x 240, not 300 - 40 = 260).
    expect(ends).toEqual({ wide: [240, 160], normal: [260, 140], narrow: [250, 140], free: [253, 143] })
  })

  it('a dragged selection moves by one step offset and keeps its layout; one 실행 취소 brings it all back', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    await editCommand(w, 'edit.select-all')
    expect(canvas(w).attributes('data-selected-node-ids')!.split(' ').sort()).toEqual([...ALL].sort())
    await chooseDensity(w, 'normal', 'toolbar')

    // N under the pointer, dragged by (67, 31): 보통 (20) moves every card by (60, 40).
    const shown = await drag(w, [N, R, D], [67, 31])
    expect(shown.halfway).toEqual(moved([40, 20], [N, R, D]))
    expect(cardsAt(w)).toEqual(moved([60, 40]))
    expect(miniMap(w).nodes).toEqual(moved([60, 40]))

    await editCommand(w, 'edit.undo')
    expect(cardsAt(w)).toEqual(LEFT)
    await editCommand(w, 'edit.redo')
    expect(cardsAt(w)).toEqual(moved([60, 40]))
    await waitSaves()
    expect(serverSaved).toEqual(moved([60, 40]))
  })

  it('choosing a density changes no card, Edge, MiniMap, saved position or zoom', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    await chooseDensity(w, 'wide', 'toolbar')
    await drag(w, [R], [47, 23])
    await waitSaves()
    const cards = cardsAt(w)
    const map = miniMap(w)
    const saves = putRequest.mock.calls.length
    const zoom = shownZoom(w)
    expect(map.edges).toHaveLength(1)
    expect(canvas(w).findAll('.vue-flow__edge')).toHaveLength(1)
    for (const [level, from] of [['free', 'menu'], ['narrow', 'toolbar'], ['normal', 'menu'], ['wide', 'menu']] as const) {
      await chooseDensity(w, level, from)
      expect(cardsAt(w)).toEqual(cards)
      expect(miniMap(w)).toEqual(map)
      expect(shownZoom(w)).toBe(zoom)
      expect(disabled(tool(w, 'edit.redo'))).toBe(true)
    }
    await waitSaves()
    expect(putRequest.mock.calls.length).toBe(saves)
    // Still one 실행 취소 entry: the drag, not the density choices.
    await tool(w, 'edit.undo').trigger('click')
    await settle()
    expect(cardsAt(w)).toEqual(LEFT)
    expect(disabled(tool(w, 'edit.undo'))).toBe(true)
  })

  it('the stepped positions are what is saved and come back when the Designer is opened again', async () => {
    let w = await mountShell()
    await openCanvasFlow(w)
    await chooseDensity(w, 'wide', 'menu')
    await drag(w, [R], [47, 23])
    await chooseDensity(w, 'normal', 'toolbar')
    await drag(w, [D], [-31, 18])
    const cards = cardsAt(w)
    // D from (90, 400) — not on the 20 lattice — dragged to (59, 418) lands on the 20 point (60, 420).
    expect(cards).toEqual({ ...LEFT, [R]: [40, 40], [D]: [60, 420] })
    const map = miniMap(w)
    await waitSaves()
    expect(serverSaved).toEqual({ [R]: [40, 40], [D]: [60, 420] })

    wrapper!.unmount()
    wrapper = null
    w = await mountShell()
    await openCanvasFlow(w)
    expect(cardsAt(w)).toEqual(cards)
    expect(miniMap(w)).toEqual(map)
    // The density is screen state only: a new Designer starts at 보통 (20).
    expect(w.get('[data-test="designer-tool-density"] [aria-checked="true"]').attributes('data-density')).toBe('normal')
    expect(canvas(w).attributes('data-move-step')).toBe('20')
  })
})

async function viewMenuCommand(w: VueWrapper<any>, command: string) {
  await w.get('[data-test="designer-menu"][data-menu="view"]').trigger('click')
  await w.get(`[data-test="designer-menu-item"][data-command="${command}"]`).trigger('click')
  await settle()
}

describe('Designer Shell — the Canvas dot grid is the density step lattice (0018-TR rework)', () => {
  it('each density shows its own dot spacing (넓게 40, 보통 20, 좁게 10, 자율형 none) and moves no card', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    expect(viewOf().zoom).toBe(1)
    // A new Designer starts at 보통 (20).
    expect(gridOf(w)).toMatchObject({ gap: 20, size: 20, backgroundSize: '20px 20px' })
    const seen: Record<string, number | null> = {}
    for (const [level, from] of [['wide', 'toolbar'], ['normal', 'menu'], ['narrow', 'toolbar'], ['free', 'menu'], ['wide', 'menu']] as const) {
      await chooseDensity(w, level, from)
      const grid = gridOf(w)
      seen[level] = grid?.gap ?? null
      if (grid) {
        expect(grid.size).toBe(grid.gap)
        expect(grid.backgroundSize).toBe(`${grid.gap}px ${grid.gap}px`)
      }
      expect(cardsAt(w)).toEqual(LEFT)
    }
    expect(seen).toEqual({ wide: 40, normal: 20, narrow: 10, free: null })
  })

  it('the dots fall on the Graph origin and every whole step, where Vue Flow draws the Graph points', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    const flow = useVueFlow(`designer-canvas:${FLOW}`)
    // Panned right and down (the pan bound keeps the Graph origin from leaving the top-left: x, y <= 0).
    await flow.setViewport({ x: -37, y: -53, zoom: 1 })
    await settle()
    expect(viewOf()).toEqual({ x: -37, y: -53, zoom: 1 })
    for (const level of ['wide', 'normal', 'narrow'] as const) {
      await chooseDensity(w, level, 'toolbar')
      const step = DENSITY_MOVE_STEP[level]!
      // Origin at (-37, -53) on screen: the first tile starts half a step before it, so its dot is the origin itself.
      expect(gridOf(w)).toMatchObject({ gap: step, x: -37, y: -53, backgroundPosition: `${-37 - step / 2}px ${-53 - step / 2}px` })
      expect(onDot(w, [0, 0])).toBe(true)
      expect(onDot(w, [step * 3, step * 7])).toBe(true)
      expect(onDot(w, [step / 2, 0])).toBe(false)
    }
    // R (0, 0) and N (300, 120) are on the 20 / 10 lattices; D (90, 400) only on the 10 one.
    await chooseDensity(w, 'normal', 'menu')
    expect([onDot(w, LEFT[R]), onDot(w, LEFT[N]), onDot(w, LEFT[D])]).toEqual([true, true, false])
    await chooseDensity(w, 'narrow', 'menu')
    expect([onDot(w, LEFT[R]), onDot(w, LEFT[N]), onDot(w, LEFT[D])]).toEqual([true, true, true])
  })

  it('a dropped card sits on a dot at every density, also zoomed (115% / 200% / 25%) and panned', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    const flow = useVueFlow(`designer-canvas:${FLOW}`)
    const views = [
      { x: 0, y: 0, zoom: 1.15 },
      { x: -211.5, y: -87.25, zoom: 2 },
      { x: -143, y: -61, zoom: 0.25 },
    ]
    for (const view of views) {
      await flow.setViewport(view)
      await settle()
      expect(viewOf()).toEqual(view)
      for (const level of ['wide', 'normal', 'narrow'] as const) {
        await chooseDensity(w, level, 'toolbar')
        const step = DENSITY_MOVE_STEP[level]!
        const grid = gridOf(w)!
        // The grid follows the pan and the zoom: origin at the translation, step x zoom apart (far out: 2 steps).
        expect([grid.x, grid.y]).toEqual([view.x, view.y])
        expect(grid.size).toBeCloseTo(grid.gap * view.zoom, 9)
        expect(grid.gap).toBe(step * view.zoom >= CANVAS_GRID_MIN_SCREEN_GAP ? step : step * 2)
        // D from (90, 400), off the 40 / 20 lattices, dragged by (-31, 18) in Canvas units (the same at any zoom).
        const { end } = await drag(w, [D], [-31, 18])
        expect(end[D][0] % step).toBe(0)
        expect(end[D][1] % step).toBe(0)
        expect(cardsAt(w)[D]).toEqual(end[D])
        // On a dot of the drawn grid (far out the 좁게 grid shows every second 10 point: 60 / 420 are on it).
        expect(onDot(w, end[D])).toBe(true)
        await tool(w, 'edit.undo').trigger('click')
        await settle()
        expect(cardsAt(w)).toEqual(LEFT)
      }
    }
  })

  it('zooming from the Toolbar and the View menu rescales the grid; the dots stay on the dropped cards', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    await chooseDensity(w, 'normal', 'toolbar')
    await drag(w, [N], [-47, 23])
    expect(cardsAt(w)[N]).toEqual([260, 140])
    for (const percent of ['50', '150', '200', '25', '100']) {
      await zoomTo(w, percent)
      expect(viewOf().zoom).toBeCloseTo(Number(percent) / 100, 9)
      const grid = gridOf(w)!
      expect(grid.size).toBeCloseTo(grid.gap * viewOf().zoom, 9)
      expect([grid.x, grid.y]).toEqual([viewOf().x, viewOf().y])
      expect([onDot(w, cardsAt(w)[R]), onDot(w, cardsAt(w)[N])]).toEqual([true, true])
    }
    await tool(w, 'view.zoom-in').trigger('click')
    await settle()
    expect(onDot(w, cardsAt(w)[N])).toBe(true)
    await viewMenuCommand(w, 'view.zoom-out')
    expect(onDot(w, cardsAt(w)[N])).toBe(true)
    await viewMenuCommand(w, 'view.fit')
    expect(gridOf(w)!.size).toBeCloseTo(gridOf(w)!.gap * viewOf().zoom, 9)
    expect([gridOf(w)!.x, gridOf(w)!.y]).toEqual([viewOf().x, viewOf().y])
    expect(onDot(w, cardsAt(w)[N])).toBe(true)
  })

  it('a dragged selection: the card under the pointer lands on a dot and the group keeps its layout, also pushed back inside the origin', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    await editCommand(w, 'edit.select-all')
    await chooseDensity(w, 'normal', 'toolbar')
    // D under the pointer, dragged by (-97, 11) to (-7, 411): its point (0, 420), offset (-90, 20) for all. R would be
    // at x -90, so the group is pushed back by 100 (whole steps; 90 would leave D at x 90, off the 20 lattice).
    await drag(w, [D, R, N], [-97, 11])
    expect(cardsAt(w)).toEqual({ [R]: [10, 20], [N]: [310, 140], [D]: [100, 420] })
    expect(onDot(w, cardsAt(w)[D])).toBe(true)
    await waitSaves()
    expect(serverSaved).toEqual({ [R]: [10, 20], [N]: [310, 140], [D]: [100, 420] })
  })

  it('자율형: no grid and no step, the card stays where it is dropped; a density again shows its grid and moves nothing', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    await chooseDensity(w, 'free', 'toolbar')
    expect(gridOf(w)).toBeNull()
    expect((await drag(w, [D], [-31, 18])).end[D]).toEqual([59, 418])
    await chooseDensity(w, 'wide', 'menu')
    expect(gridOf(w)).toMatchObject({ gap: 40 })
    expect(cardsAt(w)[D]).toEqual([59, 418])
  })
})
