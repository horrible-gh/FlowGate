/**
 * flowgate.v02.0060 0017-T — the browser's own menu in the Designer (the mockup v2 contextmenu rule): blocking it is a
 * separate rule from the menu targets. A right click on a target opens the Designer menu; a place with no target, a
 * target whose menu has no item and a place excluded from the menu show nothing; only editable areas (input / textarea /
 * select / contenteditable) keep the browser menu. The block lives while the Designer Shell is mounted.
 * Part rule: entry.ts attachNativeMenuBlock. Shell: the real Designer Shell swept element by element, scaffold as
 * contextMenuAttachShell.0060 (the real Shell, project store, Center session and server Graph source; `@shared/api` mocked).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { attachNativeMenuBlock, blockNativeContextMenu, openContextMenu } from '@/features/designer/contextMenu/entry'
import type { DesignerMenuHost } from '@/features/designer/contextMenu/host'
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
const disposers: (() => void)[] = []
afterEach(() => {
  disposers.splice(0).forEach((dispose) => dispose())
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
function rightClick(target: EventTarget, x = 40, y = 50) {
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 2 })
  target.dispatchEvent(event)
  return event
}
const menuEl = () => document.querySelector<HTMLElement>('[data-designer-context-menu]')
const escape = () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
/** The test's own reading of the editable exception (the mockup's input, textarea, select, contenteditable). */
function editable(element: Element): boolean {
  if (element.closest('input, textarea, select')) return true
  const area = element.closest('[contenteditable]')
  return area !== null && area.getAttribute('contenteditable')?.toLowerCase() !== 'false'
}
function describeElement(element: Element): string {
  const test = element.getAttribute('data-test')
  const classes = typeof element.className === 'string' ? element.className.trim().split(/\s+/).filter(Boolean) : []
  return `${element.tagName.toLowerCase()}${test ? `[data-test=${test}]` : ''}${classes.map((c) => `.${c}`).join('')}`
}

describe('native menu block — the part rule (entry.ts)', () => {
  function place(html: string): HTMLElement {
    const box = document.createElement('div')
    box.innerHTML = html
    document.body.append(box)
    return box.firstElementChild as HTMLElement
  }

  it('blocks the browser menu everywhere but editable areas', () => {
    disposers.push(attachNativeMenuBlock(document))
    const blocked = [
      '<div>blank panel</div>', '<span data-context-menu-exclude>excluded from the menu</span>',
      '<div contenteditable="false">not editable</div>', '<svg><rect></rect></svg>', '<button type="button">button</button>',
    ]
    for (const html of blocked) expect(rightClick(place(html)).defaultPrevented, html).toBe(true)
    const kept = [
      '<input />', '<textarea></textarea>', '<select><option>a</option></select>', '<div contenteditable="true"><b>x</b></div>',
      '<div contenteditable="plaintext-only">x</div>', '<div contenteditable="">x</div>',
    ]
    for (const html of kept) {
      const element = place(html)
      expect(rightClick(element.firstElementChild ?? element).defaultPrevented, html).toBe(false)
    }
    // A text node target follows its parent element.
    const text = place('<p>words</p>').firstChild!
    expect(rightClick(text).defaultPrevented).toBe(true)
  })

  it('is separate from the menu targets: a target with no item and a null model open nothing and still block', () => {
    disposers.push(attachNativeMenuBlock(document))
    const host = { open: vi.fn(() => 1), close: vi.fn() } as unknown as DesignerMenuHost
    const results: boolean[] = []
    const empty = place('<div>empty target</div>')
    empty.addEventListener('contextmenu', (event) => { results.push(openContextMenu(event, host, { items: [] } as never)) })
    const none = place('<div>no model</div>')
    none.addEventListener('contextmenu', (event) => { results.push(openContextMenu(event, host, null)) })
    expect(rightClick(empty).defaultPrevented).toBe(true)
    expect(rightClick(none).defaultPrevented).toBe(true)
    expect(results).toEqual([false, false])
    expect(host.open).not.toHaveBeenCalled()
  })

  it('runs after the area handlers (bubble phase) and is released by its disposer', () => {
    const release = attachNativeMenuBlock(document)
    const target = place('<div>area</div>')
    const seen: boolean[] = []
    target.addEventListener('contextmenu', (event) => { seen.push(event.defaultPrevented) })
    expect(rightClick(target).defaultPrevented).toBe(true)
    expect(seen).toEqual([false])
    release()
    expect(rightClick(target).defaultPrevented).toBe(false)
    // The function alone leaves an editable target as it is.
    const input = place('<input />')
    const onInput = new MouseEvent('contextmenu', { cancelable: true })
    input.dispatchEvent(onInput)
    blockNativeContextMenu(onInput)
    expect(onInput.defaultPrevented).toBe(false)
  })
})

