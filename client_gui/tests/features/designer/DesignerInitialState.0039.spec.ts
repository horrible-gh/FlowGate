/**
 * flowgate.v02.0039 T#3 — Designer initial unselected state (0001-R rev1, confirmed design
 * MirageGlass 1jnmunhp v11 `15-designer-shell-initial-state-v7.html`).
 *
 * Entering the Designer in a Project context gives it no work target: nothing is chosen in the
 * Project Explorer and no Flow is open. Every area keeps its frame, but the Center is an empty
 * frame (no Flow tabs, guide text or Canvas), the Inspector keeps only its frame, the Minimap,
 * the right Tool Panel and the left navigation chrome are dimmed and unavailable, the Toolbar is
 * unavailable and only the confirmed menu items stay enabled. The Project tree itself stays
 * shown and clickable.
 * flowgate.v02.0042: a plain click there only selects; opening a Flow (double click / Enter)
 * continues into the existing Designer, and closing the last open Flow returns here.
 *
 * Mounted with the real Shell / parts and the real project store; only the HTTP layer
 * (`@shared/api`) is mocked, as in DesignerShell.0025.spec.ts.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell, ProjectExplorer } from '@/features/designer'
import {
  DESIGNER_MENUS,
  NO_MENU_TARGETS,
  isMenuCommandEnabled,
  isSubmenuEnabled,
  type DesignerMenuEntry,
} from '@/features/designer/top/designerTop'
import { useProjectStore, type Project } from '@/features/project'
import { answerFlowCanvas } from './flowCanvasResponse'

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
    n({ id: 'q.m.0001', parent_id: 'module:q:m', node_type: 'group', label: 'Beta Flow', number: '0001' }),
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

async function mountShell(projectId = 'p') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project(projectId, projectId === 'p' ? 'Alpha' : 'Beta'))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  return wrapper
}

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const isShown = (el: Element) => (el as HTMLElement).style.display !== 'none'
const isDisabled = (el: { attributes: (name: string) => string | undefined }) => el.attributes('disabled') !== undefined

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = w
    .get('[data-test="project-explorer-tree"]')
    .findAll('[data-explorer-row]')
    .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
  if (!found) throw new Error(`row not rendered: ${label}`)
  return found.find('.explorer-row')
}

async function click(w: VueWrapper<any>, label: string) {
  explorerRow(w, label).element.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }))
  await nextTick()
  await settle()
}

/** A real browser double click: click(detail=1), click(detail=2), dblclick. */
async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await nextTick()
  }
  await settle()
}

async function openMenu(w: VueWrapper<any>, key: string) {
  const open = w.find('[data-test="designer-menu-pop"]')
  if (!open.exists() || open.attributes('data-menu') !== key) await w.get(`[data-test="designer-menu"][data-menu="${key}"]`).trigger('click')
  return w.get(`[data-test="designer-menu-pop"][data-menu="${key}"]`)
}

/** Command ids of the enabled / disabled command items of one open menu. */
function commandStates(pop: ReturnType<VueWrapper<any>['get']>) {
  const items = pop.findAll('[data-test="designer-menu-item"][data-command]')
  return {
    enabled: items.filter((el) => !isDisabled(el)).map((el) => el.attributes('data-command')),
    disabled: items.filter((el) => isDisabled(el)).map((el) => el.attributes('data-command')),
  }
}

