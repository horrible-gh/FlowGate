/**
 * flowgate.v02.0039 T#2 — Designer entry leaves the Project Explorer unselected (0001-R rev1).
 *
 * Opening the Designer in a Project context is not a selection: right after entry no
 * Project / Group / Flow row is selected or marked active, no `[현재]` mark is drawn, and the
 * tree itself still shows. The Project context (the store's current Project id) keeps feeding
 * the Explorer and the rest of the Shell. Only an explicit click creates a selection.
 *
 * Mounted with the real Shell / Explorer and the real project store; only the HTTP layer
 * (`@shared/api`) is mocked, as in DesignerShell.0025.spec.ts.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, getActivePinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell, ProjectExplorer } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'

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

const TREES: Record<string, unknown[]> = {
  p: [
    n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
    n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
    n({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
    n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
  ],
  q: [
    n({ id: 'project:q', parent_id: null, node_type: 'project', label: 'Beta' }),
    n({ id: 'module:q:m', parent_id: 'project:q', node_type: 'module', label: 'm' }),
  ],
}

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

async function mountShell(projectId = 'p', explicitOpen: string | null = null) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project(projectId, projectId === 'p' ? 'Alpha' : 'Beta'))
  if (explicitOpen) useProjectStore().requestExplicitOpen(explicitOpen)
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  return wrapper
}

/** Re-enter the Designer on the same store (as a later Sidebar / card entry would). */
async function remountShell() {
  wrapper?.unmount()
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, getActivePinia()!] } })
  await settle()
  return wrapper
}

const leftDock = (w: VueWrapper<any>) => w.get('[data-test="designer-left-dock"]')

async function mountExplorer(props: Record<string, unknown>) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project('p', 'Alpha'))
  wrapper = mount(ProjectExplorer, { props, attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  return wrapper
}

const tree = (w: VueWrapper<any>) => w.get('[data-test="project-explorer-tree"]')
const rowLabels = (w: VueWrapper<any>) =>
  tree(w).findAll('[data-test="project-explorer-label"]').map((label) => label.text())

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = tree(w)
    .findAll('[data-explorer-row]')
    .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
  if (!found) throw new Error(`row not rendered: ${label}`)
  return found
}

async function click(w: VueWrapper<any>, label: string) {
  explorerRow(w, label).find('.explorer-row').element.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))
  await nextTick()
  await settle()
}

/** No row carries any selection / active / current state, and no `[현재]` mark is drawn. */
function expectNoSelectionUi(w: VueWrapper<any>) {
  const root = tree(w)
  expect(root.findAll('[aria-selected="true"]')).toHaveLength(0)
  expect(root.findAll('[aria-current]')).toHaveLength(0)
  expect(root.findAll('.explorer-row.is-selected')).toHaveLength(0)
  expect(root.findAll('.explorer-row.is-active')).toHaveLength(0)
  expect(root.find('.ui-badge').exists()).toBe(false)
  expect(root.text()).not.toContain('현재')
}

