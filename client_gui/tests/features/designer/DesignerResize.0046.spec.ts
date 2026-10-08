/**
 * flowgate.v02.0046 — Designer Dock resize (0001-R §2). The left / right Dock widths and the
 * bottom Dock height follow a drag on their line with the Center; the Center takes the rest.
 *
 * The Shell is mounted as in DesignerShell.0025 (real Explorers, real project store, only the
 * HTTP layer `@shared/api` mocked). jsdom lays nothing out, so each test gives the Body / the
 * Center column the size a real screen would have before dragging.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'
import {
  DESIGNER_BOTTOM_MIN_HEIGHT,
  DESIGNER_CENTER_MIN_WIDTH,
  DESIGNER_LEFT_MIN_WIDTH,
  DESIGNER_RIGHT_MIN_WIDTH,
  DESIGNER_WORKSPACE_MIN_HEIGHT,
  clampBottomHeight,
  clampDockSize,
  clampLeftWidth,
  clampRightWidth,
  createDesignerLayout,
} from '@/features/designer/shell/designerLayout'

const getRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

// --- Canvas environment for Vue Flow under jsdom (same stand-ins as DesignerShell.0025) -----
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
// -------------------------------------------------------------------------------------------

function n(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

const TREE = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
]

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (/^\/api\/v1\/projects\/p\/groups\/tree$/.test(url)) return { data: { data: { nodes: TREE } } }
    if (/\/git\/status$/.test(url)) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes)$/.exec(url)
    if (group && group[2] === 'tree') return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: [] } } }
    if (group) {
      return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: params.base ?? 'v0.2', changes: [] } } }
    }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
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

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)

/** Gives an element the client size a laid-out screen would report. */
function sized(el: Element, prop: 'clientWidth' | 'clientHeight', value: number) {
  Object.defineProperty(el, prop, { configurable: true, value })
}

async function mountShell(body = { width: 1600, height: 900 }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project('p', 'Alpha'))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  sized(get(wrapper, 'designer-body').element, 'clientWidth', body.width)
  sized(get(wrapper, 'designer-center-column').element, 'clientHeight', body.height)
  return wrapper
}

/** Opens the Canvas Flow (Explorer double click), which makes the Dock collapse available. */
async function openCanvas(w: VueWrapper<any>) {
  for (const label of ['UI 개선', 'Canvas']) {
    const row = w.findAll('[data-explorer-row]').find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
    if (!row) throw new Error(`row not rendered: ${label}`)
    for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
      row.find('.explorer-row').element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
      await nextTick()
    }
    await settle()
  }
}

/** A primary-button drag of the handle by (dx, dy), ending with mouseup. */
async function drag(w: VueWrapper<any>, handle: string, dx: number, dy: number) {
  const start = { clientX: 500, clientY: 500 }
  get(w, handle).element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, ...start }))
  document.dispatchEvent(new MouseEvent('mousemove', { clientX: start.clientX + dx, clientY: start.clientY + dy }))
  document.dispatchEvent(new MouseEvent('mouseup'))
  await nextTick()
}

const leftWidth = (w: VueWrapper<any>) => Number(get(w, 'designer-body').attributes('data-left-width'))
const rightWidth = (w: VueWrapper<any>) => Number(get(w, 'designer-body').attributes('data-right-width'))
const columns = (w: VueWrapper<any>) => (get(w, 'designer-body').element as HTMLElement).style.gridTemplateColumns
const bottomHeight = (w: VueWrapper<any>) => (get(w, 'designer-bottom-dock').element as HTMLElement).style.height

