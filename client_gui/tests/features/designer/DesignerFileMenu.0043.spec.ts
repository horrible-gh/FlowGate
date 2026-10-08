/**
 * flowgate.v02.0043 — the Designer File menu connected to the product (0001-R).
 *
 * Every item of 파일 — Project / Group / Flow 새로 만들기 · 열기 · 최근 · 닫기 and Designer 닫기 — acts
 * on what already exists: the Project Dialog and its "열기" (0040), the Project Explorer's
 * selection / expansion (0023), the Center session's Flow tabs (0026) and the v0.1 module /
 * group creates. Opening a Flow is the Explorer's double click (select + open); a selection
 * alone still activates nothing (0042). Availability follows each command's precondition.
 *
 * Mounted with the real Shell / parts and the real project store; only the HTTP layer
 * (`@shared/api`) is mocked, as in DesignerInitialState.0039.spec.ts. Dialog content lives
 * behind ModalShell's Teleport, so it is reached through `document`.
 *
 * Review 1841 (TR rev 0): the recent Group entry written when a Flow is opened keeps the Group's
 * name even while the tree cache is empty (a create has just invalidated it), and an explicit
 * open from the File menu is seen in the Explorer even when its search hid the row.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
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

let trees: Record<string, unknown[]>

function baseTrees(): Record<string, unknown[]> {
  return {
    p: [
      n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
      n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
      n({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
      n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
      n({ id: 'module:p:core', parent_id: 'project:p', node_type: 'module', label: 'core' }),
      n({ id: 'p.core.0001', parent_id: 'module:p:core', node_type: 'group', label: 'Engine', number: '0001' }),
    ],
    q: [
      n({ id: 'project:q', parent_id: null, node_type: 'project', label: 'Beta' }),
      n({ id: 'module:q:m', parent_id: 'project:q', node_type: 'module', label: 'm' }),
      n({ id: 'q.m.0001', parent_id: 'module:q:m', node_type: 'group', label: 'Beta Flow', number: '0001' }),
    ],
  }
}

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  setCurrentUser({ username: 'test', email: null, isAdmin: true })
  trees = baseTrees()
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    const tree = /^\/api\/v1\/projects\/([^/]+)\/groups\/tree$/.exec(url)
    if (tree) return { data: { data: { nodes: trees[tree[1]] ?? [] } } }
    const git = /^\/api\/v1\/projects\/([^/]+)\/git\/status$/.exec(url)
    if (git) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    if (url === '/api/v1/projects') return { data: { projects: [] } }
    throw new Error(`unexpected request ${url}`)
  })
  postRequest.mockReset()
  postRequest.mockImplementation(async (url: string) => {
    throw new Error(`unexpected request ${url}`)
  })
})

let wrapper: VueWrapper<any> | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setCurrentUser(null)
  document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
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
const isDisabled = (el: { attributes: (name: string) => string | undefined }) => el.attributes('disabled') !== undefined
const leftDock = (w: VueWrapper<any>) => get(w, 'designer-left-dock')
const rowLabels = (w: VueWrapper<any>) =>
  get(w, 'project-explorer-tree').findAll('[data-test="project-explorer-label"]').map((label) => label.text())

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = get(w, 'project-explorer-tree')
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

/** A real browser double click: click(detail=1), click(detail=2), dblclick. */
async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).find('.explorer-row').element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await nextTick()
  }
  await settle()
}

async function openMenu(w: VueWrapper<any>, key: string) {
  const open = w.find('[data-test="designer-menu-pop"]')
  if (!open.exists() || open.attributes('data-menu') !== key) await w.get(`[data-test="designer-menu"][data-menu="${key}"]`).trigger('click')
  return w.get(`[data-test="designer-menu-pop"][data-menu="${key}"]`)
}

async function chooseCommand(w: VueWrapper<any>, command: string) {
  const pop = await openMenu(w, 'file')
  await pop.get(`[data-command="${command}"]`).trigger('click')
  await settle()
}

async function openRecent(w: VueWrapper<any>, kind: string) {
  const pop = await openMenu(w, 'file')
  await pop.get(`[data-test="designer-menu-sub"][data-sub="recent-${kind}"]`).trigger('click')
  await nextTick()
  return w.get(`[data-test="designer-menu-subpop"][data-sub="recent-${kind}"]`)
}

