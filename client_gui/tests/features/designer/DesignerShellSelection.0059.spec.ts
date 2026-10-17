/**
 * flowgate.v02.0059 T#1 — the Toolbar's [선택] toggle and the Edit menu's 전체 선택 drive one
 * Selection on the open Flow's Canvas (0011-CH contract A); the Inspector opens on a single card only.
 * Scaffold as DesignerShellViewControl.0059: the real Shell, project store, Center session and server
 * Graph source; only `@shared/api` is mocked.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
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

const selectTool = (w: VueWrapper<any>) => tool(w, 'edit.select-mode')
const canvas = (w: VueWrapper<any>) => get(w, 'designer-canvas')
const cards = (w: VueWrapper<any>) => w.findAll('.vue-flow__node')
const selectedCards = (w: VueWrapper<any>) =>
  cards(w).filter((n) => n.find('.flow-node-card.is-selected').exists()).map((n) => n.attributes('data-id'))
const inspectorTarget = (w: VueWrapper<any>) => get(w, 'designer-inspector').attributes('data-selected')

async function editCommand(w: VueWrapper<any>, command: string) {
  await w.get('[data-test="designer-menu"][data-menu="edit"]').trigger('click')
  await w.get(`[data-test="designer-menu-item"][data-command="${command}"]`).trigger('click')
  await settle()
}

describe('Designer Shell — [선택] mode and 전체 선택 (flowgate.v02.0059 T#1)', () => {
  it('the Toolbar shows [선택] and its ON / OFF state, and the Canvas follows it', async () => {
    const w = await mountShell()
    expect(selectTool(w).attributes('disabled')).toBeDefined()
    await openFlow(w, 'Canvas')
    expect(selectTool(w).text()).toBe('선택')
    expect(selectTool(w).attributes('disabled')).toBeUndefined()
    expect(selectTool(w).attributes('aria-pressed')).toBe('false')
    expect(canvas(w).attributes('data-select-mode')).toBe('false')

    await selectTool(w).trigger('click')
    await settle()
    expect(selectTool(w).attributes('aria-pressed')).toBe('true')
    expect(selectTool(w).classes()).toContain('is-on')
    expect(selectTool(w).attributes('title')).toContain('켜짐')
    expect(canvas(w).attributes('data-select-mode')).toBe('true')

    await selectTool(w).trigger('click')
    await settle()
    expect(selectTool(w).attributes('aria-pressed')).toBe('false')
    expect(canvas(w).attributes('data-select-mode')).toBe('false')
  })

  it('OFF: a card click selects that one card and the Inspector opens on it, as before', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const [first, second] = cards(w).map((c) => c.attributes('data-id'))
    await w.get(`.vue-flow__node[data-id="${first}"]`).trigger('click')
    await settle()
    await w.get(`.vue-flow__node[data-id="${second}"]`).trigger('click')
    await settle()
    expect(selectedCards(w)).toEqual([second])
    expect(inspectorTarget(w)).toBe('true')
  })

  it('ON: card clicks add / remove cards; two or more are no single Inspector target', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    await selectTool(w).trigger('click')
    await settle()
    const [first, second] = cards(w).map((c) => c.attributes('data-id'))
    await w.get(`.vue-flow__node[data-id="${first}"]`).trigger('click')
    await settle()
    expect(selectedCards(w)).toEqual([first])
    expect(inspectorTarget(w)).toBe('true')

    await w.get(`.vue-flow__node[data-id="${second}"]`).trigger('click')
    await settle()
    expect(selectedCards(w)).toEqual([first, second])
    expect(canvas(w).attributes('data-selected-node-ids')).toBe(`${first} ${second}`)
    expect(inspectorTarget(w)).toBe('false')

    await w.get(`.vue-flow__node[data-id="${first}"]`).trigger('click')
    await settle()
    expect(selectedCards(w)).toEqual([second])
    expect(inspectorTarget(w)).toBe('true')
  })

  it('the Edit menu 전체 선택 selects every card in the same Selection and lights [선택]', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const ids = cards(w).map((c) => c.attributes('data-id'))
    expect(ids.length).toBeGreaterThan(1)

    await editCommand(w, 'edit.select-all')
    expect(selectedCards(w)).toEqual(ids)
    expect(canvas(w).attributes('data-selected-node-ids')).toBe(ids.join(' '))
    expect(selectTool(w).attributes('aria-pressed')).toBe('true')
    expect(inspectorTarget(w)).toBe('false')

    // Turning [선택] off only clears that Selection.
    await selectTool(w).trigger('click')
    await settle()
    expect(selectedCards(w)).toEqual([])
    expect(cards(w).map((c) => c.attributes('data-id'))).toEqual(ids)
  })
})
