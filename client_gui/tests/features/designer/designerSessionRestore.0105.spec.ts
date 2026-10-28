/**
 * flowgate.v02.0105 T#1 — an F5 brings the Designer's working screen back (NR flowgate.v02.0101.0003 §5.4 / §5.5,
 * Q #510 E-1): the current Project, its Explorer Group context (selection and open Groups), the Flow tabs in order,
 * the active Flow and the left / right panel tabs, kept per browser tab for the signed-in user.
 *
 * Mounted with the real Shell / parts and the real project store; only the HTTP layer (`@shared/api`) is mocked, as
 * in DesignerFileMenu.0043.spec.ts. An F5 is a new app state (a new Pinia, so no current Project) and a new Shell in
 * the same browser tab (the same sessionStorage).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { clearStoredAuth } from '@/core/auth/logout'
import { setCurrentUser } from '@/core/auth/currentUser'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'
import { parseDesignerSession } from '@/features/project/designerSessionStorage'
import { answerFlowCanvas } from './flowCanvasResponse'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  putRequest,
  serverLogout: vi.fn(),
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

const KEY = 'flowgate.user.test.designer.session'

function n(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

// The pruned tree (what the Explorer reads); `terminal` rows come only with include_terminal=true.
const TREES: Record<string, unknown[]> = {
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
const TERMINAL: Record<string, unknown[]> = {
  p: [n({ id: 'p.ui.0003', parent_id: 'module:p:ui', node_type: 'group', label: 'Archived', number: '0003' })],
}

type Hook<A extends unknown[]> = ((...args: A) => Promise<unknown> | undefined) | null
// A test's own answer for a tree read (pid, include_terminal) or a Canvas read (flowId); undefined = the default.
let treeHook: Hook<[string, boolean]> = null
let canvasHook: Hook<[string]> = null

const axiosError = (status: number) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { isAxiosError: true, response: { status, data: {} } })
const networkError = () => Object.assign(new Error('Network Error'), { isAxiosError: true })

function deferred<T = unknown>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const treeAnswer = (pid: string, full: boolean) => ({
  data: { data: { nodes: [...(TREES[pid] ?? []), ...(full ? TERMINAL[pid] ?? [] : [])] } },
})

function project(id: string): Project {
  return { id, name: id === 'p' ? 'Alpha' : 'Beta', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  i18n.global.locale.value = 'ko'
  setCurrentUser({ username: 'test', email: null, isAdmin: true })
  treeHook = null
  canvasHook = null
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    const tree = /^\/api\/v1\/projects\/([^/]+)\/groups\/tree$/.exec(url)
    if (tree) {
      const full = params.include_terminal === 'true'
      return (await treeHook?.(tree[1], full)) ?? treeAnswer(tree[1], full)
    }
    const canvas = /^\/api\/v1\/flows\/([^/]+)\/canvas$/.exec(url)
    if (canvas) {
      const answer = await canvasHook?.(decodeURIComponent(canvas[1]))
      if (answer) return answer
    }
    const git = /^\/api\/v1\/projects\/([^/]+)\/git\/status$/.exec(url)
    if (git) return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    if (url === '/api/v1/projects') return { data: { projects: [] } }
    const answer = answerFlowCanvas(url)
    if (answer) return answer
    throw new Error(`unexpected request ${url}`)
  })
  postRequest.mockReset()
  postRequest.mockImplementation(async (url: string) => {
    throw new Error(`unexpected request ${url}`)
  })
  putRequest.mockReset()
  putRequest.mockImplementation(async (url: string) => {
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
  for (let i = 0; i < 8; i += 1) await flushPromises()
}

/** A Designer entry: a new app state, the Project already current only when given (the Dialog's 열기). */
async function mountShell(projectId: string | null = null) {
  wrapper?.unmount()
  const pinia = createPinia()
  setActivePinia(pinia)
  if (projectId) useProjectStore().setCurrentProject(project(projectId))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  return wrapper
}