const recentEntries = (sub: ReturnType<VueWrapper<any>['get']>) =>
  sub.findAll('[data-test="designer-menu-recent"]').map((el) => [el.attributes('data-id'), el.text()])

function commandStates(pop: ReturnType<VueWrapper<any>['get']>) {
  const items = pop.findAll('[data-test="designer-menu-item"][data-command]')
  return {
    enabled: items.filter((el) => !isDisabled(el)).map((el) => el.attributes('data-command')),
    disabled: items.filter((el) => isDisabled(el)).map((el) => el.attributes('data-command')),
  }
}

// Dialog content is teleported to body.
function dialog(selector: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(selector)
  if (!el) throw new Error(`dialog element missing: ${selector}`)
  return el
}

async function clickIn(selector: string) {
  dialog(selector).dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
}

async function typeIn(selector: string, value: string) {
  const input = dialog(selector) as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await settle()
}

const treeRequests = () => getRequest.mock.calls.filter(([url]) => String(url).endsWith('/groups/tree'))

/** The Designer's initial (unselected) state as the confirmed design draws it (0039 / 0042). */
function expectUnselected(w: VueWrapper<any>) {
  expect(leftDock(w).attributes('data-unselected')).toBe('true')
  expect(w.find('[data-test="designer-center-unselected"]').exists()).toBe(true)
  expect(w.find('[data-test="designer-flow-tab"]').exists()).toBe(false)
  expect(get(w, 'designer-toolbar').attributes('aria-disabled')).toBe('true')
}

describe('File menu availability (0043 0001-R §3)', () => {
  it('with the Project context the Group / Flow commands and Project 닫기 are available; Group / Flow 닫기 wait', async () => {
    const w = await mountShell()
    const file = await openMenu(w, 'file')
    expect(commandStates(file)).toEqual({
      enabled: ['project.new', 'project.open', 'project.close', 'group.new', 'group.open', 'flow.new', 'flow.open', 'designer.close'],
      disabled: ['group.close', 'flow.close'],
    })
    expect(file.findAll('[data-test="designer-menu-sub"]').map((el) => [el.attributes('data-sub'), isDisabled(el)])).toEqual([
      ['recent-project', false],
      ['recent-group', false],
      ['recent-flow', false],
    ])
    // Edit / Run / View still wait for the open Flow (0042).
    for (const key of ['edit', 'run', 'view']) expect(commandStates(await openMenu(w, key)).enabled, key).toEqual([])
  })

  it('a chosen Group enables Group 닫기, an open Flow enables Flow 닫기, no Project leaves only the Project entries', async () => {
    const w = await mountShell()
    await click(w, 'UI 개선')
    expect(commandStates(await openMenu(w, 'file')).disabled).toEqual(['flow.close'])
    expectUnselected(w)

    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Canvas')
    expect(commandStates(await openMenu(w, 'file')).disabled).toEqual(['group.close'])
    expect(leftDock(w).attributes('data-unselected')).toBe('false')

    await chooseCommand(w, 'project.close')
    const file = await openMenu(w, 'file')
    expect(commandStates(file).enabled).toEqual(['project.new', 'project.open', 'designer.close'])
    expect(file.findAll('[data-test="designer-menu-sub"]').map((el) => [el.attributes('data-sub'), isDisabled(el)])).toEqual([
      ['recent-project', false],
      ['recent-group', true],
      ['recent-flow', true],
    ])
  })
})

