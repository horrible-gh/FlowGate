/** flowgate.v02test.0078 · 0015-T A (터미널 디자이너 꽉 채우기 화면): 모달리스 ↔ 디자이너.
 *  - dock.ts: the per-Flow place (floating / filling the Designer);
 *  - the Host: [⇲ 디자이너로] / [⇱ 모달리스로] keep the same session, per Flow, across Flow tabs, the toolbar's [터미널]
 *    (focus), [새로 열기], [✕] (the next one opens floating) and a closed Flow tab;
 *  - the window (with xterm.js and the WebSocket replaced): the move puts the same screen DOM into the Designer area,
 *    makes no new Terminal / WebSocket, fits again and sends the rows / columns; the area's size changes follow;
 *  - the bottom Dock is back to the Inspector and the Minimap only (no Inspector | 터미널 switch, no terminal place). */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  readTerminalStatus: vi.fn(),
  openTerminalSession: vi.fn(),
  closeTerminalSession: vi.fn(async () => {}),
}))
const auth = vi.hoisted(() => ({ admin: true }))
const xterm = vi.hoisted(() => ({ created: [] as any[], fits: 0, size: { cols: 80, rows: 20 }, reveals: [] as string[] }))

vi.mock('@/features/designer/flowTerminal/api', async (original) => ({
  ...(await original<typeof import('@/features/designer/flowTerminal/api')>()),
  readTerminalStatus: api.readTerminalStatus,
  openTerminalSession: api.openTerminalSession,
  closeTerminalSession: api.closeTerminalSession,
}))
vi.mock('@shared/auth', async (original) => ({
  ...(await original<typeof import('@shared/auth')>()),
  isAdminFromToken: () => auth.admin,
}))
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = xterm.size.cols
    rows = xterm.size.rows
    resizeListeners: ((size: { cols: number; rows: number }) => void)[] = []
    element: HTMLElement | null = null
    constructor() { xterm.created.push(this) }
    loadAddon(addon: { term?: unknown }) { addon.term = this }
    open(parent: HTMLElement) {
      const el = document.createElement('div')
      el.className = 'xterm'
      parent.appendChild(el)
      this.element = el
    }
    attachCustomKeyEventHandler() {}
    onData() { return { dispose() {} } }
    onResize(listener: (size: { cols: number; rows: number }) => void) { this.resizeListeners.push(listener); return { dispose() {} } }
    write() {}
    focus() {}
    dispose() {}
    hasSelection() { return false }
    getSelection() { return '' }
    clearSelection() {}
  },
}))
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    term: any
    fit() {
      xterm.fits++
      const t = this.term
      if (t.cols === xterm.size.cols && t.rows === xterm.size.rows) return
      t.cols = xterm.size.cols
      t.rows = xterm.size.rows
      for (const listener of t.resizeListeners) listener({ cols: t.cols, rows: t.rows })
    }
  },
}))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))
// The Host tests use a stand-in window (as flowTerminal.v02test0078.spec.ts does); the window tests import the real one.
vi.mock('@/features/designer/flowTerminal/FlowTerminalWindow.vue', async () => {
  const { defineComponent: define, h: render } = await import('vue')
  return { default: define({
    name: 'FlowTerminalWindow',
    props: ['flowId', 'title', 'session', 'position', 'visible', 'focused', 'zIndex', 'designer', 'fillTarget'],
    emits: ['focus', 'close', 'reopen', 'designer'],
    setup(props, { expose, emit }) {
      expose({ reveal: () => { xterm.reveals.push(props.flowId) }, requestClose: () => emit('close') })
      return () => render('div', {
        'data-test': 'terminal-standin', 'data-flow-id': props.flowId, 'data-visible': String(props.visible),
        'data-designer': String(!!props.designer), 'data-session': props.session?.session_id,
      })
    },
  }) }
})

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import FlowTerminalHost from '@/features/designer/flowTerminal/FlowTerminalHost.vue'
import DesignerBottomDock from '@/features/designer/shell/DesignerBottomDock.vue'
import { flowTerminalUi } from '@/features/designer/flowTerminal/state'
import { createTerminalPlaceState, forgetFlow, isDesignerNow, toDesigner, toModeless } from '@/features/designer/flowTerminal/dock'
import { resizeFrame } from '@/features/designer/flowTerminal/protocol'
import type { TerminalStatus } from '@/features/designer/flowTerminal/api'

registerAppMessages(i18n)

