/**
 * flowgate.v02.0025 — Designer Shell (0003-D §7.1).
 *
 * The Shell is mounted with the real 0023 Project Explorer, the real 0024 File Explorer and
 * the real project store; only the HTTP layer (`@shared/api`) is mocked. Assertions go
 * through the DOM the user sees and the props the Explorers receive.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

async function mountShell(projectId: string | null = 'p') {
  const pinia = createPinia()
  setActivePinia(pinia)
  if (projectId) useProjectStore().setCurrentProject(project(projectId, projectId === 'p' ? 'Alpha' : 'Beta'))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  return wrapper
}

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const isShown = (el: Element) => (el as HTMLElement).style.display !== 'none'

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

async function activateCanvas(w: VueWrapper<any>) {
  await doubleClick(w, 'UI 개선')
  await doubleClick(w, 'Canvas')
}

async function selectMode(w: VueWrapper<any>, label: string) {
  const tab = w.findAll('[data-test="designer-explorer-mode-tabs"] [role="tab"]').find((b) => b.text() === label)
  if (!tab) throw new Error(`tab not rendered: ${label}`)
  await tab.trigger('click')
  await settle()
}

describe('DesignerShell — container hierarchy', () => {
  it('builds Shell > Top + Body, Body > Left + Center + Right, Center > Workspace + Bottom', async () => {
    const w = await mountShell()
    const shell = get(w, 'designer-shell').element
    const child = (el: Element, test: string) => el.querySelector(`:scope > [data-test="${test}"]`)

    expect([...shell.children].map((c) => c.getAttribute('data-test'))).toEqual(['designer-top', 'designer-body'])
    const body = child(shell, 'designer-body')!
    expect([...body.children].map((c) => c.getAttribute('data-test'))).toEqual([
      'designer-left-dock',
      'designer-center-column',
      'designer-right-dock',
    ])
    const center = child(body, 'designer-center-column')!
    expect([...center.children].map((c) => c.getAttribute('data-test'))).toEqual([
      'designer-workspace',
      'designer-bottom-dock',
    ])
    // The Bottom Dock lives in the Center column only — never a direct child of the Body.
    expect(child(body, 'designer-bottom-dock')).toBeNull()
    expect(get(w, 'designer-bottom-dock').element.parentElement).toBe(center)
  })

  it('shows each placeholder inside its final container', async () => {
    const w = await mountShell()
    const label = (container: string) =>
      get(w, container).get('[data-test="designer-placeholder-label"]').text()

    expect(label('designer-top')).toBe('TOP')
    expect(get(w, 'designer-top').text()).toContain('Header / MenuBar / Toolbar')
    expect(label('designer-workspace')).toBe('CENTER / WORKSPACE')
    expect(get(w, 'designer-workspace').text()).toContain('FlowTabs / Canvas')
    expect(get(w, 'designer-workspace-flow').text()).toBe('활성 Flow 없음')
    expect(label('designer-bottom-dock')).toBe('BOTTOM')
    expect(get(w, 'designer-bottom-dock').text()).toContain('Inspector / Minimap')
    expect(label('designer-right-dock')).toBe('RIGHT')
    expect(get(w, 'designer-right-dock').text()).toContain('Palette / Git')
    // The left Dock holds the real Explorer, not a placeholder.
    expect(get(w, 'designer-left-dock').find('[data-test^="designer-placeholder-"]').exists()).toBe(false)
  })

  it('applies the initial layout sizes', async () => {
    const w = await mountShell()
    const body = get(w, 'designer-body')
    expect(body.attributes('data-left-width')).toBe('280')
    expect(body.attributes('data-right-width')).toBe('300')
    expect(get(w, 'designer-bottom-dock').attributes('style')).toContain('height: 200px')
  })
})

describe('DesignerShell — left Dock modes', () => {
  it('starts on the Project Explorer and mounts the File Explorer only when its tab first opens', async () => {
    const w = await mountShell()

    expect(get(w, 'designer-left-dock').attributes('data-mode')).toBe('project')
    expect(isShown(get(w, 'project-explorer').element)).toBe(true)
    expect(w.findComponent(ProjectExplorer).props('projectId')).toBe('p')
    expect(w.findComponent(DesignerFileExplorer).exists()).toBe(false)
    expect(w.find('[data-test="designer-file-explorer"]').exists()).toBe(false)

    await selectMode(w, '파일')

    expect(get(w, 'designer-left-dock').attributes('data-mode')).toBe('file')
    expect(isShown(get(w, 'designer-file-explorer').element)).toBe(true)
    expect(w.findComponent(DesignerFileExplorer).props('projectId')).toBe('p')
    // The Project Explorer is hidden, not unmounted.
    expect(w.find('[data-test="project-explorer"]').exists()).toBe(true)
    expect(isShown(get(w, 'project-explorer').element)).toBe(false)
  })

  it('keeps each Explorer state across tab switches without reloading', async () => {
    const w = await mountShell()
    await get(w, 'project-explorer-search').setValue('Canvas')
    await settle()
    const treeCalls = () => getRequest.mock.calls.filter(([url]) => String(url).endsWith('/groups/tree')).length
    const before = treeCalls()

    await selectMode(w, '파일')
    await selectMode(w, '프로젝트')

    expect((get(w, 'project-explorer-search').element as HTMLInputElement).value).toBe('Canvas')
    expect(treeCalls()).toBe(before)
    const fileExplorerEl = get(w, 'designer-file-explorer').element

    await selectMode(w, '파일')
    // Same File Explorer instance as before — not remounted.
    expect(get(w, 'designer-file-explorer').element === fileExplorerEl).toBe(true)
  })
})

describe('DesignerShell — active Flow', () => {
  it('activating a Flow feeds the Project Explorer, File Explorer and Workspace without switching tabs', async () => {
    const w = await mountShell()
    await activateCanvas(w)

    expect(get(w, 'designer-workspace-flow').text()).toBe('활성 Flow: Canvas')
    expect(w.findComponent(ProjectExplorer).props('activeFlowId')).toBe('p.ui.0002')
    expect(explorerRow(w, 'Canvas').element.closest('[data-explorer-row]')!.querySelector('[data-test="project-explorer-active-badge"]')).not.toBeNull()
    // The user's mode choice stays.
    expect(get(w, 'designer-left-dock').attributes('data-mode')).toBe('project')

    await selectMode(w, '파일')
    expect(w.findComponent(DesignerFileExplorer).props('flow')).toEqual({ groupId: 'p.ui.0002', name: 'Canvas' })
    expect(getRequest.mock.calls.some(([url]) => String(url) === '/api/v1/projects/p/git/groups/p.ui.0002/tree')).toBe(true)
    expect(w.find('[data-test="dfx-no-flow"]').exists()).toBe(false)
  })

  it('a single click selects only; the active Flow does not change', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    explorerRow(w, 'Canvas').element.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))
    await settle()
    expect(get(w, 'designer-workspace-flow').text()).toBe('활성 Flow 없음')
    expect(w.findComponent(ProjectExplorer).props('activeFlowId')).toBeNull()
  })

  it('changing the current Project clears the active Flow', async () => {
    const w = await mountShell()
    await activateCanvas(w)
    await selectMode(w, '파일')
    expect(w.findComponent(DesignerFileExplorer).props('flow')).not.toBeNull()

    useProjectStore().setCurrentProject(project('q', 'Beta'))
    await settle()

    expect(get(w, 'designer-workspace-flow').text()).toBe('활성 Flow 없음')
    expect(w.findComponent(DesignerFileExplorer).props('flow')).toBeNull()
    expect(w.findComponent(DesignerFileExplorer).props('projectId')).toBe('q')
    expect(w.findComponent(ProjectExplorer).props('projectId')).toBe('q')
    expect(w.findComponent(ProjectExplorer).props('activeFlowId')).toBeNull()
  })

  it('without a current Project both Explorers show their own empty state', async () => {
    const w = await mountShell(null)
    expect(w.find('[data-test="project-explorer-no-project"]').exists()).toBe(true)
    await selectMode(w, '파일')
    expect(w.find('[data-test="dfx-no-project"]').exists()).toBe(true)
    expect(getRequest).not.toHaveBeenCalled()
  })
})

describe('DesignerShell — left collapse', () => {
  it('collapses to a rail and expands back to the same mode, width and Explorer state', async () => {
    const w = await mountShell()
    await selectMode(w, '파일')
    await selectMode(w, '프로젝트')
    await get(w, 'project-explorer-search').setValue('Canvas')
    await selectMode(w, '파일')
    const fileExplorerEl = get(w, 'designer-file-explorer').element

    await get(w, 'designer-left-collapse').trigger('click')

    const dock = get(w, 'designer-left-dock')
    expect(dock.attributes('data-collapsed')).toBe('true')
    expect(get(w, 'designer-body').attributes('data-left-width')).toBe('40')
    expect(get(w, 'designer-left-rail-mode').text()).toBe('파일')
    expect(get(w, 'designer-left-expand').attributes('aria-label')).toBe('좌측 패널 펼치기')
    // Collapse does not change the mode and keeps both Explorers mounted (hidden).
    expect(dock.attributes('data-mode')).toBe('file')
    expect(w.find('[data-test="project-explorer"]').exists()).toBe(true)
    expect(w.find('[data-test="designer-file-explorer"]').exists()).toBe(true)
    expect(isShown(get(w, 'designer-left-explorers').element)).toBe(false)

    await get(w, 'designer-left-expand').trigger('click')

    expect(dock.attributes('data-collapsed')).toBe('false')
    expect(w.find('[data-test="designer-left-rail"]').exists()).toBe(false)
    expect(get(w, 'designer-body').attributes('data-left-width')).toBe('280')
    expect(dock.attributes('data-mode')).toBe('file')
    expect(isShown(get(w, 'designer-file-explorer').element)).toBe(true)
    expect(get(w, 'designer-file-explorer').element === fileExplorerEl).toBe(true)

    await selectMode(w, '프로젝트')
    expect((get(w, 'project-explorer-search').element as HTMLInputElement).value).toBe('Canvas')
  })

  it('the collapse button sits in the Explorer header with an accessible name', async () => {
    const w = await mountShell()
    const button = get(w, 'designer-explorer-header').get('[data-test="designer-left-collapse"]')
    expect(button.attributes('aria-label')).toBe('좌측 패널 접기')
    expect(get(w, 'designer-explorer-header').text()).toContain('탐색기')
  })
})