describe('Project commands', () => {
  it('Project 열기… opens the Project Dialog; 새 Project opens it asking for the create child', async () => {
    const w = await mountShell()
    const store = useProjectStore()
    await chooseCommand(w, 'project.open')
    expect(store.dialogOpen).toBe(true)
    expect(store.takeCreateRequest()).toBe(false)
    store.closeDialog()

    await chooseCommand(w, 'project.new')
    expect(store.dialogOpen).toBe(true)
    expect(store.takeCreateRequest()).toBe(true)
    expect(store.takeCreateRequest()).toBe(false)
  })

  it('Project 닫기 clears the Project context: no tree, no tabs, initial state, stored id gone', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Canvas')
    expect(w.findAll('[data-test="designer-flow-tab"]')).toHaveLength(1)

    await chooseCommand(w, 'project.close')
    expect(useProjectStore().currentProjectId).toBeNull()
    expect(localStorage.getItem('fg_current_project_id')).toBeNull()
    expect(w.find('[data-test="project-explorer-tree"]').exists()).toBe(false)
    expectUnselected(w)
  })

  it('최근 Project lists the opened Projects and opens one the way 열기 does: current, selected in the Explorer, not activated', async () => {
    const w = await mountShell()
    const store = useProjectStore()
    expect(recentEntries(await openRecent(w, 'project'))).toEqual([['p', 'Alpha']])

    // Another Project opened (as the Dialog's 열기 does) goes to the front of the list.
    store.openRecentProject({ id: 'q', name: 'Beta' })
    await settle()
    expect(rowLabels(w)).toEqual(['Beta', 'm'])
    expect(explorerRow(w, 'Beta').attributes('aria-selected')).toBe('true')
    expect(recentEntries(await openRecent(w, 'project'))).toEqual([['q', 'Beta'], ['p', 'Alpha']])

    await w.get('[data-test="designer-menu-recent"][data-id="p"]').trigger('click')
    await settle()
    expect(store.currentProjectId).toBe('p')
    expect(localStorage.getItem('fg_current_project_id')).toBe('p')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'core'])
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('true')
    expectUnselected(w)
    expect(store.explicitOpenProjectId).toBeNull()

    // The same Project opened again from the list: it changes no Project, yet is selected again.
    await click(w, 'UI 개선')
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('false')
    const sub = await openRecent(w, 'project')
    await sub.get('[data-test="designer-menu-recent"][data-id="p"]').trigger('click')
    await settle()
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('true')
    expect(store.explicitOpenProjectId).toBeNull()
    expect(recentEntries(await openRecent(w, 'project'))).toEqual([['p', 'Alpha'], ['q', 'Beta']])
  })
})

