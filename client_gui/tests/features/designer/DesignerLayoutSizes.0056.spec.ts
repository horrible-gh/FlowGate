/**
 * flowgate.v02test.0056 — the Designer's Dock sizes are saved per person, and 창 › 창 배치 초기화
 * (R 0056.0001 §2 / §3).
 *
 * The Shell is mounted as in DesignerResize.0046 (real Explorers, real project store, only the
 * HTTP layer `@shared/api` mocked). jsdom lays nothing out, so the "window too small" drawing is
 * pinned on the pure functions and on a ResizeObserver stand-in that reports a size.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { useProjectStore, type Project } from '@/features/project'
import {
  DESIGNER_CENTER_MIN_WIDTH,
  dockSizesFromStored,
  fitBottomHeight,
  fitDockWidths,
  normalizeDockSize,
} from '@/features/designer/shell/designerLayout'
import { DESIGNER_MENUS } from '@/features/designer/top/designerTop'

const getRequest = vi.hoisted(() => vi.fn())
const patchRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  patchRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

const UI_SETTINGS = '/api/v1/me/ui-settings'

// --- Canvas environment for Vue Flow under jsdom, plus a ResizeObserver that can report ---------
const observers: { callback: () => void; target: Element | null }[] = []
class ResizeObserverStub {
  private entry: { callback: () => void; target: Element | null }
  constructor(callback: () => void) {
    this.entry = { callback, target: null }
    observers.push(this.entry)
  }
  observe(target: Element) { this.entry.target = target }
  unobserve() {}
  disconnect() { this.entry.target = null }
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

const TREE = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
]

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

/** What the server holds; null = the settings read fails. */
let stored: Record<string, unknown> | null = {}
let settingsRead: Promise<void> = Promise.resolve()

beforeEach(() => {
  localStorage.clear()
  observers.length = 0
  stored = {}
  settingsRead = Promise.resolve()
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  patchRequest.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (url === UI_SETTINGS) {
      await settingsRead
      if (stored === null) throw new Error('settings read failed')
      return { data: { ok: true, settings: { ai_finished_card_retention_minutes: -1, ...stored } } }
    }
    if (/^\/api\/v1\/projects\/p\/groups\/tree$/.test(url)) return { data: { data: { nodes: TREE } } }
    if (/\/git\/status$/.test(url)) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    const group = /\/git\/groups\/([^/]+)\/(tree|changes)$/.exec(url)
    if (group && group[2] === 'tree') return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: [] } } }
    if (group) {
      return { data: { data: { group_id: decodeURIComponent(group[1]), branch: 'flow', commit: 'c0ffee', base_branch: params.base ?? 'v0.2', changes: [] } } }
    }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    throw new Error(`unexpected request ${url}`)
  })
  patchRequest.mockImplementation(async (_url: string, body: Record<string, unknown>) => {
    stored = { ...(stored ?? {}), ...body }
    return { data: { ok: true, settings: stored } }
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

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)

function sized(el: Element, prop: 'clientWidth' | 'clientHeight', value: number) {
  Object.defineProperty(el, prop, { configurable: true, value })
}

/** Gives the Body / Center column a size and lets their ResizeObservers report it. */
async function resizeWindow(w: VueWrapper<any>, width: number, height: number) {
  sized(get(w, 'designer-body').element, 'clientWidth', width)
  sized(get(w, 'designer-center-column').element, 'clientHeight', height)
  for (const observer of observers) {
    const test = observer.target?.getAttribute('data-test')
    if (test === 'designer-body' || test === 'designer-center-column') observer.callback()
  }
  await nextTick()
}

async function mountShell(body = { width: 1600, height: 900 }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project('p', 'Alpha'))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  await resizeWindow(wrapper, body.width, body.height)
  return wrapper
}

async function drag(w: VueWrapper<any>, handle: string, dx: number, dy: number, release = true) {
  const start = { clientX: 500, clientY: 500 }
  get(w, handle).element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, ...start }))
  document.dispatchEvent(new MouseEvent('mousemove', { clientX: start.clientX + dx, clientY: start.clientY + dy }))
  if (release) document.dispatchEvent(new MouseEvent('mouseup'))
  await settle()
}