const status = (over: Partial<TerminalStatus> = {}): TerminalStatus => ({
  enabled: true, allowed: true, reason: null, max_sessions: 3, active_sessions: 0, flow_session_id: null, ...over,
})
const session = (id = 's1') => ({ session_id: id, cwd: 'C:/work/p/0001', ticket: `t-${id}`, ticket_expires_in: 30 })

class FakeSocket {
  static OPEN = 1
  static instances: FakeSocket[] = []
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) { FakeSocket.instances.push(this) }
  send(frame: string) { this.sent.push(frame) }
  close() { this.readyState = 3 }
  open() { this.readyState = 1; this.onopen?.() }
}
class FakeResizeObserver {
  static all: FakeResizeObserver[] = []
  constructor(public callback: () => void) { FakeResizeObserver.all.push(this) }
  observe() {}
  disconnect() {}
}

let wrapper: VueWrapper<any> | null = null
let target: HTMLElement
const offsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')
beforeEach(() => {
  i18n.global.locale.value = 'ko'
  auth.admin = true
  api.readTerminalStatus.mockReset().mockResolvedValue(status())
  api.openTerminalSession.mockReset()
  api.closeTerminalSession.mockClear()
  flowTerminalUi.status = null
  flowTerminalUi.openFlows = {}
  flowTerminalUi.currentOpen = false
  flowTerminalUi.open = null
  xterm.created.length = 0
  xterm.fits = 0
  xterm.reveals.length = 0
  xterm.size = { cols: 80, rows: 20 }
  FakeSocket.instances = []
  FakeResizeObserver.all = []
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  // jsdom has no layout: every element is "drawn" 600 px wide so the screen can be fitted.
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 600 })
  target = document.createElement('div')
  target.setAttribute('data-test', 'fill-place')
  document.body.appendChild(target)
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  if (offsetWidth) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', offsetWidth)
  document.body.innerHTML = ''
})

describe('place state (dock.ts)', () => {
  it('opens floating, fills the Designer, goes back to modeless, forgets a closed Flow', () => {
    const state = createTerminalPlaceState()
    expect(isDesignerNow(state, 'a', true, true)).toBe(false)
    toDesigner(state, 'a')
    expect(isDesignerNow(state, 'a', true, true)).toBe(true)
    expect(isDesignerNow(state, 'a', true, false)).toBe(false) // no area to fill: floating
    expect(isDesignerNow(state, 'a', false, true)).toBe(false) // no terminal: nothing to draw
    expect(isDesignerNow(state, 'b', true, true)).toBe(false) // per Flow
    expect(isDesignerNow(state, null, true, true)).toBe(false)
    toModeless(state, 'a')
    expect(isDesignerNow(state, 'a', true, true)).toBe(false)
    toDesigner(state, 'c')
    forgetFlow(state, 'c')
    expect(state.designer).toEqual({})
  })
})

