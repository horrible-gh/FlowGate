/**
 * flowgate.v02.0060 T#2 (WP 0012, 0006-D rev2 §4.3 2-B, §9.5) — the context menu's 11 existing server commands in the
 * real Designer Shell: 그룹 / 플로우 이름 변경, 플로우 삭제 (= the existing dispose, DC), 새 폴더 / 새 파일, 폴더 다운로드 /
 * 업로드 / 삭제, 파일 다운로드 / Git › 수정 확인 / 삭제. Each through its window (confirmation, re-check right before the
 * request, the server's refusal shown and nothing changed) and the existing server contract (features/project →
 * `@shared/api`, mocked here: the request each sends is checked), then the existing refresh paths (Project tree, recent
 * lists, tab names / close, File Explorer tree). Scaffold as contextMenuAttachShell.0060.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'
import { diffRows, lineDiff } from '@/features/designer/contextMenu/attach/menuActions'
import { answerFlowCanvas } from './flowCanvasResponse'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
const patchRequest = vi.hoisted(() => vi.fn())
const postFormRequest = vi.hoisted(() => vi.fn())
const downloadBlobRequest = vi.hoisted(() => vi.fn())
const deleteRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  default: { delete: deleteRequest },
  getRequest,
  postRequest,
  putRequest,
  patchRequest,
  postFormRequest,
  downloadBlobRequest,
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
const urlApi = { create: URL.createObjectURL, revoke: URL.revokeObjectURL }
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
  URL.createObjectURL = urlApi.create
  URL.revokeObjectURL = urlApi.revoke
})
// -------------------------------------------------------------------------------------------

function n(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}
const TREE = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'module:p:api', parent_id: 'project:p', node_type: 'module', label: 'api', title: '서버' }),
  n({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
]
const FILE_NODES = [
  { id: 'n:src', parent_id: null, type: 'folder', name: 'src', label: 'src', path: 'src', permissions: ['read'] },
  { id: 'n:src/app.ts', parent_id: 'n:src', type: 'file', name: 'app.ts', label: 'app.ts', path: 'src/app.ts', permissions: ['read'] },
  { id: 'n:src/util.ts', parent_id: 'n:src', type: 'file', name: 'util.ts', label: 'util.ts', path: 'src/util.ts', permissions: ['read'] },
]

/** What the mocked server answers now (a test changes these). */
let tree = TREE
let writable = true
let changes: Array<{ path: string; status: string }> = []
let diffBase: string | null = null
let fileNodes: Array<Record<string, unknown>> = FILE_NODES
/** What the File Explorer tree read throws once the explorer has read it (null = it answers `fileNodes`). */
let fileTreeError: unknown = null
/** What the 수정 확인 diff read throws instead of answering, and the two texts it answers. */
let diffError: unknown = null
const DIFF_OLD = 'const a = 1\nexport { a }\n'
const DIFF_NEW = 'const a = 2\nexport { a }\n'
let diffOld = DIFF_OLD
let diffNew = DIFF_NEW

function axiosError(status: number, data: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data } })
}
function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}
const treeReads = () => getRequest.mock.calls.filter(([url]) => /\/groups\/tree$/.test(url)).length
const fileTreeReads = () => getRequest.mock.calls.filter(([url]) => /\/git\/groups\/[^/]+\/tree$/.test(url)).length
/** Every mutating request sent (anything but a GET). */
const mutations = () => [
  ...postRequest.mock.calls.map(([url, body]) => ['POST', url, body]),
  ...putRequest.mock.calls.map(([url, body]) => ['PUT', url, body]),
  ...patchRequest.mock.calls.map(([url, body]) => ['PATCH', url, body]),
  ...postFormRequest.mock.calls.map(([url]) => ['FORM', url]),
  ...deleteRequest.mock.calls.map(([url, config]) => ['DELETE', url, config]),
]

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  tree = TREE
  writable = true
  changes = []
  diffBase = null
  fileNodes = FILE_NODES
  fileTreeError = null
  diffError = null
  diffOld = DIFF_OLD
  diffNew = DIFF_NEW
  for (const mock of [postRequest, putRequest, patchRequest, postFormRequest, downloadBlobRequest, deleteRequest, getRequest]) mock.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (/^\/api\/v1\/projects\/p\/groups\/tree$/.test(url)) return { data: { data: { nodes: tree } } }
    if (/^\/api\/v1\/projects\/p\/git\/status$/.test(url)) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true,
        slots: ['p.ui.0001', 'p.ui.0002'].map((id) => ({ group_id: id, branch: `flow/${id}`, status: 'ready', writable })) } } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes|diff)$/.exec(url)
    if (group && group[2] === 'tree') {
      if (fileTreeError) throw fileTreeError
      return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: fileNodes } } }
    }
    if (group && group[2] === 'changes') {
      return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: params.base ?? 'v0.2', changes } } }
    }
    if (group && group[2] === 'diff') {
      if (diffError) throw diffError
      return { data: { data: {
        group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: diffBase ?? params.base, merge_base: 'b45e',
        path: params.path, status: 'M',
        old: { exists: true, binary: false, truncated: false, size: diffOld.length, content: diffOld },
        new: { exists: true, binary: false, truncated: false, size: diffNew.length, content: diffNew },
      } } }
    }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    const canvas = answerFlowCanvas(url)
    if (canvas) return canvas
    throw new Error(`unexpected request ${url}`)
  })
})

let wrapper: VueWrapper<any> | null = null
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

async function settle() {
  for (let i = 0; i < 8; i += 1) await flushPromises()
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
  return found
}
const labels = (w: VueWrapper<any>) => w.findAll('[data-test="project-explorer-label"]').map((label) => label.text())
async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).find('.explorer-row').element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await flushPromises()
  }
  await settle()
}
async function openFlow(w: VueWrapper<any>, label: string) {
  if (!labels(w).includes(label)) await doubleClick(w, 'UI 개선')
  await doubleClick(w, label)
}
function rightClick(element: Element) {
  element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 50, button: 2 }))
}
const items = (level = 0) => Array.from(document.querySelectorAll<HTMLElement>(`[data-designer-context-menu] [data-level="${level}"] [role="menuitem"]`))
const item = (key: string, level = 0) => items(level).find((entry) => entry.dataset.key === key)!
const states = (level = 0) => items(level).map((entry) => [entry.dataset.key, entry.getAttribute('aria-disabled'), entry.title])
const menuEl = () => document.querySelector('[data-designer-context-menu]')
const dialog = () => document.querySelector<HTMLElement>('[data-test^="dma-"][data-kind]')
const q = <T extends HTMLElement = HTMLElement>(test: string) => document.querySelector<T>(`[data-test="${test}"]`)
const submitButton = () => q<HTMLButtonElement>('dma-submit')!
function type(test: string, value: string) {
  const field = q<HTMLInputElement | HTMLTextAreaElement>(test)!
  field.value = value
  field.dispatchEvent(new Event('input', { bubbles: true }))
}
async function choose(key: string, level = 0) {
  item(key, level).click()
  await settle()
}
function pick(names: readonly string[]) {
  const input = q<HTMLInputElement>('dma-files')!
  Object.defineProperty(input, 'files', { configurable: true, value: names.map((name) => new File([name], name)) })
  input.dispatchEvent(new Event('change'))
}
async function confirmOverwrite() {
  const overwrite = q<HTMLInputElement>('dma-overwrite')!
  overwrite.checked = true
  overwrite.dispatchEvent(new Event('change'))
  await settle()
}
const tabNames = (w: VueWrapper<any>) => w.findAll('[data-test="designer-flow-tab-button"]').map((tab) => tab.text())
const activeTab = (w: VueWrapper<any>) => w.get('[data-test="designer-flow-tab"] [role="tab"][aria-selected="true"]').text()

// ── ① project tree ─────────────────────────────────────────────────────────────────────────────

