/**
 * flowgate.v02.0060 T#1 (WP 0012 [B]) — 정렬 · 배치 (blank Canvas) and 정렬 (multi-selected card) of the Designer context
 * menu in the real Designer Shell: the same commands, words, enabled state and path as 편집 › 정렬 / 배치 (the Shell's
 * DESIGNER_ARRANGE_GROUPS, isMenuCommandEnabled and onTopCommand → center.arrangeNodes): the cards move as the Edit
 * menu moves them, one 실행 취소 brings them back, the selection and select mode stay, a selection change after
 * opening runs nothing, and the 미구현 items of the multi-selection menu change nothing. Scaffold as
 * DesignerShellArrange.0059: real Shell, project store, Center session and server Graph source; `@shared/api` mocked.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  putRequest,
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
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
]

/** Three movable document cards where the user left them (as DesignerShellArrange.0059). */
function canvasResponse(flowId: string) {
  const card = (seq: number, type: string, kind: string, x: number, y: number) => ({
    node_key: `doc:${flowId}.000${seq}-${type}`, identity_source: 'document', origin: 'document', kind, doc_type: type,
    title: `${type} card`, state: 'done', position: { x, y, source: 'saved' }, editable: { move: true, structure: false },
    document: null, slot: null, graph_node: null, node_run: null,
  })
  return {
    ok: true,
    flow_id: flowId,
    project_id: 'p',
    ownership: { state: 'unassigned', sequence_root_doc_id: null, graph_revision_no: null, assigned_at: null },
    projection: 'documents',
    view: null,
    graph: { draft: null, latest_revision_no: null, root_binding: null },
    nodes: [card(1, 'R', 'requirement', 0, 0), card(2, 'N', 'research', 300, 120), card(3, 'D', 'design', 90, 400)],
    edges: [],
    external_relations: [],
    viewport: null,
    run: null,
    identity: { sequence_tag: null, rematched: false, retired_node_keys: [] },
    collapsed: { applied: [], ignored_keys: [], hidden_counts: {} },
  }
}

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  putRequest.mockImplementation(async () => ({ data: { ok: true, saved_positions: 1, viewport_saved: false, ignored: [] } }))
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (url === '/api/v1/projects/p/groups/tree') return { data: { data: { nodes: TREE } } }
    if (/\/git\/status$/.test(url)) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes)$/.exec(url)
    if (group && group[2] === 'tree') return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: [] } } }
    if (group) return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: 'v0.2', changes: [] } } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    const canvas = /^\/api\/v1\/flows\/([^/]+)\/canvas$/.exec(url)
    if (canvas) return { data: canvasResponse(decodeURIComponent(canvas[1])) }
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
  return found.find('.explorer-row')
}
async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await flushPromises()
  }
  await settle()
}
async function openCanvasFlow(w: VueWrapper<any>) {
  await doubleClick(w, 'UI 개선')
  await doubleClick(w, 'Canvas')
}

const R = 'doc:p.ui.0002.0001-R'
const N = 'doc:p.ui.0002.0002-N'
const D = 'doc:p.ui.0002.0003-D'
function cardAt(w: VueWrapper<any>, id: string): [number, number] {
  const style = w.get(`.vue-flow__node[data-id="${id}"]`).attributes('style') ?? ''
  const match = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(style)
  if (!match) throw new Error(`no position for ${id}: ${style}`)
  return [Number(match[1]), Number(match[2])]
}
const cardsAt = (w: VueWrapper<any>) => [R, N, D].map((id) => cardAt(w, id))
const selectedCards = (w: VueWrapper<any>) => w.findAll('.vue-flow__node').filter((node) => node.find('.flow-node-card.is-selected').exists()).length
const tool = (w: VueWrapper<any>, command: string) => w.get(`[data-test="designer-tool"][data-command="${command}"]`)
const isDisabled = (el: { attributes: (name: string) => string | undefined }) => el.attributes('disabled') !== undefined

async function editCommand(w: VueWrapper<any>, command: string) {
  await w.get('[data-test="designer-menu"][data-menu="edit"]').trigger('click')
  await w.get(`[data-test="designer-menu-item"][data-command="${command}"]`).trigger('click')
  await settle()
}
/** The Edit menu's own 정렬 / 배치 submenu: [command, words, disabled] of each item. */
async function editArrange(w: VueWrapper<any>) {
  await w.get('[data-test="designer-menu"][data-menu="edit"]').trigger('click')
  await w.get('[data-test="designer-menu-sub"][data-sub="arrange"]').trigger('click')
  const pop = w.get('[data-test="designer-menu-subpop"][data-sub="arrange"]')
  const rows = pop.findAll('[data-command]').map((el) => [el.attributes('data-command'), el.text().trim(), isDisabled(el)])
  const separators = pop.findAll('[role="separator"]').length
  await w.get('[data-test="designer-menu"][data-menu="edit"]').trigger('click')
  return { rows, separators }
}

function rightClick(element: Element, x = 40, y = 50) {
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 2 })
  element.dispatchEvent(event)
  return event
}
const menuEl = () => document.querySelector<HTMLElement>('[data-designer-context-menu]')
const panelItems = (level: number) =>
  Array.from(document.querySelectorAll<HTMLElement>(`[data-designer-context-menu] [data-level="${level}"] [role="menuitem"]`))