describe('terminal Host — 모달리스 ↔ 디자이너', () => {
  const TABS = [{ flowId: 'p.m.0001', name: '플로우 1' }, { flowId: 'p.m.0002', name: '플로우 2' }]
  const mountHost = (props: Record<string, unknown> = {}) => {
    wrapper = mount(FlowTerminalHost, {
      props: { flowId: 'p.m.0001', tabs: TABS, fillTarget: target, ...props }, global: { plugins: [i18n] }, attachTo: document.body,
    })
    return wrapper
  }
  const standinOf = (flowId: string) => wrapper!.find(`[data-test="terminal-standin"][data-flow-id="${flowId}"]`)
  const windowOf = (flowId: string) =>
    wrapper!.findAllComponents({ name: 'FlowTerminalWindow' }).find(w => w.props('flowId') === flowId)!

  it('first opens modeless; [⇲ 디자이너로] fills the Designer with the same session; [⇱ 모달리스로] floats it again', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('false')
    expect(wrapper!.vm.designerActive('p.m.0001')).toBe(false)
    windowOf('p.m.0001').vm.$emit('designer', true)
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('true')
    expect(standinOf('p.m.0001').attributes('data-session')).toBe('s1')
    expect(wrapper!.vm.designerActive('p.m.0001')).toBe(true)
    expect(api.openTerminalSession).toHaveBeenCalledTimes(1)
    // The `>_` mark and the lit button are the same as for a floating window.
    expect(flowTerminalUi.openFlows).toEqual({ 'p.m.0001': true })
    expect(flowTerminalUi.currentOpen).toBe(true)
    windowOf('p.m.0001').vm.$emit('designer', false)
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('false')
    expect(wrapper!.vm.designerActive('p.m.0001')).toBe(false)
    expect(api.openTerminalSession).toHaveBeenCalledTimes(1)
    expect(api.closeTerminalSession).not.toHaveBeenCalled()
  })

  it('the toolbar [터미널] in 디자이너 mode gives the filling terminal the keyboard (no new session)', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('designer', true)
    await flushPromises()
    xterm.reveals.length = 0
    flowTerminalUi.open!()
    await flushPromises()
    expect(xterm.reveals).toEqual(['p.m.0001'])
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('true')
    expect(api.openTerminalSession).toHaveBeenCalledTimes(1)
  })

  it('keeps each Flow\'s place: one filling the Designer, one floating, across Flow tabs', async () => {
    api.openTerminalSession.mockResolvedValueOnce(session('s1')).mockResolvedValueOnce(session('s2'))
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('designer', true)
    await flushPromises()
    await wrapper!.setProps({ flowId: 'p.m.0002' })
    await flushPromises()
    // The other Flow shows its own screen: nothing fills its Designer.
    expect(wrapper!.vm.designerActive('p.m.0002')).toBe(false)
    flowTerminalUi.open!()
    await flushPromises()
    expect(standinOf('p.m.0002').attributes('data-designer')).toBe('false')
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('true')
    expect(standinOf('p.m.0001').attributes('data-visible')).toBe('false')
    await wrapper!.setProps({ flowId: 'p.m.0001' })
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-visible')).toBe('true')
    expect(wrapper!.vm.designerActive('p.m.0001')).toBe(true)
    expect(standinOf('p.m.0002').attributes('data-designer')).toBe('false')
  })

  it('[새로 열기] in 디자이너 mode keeps the place; [✕] brings the Designer back and the next one opens modeless', async () => {
    api.openTerminalSession.mockResolvedValueOnce(session('s1')).mockResolvedValueOnce(session('s2')).mockResolvedValueOnce(session('s3'))
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('designer', true)
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('reopen')
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-session')).toBe('s2')
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('true')
    windowOf('p.m.0001').vm.$emit('close')
    await flushPromises()
    expect(standinOf('p.m.0001').exists()).toBe(false)
    expect(wrapper!.vm.designerActive('p.m.0001')).toBe(false) // Canvas · Inspector · Minimap are back
    flowTerminalUi.open!()
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-session')).toBe('s3')
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('false')
  })

  it('a closed Flow tab closes its filling terminal and forgets the place', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('designer', true)
    await flushPromises()
    await wrapper!.setProps({ tabs: [TABS[1]], flowId: 'p.m.0002' })
    await flushPromises()
    expect(standinOf('p.m.0001').exists()).toBe(false)
    expect(wrapper!.vm.designerActive('p.m.0001')).toBe(false)
    expect(flowTerminalUi.openFlows).toEqual({})
    await wrapper!.setProps({ tabs: TABS, flowId: 'p.m.0001' })
    flowTerminalUi.open!()
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('false')
  })

  it('a terminal closed in 디자이너 mode gives the Designer back', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('designer', true)
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('close')
    await flushPromises()
    expect(standinOf('p.m.0001').exists()).toBe(false)
    expect(wrapper!.vm.designerActive('p.m.0001')).toBe(false)
  })

  it('without an area to fill it stays modeless', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost({ fillTarget: null })
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('designer', true)
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-designer')).toBe('false')
    expect(wrapper!.vm.designerActive('p.m.0001')).toBe(false)
  })
})

