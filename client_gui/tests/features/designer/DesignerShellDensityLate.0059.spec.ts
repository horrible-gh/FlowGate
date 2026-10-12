/**
 * flowgate.v02.0059 T#2 (0016-TR rev2) — on the real Shell (project store, Center session, server Graph source,
 * real Vue Flow Canvas; only `@shared/api` is mocked, lateLayoutServer.ts): a density whose 자동 배치 is still
 * reading (the whole Flow of a Flow with a hidden conversation, or the root document of a replaced v0.1 root)
 * and then 자율형 — from the Toolbar radios or from the View menu — leaves the cards where 자율형 found them when
 * the read answers: no card moves, no layout save goes out, no 실행 취소 appears. 자동 배치 chosen afterwards (Toolbar
 * ▾ / Edit menu) still lays the cards out at the server's spacing (자율형 = scale 1).
 * jsdom setup as DesignerShellArrange.0059.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'
import { createLateServer, documentsFlow, replacedRootFlow, type LateServer } from './lateLayoutServer'

const getRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
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
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Docs', number: '0002' }),
  n({ id: 'p.ui.0003', parent_id: 'module:p:ui', node_type: 'group', label: 'Legacy', number: '0003' }),
]

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

// Docs: R, CH (hidden), D where the user left them (the reviewer's R (2000, 1500), D (2300, 1540)).
// Legacy: a v0.1 root replaced twice (0005-R) and two documents, where the user left them.
const DOCS = { R: 'doc:p.ui.0002.0001-R', D: 'doc:p.ui.0002.0003-D' }
const LEGACY = ['doc:p.ui.0003.0005-R', 'doc:p.ui.0003.0003-D', 'doc:p.ui.0003.0004-D']

let server: LateServer

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  server = createLateServer()
  server.flows['p.ui.0002'] = documentsFlow('p.ui.0002', {
    saved: { [DOCS.R]: [2000, 1500], 'doc:p.ui.0002.0002-CH': [2600, 1580], [DOCS.D]: [2300, 1540] },
  })
  server.flows['p.ui.0003'] = replacedRootFlow('p.ui.0003', {
    [LEGACY[0]]: [2000, 1500], [LEGACY[1]]: [2300, 1540], [LEGACY[2]]: [2600, 1580],
  })
  getRequest.mockReset()
  putRequest.mockReset()
  putRequest.mockImplementation(server.put)
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (url === '/api/v1/projects/p/groups/tree') return { data: { data: { nodes: TREE } } }
    if (/\/git\/status$/.test(url)) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes)$/.exec(url)
    if (group && group[2] === 'tree') return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: [] } } }
    if (group) return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: 'v0.2', changes: [] } } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    return server.get(url, params)
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

async function openFlow(w: VueWrapper<any>, label: string) {
  if (!w.findAll('[data-explorer-row]').some((r) => r.find('[data-test="project-explorer-label"]').text() === label)) {
    await doubleClick(w, 'UI 개선')
  }
  await doubleClick(w, label)
}

/** Where the Canvas draws a card (its Vue Flow transform). */
function cardAt(w: VueWrapper<any>, id: string): [number, number] {
  const style = w.get(`.vue-flow__node[data-id="${id}"]`).attributes('style') ?? ''
  const match = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(style)
  if (!match) throw new Error(`no position for ${id}: ${style}`)
  return [Number(match[1]), Number(match[2])]
}
const cardsAt = (w: VueWrapper<any>, ids: readonly string[]) => ids.map((id) => cardAt(w, id))

const tool = (w: VueWrapper<any>, command: string) => w.get(`[data-test="designer-tool"][data-command="${command}"]`)
const disabled = (el: { attributes: (name: string) => string | undefined }) => el.attributes('disabled') !== undefined

async function chooseDensity(w: VueWrapper<any>, level: string, from: 'toolbar' | 'menu') {
  if (from === 'toolbar') {
    await w.get(`[data-test="designer-tool-density"] [data-density="${level}"]`).trigger('click')
  } else {
    await w.get('[data-test="designer-menu"][data-menu="view"]').trigger('click')
    await w.get('[data-test="designer-menu-sub"][data-sub="density"]').trigger('click')
    await w.get(`[data-test="designer-menu-density"][data-density="${level}"]`).trigger('click')
  }
  await settle()
}