describe('native menu block — the whole Designer Shell', () => {
  it('every element of the Shell: blocked unless editable; the editable ones keep the browser menu and open no menu', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    expect(w.findAll('.vue-flow__node').length).toBeGreaterThan(0)
    const root = w.get('[data-test="designer-shell"]').element
    const all = [root, ...Array.from(root.querySelectorAll('*'))]
    const wrong: string[] = []
    let blocked = 0
    let kept = 0
    for (const element of all) {
      if (!element.isConnected) continue
      const event = rightClick(element)
      const keep = editable(element)
      if (event.defaultPrevented === keep) wrong.push(`${describeElement(element)} prevented=${event.defaultPrevented}`)
      if (keep) kept += 1
      else blocked += 1
    }
    expect(wrong).toEqual([])
    expect(blocked).toBeGreaterThan(200)
    // The Shell's own editable fields (the top bar search, the toolbar select) are in the sweep.
    expect(kept).toBeGreaterThan(0)
    escape()
    await settle()
    const field = root.querySelector('input, select')!
    const onField = rightClick(field)
    await settle()
    expect(onField.defaultPrevented).toBe(false)
    expect(menuEl()).toBeNull()
  })

  it('places with no menu target show nothing: no Designer menu and no browser menu', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    for (const selector of ['[data-test="designer-minimap"]', '[data-test="designer-palette"]', '[data-test="designer-shell"]']) {
      const event = rightClick(w.get(selector).element)
      await settle()
      expect(event.defaultPrevented, selector).toBe(true)
      expect(menuEl(), selector).toBeNull()
    }
    // An open menu closes on such a place and nothing replaces it.
    rightClick(w.get('.vue-flow__pane').element)
    await settle()
    expect(menuEl()).not.toBeNull()
    const onMap = rightClick(w.get('[data-test="designer-minimap"]').element)
    await settle()
    expect(onMap.defaultPrevented).toBe(true)
    expect(menuEl()).toBeNull()
  })

  it('menu targets keep their menus (project row, tab, blank Canvas, card, Palette card)', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    const targets: [string, () => Element][] = [
      ['project row', () => explorerRow(w, 'Alpha').element],
      ['tab', () => w.get('[data-test="designer-flow-tab"]').element],
      ['blank Canvas', () => w.get('.vue-flow__pane').element],
      ['card', () => w.get('.vue-flow__node').element],
      ['Palette card', () => w.get('[data-test="designer-palette-item"]').element],
    ]
    for (const [name, element] of targets) {
      const event = rightClick(element())
      await settle()
      expect(event.defaultPrevented, name).toBe(true)
      expect(menuEl(), name).not.toBeNull()
      escape()
      await settle()
      expect(menuEl(), name).toBeNull()
    }
  })

  it('a right click inside the open menu shows neither a new menu nor the browser menu', async () => {
    const w = await mountShell()
    await openFlow(w, 'Canvas')
    rightClick(w.get('.vue-flow__pane').element)
    await settle()
    const event = rightClick(menuEl()!.querySelector('[role="menuitem"]')!)
    await settle()
    expect(event.defaultPrevented).toBe(true)
  })

  it('the block ends with the Shell: after unmount a right click is the browser’s again', async () => {
    const w = await mountShell()
    expect(rightClick(w.get('[data-test="designer-shell"]').element).defaultPrevented).toBe(true)
    w.unmount()
    wrapper = null
    const outside = document.createElement('div')
    document.body.append(outside)
    expect(rightClick(outside).defaultPrevented).toBe(false)
  })
})
