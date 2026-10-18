/**
 * flowgate.v02.0060 T#2 (WP 0012, 2-B): the 11 2-B items are connected in the Shell; here only their states — their
 * windows and requests are contextMenuServerActions.0060.
 * flowgate.v02.0060 T#2 — the Designer context menu in the real Designer Shell (0006-D rev2 §9.3 ① ② ③ ⑤, §3.4):
 * the host installed by the Shell, the project tree / File Explorer / Flow tab / Palette entry points with their real
 * data, the commands reaching the Shell's existing paths (File › 새 Group, the 새 Flow dialog with the right-clicked
 * Group), what must not open a menu, and the Minimap / Escape rules. Scaffold as DesignerShellSelection.0059: the real
 * Shell, project store, Center session and server Graph source; only `@shared/api` is mocked.
 * flowgate.v02.0060 T#1 (WP 0012, Q item 459): every mockup item shows — the ones not connected disabled with
 * 연결 예정 / 미구현 (tooltip) and no API call — so the Flow row, the file row and the root folder open their menus.
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
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
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
]

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  postRequest.mockReset()
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (/^\/api\/v1\/projects\/p\/groups\/tree$/.test(url)) return { data: { data: { nodes: TREE } } }
    if (/^\/api\/v1\/projects\/p\/git\/status$/.test(url)) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes)$/.exec(url)
    if (group && group[2] === 'tree') return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: FILE_NODES } } }
    if (group && group[2] === 'changes') {
      return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: params.base ?? 'v0.2', changes: [] } } }
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

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = w.findAll('[data-explorer-row]').find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
  if (!found) throw new Error(`row not rendered: ${label}`)
  return found
}
async function click(w: VueWrapper<any>, label: string, detail = 1) {
  explorerRow(w, label).find('.explorer-row').element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, detail }))
  await settle()
}
async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).find('.explorer-row').element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
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

function rightClick(element: Element, x = 40, y = 50) {
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 2 })
  element.dispatchEvent(event)
  return event
}
const menuEl = () => document.querySelector<HTMLElement>('[data-designer-context-menu]')
const items = () => Array.from(document.querySelectorAll<HTMLElement>('[data-designer-context-menu] [data-level="0"] [role="menuitem"]'))
const itemTexts = () => items().map((entry) => Array.from(entry.querySelectorAll('span')).map((cell) => cell.textContent!.trim()).join(' '))
const item = (key: string) => items().find((entry) => entry.dataset.key === key)!
/** [key, aria-disabled, tooltip] of each shown item. */
const states = () => items().map((entry) => [entry.dataset.key, entry.getAttribute('aria-disabled'), entry.title])
/** Choose a disabled item every way (click, Enter, Space). */
function press(entry: HTMLElement) {
  entry.click()
  entry.focus()
  for (const key of ['Enter', ' ']) entry.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
}
const selectedRows = (w: VueWrapper<any>) =>
  w.findAll('[data-explorer-row][aria-selected="true"]').map((r) => r.find('[data-test="project-explorer-label"]').text())
const expandedRows = (w: VueWrapper<any>) =>
  w.findAll('[data-explorer-row][aria-expanded="true"]').map((r) => r.find('[data-test="project-explorer-label"]').text())

