/**
 * flowgate.v02.0059 T#1 — the Toolbar and the View menu drive the same Canvas Viewport (fit, zoom in /
 * out, 100%, zoom select) and the Toolbar value follows it. Scaffold as DesignerShellCenter.0026:
 * the real Shell, project store, Center session and server Graph source; only `@shared/api` is mocked.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { useVueFlow } from '@vue-flow/core'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'
import { answerFlowCanvas } from './flowCanvasResponse'

const getRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
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

const TREES: Record<string, unknown[]> = {
  p: [
    n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
    n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
    n({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
    n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
    n({ id: 'p.ui.0003', parent_id: 'module:p:ui', node_type: 'group', label: 'Minimap', number: '0003' }),
  ],
  q: [
    n({ id: 'project:q', parent_id: null, node_type: 'project', label: 'Beta' }),
    n({ id: 'module:q:m', parent_id: 'project:q', node_type: 'module', label: 'm' }),
  ],
}

const FILE_NODES = [
  { id: 'n:src', parent_id: null, type: 'folder', name: 'src', label: 'src', path: 'src', permissions: ['read'] },
  { id: 'n:src/app.ts', parent_id: 'n:src', type: 'file', name: 'app.ts', label: 'app.ts', path: 'src/app.ts', permissions: ['read'] },
]

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    const tree = /^\/api\/v1\/projects\/([^/]+)\/groups\/tree$/.exec(url)
    if (tree) return { data: { data: { nodes: TREES[tree[1]] ?? [] } } }
    const git = /^\/api\/v1\/projects\/([^/]+)\/git\/status$/.exec(url)
    if (git) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes)$/.exec(url)
    if (group && group[2] === 'tree') return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: FILE_NODES } } }
    if (group && group[2] === 'changes') {
      return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: params.base ?? 'v0.2', changes: [] } } }
    }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    // flowgate.v02.0045: the Designer reads Flow Graphs from the server Canvas API.
    const canvas = answerFlowCanvas(url)
    if (canvas) return canvas
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

// flowgate.v02.0039 T#3: entering leaves the Designer without a work target; these suites
// check the work state, so the user first chooses the Project row (one click).
async function mountShell() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project('p', 'Alpha'))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  return wrapper
}

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = w
    .findAll('[data-explorer-row]')
    .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
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

const tool = (w: VueWrapper<any>, command: string) => w.get(`[data-test="designer-tool"][data-command="${command}"]`)
// flowgate.v02.0059 0017-T: the one zoom control shows the zoom as its chosen entry.
const toolbarZoom = (w: VueWrapper<any>) => (get(w, 'designer-tool-zoom').element as HTMLSelectElement).selectedOptions[0]?.text
const canvasZoom = (w: VueWrapper<any>) => Number(get(w, 'designer-canvas').attributes('data-zoom'))

async function menuCommand(w: VueWrapper<any>, command: string) {
  await w.get('[data-test="designer-menu"][data-menu="view"]').trigger('click')
  await w.get(`[data-test="designer-menu-item"][data-command="${command}"]`).trigger('click')
  await settle()
}

describe('Designer Shell — Canvas view controls (flowgate.v02.0059 T#1)', () => {
  it('Toolbar and View menu zoom the same Canvas and the Toolbar value follows', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const flow = useVueFlow('designer-canvas:p.ui.0002')
    await flow.setViewport({ x: 0, y: 0, zoom: 1 })
    await settle()
    expect(toolbarZoom(w)).toBe('100%')

    await tool(w, 'view.zoom-in').trigger('click')
    await settle()
    const zoomedIn = canvasZoom(w)
    expect(zoomedIn).toBeGreaterThan(1)
    expect(toolbarZoom(w)).toBe(`${Math.round(zoomedIn * 100)}%`)

    await menuCommand(w, 'view.zoom-out')
    expect(canvasZoom(w)).toBeCloseTo(1, 5)
    expect(toolbarZoom(w)).toBe('100%')

    await tool(w, 'view.zoom-out').trigger('click')
    await settle()
    expect(canvasZoom(w)).toBeLessThan(1)
    await menuCommand(w, 'view.zoom-reset')
    expect(canvasZoom(w)).toBe(1)
    expect(toolbarZoom(w)).toBe('100%')
  })

  it('the Toolbar zoom select sets the Canvas zoom', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    await get(w, 'designer-tool-zoom').setValue('50')
    await settle()
    expect(canvasZoom(w)).toBe(0.5)
    expect(toolbarZoom(w)).toBe('50%')
    expect((get(w, 'designer-tool-zoom').element as HTMLSelectElement).value).toBe('50')
  })

  // flowgate.v02.0059 0017-T: 115% (no preset) is shown once and stays one with zoom in / out, presets and 100%.
  it('a zoom that is no preset (115%) is shown once and follows zoom in / out, a preset and 100%', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const flow = useVueFlow('designer-canvas:p.ui.0002')
    await flow.setViewport({ x: 0, y: 0, zoom: 1.15 })
    await settle()
    expect(w.findAll('[data-test^="designer-tool-zoom"]')).toHaveLength(1)
    expect(w.get('[data-test="designer-toolbar"]').findAll('output')).toHaveLength(0)
    expect(toolbarZoom(w)).toBe('115%')
    const options = () => get(w, 'designer-tool-zoom').findAll('option').map((o) => o.text())
    expect(options().filter((text) => text === '115%')).toHaveLength(1)

    await tool(w, 'view.zoom-in').trigger('click')
    await settle()
    expect(toolbarZoom(w)).toBe(`${Math.round(canvasZoom(w) * 100)}%`)
    expect(canvasZoom(w)).toBeGreaterThan(1.15)
    expect(options()).not.toContain('115%')

    await menuCommand(w, 'view.zoom-out')
    expect(canvasZoom(w)).toBeCloseTo(1.15, 5)
    expect(toolbarZoom(w)).toBe('115%')

    await get(w, 'designer-tool-zoom').setValue('75')
    await settle()
    expect(canvasZoom(w)).toBe(0.75)
    expect(toolbarZoom(w)).toBe('75%')

    await menuCommand(w, 'view.zoom-reset')
    expect(canvasZoom(w)).toBe(1)
    expect(toolbarZoom(w)).toBe('100%')
  })

  it('fit from the Toolbar and from the menu keeps the zoom inside the range', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    await get(w, 'designer-tool-zoom').setValue('200')
    await settle()
    await tool(w, 'view.fit').trigger('click')
    await settle()
    expect(canvasZoom(w)).toBeGreaterThanOrEqual(0.25)
    expect(canvasZoom(w)).toBeLessThanOrEqual(2)
    await menuCommand(w, 'view.fit')
    expect(canvasZoom(w)).toBeGreaterThanOrEqual(0.25)
  })
})