/** F5: the same browser tab (its sessionStorage) with a new app state and no current Project. */
const reload = () => mountShell(null)

function seed(partial: Record<string, unknown>) {
  sessionStorage.setItem(
    KEY,
    JSON.stringify({
      v: 1,
      projectId: 'p',
      explorer: { selection: null, expandedGroupIds: [] },
      tabs: [],
      activeFlowId: null,
      leftMode: 'project',
      rightTool: 'palette',
      ...partial,
    }),
  )
}

const tab = (flowId: string, name = flowId, groupId: string | null = null) => ({ flowId, name, groupId })
const stored = () => {
  const raw = sessionStorage.getItem(KEY)
  return raw ? JSON.parse(raw) : null
}

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const tabIds = (w: VueWrapper<any>) => w.findAll('[data-test="designer-flow-tab"]').map((t) => t.attributes('data-flow-id'))
const activeTab = (w: VueWrapper<any>) =>
  w.findAll('[data-test="designer-flow-tab"]').find((t) => t.find('[role="tab"]').attributes('aria-selected') === 'true')?.attributes('data-flow-id') ?? null
const leftMode = (w: VueWrapper<any>) => get(w, 'designer-left-dock').attributes('data-mode')
const rightTool = (w: VueWrapper<any>) => get(w, 'designer-right-dock').attributes('data-tool')
const selectedRows = (w: VueWrapper<any>) =>
  w.findAll('[data-test="project-explorer-tree"] [data-explorer-row][aria-selected="true"]').map((r) => r.find('[data-test="project-explorer-label"]').text())

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = get(w, 'project-explorer-tree')
    .findAll('[data-explorer-row]')
    .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
  if (!found) throw new Error(`row not rendered: ${label}`)
  return found
}

/** A real browser double click: click(detail=1), click(detail=2), dblclick. A Group opens (selected + unfolded). */
async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).find('.explorer-row').element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await nextTick()
  }
  await settle()
}

async function chooseCommand(w: VueWrapper<any>, command: string) {
  const open = w.find('[data-test="designer-menu-pop"]')
  if (!open.exists() || open.attributes('data-menu') !== 'file') await w.get('[data-test="designer-menu"][data-menu="file"]').trigger('click')
  await w.get('[data-test="designer-menu-pop"][data-menu="file"]').get(`[data-command="${command}"]`).trigger('click')
  await settle()
}

async function pickTab(w: VueWrapper<any>, strip: string, index: number) {
  await w.findAll(`[data-test="${strip}"] [role="tab"]`)[index].trigger('click')
  await settle()
}

const treeReads = (full: boolean) =>
  getRequest.mock.calls.filter(([url, params]) => String(url).endsWith('/groups/tree') && (params?.include_terminal === 'true') === full)
const canvasReads = (flowId: string) => getRequest.mock.calls.filter(([url]) => url === `/api/v1/flows/${flowId}/canvas`)