describe('2-B in the Shell — 그룹 / 플로우 이름 변경 (display name only)', () => {
  it('the Group row: 이름 변경 is enabled; its window names the Group and its key, sends PATCH modules/{key} {title} once, and the tree re-reads', async () => {
    const w = await mountShell()
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    // v02gui.0027: the Shell also connects 그룹 아카이브 and 그룹 삭제 (each after its separator).
    expect(states()).toEqual([['group.newFlow', 'false', ''], ['group.rename', 'false', ''], ['group.info', 'true', '미구현'], ['group.archive', 'false', ''], ['group.delete', 'false', '']])
    await choose('group.rename')
    expect(dialog()?.dataset.kind).toBe('group.rename')
    // 0004-CH seq 19/20: only the name and identifier inputs — no facts, note, hint, and no message before typing.
    expect(q('dma-fact-target')).toBeNull()
    expect(q('dma-fact-id')).toBeNull()
    expect(dialog()!.querySelector('.dma-note')).toBeNull()
    expect(q('dma-ident-hint')).toBeNull()
    expect(q('dma-input-error')).toBeNull()
    expect(q('dma-ident-error')).toBeNull()
    expect(q<HTMLInputElement>('dma-input')!.value).toBe('UI 개선')
    expect(q<HTMLInputElement>('dma-ident')!.value).toBe('ui')
    // The same name or no name does not run.
    expect(submitButton().disabled).toBe(true)
    type('dma-input', '   ')
    await settle()
    expect(submitButton().disabled).toBe(true)
    patchRequest.mockImplementation(async () => {
      tree = TREE.map((node) => (node.id === 'module:p:ui' ? { ...node, title: 'UI 리뉴얼' } : node))
      return { data: { ok: true } }
    })
    type('dma-input', 'UI 리뉴얼')
    await settle()
    const reads = treeReads()
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['PATCH', '/api/v1/projects/p/modules/ui', { title: 'UI 리뉴얼' }]])
    expect(dialog()).toBeNull()
    expect(treeReads()).toBeGreaterThan(reads)
    expect(labels(w)).toContain('UI 리뉴얼')
    expect(labels(w)).not.toContain('UI 개선')
  })

  it('v02gui.0012: only the identifier changes — the name may stay; PATCH carries {title, name}; bad identifiers do not run', async () => {
    const w = await mountShell()
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    await choose('group.rename')
    expect(q<HTMLInputElement>('dma-ident')!.value).toBe('ui')
    expect(submitButton().disabled).toBe(true)
    type('dma-ident', 'UI2')
    await settle()
    expect(q('dma-ident-error')).not.toBeNull()
    expect(submitButton().disabled).toBe(true)
    type('dma-ident', 'none')
    await settle()
    expect(submitButton().disabled).toBe(true)
    patchRequest.mockImplementation(async () => ({ data: { ok: true } }))
    type('dma-ident', 'ui2')
    await settle()
    expect(q('dma-ident-error')).toBeNull()
    expect(submitButton().disabled).toBe(false)
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['PATCH', '/api/v1/projects/p/modules/ui', { title: 'UI 개선', name: 'ui2' }]])
    expect(dialog()).toBeNull()
  })

  it.each([
    ['module_identifier_taken', 409, '"v0.2.test" 그룹이 쓰는 식별자입니다.', { holder: { kind: 'group', module: 'ui2', title: 'v0.2.test' } }],
    ['module_rename_busy', 409, '이 그룹에서 실행 중인 작업이 있습니다(p.ui.0001)', { busy: [{ group_id: 'p.ui.0001', reason: 'flow_run_active' }] }],
    ['module_title_too_long', 422, '이름은 100자 이하여야 합니다.', undefined],
  ] as const)('v02gui.0012: a Group rename refused with %s says why (not the general sentence)', async (code, status, words, details) => {
    const w = await mountShell()
    patchRequest.mockRejectedValue(axiosError(status, { ok: false, code, error_message: 'refused', ...(details ? { details } : {}) }))
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    await choose('group.rename')
    type('dma-ident', 'ui2')
    await settle()
    submitButton().click()
    await settle()
    expect(q('dma-refusal')?.dataset.code).toBe(code)
    expect(q('dma-refusal-text')!.textContent).toContain(words)
    expect(q('dma-refusal-text')!.textContent).not.toContain('서버가 요청을 거절했습니다')
    expect(dialog()).not.toBeNull()
  })

  it.each([
    ['busy tasks include their reasons and cap the list at five', 'module_rename_busy', {
      busy: [
        { group_id: 'flowgate.v02test.0036', reason: 'flow_run_active' },
        { group_id: 'flowgate.v02test.0029', reason: 'ai_lease_active' },
        { group_id: 'g3', reason: 'ai_run_active' }, { group_id: 'g4', reason: 'merge_session_open' },
        { group_id: 'g5', reason: 'test_run_active' }, { group_id: 'g6', reason: 'git_job_active' },
      ],
    }, 'flowgate.v02test.0036', '플로우 실행 중', '외 1건'],
    ['an archived holder explains how to find it', 'module_identifier_taken', {
      holder: { kind: 'group', module: 'ui2', title: 'v0.2.chat', group_id: 'g1', archived: true },
    }, '아카이브된 그룹', 'v0.2.chat', '보관 포함'],
    ['a large group explains the document count and limit', 'module_rename_too_large', {
      documents: 6049, groups: 1, limit: 1000,
    }, '문서가 6049건', '한도 1000건'],
  ] as const)('v02gui.0040: %s', async (_label, code, details, ...expected) => {
    const w = await mountShell()
    patchRequest.mockRejectedValue(axiosError(409, { ok: false, code, error_message: 'refused', details }))
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    await choose('group.rename')
    type('dma-ident', 'ui2')
    await settle()
    submitButton().click()
    await settle()
    const shown = q('dma-refusal-text')!.textContent ?? ''
    for (const word of expected) expect(shown).toContain(word)
    expect(dialog()).not.toBeNull()
  })

  it('v02gui.0040: when identifier change is refused after saving the title, says the display name was saved', async () => {
    const w = await mountShell()
    patchRequest.mockRejectedValue(axiosError(409, {
      ok: false, code: 'module_identifier_taken', error_message: 'refused', title_changed: true,
      details: { holder: { kind: 'group', module: 'ui2', title: 'v0.2.test' } },
    }))
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    await choose('group.rename')
    type('dma-ident', 'ui2')
    await settle()
    submitButton().click()
    await settle()
    expect(q('dma-title-saved')!.textContent).toContain('표시명은 저장되었습니다')
    expect(q('dma-refusal-text')!.textContent).toContain('v0.2.test')
  })

  it('v02gui.0012: a Group name over 100 characters is flagged in the window and does not run', async () => {
    const w = await mountShell()
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    await choose('group.rename')
    type('dma-input', 'x'.repeat(101))
    await settle()
    expect(q('dma-input-error')!.textContent).toContain('100자')
    expect(submitButton().disabled).toBe(true)
  })

  it('a refused Group rename (403) is shown in the window; nothing is re-read and the name stays', async () => {
    const w = await mountShell()
    patchRequest.mockRejectedValue(axiosError(403, { detail: 'Forbidden' }))
    rightClick(explorerRow(w, '서버').element)
    await settle()
    await choose('group.rename')
    type('dma-input', 'Server')
    await settle()
    const reads = treeReads()
    submitButton().click()
    await settle()
    expect(patchRequest).toHaveBeenCalledTimes(1)
    expect(q('dma-refusal')?.dataset.reason).toBe('forbidden')
    expect(q('dma-refusal')!.textContent).toContain('권한이 없습니다')
    expect(dialog()).not.toBeNull()
    expect(treeReads()).toBe(reads)
    expect(labels(w)).toContain('서버')
  })

  it('the Flow row: 이름 변경 sends PUT groups/{flow} {title} once; the open tab takes the name, the active tab stays, the recent Flow is renamed', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    await openFlow(w, 'Designer v0.2')
    expect(activeTab(w)).toBe('Designer v0.2')
    putRequest.mockImplementation(async () => {
      tree = TREE.map((node) => (node.id === 'p.ui.0002' ? { ...node, label: 'Canvas 2' } : node))
      return { data: { group_id: 'p.ui.0002', updated_at: 'now', graph_execution_limit: 4 } }
    })
    rightClick(explorerRow(w, 'Canvas').element)
    await settle()
    expect(states()).toEqual([['flow.rename', 'false', ''], ['flow.baseBranch', 'false', ''], ['flow.archive', 'true', ''], ['flow.delete', 'false', '']])
    await choose('flow.rename')
    expect(q('dma-fact-target')!.textContent).toContain('Canvas')
    expect(q('dma-fact-id')!.textContent).toContain('p.ui.0002')
    type('dma-input', 'Canvas 2')
    await settle()
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['PUT', '/api/v1/groups/p.ui.0002', { title: 'Canvas 2' }]])
    expect(tabNames(w)).toEqual(['Canvas 2', 'Designer v0.2'])
    expect(activeTab(w)).toBe('Designer v0.2')
    expect(labels(w)).toContain('Canvas 2')
    expect(useProjectStore().recentFlows.find((flow) => flow.id === 'p.ui.0002')?.name).toBe('Canvas 2')
  })

  it('a disposed Flow refuses its rename (409 group_disposed): the window says so, the tab name stays', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    putRequest.mockRejectedValue(axiosError(409, { detail: 'group_disposed: p.ui.0002 has been disposed' }))
    rightClick(explorerRow(w, 'Canvas').element)
    await settle()
    await choose('flow.rename')
    type('dma-input', 'Canvas 2')
    await settle()
    submitButton().click()
    await settle()
    expect(q('dma-refusal')?.dataset.reason).toBe('disposed')
    expect(tabNames(w)).toEqual(['Canvas'])
  })
})

