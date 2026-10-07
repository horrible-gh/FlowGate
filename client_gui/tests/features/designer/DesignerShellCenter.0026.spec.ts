/**
 * flowgate.v02.0026 — Designer Shell x Designer Center (0003-D §4.1 / §7.1). The real Shell
 * with the real 0023 Project Explorer, 0024 File Explorer, project store, Center session and
 * fixture Graph source; only the HTTP layer (`@shared/api`) is mocked, the same way as
 * DesignerShell.0025. This file alone gives the Canvas a size and a ResizeObserver stand-in.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerFileExplorer, DesignerShell, ProjectExplorer } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'

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

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = w
    .findAll('[data-explorer-row]')
    .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
  if (!found) throw new Error(`row not rendered: ${label}`)
  return found.find('.explorer-row')
}

/** A real browser double click: click(detail=1), click(detail=2), dblclick. */
async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await nextTick()
  }
  await settle()
}

async function selectMode(w: VueWrapper<any>, label: string) {
  const tab = w.findAll('[data-test="designer-explorer-mode-tabs"] [role="tab"]').find((b) => b.text() === label)
  if (!tab) throw new Error(`tab not rendered: ${label}`)
  await tab.trigger('click')
  await settle()
}

/** Expands the module once, then activates the given Flows in order. */
async function activate(w: VueWrapper<any>, ...flows: string[]) {
  if (!w.findAll('[data-explorer-row]').some((r) => r.find('[data-test="project-explorer-label"]').text() === 'Canvas')) {
    await doubleClick(w, 'UI 개선')
  }
  for (const flow of flows) await doubleClick(w, flow)
}

const tabIds = (w: VueWrapper<any>) => w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))
const tab = (w: VueWrapper<any>, flowId: string) => w.get(`[data-test="designer-flow-tab"][data-flow-id="${flowId}"]`)
const activeTabId = (w: VueWrapper<any>) =>
  w.findAll('[data-test="designer-flow-tab"]').find((t) => t.get('[role="tab"]').attributes('aria-selected') === 'true')?.attributes('data-flow-id') ?? null
const projectActive = (w: VueWrapper<any>) => w.findComponent(ProjectExplorer).props('activeFlowId')
const fileFlow = (w: VueWrapper<any>) => w.findComponent(DesignerFileExplorer).props('flow')
const treeCalls = (groupId: string) =>
  getRequest.mock.calls.filter(([url]) => String(url) === `/api/v1/projects/p/git/groups/${groupId}/tree`).length

describe('Designer Shell x Center — opening Flows', () => {
  it('activating two Flows opens two tabs with the second active, and the Canvas shows its sample Graph', async () => {
    const w = await mountShell()
    await activate(w, 'Designer v0.2', 'Canvas')

    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.ui.0002'])
    expect(w.findAll('[data-test="designer-flow-tab"] [role="tab"]').map((t) => t.text())).toEqual(['Designer v0.2', 'Canvas'])
    expect(activeTabId(w)).toBe('p.ui.0002')
    expect(projectActive(w)).toBe('p.ui.0002')

    const canvas = get(w, 'designer-canvas')
    expect(canvas.attributes('data-flow-id')).toBe('p.ui.0002')
    const nodes = w.findAll('[data-test="designer-canvas-node"]')
    expect(nodes.length).toBeGreaterThan(0)
    for (const node of nodes) expect(node.attributes('data-node-id')!.startsWith('p.ui.0002:')).toBe(true)
    expect(get(w, 'designer-canvas-sample').text()).toBe('샘플 데이터')
  })

  it('double-clicking an open Flow again keeps two tabs and activates the first', async () => {
    const w = await mountShell()
    await activate(w, 'Designer v0.2', 'Canvas')
    await doubleClick(w, 'Designer v0.2')

    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.ui.0002'])
    expect(activeTabId(w)).toBe('p.ui.0001')
    expect(projectActive(w)).toBe('p.ui.0001')
    expect(get(w, 'designer-canvas').attributes('data-flow-id')).toBe('p.ui.0001')
  })
})