describe('F5 restore of the Designer screen (NR 0101-0003 §5.5)', () => {
  it('기본: tabs in order, the active Flow and both panel tabs come back — no run, no draft save, no duplicate tab', async () => {
    let w = await mountShell('p')
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    await doubleClick(w, 'Canvas')
    await doubleClick(w, 'core')
    await doubleClick(w, 'Engine')
    await w.findAll('[data-test="designer-flow-tab"] [role="tab"]')[1].trigger('click')
    await settle()
    await pickTab(w, 'designer-explorer-mode-tabs', 1)
    await pickTab(w, 'designer-right-tool-tabs', 2)
    expect([tabIds(w), activeTab(w), leftMode(w), rightTool(w)]).toEqual([['p.ui.0001', 'p.ui.0002', 'p.core.0001'], 'p.ui.0002', 'file', 'git'])

    postRequest.mockClear()
    putRequest.mockClear()
    w = await reload()

    expect(useProjectStore().currentProjectId).toBe('p')
    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.ui.0002', 'p.core.0001'])
    expect(activeTab(w)).toBe('p.ui.0002')
    expect(leftMode(w)).toBe('file')
    expect(rightTool(w)).toBe('git')
    // Opening is read-only: no run start, no draft save (no seed node), and the restore records no recent entry.
    expect(postRequest).not.toHaveBeenCalled()
    expect(putRequest).not.toHaveBeenCalled()
    expect(stored()).toMatchObject({ projectId: 'p', activeFlowId: 'p.ui.0002', leftMode: 'file', rightTool: 'git' })
    expect(stored().tabs.map((t: { flowId: string }) => t.flowId)).toEqual(['p.ui.0001', 'p.ui.0002', 'p.core.0001'])
    // 순서 경쟁: the Project watch's setProject and the Explorer's Project change did not clear what was restored.
    await settle()
    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.ui.0002', 'p.core.0001'])
  })

  it('그룹만 열어 둠: a Group opened without a Flow comes back selected and unfolded', async () => {
    let w = await mountShell('p')
    await doubleClick(w, 'UI 개선')
    expect(tabIds(w)).toEqual([])

    w = await reload()
    expect(useProjectStore().currentProjectId).toBe('p')
    expect(selectedRows(w)).toEqual(['UI 개선'])
    expect(explorerRow(w, 'UI 개선').attributes('aria-expanded')).toBe('true')
    expect(tabIds(w)).toEqual([])
    // The restore is not an opening: no recent Group entry was written for it.
    expect(useProjectStore().recentGroups).toEqual([])
  })

  it('그룹 닫기 후 F5: the closed Group stays folded and unselected, the other one stays open', async () => {
    let w = await mountShell('p')
    await doubleClick(w, 'core')
    await doubleClick(w, 'UI 개선')
    await chooseCommand(w, 'group.close')

    w = await reload()
    expect(explorerRow(w, 'UI 개선').attributes('aria-expanded')).toBe('false')
    expect(explorerRow(w, 'core').attributes('aria-expanded')).toBe('true')
    expect(selectedRows(w)).toEqual([])
  })

  it('Flow 닫기 후 F5: a closed tab does not come back', async () => {
    let w = await mountShell('p')
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    await doubleClick(w, 'Canvas')
    await chooseCommand(w, 'flow.close')
    expect(tabIds(w)).toEqual(['p.ui.0001'])

    w = await reload()
    expect(tabIds(w)).toEqual(['p.ui.0001'])
    expect(activeTab(w)).toBe('p.ui.0001')
  })

  it('Project 닫기 / Designer 닫기 drop the snapshot: F5 shows no Project', async () => {
    let w = await mountShell('p')
    await doubleClick(w, 'UI 개선')
    await chooseCommand(w, 'project.close')
    expect(stored()).toBeNull()
    w = await reload()
    expect(useProjectStore().currentProjectId).toBeNull()
    expect(treeReads(true)).toHaveLength(0)

    w = await mountShell('p')
    await doubleClick(w, 'UI 개선')
    expect(stored()).not.toBeNull()
    await chooseCommand(w, 'designer.close')
    expect(w.emitted('close-designer')).toHaveLength(1)
    expect(stored()).toBeNull()
    await reload()
    expect(useProjectStore().currentProjectId).toBeNull()
  })

  it('로그아웃: the clean-up drops the user\'s snapshot of this browser tab', async () => {
    seed({ tabs: [tab('p.ui.0001')] })
    sessionStorage.setItem('flowgate.user.other.designer.session', 'kept')
    clearStoredAuth('test')
    expect(sessionStorage.getItem(KEY)).toBeNull()
    expect(sessionStorage.getItem('flowgate.user.other.designer.session')).toBe('kept')
  })

  it('가지치기에서 빠진 유효 Flow: a Flow of a final-approved Group is restored from the complete tree', async () => {
    seed({ tabs: [tab('p.ui.0003', 'Archived', 'module:p:ui'), tab('p.ui.0001')], activeFlowId: 'p.ui.0003' })
    const w = await reload()
    expect(tabIds(w)).toEqual(['p.ui.0003', 'p.ui.0001'])
    expect(activeTab(w)).toBe('p.ui.0003')
    expect(treeReads(true).map(([, params]) => params)).toEqual([{ include_terminal: 'true', force: 'true' }])
  })

  it('일부 삭제: a Flow missing from the tree is left out and the first tab becomes active', async () => {
    seed({ tabs: [tab('p.ui.0001'), tab('p.ui.0099'), tab('p.core.0001')], activeFlowId: 'p.ui.0099' })
    const w = await reload()
    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.core.0001'])
    expect(activeTab(w)).toBe('p.ui.0001')
    expect(canvasReads('p.ui.0099')).toHaveLength(0)
    expect(stored().tabs.map((t: { flowId: string }) => t.flowId)).toEqual(['p.ui.0001', 'p.core.0001'])
  })

  it('권한 소실: a Flow in the tree whose Graph read is refused (403) closes and leaves the snapshot', async () => {
    canvasHook = (flowId) => (flowId === 'p.core.0001' ? Promise.reject(axiosError(403)) : undefined)
    seed({ tabs: [tab('p.ui.0001'), tab('p.core.0001')], activeFlowId: 'p.ui.0001' })
    const w = await reload()
    expect(tabIds(w)).toEqual(['p.ui.0001'])
    expect(w.find('[data-test="designer-canvas-retry"]').exists()).toBe(false)
    expect(stored().tabs.map((t: { flowId: string }) => t.flowId)).toEqual(['p.ui.0001'])
  })

  it('프로젝트 권한 소실: a refused tree (403) leaves no Project and drops the snapshot', async () => {
    treeHook = (pid) => (pid === 'p' ? Promise.reject(axiosError(403)) : undefined)
    seed({ tabs: [tab('p.ui.0001')], explorer: { selection: { kind: 'group', id: 'module:p:ui' }, expandedGroupIds: ['module:p:ui'] } })
    const w = await reload()
    expect(useProjectStore().currentProjectId).toBeNull()
    expect(stored()).toBeNull()
    expect(tabIds(w)).toEqual([])
    expect(canvasReads('p.ui.0001')).toHaveLength(0)
  })

  it('일시 실패: a failed tree read keeps the snapshot; the Explorer\'s successful retry restores', async () => {
    let failing = true
    treeHook = () => (failing ? Promise.reject(networkError()) : undefined)
    seed({ tabs: [tab('p.ui.0001'), tab('p.ui.0002')], activeFlowId: 'p.ui.0002', explorer: { selection: null, expandedGroupIds: ['module:p:core'] } })
    const before = sessionStorage.getItem(KEY)
    const w = await reload()
    expect(useProjectStore().currentProjectId).toBe('p')
    expect(tabIds(w)).toEqual([])
    expect(sessionStorage.getItem(KEY)).toBe(before)

    failing = false
    await get(w, 'project-explorer-retry').trigger('click')
    await settle()
    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.ui.0002'])
    expect(activeTab(w)).toBe('p.ui.0002')
    expect(explorerRow(w, 'core').attributes('aria-expanded')).toBe('true')
    expect(stored().pending).toBeUndefined()
  })

  it('일시 실패 (Graph 503): the error tab stays with its retry and in the snapshot, and the retry recovers it', async () => {
    let failing = true
    canvasHook = (flowId) => (failing && flowId === 'p.ui.0001' ? Promise.reject(axiosError(503)) : undefined)
    seed({ tabs: [tab('p.ui.0001')], activeFlowId: 'p.ui.0001' })
    const w = await reload()
    expect(tabIds(w)).toEqual(['p.ui.0001'])
    expect(w.find('[data-test="designer-canvas-retry"]').exists()).toBe(true)
    expect(stored().tabs.map((t: { flowId: string }) => t.flowId)).toEqual(['p.ui.0001'])

    failing = false
    await get(w, 'designer-canvas-retry').trigger('click')
    await settle()
    expect(w.find('[data-test="designer-canvas-retry"]').exists()).toBe(false)
    expect(tabIds(w)).toEqual(['p.ui.0001'])
  })

  it('잘못된 연결: a Flow of another Project in the snapshot is never opened', async () => {
    seed({ tabs: [tab('q.m.0001', 'Beta Flow'), tab('p.ui.0001')], explorer: { selection: { kind: 'flow', id: 'q.m.0001' }, expandedGroupIds: ['module:q:m'] } })
    const w = await reload()
    expect(tabIds(w)).toEqual(['p.ui.0001'])
    expect(canvasReads('q.m.0001')).toHaveLength(0)
    expect(selectedRows(w)).toEqual([])
  })

  it('an unknown schema or an unreadable value is ignored', async () => {
    sessionStorage.setItem(KEY, '{not json')
    await reload()
    expect(useProjectStore().currentProjectId).toBeNull()
    seed({ v: 2 })
    await reload()
    expect(useProjectStore().currentProjectId).toBeNull()
    expect(treeReads(true)).toHaveLength(0)
    expect(parseDesignerSession(JSON.stringify({ v: 1, projectId: 'p' }))).toBeNull()
  })
})