describe('2-B in the Shell — 이름 변경 re-checks its row right before the request', () => {
  it.each([
    ['group.rename', '서버', 'module:p:api'],
    ['flow.rename', 'Canvas', 'p.ui.0002'],
  ] as const)('%s: the row gone from a tree re-read while the window is open → stale, nothing is sent', async (key, label, id) => {
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    rightClick(explorerRow(w, label).element)
    await settle()
    await choose(key)
    type('dma-input', `${label} 2`)
    await settle()
    expect(submitButton().disabled).toBe(false)
    tree = TREE.filter((node) => node.id !== id && node.parent_id !== id)
    await w.get('[data-test="project-explorer-refresh"]').trigger('click')
    await settle()
    expect(q('dma-stale')).not.toBeNull()
    expect(submitButton().disabled).toBe(true)
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([])
  })
})

// ── Flow 삭제 = the existing dispose (DC), 0006-D §9.5 ─────────────────────────────────────────

describe('2-B in the Shell — 플로우 삭제 is the existing dispose (DC) (0006-D §9.5)', () => {
  async function openDispose(w: VueWrapper<any>, label = 'Canvas') {
    rightClick(explorerRow(w, label).element)
    await settle()
    expect(item('flow.delete').classList.contains('danger')).toBe(true)
    await choose('flow.delete')
  }

  it('names the right-clicked Flow, says one line (no bullets), and never runs without a reason; 취소 calls nothing', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    await openDispose(w)
    expect(dialog()?.dataset.kind).toBe('flow.delete')
    expect(dialog()!.closest('[role]')?.getAttribute('role')).toBe('alertdialog')
    expect(q('dma-fact-target')!.textContent).toContain('Canvas')
    expect(q('dma-fact-id')!.textContent).toContain('p.ui.0002')
    // v02gui.0029 (0003-CH, 시안 tfi2zm20): the Group delete's look — the red badge above the title, one sentence, the
    // Flow in its box (identifier faint), two points; still no standing help under the input.
    expect(q('dma-badge')!.dataset.icon).toBe('trash')
    expect(document.querySelector('.dma-head')!.getAttribute('data-look')).toBe('hero')
    expect(q('dma-dispose-note')!.textContent).toBe('이 플로우를 목록에서 제거합니다.')
    expect(Array.from(q('dma-dispose-points')!.querySelectorAll('li')).map((li) => li.textContent)).toEqual(['기록은 그대로 남습니다.', '플로우 목록에서는 더 이상 보이지 않습니다.'])
    expect(q('dma-dispose-effects')).toBeNull()
    expect(q('dma-reason-required')).toBeNull()
    expect(submitButton().disabled).toBe(true)
    submitButton().click()
    type('dma-reason', '   ')
    await settle()
    expect(submitButton().disabled).toBe(true)
    submitButton().click()
    await settle()
    // The input error shows only once the reason was typed in and left empty.
    expect(q('dma-reason-required')).not.toBeNull()
    expect(mutations()).toEqual([])
    q('dma-cancel')!.click()
    await settle()
    expect(dialog()).toBeNull()
    expect(mutations()).toEqual([])
    expect(tabNames(w)).toEqual(['Canvas'])
  })

  it('v02gui.0029: a reason chip fills the reason (still editable) and turns the button on; it sends that reason', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    await openDispose(w)
    postRequest.mockResolvedValue({ data: { status: 'success', group_id: 'p.ui.0002', project: 'p', doc_id: 'p.ui.0002.0003-DC' } })
    expect(Array.from(q('dma-reason-chips')!.querySelectorAll('button')).map((b) => b.textContent)).toEqual(['중복', '테스트용', '더 이상 안 씀', '잘못 만듦'])
    expect(submitButton().disabled).toBe(true)
    q('dma-reason-chips')!.querySelector<HTMLButtonElement>('[data-chip="test"]')!.click()
    await settle()
    expect(q<HTMLTextAreaElement>('dma-reason')!.value).toBe('테스트용')
    expect(submitButton().disabled).toBe(false)
    expect(mutations()).toEqual([])
    type('dma-reason', '테스트용 — 데모 끝')
    await settle()
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['POST', '/api/v1/groups/p.ui.0002/dispose', { reason_detail: '테스트용 — 데모 끝' }]])
  })

  it('with a reason: only the dispose API, once (no permanent / document / branch delete); then the tree re-reads, the recent Flow and the open tab go', async () => {
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    await openFlow(w, 'Canvas')
    expect(useProjectStore().recentFlows.map((flow) => flow.id)).toContain('p.ui.0002')
    postRequest.mockImplementation(async () => {
      tree = TREE.filter((node) => node.id !== 'p.ui.0002')
      return { data: { status: 'success', group_id: 'p.ui.0002', project: 'p', doc_id: 'p.ui.0002.0003-DC' } }
    })
    await openDispose(w)
    type('dma-reason', '중복 플로우')
    await settle()
    expect(submitButton().disabled).toBe(false)
    const reads = treeReads()
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['POST', '/api/v1/groups/p.ui.0002/dispose', { reason_detail: '중복 플로우' }]])
    expect(deleteRequest).not.toHaveBeenCalled()
    expect(getRequest.mock.calls.map(([url]) => url).filter((url) => /branches|documents|delete/.test(url))).toEqual([])
    expect(dialog()).toBeNull()
    expect(treeReads()).toBeGreaterThan(reads)
    expect(labels(w)).not.toContain('Canvas')
    expect(w.find('[data-test="designer-flow-tab"][data-flow-id="p.ui.0002"]').exists()).toBe(false)
    expect(tabNames(w)).toEqual(['Designer v0.2'])
    expect(useProjectStore().recentFlows.map((flow) => flow.id)).not.toContain('p.ui.0002')
  })

  it.each([
    ['already disposed', async () => ({ data: { status: 'error', message: 'Group is already disposed: p.ui.0002' } }), 'disposed', '이미 폐기된'],
    ['gone', async () => ({ data: { status: 'error', message: 'Group not found: p.ui.0002' } }), 'not_found', '대상이 없습니다'],
    ['no permission', async () => { throw axiosError(403, { detail: 'project.group.manage permission required.' }) }, 'forbidden', '권한이 없습니다'],
  ] as const)('a refusal (%s) is shown; the screen does not change (tab, tree, recent list)', async (_name, answer, reason, words) => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    postRequest.mockImplementation(answer)
    await openDispose(w)
    type('dma-reason', '정리')
    await settle()
    const reads = treeReads()
    submitButton().click()
    await settle()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(q('dma-refusal')?.dataset.reason).toBe(reason)
    expect(q('dma-refusal')!.textContent).toContain(words)
    expect(dialog()).not.toBeNull()
    expect(tabNames(w)).toEqual(['Canvas'])
    expect(treeReads()).toBe(reads)
    expect(labels(w)).toContain('Canvas')
    expect(useProjectStore().recentFlows.map((flow) => flow.id)).toContain('p.ui.0002')
  })

  it('the Flow gone after the menu opened: the menu closes and nothing runs; gone after the window opened: the window runs nothing', async () => {
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    // (1) The menu is open when a tree re-read drops the Flow.
    rightClick(explorerRow(w, 'Canvas').element)
    await settle()
    const stale = item('flow.delete')
    tree = TREE.filter((node) => node.id !== 'p.ui.0002')
    await w.get('[data-test="project-explorer-refresh"]').trigger('click')
    await settle()
    expect(menuEl()).toBeNull()
    stale.click()
    await settle()
    expect(dialog()).toBeNull()
    // (2) The window is open when the Flow goes.
    tree = TREE
    await w.get('[data-test="project-explorer-refresh"]').trigger('click')
    await settle()
    await openDispose(w)
    type('dma-reason', '정리')
    await settle()
    tree = TREE.filter((node) => node.id !== 'p.ui.0002')
    await w.get('[data-test="project-explorer-refresh"]').trigger('click')
    await settle()
    expect(q('dma-stale')).not.toBeNull()
    expect(submitButton().disabled).toBe(true)
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([])
  })
})

// ── ② File Explorer: the open Flow's worktree ────────────────────────────────────────────────────