describe('terminal window — moving the same screen', () => {
  const mountWindow = async (props: Record<string, unknown> = {}) => {
    const real = (await vi.importActual<{ default: any }>('@/features/designer/flowTerminal/FlowTerminalWindow.vue')).default
    wrapper = mount(real, {
      props: {
        flowId: 'p.m.0001', title: '플로우 1', session: session(), position: 0, visible: true, focused: true, zIndex: 10,
        designer: false, fillTarget: target, ...props,
      },
      global: { plugins: [i18n] }, attachTo: document.body,
    })
    return wrapper
  }
  const q = (test: string, root: ParentNode = document) => root.querySelector<HTMLElement>(`[data-test="${test}"]`)
  async function ready() {
    await vi.waitFor(() => expect(xterm.created).toHaveLength(1))
    FakeSocket.instances[0].open()
    await flushPromises()
  }

  it('[⇲ 디자이너로] moves the screen into the Designer area: same Terminal, same WebSocket, fitted again with the new size sent', async () => {
    await mountWindow()
    await ready()
    const section = q('flow-terminal-window')!
    const screen = q('flow-terminal-screen')!
    const xtermEl = screen.querySelector('.xterm')
    expect(section.contains(screen)).toBe(true)
    expect(q('flow-terminal-to-designer', section)!.textContent).toContain('⇲')
    expect(q('flow-terminal-to-designer', section)!.textContent).toContain('디자이너로')
    q('flow-terminal-to-designer', section)!.click()
    expect(wrapper!.emitted('designer')).toEqual([[true]])

    xterm.size = { cols: 180, rows: 44 }
    const socket = FakeSocket.instances[0]
    socket.sent = []
    await wrapper!.setProps({ designer: true })
    await flushPromises()
    expect(target.contains(screen)).toBe(true)
    expect(screen.querySelector('.xterm')).toBe(xtermEl)
    expect(section.style.display).toBe('none')
    expect(xterm.created).toHaveLength(1)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(api.closeTerminalSession).not.toHaveBeenCalled()
    expect(socket.sent).toContain(resizeFrame(180, 44))
    // The Designer title bar: the folder, [⇱ 모달리스로], [✕]. No Inspector | 터미널 switch, no [아래에 붙이기] / [떼기].
    expect(q('flow-terminal-designer-cwd', target)!.textContent).toBe('C:/work/p/0001')
    expect(q('flow-terminal-to-modeless', target)!.textContent).toContain('⇱')
    expect(q('flow-terminal-to-modeless', target)!.textContent).toContain('모달리스로')
    expect(q('flow-terminal-designer-close', target)).not.toBeNull()
    expect(document.body.textContent).not.toContain('아래에 붙이기')
    expect(document.body.textContent).not.toContain('떼기')
  })

  it('follows the area\'s size while filling the Designer', async () => {
    await mountWindow({ designer: true })
    await ready()
    const socket = FakeSocket.instances[0]
    socket.sent = []
    xterm.size = { cols: 150, rows: 40 }
    for (const observer of FakeResizeObserver.all) observer.callback()
    expect(socket.sent).toContain(resizeFrame(150, 40))
  })

  it('[⇱ 모달리스로] asks to float; floating again the screen is back in the window where it was, output kept', async () => {
    await mountWindow({ designer: true })
    await ready()
    const screen = q('flow-terminal-screen')!
    const xtermEl = screen.querySelector('.xterm')
    expect(target.contains(screen)).toBe(true)
    const section = q('flow-terminal-window')!
    q('flow-terminal-to-modeless', target)!.click()
    expect(wrapper!.emitted('designer')).toEqual([[false]])
    await wrapper!.setProps({ designer: false })
    await flushPromises()
    expect(section.contains(screen)).toBe(true)
    expect(target.contains(screen)).toBe(false)
    expect(screen.querySelector('.xterm')).toBe(xtermEl)
    expect(section.style.display).not.toBe('none')
    expect(xterm.created).toHaveLength(1)
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('another Flow tab\'s filling terminal is not shown; [✕] in the Designer ends the session like the window\'s', async () => {
    await mountWindow({ designer: true, visible: false })
    await ready()
    expect(q('flow-terminal-body', target)!.style.display).toBe('none')
    await wrapper!.setProps({ visible: true })
    expect(q('flow-terminal-body', target)!.style.display).not.toBe('none')
    q('flow-terminal-designer-close', target)!.click()
    await flushPromises()
    expect(api.closeTerminalSession).toHaveBeenCalledWith('s1')
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })

  it('[⇲ 디자이너로] is unavailable without an area to fill', async () => {
    await mountWindow({ fillTarget: null })
    await ready()
    expect((q('flow-terminal-to-designer') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('bottom Dock — back to the Inspector and the Minimap', () => {
  const minimap = (flowId: string | null) => ({ flowId, graph: null, viewport: null, canvasSize: null })
  function q(test: string) { return document.querySelector<HTMLElement>(`[data-test="${test}"]`) }

  it('has no Inspector | 터미널 switch and no terminal place, even with the old bridge provided', () => {
    const setTarget = vi.fn()
    wrapper = mount(defineComponent({
      setup: () => () => h(DesignerBottomDock, { height: 300, selectedNode: null, minimap: minimap('p.m.0001') }),
    }), {
      global: {
        plugins: [i18n],
        provide: { [Symbol.for('flowgate.designer.bottomDockTerminal')]: { pane: () => 'terminal', select: vi.fn(), setTarget, labels: () => null } },
      },
      attachTo: document.body,
    })
    expect(q('designer-bottom-switch')).toBeNull()
    expect(q('designer-bottom-terminal')).toBeNull()
    expect(q('designer-bottom-main')).not.toBeNull()
    expect(q('designer-bottom-minimap')).not.toBeNull()
    expect(setTarget).not.toHaveBeenCalled()
  })
})