describe('restore generation: switching, closing and the user\'s own changes (NR 0101-0003 §5.4)', () => {
  it('복원 중 프로젝트 전환: a late success of P changes nothing of Q; a late 403 keeps Q\'s snapshot', async () => {
    for (const late of ['success', '403', 'transient'] as const) {
      const pending = deferred()
      treeHook = (pid, full) => (pid === 'p' && full ? pending.promise : undefined)
      seed({ tabs: [tab('p.ui.0001')], activeFlowId: 'p.ui.0001', explorer: { selection: { kind: 'group', id: 'module:p:ui' }, expandedGroupIds: ['module:p:ui'] }, leftMode: 'file', rightTool: 'git' })
      const w = await reload()
      useProjectStore().setCurrentProject(project('q'))
      await settle()
      expect(stored().projectId).toBe('q')

      if (late === 'success') pending.resolve(treeAnswer('p', true))
      else pending.reject(late === '403' ? axiosError(403) : networkError())
      await settle()
      // A later tree read of P (the one a waiting restore would retry on) does not start it again.
      if (late === 'transient') {
        await useProjectStore().fetchGroupTree('p', { force: true })
        await settle()
      }

      expect(useProjectStore().currentProjectId).toBe('q')
      expect(tabIds(w)).toEqual([])
      expect(selectedRows(w)).toEqual([])
      expect([leftMode(w), rightTool(w)]).toEqual(['project', 'palette'])
      expect(stored()).toMatchObject({ projectId: 'q', tabs: [] })
      expect(stored().pending).toBeUndefined()
      expect(treeReads(true).filter(([url]) => String(url).includes('/projects/p/'))).toHaveLength(1)
      getRequest.mockClear()
    }
  })

  it('복원 중 명시적 닫기: a late answer or retry never brings the snapshot back; F5 shows no Project', async () => {
    for (const command of ['project.close', 'designer.close']) {
      const pending = deferred()
      treeHook = (pid, full) => (pid === 'p' && full ? pending.promise : undefined)
      seed({ tabs: [tab('p.ui.0001')], activeFlowId: 'p.ui.0001' })
      const w = await reload()
      await chooseCommand(w, command)
      expect(stored()).toBeNull()
      pending.resolve(treeAnswer('p', true))
      await settle()
      expect(stored()).toBeNull()
      await reload()
      expect(useProjectStore().currentProjectId).toBeNull()
    }

    // Closed while the restore waits for the Explorer's retry (`pending`).
    let failing = true
    treeHook = () => (failing ? Promise.reject(networkError()) : undefined)
    seed({ tabs: [tab('p.ui.0001')] })
    const w = await reload()
    expect(useProjectStore().currentProjectId).toBe('p')
    await chooseCommand(w, 'project.close')
    failing = false
    useProjectStore().fetchGroupTree('p', { force: true })
    await settle()
    expect(stored()).toBeNull()
    expect(tabIds(w)).toEqual([])
    await reload()
    expect(useProjectStore().currentProjectId).toBeNull()
  })

  it('복원 대기 중 사용자 동작: the user\'s Flow stays active, the left tab stays, restored tabs follow without duplicates', async () => {
    const pending = deferred()
    treeHook = (pid, full) => (pid === 'p' && full ? pending.promise : undefined)
    seed({
      tabs: [tab('p.ui.0001'), tab('p.ui.0002'), tab('p.core.0001')],
      activeFlowId: 'p.core.0001',
      explorer: { selection: { kind: 'group', id: 'module:p:core' }, expandedGroupIds: ['module:p:core'] },
      leftMode: 'project',
      rightTool: 'git',
    })
    const w = await reload()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Canvas')
    await pickTab(w, 'designer-explorer-mode-tabs', 1)
    // Saved from the user's first change on, with the items the restore has not confirmed kept as pending.
    expect(stored().tabs.map((t: { flowId: string }) => t.flowId)).toEqual(['p.ui.0002'])
    expect(stored().pending.tabs.map((t: { flowId: string }) => t.flowId)).toEqual(['p.ui.0001', 'p.ui.0002', 'p.core.0001'])

    pending.resolve(treeAnswer('p', true))
    await settle()
    expect(tabIds(w)).toEqual(['p.ui.0002', 'p.ui.0001', 'p.core.0001'])
    expect(activeTab(w)).toBe('p.ui.0002')
    expect(leftMode(w)).toBe('file')
    // Not changed by the user: the right tab comes back.
    expect(rightTool(w)).toBe('git')
    expect(stored()).toMatchObject({ activeFlowId: 'p.ui.0002', leftMode: 'file', rightTool: 'git' })
    expect(stored().tabs.map((t: { flowId: string }) => t.flowId)).toEqual(['p.ui.0002', 'p.ui.0001', 'p.core.0001'])
    expect(stored().pending).toBeUndefined()
  })

  it('개별 Graph 읽기 지연 중 전환: a late 403 of P\'s Flow touches neither Q\'s tabs nor its snapshot', async () => {
    const graph = deferred()
    canvasHook = (flowId) => (flowId === 'p.core.0001' ? graph.promise : undefined)
    seed({ tabs: [tab('p.ui.0001'), tab('p.core.0001')], activeFlowId: 'p.ui.0001' })
    const w = await reload()
    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.core.0001'])

    useProjectStore().setCurrentProject(project('q'))
    await settle()
    await doubleClick(w, 'm')
    await doubleClick(w, 'Beta Flow')
    graph.reject(axiosError(403))
    await settle()
    expect(tabIds(w)).toEqual(['q.m.0001'])
    expect(stored()).toMatchObject({ projectId: 'q', activeFlowId: 'q.m.0001' })
    expect(stored().tabs.map((t: { flowId: string }) => t.flowId)).toEqual(['q.m.0001'])
  })

  it('a logout while the restore waits: the late tree changes nothing and writes no snapshot', async () => {
    const pending = deferred()
    treeHook = (pid, full) => (pid === 'p' && full ? pending.promise : undefined)
    seed({ tabs: [tab('p.ui.0001')] })
    const w = await reload()
    clearStoredAuth('test')
    setCurrentUser(null)
    pending.resolve(treeAnswer('p', true))
    await settle()
    expect(tabIds(w)).toEqual([])
    expect(stored()).toBeNull()
  })
})