/** Every area of the confirmed initial-state design, as the user sees it right after entry. */
function expectUnselectedState(w: VueWrapper<any>) {
  // Center: an empty frame — no Flow tabs, no guide text, no Canvas.
  const workspace = get(w, 'designer-workspace')
  expect([...workspace.element.children].map((c) => c.getAttribute('data-test'))).toEqual(['designer-center-unselected'])
  expect(get(w, 'designer-center-unselected').text()).toBe('')
  expect(w.find('[data-test="designer-center"]').exists()).toBe(false)
  expect(w.find('[data-test="designer-flow-tab"]').exists()).toBe(false)
  expect(w.find('[data-test="designer-center-no-flow"]').exists()).toBe(false)
  expect(w.find('[data-test="designer-canvas"]').exists()).toBe(false)

  // Inspector: the bottom frame only — no line, no tabs, no Content.
  const inspector = get(w, 'designer-inspector')
  expect(inspector.attributes('data-unselected')).toBe('true')
  expect(inspector.find('[data-test="designer-inspector-frame"]').exists()).toBe(true)
  expect(inspector.find('[data-test="designer-inspector-empty"]').exists()).toBe(false)
  expect(inspector.find('[data-test="designer-inspector-tabs"]').exists()).toBe(false)
  expect(inspector.find('[data-test="designer-inspector-body"]').exists()).toBe(false)
  expect(inspector.text()).toBe('')

  // Minimap: on screen, dimmed and unavailable.
  const minimap = get(w, 'designer-bottom-minimap')
  expect(minimap.classes()).toContain('is-unavailable')
  expect(minimap.attributes('aria-disabled')).toBe('true')

  // Right Tool Panel: the tab row stays, every tab unavailable, no part shown.
  const right = get(w, 'designer-right-dock')
  expect(right.attributes('data-unselected')).toBe('true')
  const tools = right.findAll('[data-test="designer-right-tool-tabs"] [role="tab"]')
  expect(tools.map((tab) => tab.text())).toEqual(['팔레트', '대화', 'Git'])
  expect(tools.every(isDisabled)).toBe(true)
  for (const pane of right.findAll('[data-test^="designer-right-pane-"]')) expect(isShown(pane.element)).toBe(false)

  // Left navigation: the chrome is unavailable, the Project tree stays shown.
  const left = get(w, 'designer-left-dock')
  expect(left.attributes('data-unselected')).toBe('true')
  expect(left.findAll('[data-test="designer-explorer-mode-tabs"] [role="tab"]').every(isDisabled)).toBe(true)
  expect(isDisabled(get(w, 'designer-left-collapse'))).toBe(true)
  expect(isDisabled(get(w, 'project-explorer-search'))).toBe(false)
  expect(isDisabled(get(w, 'project-explorer-refresh'))).toBe(true)

  // Toolbar: every tool on screen and unavailable.
  const toolbar = get(w, 'designer-toolbar')
  expect(toolbar.attributes('aria-disabled')).toBe('true')
  expect(toolbar.classes()).toContain('is-unavailable')
  const buttons = toolbar.findAll('button')
  // flowgate.v02.0045 TR0020 rev2: + 검증 before the run buttons.
  expect(buttons.length).toBe(13)
  expect(buttons.every(isDisabled)).toBe(true)
}