async function chooseWindowReset(w: VueWrapper<any>) {
  await w.get('[data-test="designer-menu"][data-menu="window"]').trigger('click')
  await w.get('[data-test="designer-menu-pop"][data-menu="window"] [data-command="window.reset-layout"]').trigger('click')
  await settle()
}

const leftWidth = (w: VueWrapper<any>) => Number(get(w, 'designer-body').attributes('data-left-width'))
const rightWidth = (w: VueWrapper<any>) => Number(get(w, 'designer-body').attributes('data-right-width'))
const bottomHeight = (w: VueWrapper<any>) => (get(w, 'designer-bottom-dock').element as HTMLElement).style.height
const sizePatches = () => patchRequest.mock.calls.filter(([url]) => url === UI_SETTINGS).map(([, body]) => body)
const SAVED = { designer_left_width: 420, designer_right_width: 510, designer_bottom_height: 240 }

describe('designerLayout — drawing chosen sizes in a small window (0056 §2.3)', () => {
  it('draws the chosen widths as they are while they fit', () => {
    expect(fitDockWidths(400, 500, true, true, 1568)).toEqual({ left: 400, right: 500 })
    expect(fitDockWidths(400, 500, true, true, 400 + 500 + DESIGNER_CENTER_MIN_WIDTH)).toEqual({ left: 400, right: 500 })
  })

  it('an unmeasured Body changes nothing', () => {
    expect(fitDockWidths(900, 900, true, true, 0)).toEqual({ left: 900, right: 900 })
    expect(fitBottomHeight(900, 0)).toBe(900)
  })

  it('too narrow: both Docks give way in proportion and the Center keeps its minimum', () => {
    const shown = fitDockWidths(600, 500, true, true, 1100)
    expect(shown.left + shown.right + DESIGNER_CENTER_MIN_WIDTH).toBe(1100)
    expect(shown.left).toBeLessThan(600)
    expect(shown.right).toBeLessThan(500)
    expect(shown.left).toBeGreaterThanOrEqual(200)
    expect(shown.right).toBeGreaterThanOrEqual(240)
  })

  it('never goes below a Dock minimum; narrower than that the Body scrolls as before', () => {
    expect(fitDockWidths(600, 500, true, true, 700)).toEqual({ left: 200, right: 240 })
  })

  it('a collapsed Dock (its rail) is left alone', () => {
    expect(fitDockWidths(40, 900, false, true, 1100)).toEqual({ left: 40, right: 700 })
    expect(fitDockWidths(900, 40, true, false, 1100)).toEqual({ left: 700, right: 40 })
  })

  it('the Dock dragged last keeps its width; the other gives way first', () => {
    expect(fitDockWidths(400, 500, true, true, 1100, 'left')).toEqual({ left: 400, right: 340 })
    expect(fitDockWidths(400, 500, true, true, 1100, 'right')).toEqual({ left: 240, right: 500 })
    // The other is already at its minimum: the kept one gives the rest.
    expect(fitDockWidths(700, 500, true, true, 1100, 'left')).toEqual({ left: 500, right: 240 })
  })

  it('the Bottom Dock leaves the Workspace its minimum and keeps its own', () => {
    expect(fitBottomHeight(320, 900)).toBe(320)
    expect(fitBottomHeight(600, 600)).toBe(440)
    expect(fitBottomHeight(600, 250)).toBe(160)
  })

  it('a missing or strange saved size reads as its default', () => {
    expect(dockSizesFromStored({ leftWidth: 420, rightWidth: 510, bottomHeight: 240 })).toEqual({ leftWidth: 420, rightWidth: 510, bottomHeight: 240 })
    expect(dockSizesFromStored({ leftWidth: undefined, rightWidth: '510', bottomHeight: 5 })).toEqual({ leftWidth: 280, rightWidth: 300, bottomHeight: 320 })
    for (const bad of [null, '420', 199, 4001, 300.5, true, Number.NaN]) {
      expect(normalizeDockSize('leftWidth', bad), String(bad)).toBe(280)
    }
    expect(normalizeDockSize('bottomHeight', 160)).toBe(160)
  })
})