describe('Group commands', () => {
  it('Group 열기… lists the Groups of the Project; the chosen one is selected and unfolded in the Explorer, and recorded', async () => {
    const w = await mountShell()
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'core'])
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([])

    await chooseCommand(w, 'group.open')
    expect(dialog('[data-test="designer-open-dialog"]').getAttribute('data-kind')).toBe('group')
    const items = [...document.querySelectorAll('[data-test="designer-open-item"]')].map((el) => [el.getAttribute('data-id'), el.textContent?.trim()])
    expect(items).toEqual([['module:p:ui', 'UI 개선2'], ['module:p:core', 'core1']])

    await typeIn('[data-test="designer-open-search"]', 'ui')
    expect([...document.querySelectorAll('[data-test="designer-open-item"]')].map((el) => el.getAttribute('data-id'))).toEqual(['module:p:ui'])

    await clickIn('[data-test="designer-open-item"][data-id="module:p:ui"]')
    expect(document.querySelector('[data-test="designer-open-dialog"]')).toBeNull()
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2', 'Canvas', 'core'])
    expect(explorerRow(w, 'UI 개선').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'UI 개선').attributes('aria-expanded')).toBe('true')
    expect(leftDock(w).attributes('data-mode')).toBe('project')
    // A Group is a selection, not a work target (0042).
    expectUnselected(w)
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([['module:p:ui', 'UI 개선']])
    expect(commandStates(await openMenu(w, 'file')).disabled).toEqual(['flow.close'])
  })

  it('Group 닫기 folds the chosen Group and clears the selection', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    await click(w, 'UI 개선')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2', 'Canvas', 'core'])

    await chooseCommand(w, 'group.close')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'core'])
    expect(get(w, 'project-explorer-tree').findAll('[aria-selected="true"]')).toHaveLength(0)
    expect(commandStates(await openMenu(w, 'file')).disabled).toEqual(['group.close', 'flow.close'])
  })

  it('최근 Group shows the current Project’s Groups only and opens one', async () => {
    const w = await mountShell()
    const store = useProjectStore()
    store.recordRecentGroup({ id: 'module:q:m', name: 'm', projectId: 'q' })
    store.recordRecentGroup({ id: 'module:p:core', name: 'core', projectId: 'p' })
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([['module:p:core', 'core']])

    await w.get('[data-test="designer-menu-recent"][data-id="module:p:core"]').trigger('click')
    await settle()
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'core', 'Engine'])
    expect(explorerRow(w, 'core').attributes('aria-selected')).toBe('true')
  })

  it('새 Group creates the module through the project feature, re-reads the tree and opens the new Group', async () => {
    const w = await mountShell()
    postRequest.mockImplementation(async (url: string, body: Record<string, unknown>) => {
      if (url === '/api/v1/slug/romanize') return { data: { suggested: 'infra' } }
      if (url === '/api/v1/projects/p/modules') {
        trees.p.push(n({ id: `module:p:${body.name}`, parent_id: 'project:p', node_type: 'module', label: body.name, title: body.title }))
        return { data: { ok: true, module_id: `p:${body.name}`, project_id: 'p', name: body.name, title: body.title } }
      }
      throw new Error(`unexpected request ${url}`)
    })
    const treeReadsBefore = treeRequests().length

    await chooseCommand(w, 'group.new')
    expect(dialog('[data-test="designer-group-create"]')).toBeTruthy()
    // Nothing filled: the create is refused with the field errors, no request made.
    await clickIn('[data-test="designer-group-create-submit"]')
    expect(postRequest).not.toHaveBeenCalled()
    expect(dialog('[data-test="designer-group-create"]').textContent).toContain('이름을 입력하세요.')

    await typeIn('[data-test="designer-group-create-title"]', 'Infra')
    await clickIn('[data-test="designer-group-create-suggest"]')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/slug/romanize', { text: 'Infra' })
    expect((dialog('[data-test="designer-group-create-name"]') as HTMLInputElement).value).toBe('infra')

    await typeIn('[data-test="designer-group-create-name"]', 'Infra Key')
    expect(dialog('[data-test="designer-group-create"]').textContent).toContain('영문 소문자, 숫자, -, _ 만 사용할 수 있습니다.')
    await typeIn('[data-test="designer-group-create-name"]', 'infra')

    await clickIn('[data-test="designer-group-create-submit"]')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects/p/modules', { name: 'infra', title: 'Infra' })
    expect(document.querySelector('[data-test="designer-group-create"]')).toBeNull()
    expect(treeRequests().length).toBe(treeReadsBefore + 1)
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'core', 'Infra'])
    expect(explorerRow(w, 'Infra').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Infra').attributes('aria-expanded')).toBe('true')
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([['module:p:infra', 'Infra']])
    expectUnselected(w)
  })

  it('a failed Group create stays in the dialog with the error', async () => {
    const w = await mountShell()
    postRequest.mockRejectedValue(new Error('409'))
    await chooseCommand(w, 'group.new')
    await typeIn('[data-test="designer-group-create-title"]', 'Infra')
    await typeIn('[data-test="designer-group-create-name"]', 'infra')
    await clickIn('[data-test="designer-group-create-submit"]')
    expect(dialog('[data-test="designer-group-create-error"]').textContent).toContain('Failed to create the group.')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'core'])
  })
})