describe('designerLayout — resize bounds (0046)', () => {
  it('keeps a size within its minimum and the room the Center leaves', () => {
    expect(clampDockSize(100, 200, 500, 280)).toBe(200)
    expect(clampDockSize(350.4, 200, 500, 280)).toBe(350)
    expect(clampDockSize(900, 200, 500, 280)).toBe(500)
  })

  it('without room a Dock may shrink but not grow past its current size', () => {
    expect(clampDockSize(400, 200, 100, 280)).toBe(280)
    expect(clampDockSize(240, 200, 100, 280)).toBe(240)
  })

  it('leaves the Center its minimum width beside the other Dock', () => {
    const layout = createDesignerLayout()
    expect(clampLeftWidth(layout, 2000, 1600)).toBe(1600 - 300 - DESIGNER_CENTER_MIN_WIDTH)
    expect(clampRightWidth(layout, 2000, 1600)).toBe(1600 - 280 - DESIGNER_CENTER_MIN_WIDTH)
    expect(clampLeftWidth(layout, 10, 1600)).toBe(DESIGNER_LEFT_MIN_WIDTH)
    expect(clampRightWidth(layout, 10, 1600)).toBe(DESIGNER_RIGHT_MIN_WIDTH)
    // A collapsed Dock counts as its rail.
    expect(clampLeftWidth({ ...layout, rightCollapsed: true }, 2000, 1600)).toBe(1600 - 40 - DESIGNER_CENTER_MIN_WIDTH)
  })

  it('leaves the Workspace its minimum height above the bottom Dock', () => {
    const layout = createDesignerLayout()
    expect(clampBottomHeight(layout, 2000, 900)).toBe(900 - DESIGNER_WORKSPACE_MIN_HEIGHT)
    expect(clampBottomHeight(layout, 10, 900)).toBe(DESIGNER_BOTTOM_MIN_HEIGHT)
  })
})