describe('2-B in the Shell — the File Explorer commands on the open Flow worktree (group_id always sent)', () => {
  async function fileExplorer() {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const tab = w.findAll('[data-test="designer-explorer-mode-tabs"] [role="tab"]').find((b) => b.text().includes('파일'))!
    await tab.trigger('click')
    await settle()
    await w.get('[data-test="dfx-folder-row"][data-path="src"]').trigger('click')
    await settle()
    return w
  }
  const folder = (w: VueWrapper<any>) => w.get('[data-test="dfx-folder-row"][data-path="src"]').element
  const file = (w: VueWrapper<any>, path = 'src/app.ts') => w.get(`[data-test="dfx-file-row"][data-path="${path}"]`).element

  it('the folder menu: 새 폴더 / 새 파일 / 다운로드 / 업로드 / 삭제 are enabled on a writable Flow (rename and Git children stay 미구현)', async () => {
    const w = await fileExplorer()
    rightClick(folder(w))
    await settle()
    expect(states()).toEqual([
      ['folder.open', 'true', ''], ['folder.newFolder', 'false', ''], ['folder.newFile', 'false', ''],
      ['folder.rename', 'true', '미구현'], ['folder.download', 'false', ''], ['folder.upload', 'false', ''],
      ['folder.git', 'false', ''], ['folder.delete', 'false', ''],
    ])
  })

  it('a read-only Flow (slot not writable → no canMutate): the write commands are disabled and run nothing; 다운로드 stays', async () => {
    writable = false
    const w = await fileExplorer()
    rightClick(folder(w))
    await settle()
    expect(states().filter(([key]) => /newFolder|newFile|upload|delete|download/.test(key!))).toEqual([
      ['folder.newFolder', 'true', ''], ['folder.newFile', 'true', ''], ['folder.download', 'false', ''],
      ['folder.upload', 'true', ''], ['folder.delete', 'true', ''],
    ])
    for (const key of ['folder.newFolder', 'folder.newFile', 'folder.upload', 'folder.delete']) item(key).click()
    await settle()
    expect(dialog()).toBeNull()
    expect(mutations()).toEqual([])
  })

  it('새 폴더 in src: the window names the place, refuses a bad name, and sends POST storage/folder with the Flow group_id; the tree re-reads', async () => {
    const w = await fileExplorer()
    postRequest.mockResolvedValue({ data: { status: 'success' } })
    rightClick(folder(w))
    await settle()
    await choose('folder.newFolder')
    expect(q('dma-fact-location')!.textContent).toContain('src')
    // v02gui.0029: an input window — the small badge beside the title, no standing danger look.
    expect(document.querySelector('.dma-head')!.getAttribute('data-look')).toBe('compact')
    expect(q('dma-badge')!.dataset.icon).toBe('folder-plus')
    expect(q('dma-target-meta')!.textContent).toBe('만들 위치')
    type('dma-input', 'a/b')
    await settle()
    expect(q('dma-input-error')!.textContent).toContain('쓸 수 없습니다')
    expect(submitButton().disabled).toBe(true)
    type('dma-input', 'lib')
    await settle()
    const reads = fileTreeReads()
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['POST', '/api/v1/storage/folder', { project_id: 'p', parent_path: 'src', name: 'lib', group_id: 'p.ui.0002' }]])
    expect(dialog()).toBeNull()
    expect(fileTreeReads()).toBeGreaterThan(reads)
  })

  it('새 파일 on the root folder (blank area) sends parent_path ""; a name already there is refused (400) and nothing re-reads', async () => {
    const w = await fileExplorer()
    postRequest.mockRejectedValue(axiosError(400, { status: 'error', message: 'An item with the same name already exists.' }))
    rightClick(w.get('.dfx-body').element)
    await settle()
    await choose('folder.newFile')
    expect(q('dma-fact-location')!.textContent).toContain('루트 폴더')
    type('dma-input', 'README.md')
    await settle()
    const reads = fileTreeReads()
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['POST', '/api/v1/storage/file', { project_id: 'p', parent_path: '', name: 'README.md', group_id: 'p.ui.0002' }]])
    expect(q('dma-refusal')?.dataset.reason).toBe('exists')
    expect(fileTreeReads()).toBe(reads)
  })

  it('업로드: picked files are checked against the folder now (overwrite needs a confirmation), sent as one multipart batch; skipped files are shown', async () => {
    const w = await fileExplorer()
    postFormRequest.mockResolvedValue({ data: { uploaded: [{ path: 'src/app.ts', size: 1 }], skipped: [{ path: '../x', reason: 'PATH_TRAVERSAL' }] } })
    rightClick(folder(w))
    await settle()
    await choose('folder.upload')
    expect(submitButton().disabled).toBe(true)
    const input = q<HTMLInputElement>('dma-files')!
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['a'], 'app.ts'), new File(['b'], 'new.ts')] })
    input.dispatchEvent(new Event('change'))
    await settle()
    expect(q('dma-conflicts')!.textContent).toContain('app.ts')
    expect(q('dma-conflicts')!.textContent).not.toContain('new.ts')
    expect(submitButton().disabled).toBe(true)
    await confirmOverwrite()
    expect(submitButton().disabled).toBe(false)
    const reads = fileTreeReads()
    submitButton().click()
    await settle()
    expect(postFormRequest).toHaveBeenCalledTimes(1)
    const [url, form] = postFormRequest.mock.calls[0] as [string, FormData]
    expect(url).toBe('/api/v1/projects/p/files/upload')
    expect(form.get('target_path')).toBe('src')
    expect(form.get('group_id')).toBe('p.ui.0002')
    expect((form.getAll('files[]') as File[]).map((f) => f.name)).toEqual(['app.ts', 'new.ts'])
    expect(q('dma-uploaded')!.textContent).toContain('1')
    expect(q('dma-skipped')!.textContent).toContain('1')
    expect(q('dma-upload-result')!.textContent).toContain('PATH_TRAVERSAL')
    expect(fileTreeReads()).toBeGreaterThan(reads)
  })

  it('업로드 refused (413): the window says so and nothing re-reads', async () => {
    const w = await fileExplorer()
    postFormRequest.mockRejectedValue(axiosError(413, { error: { code: 'PAYLOAD_TOO_LARGE', message: 'Single file exceeds 100 MB' } }))
    rightClick(folder(w))
    await settle()
    await choose('folder.upload')
    const input = q<HTMLInputElement>('dma-files')!
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['b'], 'big.bin')] })
    input.dispatchEvent(new Event('change'))
    await settle()
    await confirmOverwrite()
    const reads = fileTreeReads()
    submitButton().click()
    await settle()
    expect(q('dma-refusal')?.dataset.reason).toBe('too_large')
    expect(fileTreeReads()).toBe(reads)
  })

  it('폴더 다운로드: the window says empty folders are not in the ZIP; download-zip with path and group_id; the root is "."', async () => {
    const w = await fileExplorer()
    const saved: string[] = []
    URL.createObjectURL = vi.fn(() => 'blob:zip')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download) })
    downloadBlobRequest.mockResolvedValue({ data: new Blob(['zip']), headers: { 'content-disposition': "attachment; filename*=UTF-8''src.zip" } })
    rightClick(folder(w))
    await settle()
    await choose('folder.download')
    expect(q('dma-empty-folders')!.textContent).toContain('빈 폴더는 ZIP에 들어가지 않습니다')
    submitButton().click()
    await settle()
    expect(downloadBlobRequest).toHaveBeenCalledWith('/api/v1/projects/p/files/download-zip', { path: 'src', group_id: 'p.ui.0002' })
    expect(saved).toEqual(['src.zip'])
    expect(dialog()).toBeNull()
    rightClick(w.get('.dfx-body').element)
    await settle()
    await choose('folder.download')
    submitButton().click()
    await settle()
    expect(downloadBlobRequest).toHaveBeenLastCalledWith('/api/v1/projects/p/files/download-zip', { path: '.', group_id: 'p.ui.0002' })
    expect(mutations()).toEqual([])
  })

  it('파일 다운로드 runs at once (download with path and group_id) and says so; a 404 is a refusal toast', async () => {
    const w = await fileExplorer()
    const saved: string[] = []
    URL.createObjectURL = vi.fn(() => 'blob:file')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download) })
    downloadBlobRequest.mockResolvedValueOnce({ data: new Blob(['x']), headers: { 'content-disposition': 'attachment; filename="app.ts"' } })
    rightClick(file(w))
    await settle()
    expect(states()).toEqual([
      ['file.open', 'true', '미구현'], ['file.rename', 'true', '미구현'], ['file.download', 'false', ''],
      ['file.git', 'false', ''], ['file.delete', 'false', ''],
    ])
    await choose('file.download')
    expect(dialog()).toBeNull()
    expect(downloadBlobRequest).toHaveBeenCalledWith('/api/v1/projects/p/files/download', { path: 'src/app.ts', group_id: 'p.ui.0002' })
    expect(saved).toEqual(['app.ts'])
    expect(document.body.textContent).toContain('다운로드했습니다: app.ts')
    // flowgate.v02.0088 F16: the toast still on screen is said again in the language switched to; the file name stays.
    i18n.global.locale.value = 'ja'
    await settle()
    expect(document.body.textContent).toContain('ダウンロードしました: app.ts')
    expect(document.body.textContent).not.toContain('다운로드했습니다: app.ts')
    i18n.global.locale.value = 'ko'
    await settle()
    downloadBlobRequest.mockRejectedValueOnce(axiosError(404, new Blob([JSON.stringify({ error: { code: 'FILE_NOT_FOUND', message: 'file does not exist' } })])))
    rightClick(file(w, 'src/util.ts'))
    await settle()
    await choose('file.download')
    expect(document.body.textContent).toContain('다운로드하지 못했습니다')
    expect(saved).toEqual(['app.ts'])
  })

  it('폴더 삭제: the window names the folder and the whole range; DELETE files {path, type folder, group_id}; the tree re-reads', async () => {
    const w = await fileExplorer()
    deleteRequest.mockResolvedValue({ data: { deleted: 'src', type: 'folder' } })
    rightClick(folder(w))
    await settle()
    await choose('folder.delete')
    expect(dialog()!.closest('[role]')?.getAttribute('role')).toBe('alertdialog')
    expect(q('dma-fact-target')!.textContent).toContain('src')
    // v02gui.0029: titled 폴더 삭제; the folder in a red-edged box; what goes with it and "cannot be undone" as red points.
    expect(document.querySelector('.dma-title')!.textContent).toBe('폴더 삭제')
    expect(submitButton().textContent).toContain('폴더 삭제')
    expect(q('dma-fact-target')!.classList.contains('is-danger')).toBe(true)
    expect(q('dma-scope')!.textContent).toBe('이 폴더를 삭제합니다.')
    expect(q('dma-remove-points')!.textContent).toContain('안에 있는 모든 파일·폴더가 함께 지워집니다.')
    expect(q('dma-remove-points')!.textContent).toContain('되돌릴 수 없습니다')
    const reads = fileTreeReads()
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['DELETE', '/api/v1/projects/p/files', { data: { path: 'src', type: 'folder', group_id: 'p.ui.0002' } }]])
    expect(fileTreeReads()).toBeGreaterThan(reads)
  })

  it.each([
    [409, { error: { code: 'TR2_MANAGED_FILE', message: 'TR2-managed source paths can only be changed through TR2' } }, 'managed'],
    [409, { error: { code: 'SOURCE_MUTATION_BUSY', message: 'group source mutation is temporarily unavailable' } }, 'locked'],
    [403, { error: { code: 'FORBIDDEN', message: 'insufficient permission for this operation' } }, 'forbidden'],
    [404, { error: { code: 'NOT_FOUND', message: 'path does not exist' } }, 'not_found'],
  ] as const)('파일 삭제 refused (%i %s) is shown; nothing re-reads', async (status, data, reason) => {
    const w = await fileExplorer()
    deleteRequest.mockRejectedValue(axiosError(status, data))
    rightClick(file(w))
    await settle()
    await choose('file.delete')
    expect(q('dma-scope')!.textContent).toContain('이 파일 하나')
    const reads = fileTreeReads()
    submitButton().click()
    await settle()
    expect(deleteRequest).toHaveBeenCalledWith('/api/v1/projects/p/files', { data: { path: 'src/app.ts', type: 'file', group_id: 'p.ui.0002' } })
    expect(q('dma-refusal')?.dataset.reason).toBe(reason)
    expect(fileTreeReads()).toBe(reads)
  })

  it('파일 삭제: the window names the file and its range; DELETE files {path, type file, group_id}; the window closes and the tree re-reads', async () => {
    const w = await fileExplorer()
    deleteRequest.mockResolvedValue({ data: { deleted: 'src/util.ts', type: 'file' } })
    rightClick(file(w, 'src/util.ts'))
    await settle()
    await choose('file.delete')
    // v02gui.0029: titled 파일 삭제; the file's name large, its folder faint, the whole path as the box's tooltip.
    expect(document.querySelector('.dma-title')!.textContent).toBe('파일 삭제')
    expect(q('dma-fact-target')!.dataset.path).toBe('src/util.ts')
    expect(q('dma-fact-target')!.querySelector('.dma-target-name')!.textContent).toBe('util.ts')
    expect(q('dma-target-meta')!.textContent).toBe('src/')
    expect(q('dma-remove-points')!.textContent).not.toContain('안에 있는')
    const reads = fileTreeReads()
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['DELETE', '/api/v1/projects/p/files', { data: { path: 'src/util.ts', type: 'file', group_id: 'p.ui.0002' } }]])
    expect(dialog()).toBeNull()
    expect(fileTreeReads()).toBeGreaterThan(reads)
  })

  it('폴더 다운로드 refused (409, the Flow worktree unavailable): the window says so and nothing is saved', async () => {
    const w = await fileExplorer()
    const saved: string[] = []
    URL.createObjectURL = vi.fn(() => 'blob:zip')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download) })
    downloadBlobRequest.mockRejectedValue(axiosError(409, new Blob([JSON.stringify({ detail: 'Group worktree is unavailable (missing); base checkout was not used' })])))
    rightClick(folder(w))
    await settle()
    await choose('folder.download')
    submitButton().click()
    await settle()
    expect(q('dma-refusal')?.dataset.reason).toBe('worktree')
    expect(saved).toEqual([])
    expect(dialog()).not.toBeNull()
  })

  // T 4항: right before the request the window builds the menu again from now — another open Flow runs nothing.
  it.each([
    ['folder.newFile', true],
    ['folder.upload', false],
    ['folder.download', false],
    ['folder.delete', false],
    ['file.delete', false],
  ] as const)('%s: the open Flow changes while the window is open → stale, nothing is sent', async (key, onRoot) => {
    const w = await fileExplorer()
    rightClick(key.startsWith('file.') ? file(w) : onRoot ? w.get('.dfx-body').element : folder(w))
    await settle()
    await choose(key)
    if (key === 'folder.newFile') type('dma-input', 'b.ts')
    if (key === 'folder.upload') {
      const input = q<HTMLInputElement>('dma-files')!
      Object.defineProperty(input, 'files', { configurable: true, value: [new File(['n'], 'fresh.ts')] })
      input.dispatchEvent(new Event('change'))
      await settle()
      await confirmOverwrite()
    }
    await settle()
    expect(submitButton().disabled).toBe(false)
    await openFlow(w, 'Designer v0.2')
    expect(q('dma-stale')).not.toBeNull()
    expect(submitButton().disabled).toBe(true)
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([])
    expect(downloadBlobRequest).not.toHaveBeenCalled()
  })

  it('Git › 수정 확인 of a changed file: the diff against the project Base (named), read with base; an unchanged file keeps it disabled', async () => {
    changes = [{ path: 'src/app.ts', status: 'M' }]
    const w = await fileExplorer()
    rightClick(file(w, 'src/util.ts'))
    await settle()
    item('file.git').dispatchEvent(new MouseEvent('mouseenter'))
    await settle()
    expect(states(1)[0]).toEqual(['file.git.changes', 'true', ''])
    rightClick(file(w))
    await settle()
    item('file.git').dispatchEvent(new MouseEvent('mouseenter'))
    await settle()
    expect(states(1)).toEqual([['file.git.changes', 'false', ''], ['file.git.revert', 'true', '미구현'], ['file.git.history', 'true', '미구현']])
    await choose('file.git.changes', 1)
    expect(dialog()?.dataset.kind).toBe('file.git.changes')
    const diffCall = getRequest.mock.calls.find(([url]) => /\/git\/groups\/p\.ui\.0002\/diff$/.test(url))!
    expect(diffCall[1]).toEqual({ path: 'src/app.ts', base: 'v0.2' })
    expect(q('dma-fact-base')!.textContent).toContain('프로젝트 Base v0.2')
    const lines = Array.from(document.querySelectorAll<HTMLElement>('[data-test="dma-diff-lines"] [data-kind]')).map((line) => line.dataset.kind)
    expect(lines).toEqual(['del', 'add', 'context'])
    expect(mutations()).toEqual([])
  })

  it('수정 확인 answered for another base is not shown as a comparison', async () => {
    changes = [{ path: 'src/app.ts', status: 'M' }]
    diffBase = 'main-work'
    const w = await fileExplorer()
    rightClick(file(w))
    await settle()
    item('file.git').dispatchEvent(new MouseEvent('mouseenter'))
    await settle()
    await choose('file.git.changes', 1)
    expect(q('dma-refusal')?.dataset.reason).toBe('base_mismatch')
    expect(q('dma-diff-lines')).toBeNull()
  })

  // T 3항: the server overwrites a same-named file without asking, and the tree read is no full list of what is
  // there (it leaves out ignored and unreadable files, and it can fail) — so every upload asks the confirmation.
  it.each([
    ['an ignored file the tree read leaves out', () => {}, ['local.env'], 'dma-no-known'],
    ['an unreadable file the tree read filters out', () => {
      fileNodes = [...FILE_NODES, { id: 'n:src/secret.ts', parent_id: 'n:src', type: 'file', name: 'secret.ts', label: 'secret.ts', path: 'src/secret.ts', permissions: [] }]
    }, ['secret.ts'], 'dma-no-known'],
    ['a failed tree read', () => { fileTreeError = axiosError(503, { detail: 'busy' }) }, ['app.ts'], 'dma-check-failed'],
  ] as const)('업로드 over %s still needs the overwrite confirmation; without it nothing is sent', async (_case, arrange, names, shownTest) => {
    const w = await fileExplorer()
    postFormRequest.mockResolvedValue({ data: { uploaded: names.map((name) => ({ path: `src/${name}`, size: 1 })), skipped: [] } })
    arrange()
    rightClick(folder(w))
    await settle()
    await choose('folder.upload')
    pick(names)
    await settle()
    expect(q(shownTest)).not.toBeNull()
    expect(q('dma-conflicts')).toBeNull()
    expect(q('dma-overwrite-note')!.textContent).toContain('.gitignore')
    expect(q<HTMLInputElement>('dma-overwrite')!.checked).toBe(false)
    expect(submitButton().disabled).toBe(true)
    submitButton().click()
    await settle()
    expect(postFormRequest).not.toHaveBeenCalled()
    await confirmOverwrite()
    expect(submitButton().disabled).toBe(false)
    submitButton().click()
    await settle()
    expect(postFormRequest).toHaveBeenCalledTimes(1)
    expect((postFormRequest.mock.calls[0][1] as FormData).getAll('files[]').map((f) => (f as File).name)).toEqual([...names])
    expect(q('dma-upload-result')).not.toBeNull()
  })

  it('업로드 in flight: the choice and the confirmation are locked; a change then is ignored and the result shows when the upload answers', async () => {
    const w = await fileExplorer()
    let answer: (value: unknown) => void = () => {}
    postFormRequest.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    rightClick(folder(w))
    await settle()
    await choose('folder.upload')
    pick(['app.ts'])
    await settle()
    await confirmOverwrite()
    submitButton().click()
    await settle()
    expect(postFormRequest).toHaveBeenCalledTimes(1)
    expect(submitButton().disabled).toBe(true)
    expect(q<HTMLInputElement>('dma-files')!.disabled).toBe(true)
    expect(q<HTMLInputElement>('dma-overwrite')!.disabled).toBe(true)
    const reads = fileTreeReads()
    pick(['other.ts'])
    await settle()
    expect(fileTreeReads()).toBe(reads)
    expect(q('dma-file-list')!.textContent).toContain('app.ts')
    expect(q('dma-file-list')!.textContent).not.toContain('other.ts')
    answer({ data: { uploaded: [{ path: 'src/app.ts', size: 1 }], skipped: [{ path: 'src/x', reason: 'PATH_TRAVERSAL' }] } })
    await settle()
    expect(q('dma-upload-result')).not.toBeNull()
    expect(q('dma-uploaded')!.textContent).toContain('1')
    expect(q('dma-skipped')!.textContent).toContain('1')
    expect(q('dma-upload-result')!.textContent).toContain('PATH_TRAVERSAL')
    expect(q('dma-submit')).toBeNull()
    expect(postFormRequest).toHaveBeenCalledTimes(1)
  })

  it('업로드 closed while in flight: its late answer is dropped and the next upload window starts free (not busy)', async () => {
    const w = await fileExplorer()
    let answer: (value: unknown) => void = () => {}
    postFormRequest.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    rightClick(folder(w))
    await settle()
    await choose('folder.upload')
    pick(['app.ts'])
    await settle()
    await confirmOverwrite()
    submitButton().click()
    await settle()
    q('dma-cancel')!.click()
    await settle()
    expect(dialog()).toBeNull()
    answer({ data: { uploaded: [{ path: 'src/app.ts', size: 1 }], skipped: [] } })
    await settle()
    expect(dialog()).toBeNull()
    rightClick(folder(w))
    await settle()
    await choose('folder.upload')
    expect(q('dma-upload-result')).toBeNull()
    expect(q<HTMLInputElement>('dma-files')!.disabled).toBe(false)
    pick(['new.ts'])
    await settle()
    await confirmOverwrite()
    expect(submitButton().disabled).toBe(false)
  })

  // T 4항: a refusal of the diff read reaches the window through the existing loader (its `cause`), as the server said it.
  it.each([
    ['403', axiosError(403, { ok: false, error: { code: 'forbidden', message: 'Forbidden' } }), 'forbidden', '403', '권한이 없습니다'],
    ['404', axiosError(404, { ok: false, error: { code: 'not_found', message: "path 'src/app.ts' not found" } }), 'not_found', '404', '대상이 없습니다'],
    ['409', axiosError(409, { ok: false, error: { code: 'invalid_state', message: 'group worktree is not available' } }), 'worktree', '409', '워크트리'],
    ['no answer', Object.assign(new Error('Network Error'), { request: {} }), 'network', '', '연결하지 못했습니다'],
  ] as const)('수정 확인 refused (%s) through the existing diff loader shows that refusal', async (_name, error, reason, status, words) => {
    changes = [{ path: 'src/app.ts', status: 'M' }]
    diffError = error
    const w = await fileExplorer()
    rightClick(file(w))
    await settle()
    item('file.git').dispatchEvent(new MouseEvent('mouseenter'))
    await settle()
    await choose('file.git.changes', 1)
    expect(getRequest.mock.calls.some(([url]) => /\/git\/groups\/p\.ui\.0002\/diff$/.test(url))).toBe(true)
    expect(q('dma-refusal')?.dataset.reason).toBe(reason)
    expect(q('dma-refusal')!.textContent).toContain(words)
    // The status and code are the sentence's tooltip, not shown text.
    expect(q('dma-refusal')!.textContent!.trim()).toBe(q('dma-refusal-text')!.textContent!.trim())
    if (status) {
      expect(q('dma-refusal')!.dataset.status).toBe(status)
      expect(q('dma-refusal-text')!.getAttribute('title')).toContain(status)
    } else {
      expect(q('dma-refusal')!.dataset.status).toBeUndefined()
      expect(q('dma-refusal-text')!.getAttribute('title')).toBeNull()
    }
    expect(q('dma-diff-lines')).toBeNull()
    expect(q('dma-retry')).not.toBeNull()
  })

  async function openDiff(w: VueWrapper<any>) {
    rightClick(file(w))
    await settle()
    item('file.git').dispatchEvent(new MouseEvent('mouseenter'))
    await settle()
    await choose('file.git.changes', 1)
  }
  /** The two diffs below build and render a few thousand rows; under the full suite that takes longer than the default 5 s. */
  const HEAVY_DIFF_TIMEOUT = 30_000
  const diffKinds = () => Array.from(document.querySelectorAll<HTMLElement>('[data-test="dma-diff-lines"] [data-kind]')).map((row) => row.dataset.kind)

  it('수정 확인 of a change after 2500 equal lines shows the change; the equal lines fold into one row with their count', async () => {
    const head = Array.from({ length: 2500 }, (_, i) => `line ${i + 1}`).join('\n') + '\n'
    diffOld = `${head}tail\n`
    diffNew = `${head}TAIL\n`
    changes = [{ path: 'src/app.ts', status: 'M' }]
    const w = await fileExplorer()
    await openDiff(w)
    expect(diffKinds()).toEqual(['gap', 'context', 'context', 'context', 'del', 'add'])
    expect(document.querySelector('[data-test="dma-diff-lines"] [data-kind="gap"]')!.textContent).toContain('같은 줄 2497개')
    const changed = Array.from(document.querySelectorAll<HTMLElement>('[data-test="dma-diff-lines"] .is-del, [data-test="dma-diff-lines"] .is-add'))
    expect(changed.map((row) => row.textContent)).toEqual(['2501-tail', '2501+TAIL'])
    expect(q('dma-diff-counts')!.textContent).toContain('추가 1줄 · 삭제 1줄')
    expect(q('dma-diff-partial')).toBeNull()
  }, HEAVY_DIFF_TIMEOUT)

  it('수정 확인 with more rows than one page says how many are shown and shows the rest on 더 보기', async () => {
    diffOld = ''
    diffNew = Array.from({ length: 2500 }, (_, i) => `row ${i + 1}`).join('\n') + '\n'
    changes = [{ path: 'src/app.ts', status: 'M' }]
    const w = await fileExplorer()
    await openDiff(w)
    expect(diffKinds()).toHaveLength(2000)
    expect(q('dma-diff-partial')!.textContent).toContain('2000 / 2500')
    q('dma-diff-more')!.click()
    await settle()
    expect(diffKinds()).toHaveLength(2500)
    expect(q('dma-diff-partial')).toBeNull()
  }, HEAVY_DIFF_TIMEOUT)

  it('수정 확인 of a removed final newline is a change, marked "파일 끝 개행 없음" (not "차이 없음")', async () => {
    diffOld = 'hello\n'
    diffNew = 'hello'
    changes = [{ path: 'src/app.ts', status: 'M' }]
    const w = await fileExplorer()
    await openDiff(w)
    expect(q('dma-diff-same')).toBeNull()
    expect(diffKinds()).toEqual(['del', 'add'])
    const marks = Array.from(document.querySelectorAll<HTMLElement>('[data-test="dma-diff-lines"] [data-end]'))
    expect(marks.map((mark) => [mark.dataset.end, mark.textContent])).toEqual([['none', '파일 끝 개행 없음']])
  })

  it('the open Flow changes while a window is open: it runs nothing (re-check right before the request)', async () => {
    const w = await fileExplorer()
    rightClick(folder(w))
    await settle()
    await choose('folder.newFolder')
    type('dma-input', 'lib')
    await settle()
    await openFlow(w, 'Designer v0.2')
    expect(q('dma-stale')).not.toBeNull()
    expect(submitButton().disabled).toBe(true)
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([])
  })
})