describe('Flow commands', () => {
  it('Flow 열기… lists the Flows under their Groups and opens the chosen one like a double click', async () => {
    const w = await mountShell()
    await chooseCommand(w, 'flow.open')
    expect(dialog('[data-test="designer-open-dialog"]').getAttribute('data-kind')).toBe('flow')
    expect([...document.querySelectorAll('[data-test="designer-open-group"]')].map((el) => el.textContent?.trim())).toEqual(['UI 개선', 'core'])
    expect([...document.querySelectorAll('[data-test="designer-open-item"]')].map((el) => el.getAttribute('data-id'))).toEqual([
      'p.ui.0001', 'p.ui.0002', 'p.core.0001',
    ])

    await typeIn('[data-test="designer-open-search"]', 'canv')
    expect([...document.querySelectorAll('[data-test="designer-open-item"]')].map((el) => el.getAttribute('data-id'))).toEqual(['p.ui.0002'])

    await clickIn('[data-test="designer-open-item"][data-id="p.ui.0002"]')
    expect(document.querySelector('[data-test="designer-open-dialog"]')).toBeNull()
    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))).toEqual(['p.ui.0002'])
    expect(leftDock(w).attributes('data-unselected')).toBe('false')
    expect(w.find('[data-test="designer-canvas"]').exists()).toBe(true)
    // The Explorer shows the opened Flow: its Group unfolded, the row selected and active.
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2', 'Canvas', 'core'])
    expect(explorerRow(w, 'Canvas').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Canvas').attributes('aria-current')).toBe('true')
    expect(recentEntries(await openRecent(w, 'flow'))).toEqual([['p.ui.0002', 'Canvas']])
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([['module:p:ui', 'UI 개선']])
  })

  it('Flow 닫기 closes the active tab; 최근 Flow opens it again', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    await doubleClick(w, 'Canvas')
    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))).toEqual(['p.ui.0001', 'p.ui.0002'])
    expect(recentEntries(await openRecent(w, 'flow'))).toEqual([['p.ui.0002', 'Canvas'], ['p.ui.0001', 'Designer v0.2']])

    await chooseCommand(w, 'flow.close')
    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))).toEqual(['p.ui.0001'])
    await chooseCommand(w, 'flow.close')
    expectUnselected(w)
    expect(commandStates(await openMenu(w, 'file')).disabled).toEqual(['group.close', 'flow.close'])

    const sub = await openRecent(w, 'flow')
    await sub.get('[data-test="designer-menu-recent"][data-id="p.ui.0002"]').trigger('click')
    await settle()
    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))).toEqual(['p.ui.0002'])
    expect(explorerRow(w, 'Canvas').attributes('aria-current')).toBe('true')
    expect(leftDock(w).attributes('data-unselected')).toBe('false')
  })

  it('최근 Flow shows the current Project’s Flows only', async () => {
    const w = await mountShell()
    useProjectStore().recordRecentFlow({ id: 'q.m.0001', name: 'Beta Flow', projectId: 'q', groupId: 'module:q:m' })
    expect(recentEntries(await openRecent(w, 'flow'))).toEqual([])
    expect(w.get('[data-test="designer-menu-subpop"][data-sub="recent-flow"] [data-test="designer-menu-recent-empty"]').text()).toBe('최근 항목 없음')
  })

  it('새 Flow creates the group through the project feature under the chosen Group, re-reads the tree and opens it', async () => {
    const w = await mountShell()
    postRequest.mockImplementation(async (url: string, body: Record<string, unknown>) => {
      if (url === '/api/v1/groups') {
        trees.p.push(n({ id: 'p.core.0002', parent_id: `module:p:${body.module}`, node_type: 'group', label: body.title, number: '0002' }))
        return { data: { group_id: 'p.core.0002', created_at: '2026-10-08T00:00:00+09:00' } }
      }
      throw new Error(`unexpected request ${url}`)
    })
    await click(w, 'core')
    const treeReadsBefore = treeRequests().length

    await chooseCommand(w, 'flow.new')
    const select = dialog('[data-test="designer-flow-create-group"]') as HTMLSelectElement
    expect([...select.options].map((o) => [o.value, o.text])).toEqual([['module:p:ui', 'UI 개선'], ['module:p:core', 'core']])
    // The Group chosen in the Explorer is preselected.
    expect(select.value).toBe('module:p:core')

    await clickIn('[data-test="designer-flow-create-submit"]')
    expect(postRequest).not.toHaveBeenCalled()
    await typeIn('[data-test="designer-flow-create-title"]', 'Scheduler')
    await clickIn('[data-test="designer-flow-create-submit"]')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/groups', {
      project_id: 'p', title: 'Scheduler', module: 'core', parent_id: null, priority: null,
    })
    expect(document.querySelector('[data-test="designer-flow-create"]')).toBeNull()
    expect(treeRequests().length).toBe(treeReadsBefore + 1)
    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => [t.attributes('data-flow-id'), t.find('[role="tab"]').text()])).toEqual([
      ['p.core.0002', 'Scheduler'],
    ])
    expect(leftDock(w).attributes('data-unselected')).toBe('false')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'core', 'Engine', 'Scheduler'])
    expect(explorerRow(w, 'Scheduler').attributes('aria-current')).toBe('true')
    expect(recentEntries(await openRecent(w, 'flow'))).toEqual([['p.core.0002', 'Scheduler']])
    // Review 1841: the create invalidated the cached tree before the Flow was opened, yet the
    // recent Group entry carries the Group's name the dialog showed, not its `module:p:core` id.
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([['module:p:core', 'core']])
  })

  it('a Flow opened while the tree cache is empty keeps the recorded Group name instead of the raw id (review 1841)', async () => {
    const w = await mountShell()
    const store = useProjectStore()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([['module:p:ui', 'UI 개선']])

    // A create through the store drops the Project's cached tree (nothing re-reads it here).
    postRequest.mockResolvedValue({ data: { group_id: 'p.ui.0009', created_at: '2026-10-08T00:00:00+09:00' } })
    await store.createFlow({ projectId: 'p', moduleName: 'ui', title: 'elsewhere' })
    expect(store.getCachedGroupTree('p')).toBeUndefined()

    const sub = await openRecent(w, 'flow')
    await sub.get('[data-test="designer-menu-recent"][data-id="p.ui.0001"]').trigger('click')
    await settle()
    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))).toEqual(['p.ui.0001'])
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([['module:p:ui', 'UI 개선']])
    expect(JSON.parse(localStorage.getItem('fg_recent_groups') ?? '[]')).toEqual([{ id: 'module:p:ui', name: 'UI 개선', projectId: 'p' }])
  })
})