describe('Designer context menu in the Shell — ① project tree', () => {
  it('the Project row: 새 그룹 opens File › 새 Group; nothing is selected or toggled by the right click', async () => {
    const w = await mountShell()
    const selected = selectedRows(w)
    const expanded = expandedRows(w)
    const event = rightClick(explorerRow(w, 'Alpha').element)
    await settle()
    expect(event.defaultPrevented).toBe(true)
    expect(itemTexts()).toEqual(['＋ 새 그룹', 'ⓘ 프로젝트 정보'])
    expect(states()).toEqual([['project.newGroup', 'false', ''], ['project.info', 'true', '미구현']])
    expect(selectedRows(w)).toEqual(selected)
    expect(expandedRows(w)).toEqual(expanded)
    item('project.newGroup').click()
    await settle()
    expect(document.querySelector('[data-test="designer-group-create"]')).not.toBeNull()
    expect(selectedRows(w)).toEqual(selected)
  })

  it('a Group row: 새 플로우 opens the 새 Flow dialog on the right-clicked Group while another Group stays selected', async () => {
    const w = await mountShell()
    await click(w, '서버')
    expect(selectedRows(w)).toEqual(['서버'])
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    expect(itemTexts()).toEqual(['＋ 새 플로우', '✎ 이름 변경', 'ⓘ 정보'])
    item('group.newFlow').click()
    await settle()
    const select = document.querySelector<HTMLSelectElement>('[data-test="designer-flow-create-group"]')!
    expect(select).not.toBeNull()
    expect(select.value).toBe('module:p:ui')
    expect(selectedRows(w)).toEqual(['서버'])
    // The next 새 Flow of the File menu is back on its own default (the selected Group).
    document.querySelector<HTMLElement>('[data-test="designer-flow-create-cancel"]')!.click()
    await settle()
    await w.get('[data-test="designer-menu"][data-menu="file"]').trigger('click')
    await w.get('[data-test="designer-menu-item"][data-command="flow.new"]').trigger('click')
    await settle()
    expect(document.querySelector<HTMLSelectElement>('[data-test="designer-flow-create-group"]')!.value).toBe('module:p:api')
  })

  // Q item 459 / T#2 (2-B): 이름 변경 / 삭제 are connected now — 삭제 is the dispose (DC) meaning and only opens its
  // confirmation (contextMenuServerActions.0060); the right click itself selects or opens nothing.
  it('a Flow row opens its menu: 이름 변경 / 삭제 are enabled (T#2); choosing 삭제 opens its confirmation only — nothing is called', async () => {
    const w = await mountShell()
    await doubleClick(w, 'UI 개선')
    const selected = selectedRows(w)
    const event = rightClick(explorerRow(w, 'Canvas').element)
    await settle()
    expect(event.defaultPrevented).toBe(true)
    expect(itemTexts()).toEqual(['✎ 이름 변경', '× 삭제'])
    expect(states()).toEqual([['flow.rename', 'false', ''], ['flow.delete', 'false', '']])
    expect(item('flow.delete').classList.contains('danger')).toBe(true)
    item('flow.delete').click()
    await settle()
    expect(menuEl()).toBeNull()
    expect(document.querySelector('[data-test="dma-flow.delete"]')).not.toBeNull()
    expect(postRequest).not.toHaveBeenCalled()
    expect(w.findAll('[data-test="designer-flow-tab"]')).toHaveLength(0)
    expect(selectedRows(w)).toEqual(selected)
  })

  it('a Group removed by a tree re-read closes its menu: no 새 Flow dialog opens and nothing is created', async () => {
    const w = await mountShell()
    // The Explorer's refresh waits for a work target (0039 T#3): a Flow of another Group is open.
    await openFlow(w, 'Canvas')
    rightClick(explorerRow(w, '서버').element)
    await settle()
    expect(itemTexts()).toEqual(['＋ 새 플로우', '✎ 이름 변경', 'ⓘ 정보'])
    const stale = item('group.newFlow')
    // The Project's tree is read again without that Group (removed elsewhere).
    const answer = getRequest.getMockImplementation()!
    getRequest.mockImplementation(async (url: string, params?: Record<string, unknown>) => {
      if (/^\/api\/v1\/projects\/p\/groups\/tree$/.test(url)) {
        return { data: { data: { nodes: TREE.filter((node) => node.id !== 'module:p:api') } } }
      }
      return answer(url, params)
    })
    await w.get('[data-test="project-explorer-refresh"]').trigger('click')
    await settle()
    expect(w.findAll('[data-test="project-explorer-label"]').map((label) => label.text())).not.toContain('서버')
    expect(menuEl()).toBeNull()
    stale.click()
    await settle()
    expect(document.querySelector('[data-test="designer-flow-create-group"]')).toBeNull()
    expect(postRequest).not.toHaveBeenCalled()
    // A Group still in the tree keeps its menu.
    rightClick(explorerRow(w, 'UI 개선').element)
    await settle()
    expect(itemTexts()).toEqual(['＋ 새 플로우', '✎ 이름 변경', 'ⓘ 정보'])
  })
})