describe('2-B in the Shell — the 16 미구현 items still request nothing', () => {
  it('Project / Group 정보, 파일 열기 / 이름 변경, Git 되돌리기 / 이력: disabled 미구현, no request on click / Enter / Space', async () => {
    const w = await mountShell()
    const press = (entry: HTMLElement) => {
      entry.click()
      entry.focus()
      for (const key of ['Enter', ' ']) entry.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    }
    rightClick(explorerRow(w, 'Alpha').element)
    await settle()
    press(item('project.info'))
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    press(item('group.info'))
    await settle()
    expect(dialog()).toBeNull()
    expect(mutations()).toEqual([])
  })
})

describe('2-B — the 수정 확인 line diff keeps how each line ends and folds equal runs', () => {
  const view = (oldText: string | null, newText: string | null) => lineDiff(oldText, newText).map((line) => [line.kind, line.text, line.end])

  it('a final newline removed or added is a change', () => {
    expect(view('hello\n', 'hello')).toEqual([['del', 'hello', 'lf'], ['add', 'hello', 'none']])
    expect(view('hello', 'hello\n')).toEqual([['del', 'hello', 'none'], ['add', 'hello', 'lf']])
    expect(view('a\nb\n', 'a\nb')).toEqual([['context', 'a', 'lf'], ['del', 'b', 'lf'], ['add', 'b', 'none']])
  })

  it('an empty file against a newline-only file is a change; equal texts have no rows', () => {
    expect(view('', '\n')).toEqual([['add', '', 'lf']])
    expect(view('\n', '')).toEqual([['del', '', 'lf']])
    expect(view(null, '\n')).toEqual([['add', '', 'lf']])
    expect(view('', '')).toEqual([])
    for (const same of ['', '\n', 'hello', 'hello\n', 'a\r\nb\r\n']) expect(diffRows(lineDiff(same, same))).toEqual([])
  })

  it('CRLF against LF is a change', () => {
    expect(view('a\r\n', 'a\n')).toEqual([['del', 'a', 'crlf'], ['add', 'a', 'lf']])
    expect(view('a\r\nb\r\n', 'a\r\nc\r\n')).toEqual([['context', 'a', 'crlf'], ['del', 'b', 'crlf'], ['add', 'c', 'crlf']])
  })

  it('diffRows keeps every change with 3 lines around it and folds the rest into counted gaps', () => {
    const old = Array.from({ length: 20 }, (_, i) => `l${i}`).join('\n') + '\n'
    const next = old.replace('l10\n', 'L10\n')
    const rows = diffRows(lineDiff(old, next)).map((row) => (row.kind === 'gap' ? `gap${row.count}` : row.kind))
    expect(rows).toEqual(['gap7', 'context', 'context', 'context', 'del', 'add', 'context', 'context', 'context', 'gap6'])
  })
})