describe('Explorer visibility after a File menu open (review 1841)', () => {
  const search = (w: VueWrapper<any>) => get(w, 'project-explorer-search')
  const searchValue = (w: VueWrapper<any>) => (search(w).element as HTMLInputElement).value

  /** A Flow open so the Explorer tools are available (0039 T#3), then a search typed. */
  async function openAndSearch(w: VueWrapper<any>, query: string) {
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    expect(isDisabled(search(w))).toBe(false)
    await search(w).setValue(query)
    await settle()
  }

  it('Flow 열기… of a Flow the search hides clears the search: the opened Flow is selected and active in the tree', async () => {
    const w = await mountShell()
    await openAndSearch(w, 'engine')
    expect(rowLabels(w)).toEqual(['Alpha', 'core', 'Engine'])

    await chooseCommand(w, 'flow.open')
    await clickIn('[data-test="designer-open-item"][data-id="p.ui.0002"]')
    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))).toEqual(['p.ui.0001', 'p.ui.0002'])
    expect(searchValue(w)).toBe('')
    expect(w.find('[data-test="project-explorer-search-clear"]').exists()).toBe(false)
    // The tree is back to what it was before the search, with the opened Flow's Group unfolded.
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2', 'Canvas', 'core'])
    expect(explorerRow(w, 'Canvas').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Canvas').attributes('aria-current')).toBe('true')
  })

  it('최근 Group outside the search clears the search: the Group is selected and unfolded', async () => {
    const w = await mountShell()
    const store = useProjectStore()
    store.recordRecentGroup({ id: 'module:p:core', name: 'core', projectId: 'p' })
    await openAndSearch(w, 'canv')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Canvas'])

    const sub = await openRecent(w, 'group')
    await sub.get('[data-test="designer-menu-recent"][data-id="module:p:core"]').trigger('click')
    await settle()
    expect(searchValue(w)).toBe('')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2', 'Canvas', 'core', 'Engine'])
    expect(explorerRow(w, 'core').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'core').attributes('aria-expanded')).toBe('true')
    // Still the open Flow's Designer: the Group is a selection, not a work target (0042).
    expect(explorerRow(w, 'Designer v0.2').attributes('aria-current')).toBe('true')
    expect(leftDock(w).attributes('data-unselected')).toBe('false')
  })

  it('최근 Flow still shown by the search keeps the search and shows the Flow selected and active', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    await doubleClick(w, 'Canvas')
    await search(w).setValue('designer')
    await settle()
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2'])

    const sub = await openRecent(w, 'flow')
    await sub.get('[data-test="designer-menu-recent"][data-id="p.ui.0001"]').trigger('click')
    await settle()
    expect(searchValue(w)).toBe('designer')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2'])
    expect(explorerRow(w, 'Designer v0.2').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Designer v0.2').attributes('aria-current')).toBe('true')
  })

  it('최근 Project of the shown Project with a search that has no result clears the search and selects the root', async () => {
    const w = await mountShell()
    const store = useProjectStore()
    await openAndSearch(w, 'zzz')
    expect(w.find('[data-test="project-explorer-no-results"]').exists()).toBe(true)
    expect(w.find('[data-test="project-explorer-tree"]').exists()).toBe(false)

    const sub = await openRecent(w, 'project')
    await sub.get('[data-test="designer-menu-recent"][data-id="p"]').trigger('click')
    await settle()
    expect(store.currentProjectId).toBe('p')
    expect(store.explicitOpenProjectId).toBeNull()
    expect(searchValue(w)).toBe('')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2', 'Canvas', 'core'])
    expect(explorerRow(w, 'Alpha').attributes('aria-selected')).toBe('true')
    // No Project change: the open Flow and its tab stay.
    expect(w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))).toEqual(['p.ui.0001'])
    expect(explorerRow(w, 'Designer v0.2').attributes('aria-current')).toBe('true')
  })

  it('새 Flow while searching clears the search so the new Flow is seen when the tree lands', async () => {
    const w = await mountShell()
    postRequest.mockImplementation(async (url: string, body: Record<string, unknown>) => {
      if (url === '/api/v1/groups') {
        trees.p.push(n({ id: 'p.core.0002', parent_id: `module:p:${body.module}`, node_type: 'group', label: body.title, number: '0002' }))
        return { data: { group_id: 'p.core.0002', created_at: '2026-10-08T00:00:00+09:00' } }
      }
      throw new Error(`unexpected request ${url}`)
    })
    await openAndSearch(w, 'canv')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Canvas'])

    await chooseCommand(w, 'flow.new')
    const select = dialog('[data-test="designer-flow-create-group"]') as HTMLSelectElement
    select.value = 'module:p:core'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await typeIn('[data-test="designer-flow-create-title"]', 'Scheduler')
    await clickIn('[data-test="designer-flow-create-submit"]')
    expect(document.querySelector('[data-test="designer-flow-create"]')).toBeNull()
    expect(searchValue(w)).toBe('')
    expect(rowLabels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2', 'Canvas', 'core', 'Engine', 'Scheduler'])
    expect(explorerRow(w, 'Scheduler').attributes('aria-selected')).toBe('true')
    expect(explorerRow(w, 'Scheduler').attributes('aria-current')).toBe('true')
    expect(recentEntries(await openRecent(w, 'group'))).toEqual([['module:p:core', 'core'], ['module:p:ui', 'UI 개선']])
  })
})