describe('Designer initial state — no work target right after entry (0039 T#3)', () => {
  it('keeps the whole layout frame: Top + Body, Left + Center + Right, Workspace + Bottom', async () => {
    const w = await mountShell()
    const shell = get(w, 'designer-shell').element
    expect([...shell.children].map((c) => c.getAttribute('data-test'))).toEqual(['designer-top', 'designer-body'])
    expect([...get(w, 'designer-top').element.children].map((c) => c.getAttribute('data-test'))).toEqual([
      'designer-header',
      'designer-menubar',
      'designer-toolbar',
    ])
    // flowgate.v02.0046: a resize handle on each Dock ↔ Center line.
    expect([...get(w, 'designer-body').element.children].map((c) => c.getAttribute('data-test'))).toEqual([
      'designer-left-dock',
      'designer-left-resize',
      'designer-center-column',
      'designer-right-resize',
      'designer-right-dock',
    ])
    expect([...get(w, 'designer-center-column').element.children].map((c) => c.getAttribute('data-test'))).toEqual([
      'designer-workspace',
      'designer-bottom-resize',
      'designer-bottom-dock',
    ])
    expect(get(w, 'designer-bottom-dock').find('[data-test="designer-inspector"]').exists()).toBe(true)
    expect(get(w, 'designer-body').attributes('data-left-width')).toBe('280')
    expect(get(w, 'designer-body').attributes('data-right-width')).toBe('300')
  })

  it('shows every area as the confirmed initial-state design draws it', async () => {
    const w = await mountShell()
    expectUnselectedState(w)
    // On entry no right part has been mounted at all.
    expect(w.find('[data-test^="designer-right-pane-"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-palette"]').exists()).toBe(false)
  })

  it('keeps the Project tree shown and unselected, on the entered Project context', async () => {
    const w = await mountShell()
    const rows = get(w, 'project-explorer-tree').findAll('[data-test="project-explorer-label"]').map((l) => l.text())
    expect(rows).toEqual(['Alpha', 'UI 개선'])
    expect(get(w, 'project-explorer-tree').findAll('[aria-selected="true"]')).toHaveLength(0)
    expect(w.getComponent(ProjectExplorer).props('projectId')).toBe('p')
    expect(useProjectStore().currentProjectId).toBe('p')
    // Nothing is unfolded on entry: only the Project tree was read, no Git / Conversation data.
    expect(getRequest.mock.calls.map(([url]) => String(url)).filter((url) => !url.endsWith('/groups/tree'))).toEqual([])
  })

  // flowgate.v02.0043: the File menu follows each command's own precondition — with the entered
  // Project context the Group / Flow commands and Project 닫기 are available, Group 닫기 waits for a
  // chosen Group and Flow 닫기 for an open Flow (DesignerFileMenu.0043.spec.ts). Edit / Run / View
  // still wait for the work target.
  it('enables only the confirmed menu items', async () => {
    const w = await mountShell()

    const file = await openMenu(w, 'file')
    expect(commandStates(file)).toEqual({
      enabled: ['project.new', 'project.open', 'project.close', 'group.new', 'group.open', 'flow.new', 'flow.open', 'designer.close'],
      disabled: ['group.close', 'flow.close'],
    })
    const subs = file.findAll('[data-test="designer-menu-sub"]')
    expect(subs.map((el) => [el.attributes('data-sub'), isDisabled(el)])).toEqual([
      ['recent-project', false],
      ['recent-group', false],
      ['recent-flow', false],
    ])

    for (const key of ['edit', 'run', 'view']) {
      const pop = await openMenu(w, key)
      expect(commandStates(pop).enabled, key).toEqual([])
    }
    const view = await openMenu(w, 'view')
    expect(isDisabled(view.get('[data-test="designer-menu-sub"][data-sub="density"]'))).toBe(true)

    const windowMenu = await openMenu(w, 'window')
    expect(windowMenu.findAll('.designer-menu-item').every((el) => !isDisabled(el))).toBe(true)
    const help = await openMenu(w, 'help')
    expect(commandStates(help).disabled).toEqual([])
  })

  it('a disabled submenu does not open on hover', async () => {
    const w = await mountShell()
    const view = await openMenu(w, 'view')
    await view.get('[data-sub="density"]').element.parentElement!.dispatchEvent(new MouseEvent('mouseenter'))
    await nextTick()
    expect(w.find('[data-test="designer-menu-subpop"][data-sub="density"]').exists()).toBe(false)

    const file = await openMenu(w, 'file')
    await file.get('[data-test="designer-menu-sub"][data-sub="recent-project"]').trigger('click')
    expect(w.find('[data-test="designer-menu-subpop"][data-sub="recent-project"]').exists()).toBe(true)
  })

  it('the unavailable run buttons do not start a run', async () => {
    const w = await mountShell()
    await w.get('[data-test="designer-tool-run"][data-run="next"]').trigger('click')
    expect(w.get('[data-test="designer-tool-run"][data-run="next"]').attributes('aria-pressed')).toBe('false')
  })
})