describe('Designer entry — no default Explorer selection (0039 T#2)', () => {
  it('shows the entered Project tree with nothing selected and no [현재] mark', async () => {
    const w = await mountShell()

    // The tree itself is displayed as before: Project root open, Groups listed.
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선'])
    expect(explorerRow(w, 'Alpha').attributes('aria-expanded')).toBe('true')
    expectNoSelectionUi(w)
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('false')
    expect(w.get('[data-test="designer-left-dock"]').text()).not.toContain('현재')
  })

  it('keeps the Project context: the Explorer and the Shell still work on the entered Project', async () => {
    const w = await mountShell()

    expect(w.getComponent(ProjectExplorer).props('projectId')).toBe('p')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/groups/tree', { include_terminal: 'false', force: 'false' })
    expect(useProjectStore().currentProjectId).toBe('p')
    // No Flow is opened just by entering.
    expect(w.find('[data-test="designer-workspace-flow"]').exists()).toBe(false)
  })

  it('creates a selection only after an explicit click, without touching the Project context', async () => {
    const w = await mountShell()
    expectNoSelectionUi(w)

    await click(w, 'Alpha')
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Alpha').find('.explorer-row').classes()).toContain('is-selected')
    // Selecting is not "current": still no badge / text, and the context is unchanged.
    expect(tree(w).find('.ui-badge').exists()).toBe(false)
    expect(tree(w).text()).not.toContain('현재')
    expect(useProjectStore().currentProjectId).toBe('p')

    await click(w, 'UI 개선')
    expect(explorerRow(w, 'UI 개선').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('false')
  })

  it('a Project context change starts the new tree unselected again', async () => {
    const w = await mountShell()
    await click(w, 'Alpha')
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('true')

    useProjectStore().setCurrentProject(project('q', 'Beta'))
    await settle()

    expect(rowLabels(w)).toEqual(['Beta', 'm'])
    expectNoSelectionUi(w)
  })
})

// flowgate.v02.0040 — the Project Dialog's "열기" is an explicit choice. It reaches the Designer
// as the store's one-shot explicit-open signal: with it the entered Project is selected and the
// Designer has a work target; without it (Sidebar / card entry) the 0039 unselected start stays.
describe('Designer entry after Project Dialog 열기 (flowgate.v02.0040)', () => {
  it('selects the opened Project in the Explorer and gives the Designer a work target', async () => {
    const w = await mountShell('p', 'p')

    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선'])
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Alpha').find('.explorer-row').classes()).toContain('is-selected')
    expect(explorerRow(w, 'UI 개선').attributes('aria-selected')).toBe('false')
    expect(leftDock(w).attributes('data-unselected')).toBe('false')
    // Selected, not "current": still no badge / text.
    expect(tree(w).find('.ui-badge').exists()).toBe(false)
    expect(tree(w).text()).not.toContain('현재')
    expect(useProjectStore().currentProjectId).toBe('p')
  })

  it('without the signal the entry stays unselected with no work target', async () => {
    const w = await mountShell('p')

    expectNoSelectionUi(w)
    expect(leftDock(w).attributes('data-unselected')).toBe('true')
  })

  it('consumes the signal once: a later entry without 열기 starts unselected again', async () => {
    let w = await mountShell('p', 'p')
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('true')
    expect(useProjectStore().takeExplicitOpen()).toBeNull()

    w = await remountShell()
    expectNoSelectionUi(w)
    expect(leftDock(w).attributes('data-unselected')).toBe('true')
  })

  it('ignores (and clears) a signal raised for another Project', async () => {
    const w = await mountShell('p', 'q')

    expectNoSelectionUi(w)
    expect(leftDock(w).attributes('data-unselected')).toBe('true')
    expect(useProjectStore().takeExplicitOpen()).toBeNull()
  })

  it('the explicit selection is still the user\'s: another click moves it, a Project change clears it', async () => {
    const w = await mountShell('p', 'p')

    await click(w, 'UI 개선')
    expect(explorerRow(w, 'UI 개선').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('false')

    useProjectStore().setCurrentProject(project('q', 'Beta'))
    await settle()
    expect(rowLabels(w)).toEqual(['Beta', 'm'])
    expectNoSelectionUi(w)
    expect(leftDock(w).attributes('data-unselected')).toBe('true')
  })

  it('ProjectExplorer alone takes the signal and reports the selection upward', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useProjectStore().setCurrentProject(project('p', 'Alpha'))
    useProjectStore().requestExplicitOpen('p')
    wrapper = mount(ProjectExplorer, { props: {}, attachTo: document.body, global: { plugins: [i18n, pinia] } })
    await settle()

    expect(explorerRow(wrapper, 'Alpha').attributes('aria-selected')).toBe('true')
    const reported = wrapper.emitted('update:selection') ?? []
    expect(reported[reported.length - 1]).toEqual([{ kind: 'project', id: 'project:p' }])
    expect(useProjectStore().takeExplicitOpen()).toBeNull()
  })
})

// flowgate.v02.0040 (0001-R §1/§2): before the person opens a Project with "열기" the Designer has
// no Project at all. A stored `fg_current_project_id` (v0.1 / an earlier session) is not restored,
// so no Project / Group / Flow row can appear as if it had been chosen; the Project Dialog's
// list refresh does not pick one either.
describe('Designer entry with no Project opened in this session (flowgate.v02.0040)', () => {
  it('shows no Project tree even when a current Project id is stored', async () => {
    localStorage.setItem('fg_current_project_id', 'p')
    const pinia = createPinia()
    setActivePinia(pinia)
    wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
    await settle()

    expect(useProjectStore().currentProjectId).toBeNull()
    expect(wrapper.find('[data-test="project-explorer-tree"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="project-explorer-no-project"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('선택된 프로젝트가 없습니다')
    expect(wrapper.text()).not.toContain('프로젝트를 열면 그룹과 플로우가')
    expect(wrapper.findAll('[data-explorer-row][aria-selected="true"]')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('현재')
    expect(leftDock(wrapper).attributes('data-unselected')).toBe('true')
    expect(getRequest).not.toHaveBeenCalledWith(expect.stringMatching(/\/groups\/tree$/), expect.anything())
    // Left for v0.1; it does not drive the Designer.
    expect(localStorage.getItem('fg_current_project_id')).toBe('p')
  })

  it('only 열기 gives the Designer its Project, selected; a later plain entry shows it unselected', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
    await settle()
    expect(wrapper.find('[data-test="project-explorer-no-project"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('선택된 프로젝트가 없습니다')
    expect(wrapper.text()).not.toContain('프로젝트를 열면 그룹과 플로우가')

    // What ProjectDialog.openProject() does on "열기".
    useProjectStore().setCurrentProject(project('p', 'Alpha'))
    useProjectStore().requestExplicitOpen('p')
    let w = await remountShell()
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선'])
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('true')
    expect(leftDock(w).attributes('data-unselected')).toBe('false')

    w = await remountShell()
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선'])
    expectNoSelectionUi(w)
    expect(leftDock(w).attributes('data-unselected')).toBe('true')
  })
})

describe('ProjectExplorer — Project context is not a selection (0039 T#2)', () => {
  it('starts unselected when the Project comes as an explicit prop', async () => {
    const w = await mountExplorer({ projectId: 'p' })
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선'])
    expectNoSelectionUi(w)
    expect(w.emitted('select-group')).toBeUndefined()
    expect(w.emitted('select-flow')).toBeUndefined()
    expect(w.emitted('activate-flow')).toBeUndefined()
  })

  it('starts unselected when it follows the store current Project', async () => {
    const w = await mountExplorer({})
    expect(getRequest.mock.calls[0][0]).toBe('/api/v1/projects/p/groups/tree')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선'])
    expectNoSelectionUi(w)
  })
})