describe('restore rework (TR0004 rev1): closed Flows, untouched items, changed-back items, folded Groups, retry', () => {
  /** A single click on a row: selection only. */
  async function click(w: VueWrapper<any>, label: string) {
    explorerRow(w, label).find('.explorer-row').element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }))
    await settle()
  }
  const flowIds = (tabs: { flowId: string }[]) => tabs.map((t) => t.flowId)

  it('a Flow the user closed while the restore waits is not opened again — on the late answer nor on another F5', async () => {
    for (const when of ['answer', 'f5'] as const) {
      const pending = deferred()
      treeHook = (pid, full) => (pid === 'p' && full ? pending.promise : undefined)
      seed({ tabs: [tab('p.ui.0001'), tab('p.ui.0002')], activeFlowId: 'p.ui.0002' })
      let w = await reload()
      await doubleClick(w, 'UI 개선')
      await doubleClick(w, 'Canvas')
      await chooseCommand(w, 'flow.close')
      expect(tabIds(w)).toEqual([])
      expect(flowIds(stored().pending.tabs)).toEqual(['p.ui.0001'])

      if (when === 'answer') {
        pending.resolve(treeAnswer('p', true))
        await settle()
      } else {
        treeHook = null
        w = await reload()
      }
      expect(tabIds(w)).toEqual(['p.ui.0001'])
      expect(flowIds(stored().tabs)).toEqual(['p.ui.0001'])
      expect(stored().pending).toBeUndefined()
    }
  })

  it('the first change while the restore waits keeps the active Flow and both panel tabs it has not restored yet (next F5)', async () => {
    const pending = deferred()
    treeHook = (pid, full) => (pid === 'p' && full ? pending.promise : undefined)
    seed({ tabs: [tab('p.ui.0001'), tab('p.ui.0002')], activeFlowId: 'p.ui.0002', leftMode: 'file', rightTool: 'git' })
    let w = await reload()
    await click(w, 'core')
    expect(stored()).toMatchObject({
      activeFlowId: null,
      rightTool: 'palette',
      explorer: { selection: { kind: 'group', id: 'module:p:core' } },
      pending: { activeFlowId: 'p.ui.0002', leftMode: 'file', rightTool: 'git' },
    })

    treeHook = null
    w = await reload()
    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.ui.0002'])
    expect(activeTab(w)).toBe('p.ui.0002')
    expect([leftMode(w), rightTool(w)]).toEqual(['file', 'git'])
    expect(stored()).toMatchObject({ activeFlowId: 'p.ui.0002', leftMode: 'file', rightTool: 'git', explorer: { selection: { kind: 'group', id: 'module:p:core' } } })
    expect(stored().pending).toBeUndefined()
  })

  it('an item the user changed and changed back stays the user\'s: 대화 → 팔레트 is not replaced by the saved Git', async () => {
    const pending = deferred()
    treeHook = (pid, full) => (pid === 'p' && full ? pending.promise : undefined)
    seed({ tabs: [tab('p.core.0001')], activeFlowId: 'p.core.0001', rightTool: 'git' })
    const w = await reload()
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    await pickTab(w, 'designer-right-tool-tabs', 1)
    await pickTab(w, 'designer-right-tool-tabs', 0)
    expect(rightTool(w)).toBe('palette')
    expect(stored().pending.rightTool).toBeUndefined()

    pending.resolve(treeAnswer('p', true))
    await settle()
    expect(rightTool(w)).toBe('palette')
    expect(tabIds(w)).toEqual(['p.ui.0001', 'p.core.0001'])
    expect(activeTab(w)).toBe('p.ui.0001')
    expect(stored().rightTool).toBe('palette')
  })

  it('a Group folded over its selected Flow stays folded after F5', async () => {
    let w = await mountShell('p')
    await doubleClick(w, 'UI 개선')
    await click(w, 'Designer v0.2')
    await explorerRow(w, 'UI 개선').get('[data-test="project-explorer-caret"]').trigger('click')
    await settle()
    expect(stored().explorer).toEqual({ selection: { kind: 'flow', id: 'p.ui.0001' }, expandedGroupIds: [] })

    w = await reload()
    expect(explorerRow(w, 'UI 개선').attributes('aria-expanded')).toBe('false')
    expect(stored().explorer).toEqual({ selection: { kind: 'flow', id: 'p.ui.0001' }, expandedGroupIds: [] })
  })

  it('only the complete tree fails: the Explorer shows the failure with its retry (refresh enabled), and that retry restores', async () => {
    let failing = true
    treeHook = (pid, full) => (pid === 'p' && full && failing ? Promise.reject(networkError()) : undefined)
    seed({ tabs: [tab('p.ui.0003', 'Archived', 'module:p:ui'), tab('p.ui.0001')], activeFlowId: 'p.ui.0003' })
    const before = sessionStorage.getItem(KEY)
    const w = await reload()
    expect(tabIds(w)).toEqual([])
    expect(w.find('[data-test="project-explorer-error"]').exists()).toBe(false)
    expect(w.find('[data-test="project-explorer-refresh-error"]').exists()).toBe(true)
    expect(get(w, 'project-explorer-refresh').attributes('disabled')).toBeUndefined()
    expect(sessionStorage.getItem(KEY)).toBe(before)

    failing = false
    const reads = treeReads(true).length
    await get(w, 'project-explorer-refresh-retry').trigger('click')
    await settle()
    expect(treeReads(true).length).toBe(reads + 1)
    expect(tabIds(w)).toEqual(['p.ui.0003', 'p.ui.0001'])
    expect(activeTab(w)).toBe('p.ui.0003')
    expect(w.find('[data-test="project-explorer-refresh-error"]').exists()).toBe(false)
    expect(stored().pending).toBeUndefined()
  })
})