describe('Designer 닫기', () => {
  it('the Shell hands the close to the App Shell', async () => {
    const w = await mountShell()
    await chooseCommand(w, 'designer.close')
    expect(w.emitted('close-designer')).toEqual([[]])
  })

  it('the App Shell leaves the Designer for the Dashboard', async () => {
    window.history.replaceState({}, '', '/app/designer')
    ;(window as unknown as { __accessToken__?: string }).__accessToken__ = 'token-file-menu-test'
    const [{ default: router }, { default: App }] = await Promise.all([import('@/app/router/index'), import('@/app/App.vue')])
    const pinia = createPinia()
    setActivePinia(pinia)
    useProjectStore().setCurrentProject(project('p', 'Alpha'))
    await router.push('/designer')
    wrapper = mount(App, { attachTo: document.body, global: { plugins: [i18n, router, pinia] } })
    await router.isReady()
    await settle()
    expect(router.currentRoute.value.name).toBe('designer')

    await chooseCommand(wrapper, 'designer.close')
    await settle()
    expect(router.currentRoute.value.fullPath).toBe('/')
    expect(wrapper.find('.designer-shell').exists()).toBe(false)
    expect(wrapper.find('.dash').exists()).toBe(true)
    delete (window as unknown as { __accessToken__?: string }).__accessToken__
  })
})

describe('features/designer/fileMenu structure', () => {
  const SRC = resolve(__dirname, '../../../src')
  const DIR = join(SRC, 'features/designer/fileMenu')
  const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])

  it('holds exactly the three File menu dialogs', () => {
    expect(readdirSync(DIR).sort()).toEqual(['DesignerFlowCreateDialog.vue', 'DesignerGroupCreateDialog.vue', 'DesignerOpenDialog.vue'])
  })

  it('imports only vue / vue-i18n, ui primitives, the features/project surface and the Explorer tree model', () => {
    const allowed = [/^vue$/, /^vue-i18n$/, /^\.\.\/\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/, /^@\/features\/project$/, /^\.\.\/projectExplorer\/projectExplorerTree$/]
    for (const name of readdirSync(DIR)) {
      const src = readFileSync(join(DIR, name), 'utf8')
      for (const spec of importsOf(src)) expect(allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
      expect(src, name).not.toMatch(/localStorage|sessionStorage|defineStore|getRequest|postRequest/)
    }
  })
})