// flowgate.v02.0042: only an open Flow is the work target. A plain click (click = select) on a
// Project, Group or Flow row leaves the Designer unselected; a Flow double click / Enter
// (select + open) continues into the existing Designer.
describe('Designer initial state — only an open Flow continues into the Designer (0042)', () => {
  it('one click on the Project, Group or Flow row selects it and keeps the initial state', async () => {
    const w = await mountShell()
    await click(w, 'Alpha')
    expect(explorerRow(w, 'Alpha').classes()).toContain('is-selected')
    expectUnselectedState(w)

    await click(w, 'UI 개선')
    expect(explorerRow(w, 'UI 개선').classes()).toContain('is-selected')
    expectUnselectedState(w)

    await doubleClick(w, 'UI 개선')
    await click(w, 'Canvas')
    expect(explorerRow(w, 'Canvas').classes()).toContain('is-selected')
    expect(w.getComponent(ProjectExplorer).props('activeFlowId')).toBeNull()
    expectUnselectedState(w)
    expect(w.find('[data-test="designer-palette"]').exists()).toBe(false)

    const file = await openMenu(w, 'file')
    expect(commandStates(file).disabled).toEqual(['group.close', 'flow.close'])
    expect(commandStates(file).enabled).not.toContain('flow.close')
  })

  it('a Flow double click opens it and brings back the existing Designer content and controls', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Canvas')

    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))).toEqual(['p.ui.0002'])
    expect(get(w, 'designer-workspace').find('[data-test="designer-center"]').exists()).toBe(true)
    expect(w.find('[data-test="designer-center-unselected"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-canvas"]').exists()).toBe(true)
    expect(w.find('[data-test="designer-center-no-flow"]').exists()).toBe(false)
    // No Node selected: an empty Inspector area, no guidance text.
    expect(get(w, 'designer-inspector-empty').text()).toBe('')
    expect(get(w, 'designer-inspector').text()).toBe('')
    expect(w.find('[data-test="designer-inspector-frame"]').exists()).toBe(false)
    expect(get(w, 'designer-bottom-minimap').classes()).not.toContain('is-unavailable')
    expect(get(w, 'designer-right-dock').attributes('data-unselected')).toBe('false')
    expect(w.find('[data-test="designer-palette"]').exists()).toBe(true)
    expect(get(w, 'designer-left-dock').attributes('data-unselected')).toBe('false')
    expect(get(w, 'designer-explorer-mode-tabs').findAll('[role="tab"]').some(isDisabled)).toBe(false)
    expect(isDisabled(get(w, 'project-explorer-search'))).toBe(false)
    expect(get(w, 'designer-toolbar').attributes('aria-disabled')).toBeUndefined()
    // flowgate.v02.0045 TR0020 rev2: 검증 waits for an edited structure (none on this Flow).
    expect(get(w, 'designer-toolbar').findAll('button:not([data-command="run.validate"])').some(isDisabled)).toBe(false)

    const file = await openMenu(w, 'file')
    // The chosen row is the Flow, not a Group: only Group 닫기 has nothing to act on.
    expect(commandStates(file).disabled).toEqual(['group.close'])
    const run = await openMenu(w, 'run')
    expect(commandStates(run).disabled).toEqual(['run.stop'])
  })

  it('closing the last open Flow returns to the initial state, even with a row still selected', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Canvas')
    await w.get('[data-test="designer-tool-run"][data-run="all"]').trigger('click')
    expect(w.get('[data-test="designer-tool-run"][data-run="all"]').attributes('aria-pressed')).toBe('true')

    await w.get('[data-test="designer-flow-tab"][data-flow-id="p.ui.0002"] [data-test="designer-flow-tab-close"]').trigger('click')
    await settle()

    expect(explorerRow(w, 'Canvas').classes()).toContain('is-selected')
    expectUnselectedState(w)
    // The run state does not outlive its target.
    expect(w.get('[data-test="designer-tool-run"][data-run="all"]').attributes('aria-pressed')).toBe('false')
  })

  it('a Project context change starts the new Project in the initial state again', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Canvas')
    await w.get('[data-test="designer-tool-run"][data-run="all"]').trigger('click')
    expect(w.get('[data-test="designer-tool-run"][data-run="all"]').attributes('aria-pressed')).toBe('true')

    useProjectStore().setCurrentProject(project('q', 'Beta'))
    await settle()

    expectUnselectedState(w)
    // The run state does not outlive its target.
    expect(w.get('[data-test="designer-tool-run"][data-run="all"]').attributes('aria-pressed')).toBe('false')
  })

  it('a Project context change after the Git / Conversation parts were shown reads nothing for the new Project', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Canvas')
    const tool = (label: string) => get(w, 'designer-right-tool-tabs').findAll('[role="tab"]').find((tab) => tab.text() === label)!
    await tool('대화').trigger('click')
    await settle()
    await tool('Git').trigger('click')
    await settle()
    expect(isShown(get(w, 'designer-right-pane-git').element)).toBe(true)
    const urls = () => getRequest.mock.calls.map(([url]) => String(url))
    expect(urls()).toContain('/api/v1/projects/p/git/status')

    getRequest.mockClear()
    useProjectStore().setCurrentProject(project('q', 'Beta'))
    await settle()

    expectUnselectedState(w)
    // The parts stay mounted (hidden) but get no context: only the new Project tree is read.
    expect(w.find('[data-test="designer-right-pane-git"]').exists()).toBe(true)
    expect(w.find('[data-test="designer-right-pane-conversation"]').exists()).toBe(true)
    expect(urls().filter((url) => !url.endsWith('/groups/tree'))).toEqual([])

    // A plain click in the new Project still reads nothing; opening its Flow hands the Git part
    // the new context.
    await click(w, 'Beta')
    expect(urls().filter((url) => !url.endsWith('/groups/tree'))).toEqual([])
    await doubleClick(w, 'm')
    await doubleClick(w, 'Beta Flow')
    expect(isShown(get(w, 'designer-right-pane-git').element)).toBe(true)
    expect(urls()).toContain('/api/v1/projects/q/git/status')
    expect(urls()).not.toContain('/api/v1/projects/p/git/status')
  })
})