describe('Designer Shell — saved Dock sizes (0056 §2)', () => {
  it('opens with the saved sizes', async () => {
    stored = { ...SAVED }
    const w = await mountShell()
    expect([leftWidth(w), rightWidth(w), bottomHeight(w)]).toEqual([420, 510, '240px'])
    expect(sizePatches()).toEqual([])
  })

  it('keeps the Body unseen until the saved sizes are read, so the defaults never flash', async () => {
    stored = { ...SAVED }
    let answer!: () => void
    settingsRead = new Promise<void>((resolve) => { answer = resolve })
    const w = await mountShell()
    expect(get(w, 'designer-body').classes()).toContain('is-awaiting-sizes')
    expect(get(w, 'designer-top').exists()).toBe(true)
    answer()
    await settle()
    expect(get(w, 'designer-body').classes()).not.toContain('is-awaiting-sizes')
    expect(leftWidth(w)).toBe(420)
  })

  it('a failed read draws the defaults and shows the Body', async () => {
    stored = null
    const w = await mountShell()
    expect(get(w, 'designer-body').classes()).not.toContain('is-awaiting-sizes')
    expect([leftWidth(w), rightWidth(w), bottomHeight(w)]).toEqual([280, 300, '320px'])
  })

  it('a read that takes too long stops hiding the Body', async () => {
    vi.useFakeTimers()
    try {
      settingsRead = new Promise<void>(() => {})
      const pinia = createPinia()
      setActivePinia(pinia)
      useProjectStore().setCurrentProject(project('p', 'Alpha'))
      wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
      await nextTick()
      expect(get(wrapper, 'designer-body').classes()).toContain('is-awaiting-sizes')
      vi.advanceTimersByTime(1500)
      await nextTick()
      expect(get(wrapper, 'designer-body').classes()).not.toContain('is-awaiting-sizes')
    } finally {
      vi.useRealTimers()
    }
  })

  it('saves once when the handle is let go, not while dragging', async () => {
    const w = await mountShell()
    await drag(w, 'designer-left-resize', 120, 0, false)
    expect(leftWidth(w)).toBe(400)
    expect(sizePatches()).toEqual([])
    document.dispatchEvent(new MouseEvent('mouseup'))
    await settle()
    expect(sizePatches()).toEqual([{ designer_left_width: 400, designer_right_width: 300, designer_bottom_height: 320 }])

    await drag(w, 'designer-right-resize', -100, 0)
    await drag(w, 'designer-bottom-resize', 0, 80)
    expect(sizePatches().slice(1)).toEqual([
      { designer_left_width: 400, designer_right_width: 400, designer_bottom_height: 320 },
      { designer_left_width: 400, designer_right_width: 400, designer_bottom_height: 240 },
    ])
  })

  it('a let-go that changed nothing sends nothing', async () => {
    const w = await mountShell()
    await drag(w, 'designer-left-resize', 0, 0)
    expect(sizePatches()).toEqual([])
  })

  it('a failed save keeps the dragged size, shows nothing, and the next let-go saves again', async () => {
    const w = await mountShell()
    patchRequest.mockRejectedValueOnce(new Error('offline'))
    await drag(w, 'designer-left-resize', 120, 0)
    expect(leftWidth(w)).toBe(400)
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(sizePatches()).toHaveLength(1)
    // Same size again: it is still sent, because the server never got it.
    await drag(w, 'designer-left-resize', 0, 0)
    expect(sizePatches()).toHaveLength(2)
    expect(stored).toMatchObject({ designer_left_width: 400 })
  })

  it('a small window draws the Docks smaller without saving; a large one shows the chosen sizes again', async () => {
    stored = { designer_left_width: 600, designer_right_width: 500, designer_bottom_height: 600 }
    const w = await mountShell({ width: 1568, height: 800 })
    expect([leftWidth(w), rightWidth(w), bottomHeight(w)]).toEqual([600, 500, '600px'])

    await resizeWindow(w, 1100, 600)
    expect(leftWidth(w) + rightWidth(w) + DESIGNER_CENTER_MIN_WIDTH).toBe(1100)
    expect(leftWidth(w)).toBeLessThan(600)
    expect(rightWidth(w)).toBeLessThan(500)
    expect(bottomHeight(w)).toBe('440px')
    expect(sizePatches()).toEqual([])

    await resizeWindow(w, 1568, 800)
    expect([leftWidth(w), rightWidth(w), bottomHeight(w)]).toEqual([600, 500, '600px'])
    expect(sizePatches()).toEqual([])
  })

  it('a drag in a small window starts from the size on screen', async () => {
    stored = { designer_left_width: 600, designer_right_width: 500, designer_bottom_height: 320 }
    const w = await mountShell({ width: 1100, height: 900 })
    const shown = leftWidth(w)
    await drag(w, 'designer-left-resize', -50, 0)
    expect(leftWidth(w)).toBe(shown - 50)
    expect(sizePatches().at(-1)).toMatchObject({ designer_left_width: shown - 50, designer_right_width: 500 })
  })
})