describe('Designer Shell — Dock resize handles (0046)', () => {
  it('puts a handle on each Dock ↔ Center line', async () => {
    const w = await mountShell()
    for (const [test, orientation] of [
      ['designer-left-resize', 'vertical'],
      ['designer-right-resize', 'vertical'],
      ['designer-bottom-resize', 'horizontal'],
    ] as const) {
      expect(get(w, test).attributes('role'), test).toBe('separator')
      expect(get(w, test).attributes('aria-orientation'), test).toBe(orientation)
    }
    expect(get(w, 'designer-left-resize').element.nextElementSibling?.getAttribute('data-test')).toBe('designer-center-column')
    expect(get(w, 'designer-right-resize').element.nextElementSibling?.getAttribute('data-test')).toBe('designer-right-dock')
    expect(get(w, 'designer-bottom-resize').element.nextElementSibling?.getAttribute('data-test')).toBe('designer-bottom-dock')
  })

  it('dragging the left line resizes the left Dock; the Center takes the rest', async () => {
    const w = await mountShell()
    await drag(w, 'designer-left-resize', 120, 0)
    expect(leftWidth(w)).toBe(400)
    expect(columns(w)).toBe(`400px minmax(${DESIGNER_CENTER_MIN_WIDTH}px, 1fr) 300px`)
    await drag(w, 'designer-left-resize', -150, 0)
    expect(leftWidth(w)).toBe(250)
    expect(rightWidth(w)).toBe(300)
  })

  it('dragging the right line toward the Center widens the right Dock', async () => {
    const w = await mountShell()
    await drag(w, 'designer-right-resize', -100, 0)
    expect(rightWidth(w)).toBe(400)
    expect(columns(w)).toBe(`280px minmax(${DESIGNER_CENTER_MIN_WIDTH}px, 1fr) 400px`)
    await drag(w, 'designer-right-resize', 60, 0)
    expect(rightWidth(w)).toBe(340)
    expect(leftWidth(w)).toBe(280)
  })

  it('dragging the bottom line up raises the bottom Dock; the Workspace takes the rest', async () => {
    const w = await mountShell()
    await drag(w, 'designer-bottom-resize', 0, -100)
    expect(bottomHeight(w)).toBe('400px')
    await drag(w, 'designer-bottom-resize', 0, 180)
    expect(bottomHeight(w)).toBe('220px')
  })

  it('follows the pointer while dragging and stops after mouseup', async () => {
    const w = await mountShell()
    const handle = get(w, 'designer-left-resize').element
    handle.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, clientX: 280 }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 300 }))
    await nextTick()
    expect(leftWidth(w)).toBe(300)
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 330 }))
    await nextTick()
    expect(leftWidth(w)).toBe(330)
    document.dispatchEvent(new MouseEvent('mouseup'))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 500 }))
    await nextTick()
    expect(leftWidth(w)).toBe(330)
  })

  it('only the primary button drags', async () => {
    const w = await mountShell()
    get(w, 'designer-left-resize').element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, clientX: 280 }))
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 400 }))
    document.dispatchEvent(new MouseEvent('mouseup'))
    await nextTick()
    expect(leftWidth(w)).toBe(280)
  })

  it('stops at each minimum and keeps the Center / Workspace minimum', async () => {
    const w = await mountShell()
    await drag(w, 'designer-left-resize', -1000, 0)
    expect(leftWidth(w)).toBe(DESIGNER_LEFT_MIN_WIDTH)
    await drag(w, 'designer-right-resize', 1000, 0)
    expect(rightWidth(w)).toBe(DESIGNER_RIGHT_MIN_WIDTH)
    await drag(w, 'designer-bottom-resize', 0, 1000)
    expect(bottomHeight(w)).toBe(`${DESIGNER_BOTTOM_MIN_HEIGHT}px`)

    await drag(w, 'designer-left-resize', 3000, 0)
    expect(leftWidth(w)).toBe(1600 - DESIGNER_RIGHT_MIN_WIDTH - DESIGNER_CENTER_MIN_WIDTH)
    // The left Dock already took the room; the right Dock cannot grow past its size.
    await drag(w, 'designer-right-resize', -3000, 0)
    expect(rightWidth(w)).toBe(DESIGNER_RIGHT_MIN_WIDTH)
    await drag(w, 'designer-bottom-resize', 0, -3000)
    expect(bottomHeight(w)).toBe(`${900 - DESIGNER_WORKSPACE_MIN_HEIGHT}px`)
  })

  it('a collapsed Dock has no handle and expands back to its dragged width', async () => {
    const w = await mountShell()
    await openCanvas(w)
    await drag(w, 'designer-left-resize', 70, 0)
    expect(leftWidth(w)).toBe(350)

    await get(w, 'designer-left-collapse').trigger('click')
    expect(leftWidth(w)).toBe(40)
    expect(w.find('[data-test="designer-left-resize"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-right-resize"]').exists()).toBe(true)

    await get(w, 'designer-left-expand').trigger('click')
    expect(leftWidth(w)).toBe(350)
    expect(w.find('[data-test="designer-left-resize"]').exists()).toBe(true)
  })

  it('keeps the Docks, their parts and the open Flow in place while resizing', async () => {
    const w = await mountShell()
    await openCanvas(w)
    await drag(w, 'designer-left-resize', 50, 0)
    await drag(w, 'designer-right-resize', -50, 0)
    await drag(w, 'designer-bottom-resize', 0, -50)
    expect(w.find('[data-test="project-explorer"]').exists()).toBe(true)
    expect(w.findAll('[data-test="designer-flow-tab"] [role="tab"]').map((t) => t.text())).toEqual(['Canvas'])
    expect(get(w, 'designer-right-dock').find('[data-test="designer-palette"]').exists()).toBe(true)
    expect(get(w, 'designer-bottom-dock').find('[data-test="designer-inspector"]').exists()).toBe(true)
    expect(get(w, 'designer-right-dock').find('[data-test="designer-right-tool-tabs"]').exists()).toBe(true)
    expect(leftWidth(w)).toBe(330)
    expect(rightWidth(w)).toBe(350)
    expect(bottomHeight(w)).toBe('350px')
  })
})