const item = (key: string, level = 0) => panelItems(level).find((entry) => entry.dataset.key === key)!
/** The context submenu as [Edit command, words, disabled], to set beside the Edit menu's. */
const contextArrange = (parent: string) => ({
  rows: panelItems(1).map((entry) => [entry.dataset.key!.slice(parent.length + 1), entry.querySelector('.designer-context-label')!.textContent!.trim(), entry.getAttribute('aria-disabled') === 'true']),
  separators: document.querySelectorAll('[data-designer-context-menu] [data-level="1"] [role="separator"]').length,
})
async function openSub(target: Element, parent: string) {
  rightClick(target)
  await settle()
  item(parent).dispatchEvent(new MouseEvent('mouseenter'))
  await settle()
}
async function escape() {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
  await settle()
}

describe('Designer context menu in the Shell — 정렬 · 배치 / 정렬 = 편집 › 정렬 / 배치 (T#1)', () => {
  it('blank Canvas 정렬 · 배치: disabled with no selection like the Edit menu; with the cards selected the same 8 items, words and state', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    const pane = () => w.get('.vue-flow__pane').element
    rightClick(pane())
    await settle()
    expect(item('canvas.arrange').getAttribute('aria-disabled')).toBe('true')
    expect((await editArrange(w)).rows.every(([, , off]) => off)).toBe(true)
    await escape()

    await editCommand(w, 'edit.select-all')
    const edit = await editArrange(w)
    expect(edit.rows).toHaveLength(8)
    await openSub(pane(), 'canvas.arrange')
    expect(contextArrange('canvas.arrange')).toEqual(edit)
    expect(edit.separators).toBe(2)
  })

  it('choosing 위쪽 정렬 moves the selected cards as the Edit menu does; one 실행 취소 brings them back; selection and mode stay', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
    await editCommand(w, 'edit.select-all')
    expect(selectedCards(w)).toBe(3)
    expect(isDisabled(tool(w, 'edit.undo'))).toBe(true)
    await openSub(w.get('.vue-flow__pane').element, 'canvas.arrange')
    item('canvas.arrange:edit.align-top', 1).click()
    await settle()
    expect(menuEl()).toBeNull()
    expect(cardsAt(w)).toEqual([[0, 0], [300, 0], [90, 0]])
    expect(selectedCards(w)).toBe(3)
    expect(w.get('[data-test="designer-canvas"]').attributes('data-select-mode')).toBe('true')
    expect(isDisabled(tool(w, 'edit.undo'))).toBe(false)
    await tool(w, 'edit.undo').trigger('click')
    await settle()
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
    expect(isDisabled(tool(w, 'edit.undo'))).toBe(true)
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('a multi-selected card: 정렬 runs 가로 균등 분배 on the selection (as the Edit menu); its 미구현 items change nothing', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    await editCommand(w, 'edit.select-all')
    const card = () => w.get(`.vue-flow__node[data-id="${D}"]`).element
    const event = rightClick(card())
    await settle()
    expect(event.defaultPrevented).toBe(true)
    expect(panelItems(0).map((entry) => [entry.dataset.key, entry.getAttribute('aria-disabled'), entry.title])).toEqual([
      ['multi.editSettings', 'true', '미구현'], ['multi.copy', 'true', '미구현'], ['multi.arrange', 'false', ''], ['multi.delete', 'true', '미구현'],
    ])
    for (const key of ['multi.editSettings', 'multi.copy', 'multi.delete']) {
      item(key).click()
      item(key).focus()
      for (const k of ['Enter', ' ']) item(key).dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }))
    }
    await settle()
    expect(menuEl()).not.toBeNull()
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
    expect(w.findAll('.vue-flow__node')).toHaveLength(3)
    expect(isDisabled(tool(w, 'edit.undo'))).toBe(true)
    await escape()

    const edit = await editArrange(w)
    await openSub(card(), 'multi.arrange')
    expect(contextArrange('multi.arrange')).toEqual(edit)
    item('multi.arrange:edit.distribute-horizontal', 1).click()
    await settle()
    // Along x: R 0, D 90, N 300 -> D goes halfway (as 편집 › 정렬 / 배치 › 가로 균등 분배 in DesignerShellArrange.0059).
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [150, 400]])
    expect(selectedCards(w)).toBe(3)
    await tool(w, 'edit.undo').trigger('click')
    await settle()
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('a selection change after opening (select mode off) closes the menu; the chosen 정렬 runs nothing', async () => {
    const w = await mountShell()
    await openCanvasFlow(w)
    await editCommand(w, 'edit.select-all')
    await openSub(w.get(`.vue-flow__node[data-id="${N}"]`).element, 'multi.arrange')
    const stale = item('multi.arrange:edit.align-left', 1)
    // The Toolbar [선택] off: select mode ends and the multi-selection is emptied (the menu's open selection is gone).
    await tool(w, 'edit.select-mode').trigger('click')
    await settle()
    expect(menuEl()).toBeNull()
    expect(selectedCards(w)).toBe(0)
    stale.click()
    await settle()
    expect(cardsAt(w)).toEqual([[0, 0], [300, 120], [90, 400]])
    expect(isDisabled(tool(w, 'edit.undo'))).toBe(true)
  })
})