describe('Designer Shell — 창 › 창 배치 초기화 (0056 §3)', () => {
  it('is the last item of the Window menu, under a separator', () => {
    const entries = DESIGNER_MENUS.find((menu) => menu.key === 'window')!.entries
    expect(entries.at(-1)).toEqual({ kind: 'command', command: 'window.reset-layout', label: 'window_reset_layout' })
    expect(entries.at(-2)).toEqual({ kind: 'separator' })
  })

  it.each([['ko', '창 배치 초기화'], ['en', 'Reset Window Layout'], ['ja', 'ウィンドウ配置をリセット']])('%s label', async (locale, label) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    expect(messages.designer.top.items.window_reset_layout).toBe(label)
  })

  it('goes back to the first sizes, both Docks shown and the 프로젝트 / 팔레트 tabs, and saves the sizes', async () => {
    stored = { ...SAVED }
    const w = await mountShell()
    await w.get('[data-test="designer-menu"][data-menu="window"]').trigger('click')
    await w.get('[data-test="designer-menu-pop"][data-menu="window"] [data-panel="left"]').trigger('click')
    await w.get('[data-test="designer-menu"][data-menu="window"]').trigger('click')
    await w.get('[data-test="designer-menu-pop"][data-menu="window"] [data-tab="git"]').trigger('click')
    expect(leftWidth(w)).toBe(40)

    await chooseWindowReset(w)
    expect([leftWidth(w), rightWidth(w), bottomHeight(w)]).toEqual([280, 300, '320px'])
    expect(w.find('[data-test="designer-left-resize"]').exists()).toBe(true)
    await w.get('[data-test="designer-menu"][data-menu="window"]').trigger('click')
    const pop = w.get('[data-test="designer-menu-pop"][data-menu="window"]')
    expect(pop.findAll('[data-test="designer-menu-tab"][aria-checked="true"]').map((el) => el.attributes('data-tab'))).toEqual(['project', 'palette'])
    expect(pop.findAll('[data-test="designer-menu-panel"]').map((el) => el.attributes('aria-checked'))).toEqual(['true', 'true', 'true', 'true'])
    expect(w.find('[role="dialog"]').exists()).toBe(false)
    expect(sizePatches()).toEqual([{ designer_left_width: 280, designer_right_width: 300, designer_bottom_height: 320 }])
  })

  it('already at the first layout: nothing happens and nothing is sent', async () => {
    const w = await mountShell()
    await chooseWindowReset(w)
    expect([leftWidth(w), rightWidth(w), bottomHeight(w)]).toEqual([280, 300, '320px'])
    expect(sizePatches()).toEqual([])
  })
})