describe('ProjectExplorer — reports the user selection (0039 T#3)', () => {
  it('emits update:selection only on an explicit choice, never on mount', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useProjectStore().setCurrentProject(project('p', 'Alpha'))
    wrapper = mount(ProjectExplorer, { props: { projectId: 'p' }, attachTo: document.body, global: { plugins: [i18n, pinia] } })
    await settle()
    expect(wrapper.emitted('update:selection')).toBeUndefined()

    await click(wrapper, 'Alpha')
    await click(wrapper, 'UI 개선')
    expect(wrapper.emitted('update:selection')).toEqual([[{ kind: 'project', id: 'project:p' }], [{ kind: 'group', id: 'module:p:ui' }]])

    await wrapper.setProps({ projectId: 'q' })
    await settle()
    expect(wrapper.emitted('update:selection')!.at(-1)).toEqual([null])
  })

  it('toolsDisabled makes refresh unavailable but leaves the search and the tree usable', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useProjectStore().setCurrentProject(project('p', 'Alpha'))
    wrapper = mount(ProjectExplorer, {
      props: { projectId: 'p', toolsDisabled: true },
      attachTo: document.body,
      global: { plugins: [i18n, pinia] },
    })
    await settle()
    expect(isDisabled(wrapper.get('[data-test="project-explorer-search"]'))).toBe(false)
    expect(isDisabled(wrapper.get('[data-test="project-explorer-refresh"]'))).toBe(true)
    await wrapper.get('[data-test="project-explorer-search"]').setValue('UI')
    await settle()
    expect(wrapper.findAll('[data-explorer-row]').length).toBeGreaterThan(0)
    await wrapper.get('[data-test="project-explorer-search"]').setValue('')
    await settle()
    await click(wrapper, 'Alpha')
    expect(explorerRow(wrapper, 'Alpha').classes()).toContain('is-selected')
  })
})

describe('designerTop model — work target (0039 T#3)', () => {
  const commands = DESIGNER_MENUS.flatMap((menu) => menu.entries).flatMap((entry) =>
    entry.kind === 'command' ? [entry.command] : [],
  )

  // flowgate.v02.0043: targets are the Project context, a chosen Group and the open Flow.
  it('without any target only 새 Project / Project 열기… / Designer 닫기 and Help stay enabled', () => {
    expect(commands.filter((cmd) => isMenuCommandEnabled(cmd, null, NO_MENU_TARGETS))).toEqual([
      'project.new',
      'project.open',
      'designer.close',
      'help.shortcuts',
      'help.guide',
      'help.about',
    ])
    // The run state cannot re-enable anything without a target.
    expect(isMenuCommandEnabled('run.stop', 'next', NO_MENU_TARGETS)).toBe(false)
  })

  it('with the Project context alone the Group / Flow commands and Project 닫기 join; Group / Flow 닫기 wait for theirs', () => {
    const projectOnly = { project: true, group: false, flow: false }
    expect(commands.filter((cmd) => isMenuCommandEnabled(cmd, null, projectOnly))).toEqual([
      'project.new',
      'project.open',
      'project.close',
      'group.new',
      'group.open',
      'flow.new',
      'flow.open',
      'designer.close',
      'help.shortcuts',
      'help.guide',
      'help.about',
    ])
    expect(isMenuCommandEnabled('group.close', null, { project: true, group: true, flow: false })).toBe(true)
    expect(isMenuCommandEnabled('flow.close', null, { project: true, group: false, flow: true })).toBe(true)
    expect(isMenuCommandEnabled('edit.undo', null, { project: true, group: true, flow: false })).toBe(false)
  })

  it('with every target (the default) the run state alone decides, as before', () => {
    expect(commands.filter((cmd) => !isMenuCommandEnabled(cmd, null))).toEqual(['run.stop'])
    expect(commands.filter((cmd) => !isMenuCommandEnabled(cmd, null, { project: true, group: true, flow: true }))).toEqual(['run.stop'])
  })

  it('submenus: 최근 Project always, 최근 Group / Flow with the Project context, density with the open Flow', () => {
    const subs = DESIGNER_MENUS.flatMap((menu) => menu.entries).filter(
      (entry): entry is DesignerMenuEntry => entry.kind === 'recent' || entry.kind === 'density',
    )
    expect(subs.map((entry) => isSubmenuEnabled(entry, NO_MENU_TARGETS))).toEqual([true, false, false, false])
    expect(subs.map((entry) => isSubmenuEnabled(entry, { project: true, group: false, flow: false }))).toEqual([true, true, true, false])
    expect(subs.every((entry) => isSubmenuEnabled(entry))).toBe(true)
  })
})