// ── v02gui.0027: 그룹 아카이브 / 복원 (display-only Group archive) ─────────────────────────────────────────

describe('v02gui.0027 in the Shell — 그룹 아카이브 / 그룹 복원 from the ⋯ button and the right-click menu', () => {
  /** The server's tree: the "ui" Group (with its Flows) only in an include_archived read while it is archived. */
  let uiArchived = false
  function serveArchive() {
    const base = getRequest.getMockImplementation()!
    getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
      if (/^\/api\/v1\/projects\/p\/groups\/tree$/.test(url)) {
        const inUi = (node: Record<string, unknown>) => node.id === 'module:p:ui' || node.parent_id === 'module:p:ui'
        const nodes = !uiArchived ? TREE
          : params.include_archived === 'true'
            ? TREE.map((node) => (node.id === 'module:p:ui' ? { ...node, archived: true, archived_at: '2026-11-15T00:00:00Z', archived_by: 'alice' } : node))
            : TREE.filter((node) => !inUi(node))
        return { data: { data: { nodes } } }
      }
      return base(url, params)
    })
    postRequest.mockImplementation(async (url: string) => {
      const hit = /^\/api\/v1\/projects\/p\/modules\/ui\/(archive|restore)$/.exec(url)
      if (!hit) throw new Error(`unexpected POST ${url}`)
      uiArchived = hit[1] === 'archive'
      return { data: { ok: true, name: 'ui', archived: uiArchived, archived_at: uiArchived ? '2026-11-15T00:00:00Z' : null } }
    })
  }
  beforeEach(() => { uiArchived = false })
  const menuButton = (w: VueWrapper<any>, label: string) =>
    explorerRow(w, label).find('[data-test="project-explorer-group-menu"]').element as HTMLButtonElement

  it('⋯ opens the same Group menu without selecting or toggling; 그룹 아카이브 asks, then hides the Group and its Flows', async () => {
    serveArchive()
    const w = await mountShell()
    const ui = () => explorerRow(w, 'UI 개선')
    expect(ui().attributes('aria-expanded')).toBe('false')
    menuButton(w, 'UI 개선').click()
    await settle()
    expect(states()).toEqual([['group.newFlow', 'false', ''], ['group.rename', 'false', ''], ['group.info', 'true', '미구현'], ['group.archive', 'false', ''], ['group.delete', 'false', '']])
    expect(item('group.archive').textContent).toContain('그룹 아카이브')
    expect(ui().attributes('aria-expanded')).toBe('false')
    expect(ui().attributes('aria-selected')).toBe('false')

    await choose('group.archive')
    expect(dialog()?.dataset.kind).toBe('group.archive')
    expect(q('dma-group-archive-note')!.textContent).toBe('기록은 보존하고 기본 목록에서 숨깁니다.')
    expect(q('dma-fact-target')!.textContent).toContain('UI 개선')
    expect(mutations()).toEqual([])  // nothing runs before the confirmation
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['POST', '/api/v1/projects/p/modules/ui/archive', {}]])
    expect(dialog()).toBeNull()
    expect(labels(w)).not.toContain('UI 개선')
    expect(labels(w)).toContain('서버')
  })

  it('"아카이브 포함" shows the archived Group dimmed with 그룹 복원 (no window), which brings it back as it was', async () => {
    serveArchive()
    uiArchived = true
    const w = await mountShell()
    expect(labels(w)).not.toContain('UI 개선')
    await w.get('[data-test="project-explorer-include-archived"]').setValue(true)
    await settle()
    expect(explorerRow(w, 'UI 개선').classes()).toContain('is-archived')
    expect(explorerRow(w, '서버').classes()).not.toContain('is-archived')
    // Its Flows are listed under it.
    await doubleClick(w, 'UI 개선')
    expect(labels(w)).toEqual(expect.arrayContaining(['Designer v0.2', 'Canvas']))

    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    expect(states()).toEqual([['group.newFlow', 'false', ''], ['group.rename', 'false', ''], ['group.info', 'true', '미구현'], ['group.restore', 'false', ''], ['group.delete', 'false', '']])
    expect(item('group.restore').textContent).toContain('그룹 복원')
    await choose('group.restore')
    expect(dialog()).toBeNull()
    expect(mutations()).toEqual([['POST', '/api/v1/projects/p/modules/ui/restore', {}]])
    expect(explorerRow(w, 'UI 개선').classes()).not.toContain('is-archived')
    await w.get('[data-test="project-explorer-include-archived"]').setValue(false)
    await settle()
    expect(labels(w)).toContain('UI 개선')
  })

  it('a refused archive stays in its window and nothing on screen changes', async () => {
    serveArchive()
    postRequest.mockImplementation(async () => { throw axiosError(409, { ok: false, code: 'already_archived', error_message: 'x' }) })
    const w = await mountShell()
    menuButton(w, 'UI 개선').click()
    await settle()
    await choose('group.archive')
    submitButton().click()
    await settle()
    expect(q('dma-refusal-text')!.textContent).toBe('이미 아카이브된 그룹입니다.')
    expect(dialog()?.dataset.kind).toBe('group.archive')
    expect(labels(w)).toContain('UI 개선')
  })
})