describe('Designer context menu in the Shell — ② File Explorer', () => {
  async function fileExplorer() {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const tab = w.findAll('[data-test="designer-explorer-mode-tabs"] [role="tab"]').find((b) => b.text().includes('파일'))!
    await tab.trigger('click')
    await settle()
    return w
  }
  const rowPaths = (w: VueWrapper<any>) =>
    w.findAll('[data-test="dfx-folder-row"],[data-test="dfx-file-row"]').map((r) => r.attributes('data-path'))
  const folder = (w: VueWrapper<any>, path: string) => w.get(`[data-test="dfx-folder-row"][data-path="${path}"]`).element

  it('a closed folder: 열기 unfolds it through the explorer expansion (open only)', async () => {
    const w = await fileExplorer()
    expect(rowPaths(w)).toEqual(['src'])
    const event = rightClick(folder(w, 'src'))
    await settle()
    expect(event.defaultPrevented).toBe(true)
    expect(rowPaths(w)).toEqual(['src'])
    expect(itemTexts()).toEqual(['↗ 열기', '＋ 새 폴더', '＋ 새 파일', '✎ 이름 변경', '↓ 다운로드', '↑ 업로드', '⑂ Git ›', '× 삭제'])
    expect(menuEl()!.querySelectorAll('[data-level="0"] [role="separator"]')).toHaveLength(3)
    item('folder.open').click()
    await settle()
    expect(rowPaths(w)).toEqual(['src', 'src/app.ts'])
  })

  it('an open folder: the menu opens with 열기 disabled, and choosing it changes nothing', async () => {
    const w = await fileExplorer()
    await w.get('[data-test="dfx-folder-row"][data-path="src"]').trigger('click')
    await settle()
    expect(rowPaths(w)).toEqual(['src', 'src/app.ts'])
    rightClick(folder(w, 'src'))
    await settle()
    // T#2: the 2-B items are connected; this Flow's slot is not writable (git/status slots: []), so canMutate is false and
    // the write items are disabled by their own condition (no 연결 예정 reason) — 다운로드 needs read permission only.
    expect(states()).toEqual([
      ['folder.open', 'true', ''], ['folder.newFolder', 'true', ''], ['folder.newFile', 'true', ''],
      ['folder.rename', 'true', '미구현'], ['folder.download', 'false', ''], ['folder.upload', 'true', ''],
      ['folder.git', 'false', ''], ['folder.delete', 'true', ''],
    ])
    item('folder.open').click()
    await settle()
    expect(menuEl()).not.toBeNull()
    expect(rowPaths(w)).toEqual(['src', 'src/app.ts'])
  })

  // Q item 459: the file row and the root folder (the blank tree) open their mockup menus. T#2: the 2-B items follow their
  // own conditions (a read-only Flow here, a file without a change) — the 미구현 ones request nothing.
  it('a file row opens its menu (열기 / 이름 변경 / Git 되돌리기 / 이력 미구현; 다운로드 enabled, 수정 확인 / 삭제 by condition); nothing is called', async () => {
    const w = await fileExplorer()
    await w.get('[data-test="dfx-folder-row"][data-path="src"]').trigger('click')
    await settle()
    const onFile = rightClick(w.get('[data-test="dfx-file-row"][data-path="src/app.ts"]').element)
    await settle()
    expect(onFile.defaultPrevented).toBe(true)
    expect(itemTexts()).toEqual(['↗ 열기', '✎ 이름 변경', '↓ 다운로드', '⑂ Git ›', '× 삭제'])
    expect(states()).toEqual([
      ['file.open', 'true', '미구현'], ['file.rename', 'true', '미구현'], ['file.download', 'false', ''],
      ['file.git', 'false', ''], ['file.delete', 'true', ''],
    ])
    item('file.git').dispatchEvent(new MouseEvent('mouseenter'))
    await settle()
    const sub = Array.from(document.querySelectorAll<HTMLElement>('[data-designer-context-menu] [data-level="1"] [role="menuitem"]'))
    expect(sub.map((entry) => [entry.dataset.key, entry.getAttribute('aria-disabled'), entry.title])).toEqual([
      ['file.git.changes', 'true', ''], ['file.git.revert', 'true', '미구현'], ['file.git.history', 'true', '미구현'],
    ])
    for (const entry of [...sub, ...items().filter((entry) => !['file.git', 'file.download'].includes(entry.dataset.key!))]) press(entry)
    await settle()
    expect(menuEl()).not.toBeNull()
    expect(postRequest).not.toHaveBeenCalled()
    // The row keeps its own selection / open intent: the right click selected nothing and opened no diff or tab.
    expect(w.findAll('[data-test="dfx-file-row"][aria-selected="true"]')).toHaveLength(0)
  })

  it('the blank tree is the root folder: no 열기 / 이름 변경 / 삭제; the write items off on a read-only Flow; nothing is called', async () => {
    const w = await fileExplorer()
    const onBlank = rightClick(w.get('.dfx-body').element)
    await settle()
    expect(onBlank.defaultPrevented).toBe(true)
    expect(itemTexts()).toEqual(['＋ 새 폴더', '＋ 새 파일', '↓ 다운로드', '↑ 업로드', '⑂ Git ›'])
    expect(states().map(([key, off]) => [key, off])).toEqual([
      ['folder.newFolder', 'true'], ['folder.newFile', 'true'], ['folder.download', 'false'], ['folder.upload', 'true'], ['folder.git', 'false'],
    ])
    for (const entry of items().filter((entry) => !['folder.git', 'folder.download'].includes(entry.dataset.key!))) press(entry)
    await settle()
    expect(menuEl()).not.toBeNull()
    expect(postRequest).not.toHaveBeenCalled()
  })
})