describe('Designer Shell x Center — Explorers follow the active tab', () => {
  it('a tab click moves the Project Explorer mark and the File Explorer target to that Flow', async () => {
    const w = await mountShell()
    await selectMode(w, '파일')
    await selectMode(w, '프로젝트')
    await activate(w, 'Designer v0.2', 'Canvas')
    expect(fileFlow(w)).toEqual({ groupId: 'p.ui.0002', name: 'Canvas' })

    await tab(w, 'p.ui.0001').get('[role="tab"]').trigger('click')
    await settle()

    expect(activeTabId(w)).toBe('p.ui.0001')
    expect(projectActive(w)).toBe('p.ui.0001')
    expect(explorerRow(w, 'Designer v0.2').classes()).toContain('is-active')
    expect(explorerRow(w, 'Canvas').classes()).not.toContain('is-active')
    expect(fileFlow(w)).toEqual({ groupId: 'p.ui.0001', name: 'Designer v0.2' })
    expect(treeCalls('p.ui.0001')).toBeGreaterThan(0)
    // The user's mode choice stays.
    expect(get(w, 'designer-left-dock').attributes('data-mode')).toBe('project')
  })

  it('closing another tab keeps the same FlowRef object, so the File Explorer does not reload', async () => {
    const w = await mountShell()
    await selectMode(w, '파일')
    await selectMode(w, '프로젝트')
    await activate(w, 'Designer v0.2', 'Canvas', 'Minimap')
    await tab(w, 'p.ui.0002').get('[role="tab"]').trigger('click')
    await settle()
    const flowRef = fileFlow(w)
    const calls = treeCalls('p.ui.0002')
    expect(calls).toBeGreaterThan(0)

    await tab(w, 'p.ui.0003').get('[data-test="designer-flow-tab-close"]').trigger('click')
    await settle()
    await tab(w, 'p.ui.0001').get('[data-test="designer-flow-tab-close"]').trigger('click')
    await settle()

    expect(tabIds(w)).toEqual(['p.ui.0002'])
    expect(fileFlow(w)).toBe(flowRef)
    expect(treeCalls('p.ui.0002')).toBe(calls)
  })

  it('closing the active tab falls back to its right neighbour, else its left, and the Explorers match', async () => {
    const w = await mountShell()
    await selectMode(w, '파일')
    await selectMode(w, '프로젝트')
    await activate(w, 'Designer v0.2', 'Canvas', 'Minimap')
    await tab(w, 'p.ui.0002').get('[role="tab"]').trigger('click')
    await settle()

    await tab(w, 'p.ui.0002').get('[data-test="designer-flow-tab-close"]').trigger('click')
    await settle()
    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.ui.0003'])
    expect(activeTabId(w)).toBe('p.ui.0003')
    expect(projectActive(w)).toBe('p.ui.0003')
    expect(fileFlow(w)).toEqual({ groupId: 'p.ui.0003', name: 'Minimap' })

    await tab(w, 'p.ui.0003').get('[data-test="designer-flow-tab-close"]').trigger('click')
    await settle()
    expect(activeTabId(w)).toBe('p.ui.0001')
    expect(projectActive(w)).toBe('p.ui.0001')
    expect(fileFlow(w)).toEqual({ groupId: 'p.ui.0001', name: 'Designer v0.2' })
  })

  it('closing the last tab leaves no active Flow: Center empty, File Explorer "no Flow", no Project mark', async () => {
    const w = await mountShell()
    await activate(w, 'Canvas')
    await tab(w, 'p.ui.0002').get('[data-test="designer-flow-tab-close"]').trigger('click')
    await settle()

    expect(tabIds(w)).toEqual([])
    expect(get(w, 'designer-center-no-flow').text()).toContain('열린 플로우 없음')
    expect(projectActive(w)).toBeNull()
    expect(explorerRow(w, 'Canvas').classes()).not.toContain('is-active')
    await selectMode(w, '파일')
    expect(fileFlow(w)).toBeNull()
    expect(w.find('[data-test="dfx-no-flow"]').exists()).toBe(true)
  })

  it('changing the current Project clears every tab and the Explorers’ active Flow', async () => {
    const w = await mountShell()
    await activate(w, 'Designer v0.2', 'Canvas')
    await selectMode(w, '파일')

    useProjectStore().setCurrentProject(project('q', 'Beta'))
    await settle()

    expect(tabIds(w)).toEqual([])
    expect(w.find('[data-test="designer-canvas"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-center-no-flow"]').exists()).toBe(true)
    expect(fileFlow(w)).toBeNull()
    expect(projectActive(w)).toBeNull()
    expect(w.findComponent(ProjectExplorer).props('projectId')).toBe('q')
  })
})

describe('Designer Shell x Center — Korean wording', () => {
  // flowgate.v02.0038: the top band follows its confirmed design wording (Project / Group /
  // Flow / Node, 0001-R rev2) and carries the FlowGate brand, so this rule covers the Body.
  it('names the Flow object 플로우 everywhere in the Korean Designer Body with tabs and Canvas open', async () => {
    const w = await mountShell()
    await activate(w, 'Designer v0.2', 'Canvas')
    await nextTick()
    const shell = get(w, 'designer-body')
    const labels = [...shell.element.querySelectorAll('[aria-label], [title]')].flatMap((el) => [
      el.getAttribute('aria-label') ?? '',
      el.getAttribute('title') ?? '',
    ])
    // Tab names are user data (group labels), not UI wording.
    const userNames = ['Designer v0.2', 'Canvas']
    const strip = (text: string) => userNames.reduce((acc, name) => acc.split(name).join(''), text)
    expect(strip(shell.text())).not.toMatch(/Flow|흐름|플로(?!우)/)
    for (const label of labels) expect(strip(label)).not.toMatch(/Flow|흐름|플로(?!우)/)
  })
})