/** The density the Toolbar radios and the View menu each show. */
async function shownDensity(w: VueWrapper<any>) {
  const radio = w.get('[data-test="designer-tool-density"] [aria-checked="true"]').attributes('data-density')
  await w.get('[data-test="designer-menu"][data-menu="view"]').trigger('click')
  await w.get('[data-test="designer-menu-sub"][data-sub="density"]').trigger('click')
  const menu = w.get('[data-test="designer-menu-density"][aria-checked="true"]').attributes('data-density')
  await w.get('[data-test="designer-menu"][data-menu="view"]').trigger('click')
  return [radio, menu]
}

/** 자동 배치 from the Toolbar's 정렬 / 배치 ▾ or from the Edit menu. */
async function autoLayoutCommand(w: VueWrapper<any>, from: 'toolbar' | 'menu') {
  if (from === 'toolbar') {
    await w.get('[data-tool="arrange"]').trigger('click')
    await w.get('[data-test="designer-arrange-menu"] [data-arrange="edit.auto-layout"]').trigger('click')
  } else {
    await w.get('[data-test="designer-menu"][data-menu="edit"]').trigger('click')
    await w.get('[data-test="designer-menu-pop"][data-menu="edit"] [data-test="designer-menu-item"][data-command="edit.auto-layout"]').trigger('click')
  }
  await settle()
}

const waitSaves = () => new Promise((resolve) => setTimeout(resolve, 450))

const CASES = [
  ['toolbar', 'the whole Flow read', 'Docs', 'whole', [DOCS.R, DOCS.D], [[2000, 1500], [2300, 1540]], [[0, 0], [520, 0]]],
  ['menu', 'the whole Flow read', 'Docs', 'whole', [DOCS.R, DOCS.D], [[2000, 1500], [2300, 1540]], [[0, 0], [520, 0]]],
  ['toolbar', 'the root document read', 'Legacy', 'document', LEGACY, [[2000, 1500], [2300, 1540], [2600, 1580]], [[0, 0], [260, 180], [520, 180]]],
  ['menu', 'the root document read', 'Legacy', 'document', LEGACY, [[2000, 1500], [2300, 1540], [2600, 1580]], [[0, 0], [260, 180], [520, 180]]],
] as const

describe('Designer Shell — 자율형 while a density is still reading leaves the cards (flowgate.v02.0059 T#2)', () => {
  it.each(CASES)('%s: 매우 넓게 waiting on %s (%s), then 자율형', async (from, _read, label, held, ids, left, laidOut) => {
    const w = await mountShell()
    await openFlow(w, label)
    expect(cardsAt(w, ids)).toEqual(left)
    server.hold[held] = true
    await chooseDensity(w, 'very-wide', from)
    expect(server.heldCount()).toBe(1)
    expect(cardsAt(w, ids)).toEqual(left)
    await chooseDensity(w, 'free', from)
    expect(await shownDensity(w)).toEqual(['free', 'free'])

    server.release()
    await settle()
    expect(cardsAt(w, ids)).toEqual(left)
    expect(disabled(tool(w, 'edit.undo'))).toBe(true)
    await waitSaves()
    expect(server.saves).toEqual([])

    // 자동 배치 chosen under 자율형 lays the cards out at the server's spacing, one 실행 취소.
    await autoLayoutCommand(w, from)
    expect(cardsAt(w, ids)).toEqual(laidOut)
    expect(disabled(tool(w, 'edit.undo'))).toBe(false)
    await tool(w, 'edit.undo').trigger('click')
    await settle()
    expect(cardsAt(w, ids)).toEqual(left)
  })

  it('a later density wins over one still reading: 매우 넓게 then 보통 lays the cards out at 보통 only', async () => {
    const w = await mountShell()
    await openFlow(w, 'Docs')
    server.hold.whole = true
    await chooseDensity(w, 'very-wide', 'toolbar')
    await chooseDensity(w, 'normal', 'menu')
    expect(server.heldCount()).toBe(2)
    server.release()
    await settle()
    expect(cardsAt(w, [DOCS.R, DOCS.D])).toEqual([[0, 0], [520, 0]])
    await tool(w, 'edit.undo').trigger('click')
    await settle()
    expect(cardsAt(w, [DOCS.R, DOCS.D])).toEqual([[2000, 1500], [2300, 1540]])
    expect(disabled(tool(w, 'edit.undo'))).toBe(true)
  })
})