describe('Designer context menu in the Shell — ③ ④ ⑤ and the close rules', () => {
  it('a right click on an inactive tab does not activate it', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    await openFlow(w, 'Designer v0.2')
    const active = () => w.get('[data-test="designer-flow-tab"] [role="tab"][aria-selected="true"]').text()
    expect(active()).toBe('Designer v0.2')
    rightClick(w.get('[data-test="designer-flow-tab"][data-flow-id="p.ui.0002"]').element)
    await settle()
    expect(active()).toBe('Designer v0.2')
    expect(itemTexts()).toEqual(['× 닫기', '⊟ 다른 탭 닫기', '☒ 전체 닫기'])
  })

  it('a Palette card: 카드 추가 selects it for the Canvas click placement only when chosen; the blank Palette opens nothing', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const card = () => w.get('[data-test="designer-palette-item"][data-item-id="task"]')
    rightClick(card().element)
    await settle()
    expect(card().attributes('aria-pressed')).toBe('false')
    expect(items().map((entry) => [entry.dataset.key, entry.getAttribute('aria-disabled')])).toEqual([['palette.addCard', 'false']])
    item('palette.addCard').click()
    await settle()
    expect(card().attributes('aria-pressed')).toBe('true')
    expect(w.get('[data-test="designer-canvas"]').classes()).toContain('is-placing')
    const blank = rightClick(w.get('[data-test="designer-palette"]').element)
    await settle()
    expect(menuEl()).toBeNull()
    expect(blank.defaultPrevented).toBe(false)
  })

  it('a Minimap right click only closes the open menu', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    rightClick(w.get('.vue-flow__pane').element)
    await settle()
    expect(menuEl()).not.toBeNull()
    const onMap = rightClick(w.get('[data-test="designer-minimap"]').element)
    await settle()
    expect(menuEl()).toBeNull()
    expect(onMap.defaultPrevented).toBe(false)
  })

  it('Escape closes the menu only: the Palette selection waiting for placement stays', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    await w.get('[data-test="designer-palette-item"][data-item-id="task"]').trigger('click')
    await settle()
    const card = () => w.get('[data-test="designer-palette-item"][data-item-id="task"]')
    expect(card().attributes('aria-pressed')).toBe('true')
    rightClick(w.get('.vue-flow__pane').element)
    await settle()
    expect(menuEl()).not.toBeNull()
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    ;(document.activeElement ?? document.body).dispatchEvent(escape)
    await settle()
    expect(menuEl()).toBeNull()
    expect(card().attributes('aria-pressed')).toBe('true')
  })

  it('the blank Canvas in the Shell: 카드 추가 and 전체 선택, and 전체 선택 runs Edit › 전체 선택', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    rightClick(w.get('.vue-flow__pane').element)
    await settle()
    expect(items().map((entry) => [entry.dataset.key, entry.getAttribute('aria-disabled')])).toEqual([
      ['canvas.addCard', 'false'], ['canvas.paste', 'true'], ['canvas.selectAll', 'false'], ['canvas.arrange', 'true'],
    ])
    item('canvas.selectAll').click()
    await settle()
    expect(w.get('[data-test="designer-canvas"]').attributes('data-select-mode')).toBe('true')
    expect(w.findAll('.vue-flow__node').filter((node) => node.find('.flow-node-card.is-selected').exists())).toHaveLength(2)
  })
})