// ── v02gui.0027 (0008-T): 그룹 삭제 — empty Groups only, 대신 아카이브 otherwise ──────────────────────────────

describe('v02gui.0027 in the Shell — 그룹 삭제 (empty Groups only) and 대신 아카이브', () => {
  /**
   * The server: "ui" has 3 Flows (one archived, so the default tree lists 2) and "api" (서버) none. A deleted Group
   * leaves every tree read; an archived one leaves the default read.
   */
  let deleted: string[] = []
  let archived: string[] = []
  let deleteAnswer: ((module: string) => unknown) | null = null
  function serveDelete(totals: Record<string, number> = { ui: 3, api: 0 }) {
    const base = getRequest.getMockImplementation()!
    getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
      if (/^\/api\/v1\/projects\/p\/groups\/tree$/.test(url)) {
        const moduleOf = (node: Record<string, unknown>) => String(node.node_type === 'module' ? node.id : node.parent_id ?? '').replace('module:p:', '')
        const nodes = TREE
          .filter((node) => !deleted.includes(moduleOf(node)))
          .filter((node) => params.include_archived === 'true' || !archived.includes(moduleOf(node)))
          .map((node) => {
            if (node.node_type !== 'module') return node
            const key = String(node.label)
            return { ...node, flow_total: totals[key] ?? 0,
              ...(archived.includes(key) ? { archived: true, archived_at: '2026-11-15T00:00:00Z', archived_by: 'alice' } : {}) }
          })
        return { data: { data: { nodes } } }
      }
      return base(url, params)
    })
    deleteRequest.mockImplementation(async (url: string) => {
      const hit = /^\/api\/v1\/projects\/p\/modules\/([^/]+)$/.exec(url)
      if (!hit) throw new Error(`unexpected DELETE ${url}`)
      if (deleteAnswer) return deleteAnswer(hit[1])
      deleted.push(hit[1])
      return { data: { ok: true, deleted: true, name: hit[1] } }
    })
    postRequest.mockImplementation(async (url: string) => {
      const hit = /^\/api\/v1\/projects\/p\/modules\/([^/]+)\/archive$/.exec(url)
      if (!hit) throw new Error(`unexpected POST ${url}`)
      archived.push(hit[1])
      return { data: { ok: true, name: hit[1], archived: true, archived_at: '2026-11-15T00:00:00Z' } }
    })
  }
  beforeEach(() => { deleted = []; archived = []; deleteAnswer = null })
  const menuButton = (w: VueWrapper<any>, label: string) =>
    explorerRow(w, label).find('[data-test="project-explorer-group-menu"]').element as HTMLButtonElement
  const hintOf = (key: string) => item(key).querySelector('[data-test="designer-context-hint"]')?.textContent ?? null

  it('an empty Group: the menu shows 그룹 삭제 on one line; the window confirms, DELETE runs once and the Group leaves the tree', async () => {
    serveDelete()
    const w = await mountShell()
    menuButton(w, '서버').click()
    await settle()
    expect(item('group.delete').classList.contains('danger')).toBe(true)
    expect(item('group.delete').getAttribute('aria-disabled')).toBe('false')
    expect(item('group.delete').querySelector('.designer-context-label')!.textContent).toBe('그룹 삭제')
    expect(hintOf('group.delete')).toBeNull()  // one line: the item name only (v02test.0027 0005-T)
    await choose('group.delete')
    expect(dialog()?.dataset.kind).toBe('group.delete')
    expect(q('dma-group-delete-note')).not.toBeNull()
    expect(q('dma-group-delete-blocked')).toBeNull()
    expect(q('dma-fact-target')!.textContent).toContain('서버')
    expect(q('dma-archive-instead')).toBeNull()
    expect(mutations()).toEqual([])  // nothing runs before the confirmation
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['DELETE', '/api/v1/projects/p/modules/api', undefined]])
    expect(dialog()).toBeNull()
    expect(labels(w)).not.toContain('서버')
    expect(labels(w)).toContain('UI 개선')
  })

  it('a Group with Flows (archived ones count): the menu has no hint line, the window refuses without a DELETE and 대신 아카이브 archives it', async () => {
    serveDelete()
    const w = await mountShell()
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    // The window's 3 comes from the server although the tree lists only 2 Flows of it.
    expect(hintOf('group.delete')).toBeNull()
    await choose('group.delete')
    expect(dialog()?.dataset.kind).toBe('group.delete')
    expect(q('dma-group-delete-blocked')!.textContent!.trim()).toBe('Flow 3개가 남아 있어 삭제할 수 없습니다. 먼저 아카이브하세요.')
    expect(q('dma-submit')).toBeNull()
    expect(mutations()).toEqual([])
    q<HTMLButtonElement>('dma-archive-instead')!.click()
    await settle()
    expect(mutations()).toEqual([['POST', '/api/v1/projects/p/modules/ui/archive', {}]])
    expect(dialog()).toBeNull()
    expect(labels(w)).not.toContain('UI 개선')
    expect(labels(w)).toContain('서버')
  })

  it('the server has the last word: an "empty" Group that has a Flow by now is refused (409) and nothing changes', async () => {
    serveDelete()
    deleteAnswer = () => { throw axiosError(409, { ok: false, http_status: 409, code: 'module_not_empty', flow_count: 1, error_message: 'x' }) }
    const w = await mountShell()
    menuButton(w, '서버').click()
    await settle()
    await choose('group.delete')
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['DELETE', '/api/v1/projects/p/modules/api', undefined]])
    expect(dialog()?.dataset.kind).toBe('group.delete')
    expect(q('dma-group-delete-blocked')!.textContent!.trim()).toBe('Flow 1개가 남아 있어 삭제할 수 없습니다. 먼저 아카이브하세요.')
    expect(q('dma-refusal')).toBeNull()
    expect(q('dma-archive-instead')).not.toBeNull()
    expect(labels(w)).toContain('서버')
  })

  it('an archived Group follows the same rule: with Flows it is only told why (no 대신 아카이브); empty it is deleted', async () => {
    serveDelete({ ui: 2, api: 0 })
    archived = ['ui', 'api']
    const w = await mountShell()
    await w.get('[data-test="project-explorer-include-archived"]').setValue(true)
    await settle()
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    expect(hintOf('group.delete')).toBeNull()
    await choose('group.delete')
    expect(q('dma-group-delete-blocked')!.textContent!.trim()).toBe('Flow 2개가 남아 있어 삭제할 수 없습니다.')
    expect(q('dma-archive-instead')).toBeNull()
    q<HTMLButtonElement>('dma-cancel')!.click()
    await settle()
    rightClick(explorerRow(w, '서버').element)
    await settle()
    await choose('group.delete')
    submitButton().click()
    await settle()
    expect(mutations()).toEqual([['DELETE', '/api/v1/projects/p/modules/api', undefined]])
    expect(labels(w)).not.toContain('서버')
  })

  it('a refused DELETE of another kind stays in the window as a sentence (the Group is gone elsewhere)', async () => {
    serveDelete()
    deleteAnswer = () => { throw axiosError(404, { ok: false, code: 'module_not_found', error_message: 'x' }) }
    const w = await mountShell()
    menuButton(w, '서버').click()
    await settle()
    await choose('group.delete')
    submitButton().click()
    await settle()
    expect(q('dma-refusal')!.dataset.code).toBe('module_not_found')
    expect(q('dma-group-delete-blocked')).toBeNull()
    expect(dialog()?.dataset.kind).toBe('group.delete')
  })
})