describe('Q #510 contract and browser tab isolation (NR 0101-0003 §5.5 / §5.6)', () => {
  it('A → B in one browser tab, then F5: only B comes back; choosing A again restores nothing of A', async () => {
    let w = await mountShell('p')
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    useProjectStore().setCurrentProject(project('q'))
    await settle()
    await doubleClick(w, 'm')
    await doubleClick(w, 'Beta Flow')

    w = await reload()
    expect(useProjectStore().currentProjectId).toBe('q')
    expect(tabIds(w)).toEqual(['q.m.0001'])

    useProjectStore().setCurrentProject(project('p'))
    await settle()
    expect(tabIds(w)).toEqual([])
    expect(selectedRows(w)).toEqual([])
    expect(explorerRow(w, 'UI 개선').attributes('aria-expanded')).toBe('false')
  })

  it('two browser tabs (P, Q): each F5 restores its own Project; nothing is shared through localStorage', async () => {
    let w = await mountShell('p')
    await doubleClick(w, 'UI 개선')
    await doubleClick(w, 'Designer v0.2')
    const tabP = sessionStorage.getItem(KEY)

    sessionStorage.clear() // the second browser tab starts with its own, empty sessionStorage
    w = await mountShell('q')
    await doubleClick(w, 'm')
    await doubleClick(w, 'Beta Flow')
    const tabQ = sessionStorage.getItem(KEY)
    expect(Object.keys(localStorage).filter((key) => key.includes('designer'))).toEqual([])

    sessionStorage.setItem(KEY, tabP!)
    w = await reload()
    expect([useProjectStore().currentProjectId, tabIds(w)]).toEqual(['p', ['p.ui.0001']])

    sessionStorage.setItem(KEY, tabQ!)
    w = await reload()
    expect([useProjectStore().currentProjectId, tabIds(w)]).toEqual(['q', ['q.m.0001']])
  })

  it('a Designer entry with another Project already current (열기) does not restore and replaces the snapshot', async () => {
    seed({ tabs: [tab('p.ui.0001')], activeFlowId: 'p.ui.0001' })
    const w = await mountShell('q')
    expect(tabIds(w)).toEqual([])
    expect(stored()).toMatchObject({ projectId: 'q', tabs: [] })
  })
})
