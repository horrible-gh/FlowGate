/** flowgate.v02test.0078 · 0009-T (터미널 붙이기 화면): docking a Flow terminal into the Designer's bottom Dock and back.
 *  - dock.ts: the per-Flow place (floating / docked) and the bottom Dock's Inspector | 터미널 choice;
 *  - the Host: [아래에 붙이기] / [떼기] keep the same session, per Flow, across Flow tabs, the toolbar's [터미널],
 *    [새로 열기], [닫기] and a closed Flow tab;
 *  - the window (with xterm.js and the WebSocket replaced): docking moves the same screen DOM into the Dock's place,
 *    makes no new Terminal / WebSocket, fits again and sends the rows / columns; the Dock's size changes follow;
 *  - the bottom Dock: the switch only for a docked terminal, the terminal place it gives. */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  readTerminalStatus: vi.fn(),
  openTerminalSession: vi.fn(),
  closeTerminalSession: vi.fn(async () => {}),
}))
const auth = vi.hoisted(() => ({ admin: true }))
const xterm = vi.hoisted(() => ({ created: [] as any[], fits: 0, size: { cols: 80, rows: 20 } }))

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
    props: ['flowId', 'title', 'session', 'position', 'visible', 'focused', 'zIndex', 'docked', 'dockTarget'],
    emits: ['focus', 'close', 'reopen', 'dock'],
    setup(props, { expose, emit }) {
      expose({ reveal: () => {}, requestClose: () => emit('close') })
      return () => render('div', {
        'data-test': 'terminal-standin', 'data-flow-id': props.flowId, 'data-visible': String(props.visible),
        'data-docked': String(!!props.docked), 'data-session': props.session?.session_id,
      })
    },
  }) }
})

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import FlowTerminalHost from '@/features/designer/flowTerminal/FlowTerminalHost.vue'
import DesignerBottomDock from '@/features/designer/shell/DesignerBottomDock.vue'
import { flowTerminalUi } from '@/features/designer/flowTerminal/state'
import {
  bottomPaneOf, createTerminalDockState, dockFlow, forgetFlow, isDockedNow, selectBottomPane, undockFlow,
} from '@/features/designer/flowTerminal/dock'
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
  xterm.size = { cols: 80, rows: 20 }
  FakeSocket.instances = []
  FakeResizeObserver.all = []
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  // jsdom has no layout: every element is "drawn" 600 px wide so the screen can be fitted.
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 600 })
  target = document.createElement('div')
  target.setAttribute('data-test', 'dock-place')
  document.body.appendChild(target)
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  if (offsetWidth) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', offsetWidth)
  document.body.innerHTML = ''
})

describe('dock state (dock.ts)', () => {
  it('opens floating, docks with the terminal tab chosen, switches, undocks, forgets a closed Flow', () => {
    const state = createTerminalDockState()
    expect(bottomPaneOf(state, 'a', true)).toBeNull()
    expect(isDockedNow(state, 'a', true, true)).toBe(false)
    dockFlow(state, 'a')
    expect(isDockedNow(state, 'a', true, true)).toBe(true)
    expect(isDockedNow(state, 'a', true, false)).toBe(false) // no Dock to go to: floating
    expect(isDockedNow(state, 'a', false, true)).toBe(false) // no window: nothing to draw
    expect(bottomPaneOf(state, 'a', true)).toBe('terminal')
    expect(bottomPaneOf(state, 'a', false)).toBeNull()
    expect(bottomPaneOf(state, 'b', true)).toBeNull()
    expect(bottomPaneOf(state, null, true)).toBeNull()
    selectBottomPane(state, 'a', 'inspector')
    expect(bottomPaneOf(state, 'a', true)).toBe('inspector')
    selectBottomPane(state, 'b', 'terminal') // not docked: nothing to choose
    expect(state.pane.b).toBeUndefined()
    undockFlow(state, 'a')
    expect(bottomPaneOf(state, 'a', true)).toBeNull()
    dockFlow(state, 'c')
    forgetFlow(state, 'c')
    expect(state.docked).toEqual({})
  })
})

describe('terminal Host — docking', () => {
  const TABS = [{ flowId: 'p.m.0001', name: '플로우 1' }, { flowId: 'p.m.0002', name: '플로우 2' }]
  const mountHost = (props: Record<string, unknown> = {}) => {
    wrapper = mount(FlowTerminalHost, {
      props: { flowId: 'p.m.0001', tabs: TABS, dockTarget: target, ...props }, global: { plugins: [i18n] }, attachTo: document.body,
    })
    return wrapper
  }
  const standinOf = (flowId: string) => wrapper!.find(`[data-test="terminal-standin"][data-flow-id="${flowId}"]`)
  const windowOf = (flowId: string) =>
    wrapper!.findAllComponents({ name: 'FlowTerminalWindow' }).find(w => w.props('flowId') === flowId)!

  it('first opens floating; [아래에 붙이기] docks the same session and chooses the 터미널 tab; [떼기] floats it again', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('false')
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBeNull()
    windowOf('p.m.0001').vm.$emit('dock', true)
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('true')
    expect(standinOf('p.m.0001').attributes('data-session')).toBe('s1')
    expect(api.openTerminalSession).toHaveBeenCalledTimes(1)
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBe('terminal')
    // The `>_` mark and the lit button are the same as for a floating window.
    expect(flowTerminalUi.openFlows).toEqual({ 'p.m.0001': true })
    expect(flowTerminalUi.currentOpen).toBe(true)
    windowOf('p.m.0001').vm.$emit('dock', false)
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('false')
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBeNull()
    expect(api.openTerminalSession).toHaveBeenCalledTimes(1)
  })

  it('the toolbar [터미널] chooses the bottom Dock\'s 터미널 tab of a docked terminal', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('dock', true)
    await flushPromises()
    wrapper!.vm.selectBottomPane('p.m.0001', 'inspector')
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBe('inspector')
    flowTerminalUi.open!()
    await flushPromises()
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBe('terminal')
    expect(api.openTerminalSession).toHaveBeenCalledTimes(1)
  })

  it('keeps each Flow\'s place: one docked, one floating, across Flow tabs', async () => {
    api.openTerminalSession.mockResolvedValueOnce(session('s1')).mockResolvedValueOnce(session('s2'))
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('dock', true)
    await flushPromises()
    await wrapper!.setProps({ flowId: 'p.m.0002' })
    await flushPromises()
    // The other Flow has no docked terminal: the bottom Dock is the Inspector alone.
    expect(wrapper!.vm.bottomPane('p.m.0002')).toBeNull()
    flowTerminalUi.open!()
    await flushPromises()
    expect(standinOf('p.m.0002').attributes('data-docked')).toBe('false')
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('true')
    expect(standinOf('p.m.0001').attributes('data-visible')).toBe('false')
    await wrapper!.setProps({ flowId: 'p.m.0001' })
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-visible')).toBe('true')
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('true')
    expect(standinOf('p.m.0002').attributes('data-docked')).toBe('false')
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBe('terminal')
  })

  it('[새로 열기] while docked opens the new session docked; [닫기] and opening again keeps the place', async () => {
    api.openTerminalSession.mockResolvedValueOnce(session('s1')).mockResolvedValueOnce(session('s2')).mockResolvedValueOnce(session('s3'))
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('dock', true)
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('reopen')
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-session')).toBe('s2')
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('true')
    windowOf('p.m.0001').vm.$emit('close')
    await flushPromises()
    expect(standinOf('p.m.0001').exists()).toBe(false)
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBeNull() // back to the Inspector alone
    flowTerminalUi.open!()
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-session')).toBe('s3')
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('true')
  })

  it('a closed Flow tab closes its docked terminal and forgets the place; the Dock is the Inspector again', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('dock', true)
    await flushPromises()
    await wrapper!.setProps({ tabs: [TABS[1]], flowId: 'p.m.0002' })
    await flushPromises()
    expect(standinOf('p.m.0001').exists()).toBe(false)
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBeNull()
    expect(flowTerminalUi.openFlows).toEqual({})
    await wrapper!.setProps({ tabs: TABS, flowId: 'p.m.0001' })
    flowTerminalUi.open!()
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('false')
  })

  it('without a bottom Dock there is nothing to dock into (stays floating)', async () => {
    api.openTerminalSession.mockResolvedValue(session())
    mountHost({ dockTarget: null })
    flowTerminalUi.open!()
    await flushPromises()
    windowOf('p.m.0001').vm.$emit('dock', true)
    await flushPromises()
    expect(standinOf('p.m.0001').attributes('data-docked')).toBe('false')
    expect(wrapper!.vm.bottomPane('p.m.0001')).toBeNull()
  })

  it('speaks Korean and English for the switch', async () => {
    mountHost()
    expect(wrapper!.vm.dockLabels()).toMatchObject({ inspector: '인스펙터', terminal: '터미널' })
    i18n.global.locale.value = 'en'
    await nextTick()
    expect(wrapper!.vm.dockLabels()).toMatchObject({ inspector: 'Inspector', terminal: 'Terminal' })
  })
})

describe('terminal window — moving the same screen', () => {
  const mountWindow = async (props: Record<string, unknown> = {}) => {
    const real = (await vi.importActual<{ default: any }>('@/features/designer/flowTerminal/FlowTerminalWindow.vue')).default
    wrapper = mount(real, {
      props: {
        flowId: 'p.m.0001', title: '플로우 1', session: session(), position: 0, visible: true, focused: true, zIndex: 10,
        docked: false, dockTarget: target, ...props,
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

  it('[아래에 붙이기] moves the screen into the Dock: same Terminal, same WebSocket, fitted again with the new size sent', async () => {
    await mountWindow()
    await ready()
    const section = q('flow-terminal-window')!
    const screen = q('flow-terminal-screen')!
    const xtermEl = screen.querySelector('.xterm')
    expect(section.contains(screen)).toBe(true)
    expect(q('flow-terminal-dock', section)!.getAttribute('title')).toBe('아래에 붙이기')
    q('flow-terminal-dock', section)!.click()
    expect(wrapper!.emitted('dock')).toEqual([[true]])

    xterm.size = { cols: 132, rows: 14 }
    const socket = FakeSocket.instances[0]
    socket.sent = []
    await wrapper!.setProps({ docked: true })
    await flushPromises()
    expect(target.contains(screen)).toBe(true)
    expect(screen.querySelector('.xterm')).toBe(xtermEl)
    expect(section.style.display).toBe('none')
    expect(xterm.created).toHaveLength(1)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(api.closeTerminalSession).not.toHaveBeenCalled()
    expect(socket.sent).toContain(resizeFrame(132, 14))
    // The docked header: the working folder, [떼기], [닫기].
    expect(q('flow-terminal-dock-cwd', target)!.textContent).toBe('C:/work/p/0001')
    expect(q('flow-terminal-undock', target)!.textContent).toContain('떼기')
    expect(q('flow-terminal-dock-close', target)!.textContent).toContain('닫기')
  })

  it('follows the Dock\'s height / the column\'s width while docked', async () => {
    await mountWindow({ docked: true })
    await ready()
    const socket = FakeSocket.instances[0]
    socket.sent = []
    xterm.size = { cols: 100, rows: 30 }
    for (const observer of FakeResizeObserver.all) observer.callback()
    expect(socket.sent).toContain(resizeFrame(100, 30))
  })

  it('[떼기] asks to float; floating again the screen is back in the window where it was', async () => {
    await mountWindow({ docked: true })
    await ready()
    const screen = q('flow-terminal-screen')!
    expect(target.contains(screen)).toBe(true)
    const section = q('flow-terminal-window')!
    q('flow-terminal-undock', target)!.click()
    expect(wrapper!.emitted('dock')).toEqual([[false]])
    await wrapper!.setProps({ docked: false })
    await flushPromises()
    expect(section.contains(screen)).toBe(true)
    expect(target.contains(screen)).toBe(false)
    expect(section.style.display).not.toBe('none')
    expect(xterm.created).toHaveLength(1)
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('a docked terminal of another Flow tab is not shown; [닫기] in the Dock ends the session like the window\'s', async () => {
    await mountWindow({ docked: true, visible: false })
    await ready()
    expect(q('flow-terminal-body', target)!.style.display).toBe('none')
    await wrapper!.setProps({ visible: true })
    expect(q('flow-terminal-body', target)!.style.display).not.toBe('none')
    q('flow-terminal-dock-close', target)!.click()
    await flushPromises()
    expect(api.closeTerminalSession).toHaveBeenCalledWith('s1')
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })

  it('[아래에 붙이기] is unavailable without a bottom Dock', async () => {
    await mountWindow({ dockTarget: null })
    await ready()
    expect((q('flow-terminal-dock') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('bottom Dock — Inspector | 터미널 switch', () => {
  const bridge = () => {
    const state = { pane: null as 'inspector' | 'terminal' | null }
    return {
      state,
      pane: vi.fn((flowId: string | null) => (flowId === 'p.m.0001' ? state.pane : null)),
      select: vi.fn(),
      setTarget: vi.fn(),
      labels: () => ({ inspector: '인스펙터', terminal: '터미널', switchLabel: '아래 칸 보기 전환' }),
    }
  }
  const minimap = (flowId: string | null) => ({ flowId, graph: null, viewport: null, canvasSize: null })
  const mountDock = (b: ReturnType<typeof bridge>, flowId: string | null = 'p.m.0001') => {
    wrapper = mount(defineComponent({
      setup: () => () => h(DesignerBottomDock, { height: 300, selectedNode: null, minimap: minimap(flowId) }),
    }), {
      global: { plugins: [i18n], provide: { [Symbol.for('flowgate.designer.bottomDockTerminal')]: b } }, attachTo: document.body,
    })
    return wrapper
  }

  it('gives its terminal place and takes it back when it goes away', () => {
    const b = bridge()
    mountDock(b)
    const place = q('designer-bottom-terminal')
    expect(b.setTarget).toHaveBeenCalledWith(place)
    wrapper!.unmount(); wrapper = null
    expect(b.setTarget).toHaveBeenLastCalledWith(null)
  })

  it('no docked terminal: the Inspector alone, no switch', () => {
    mountDock(bridge())
    expect(q('designer-bottom-switch')).toBeNull()
    expect(q('designer-bottom-inspector-pane')!.style.display).not.toBe('none')
    expect(q('designer-bottom-terminal')!.style.display).toBe('none')
    expect(q('designer-bottom-minimap')).not.toBeNull()
  })

  it('a docked terminal: the switch, the terminal shown in the left part, the Minimap kept; a tab asks the Host', async () => {
    const b = bridge()
    b.state.pane = 'terminal'
    mountDock(b)
    const tabs = [...document.querySelectorAll<HTMLElement>('[data-test^="designer-bottom-tab-"]')]
    expect(tabs.map(tab => tab.textContent)).toEqual(['인스펙터', '터미널'])
    expect(q('designer-bottom-tab-terminal')!.getAttribute('aria-selected')).toBe('true')
    expect(q('designer-bottom-terminal')!.style.display).not.toBe('none')
    expect(q('designer-bottom-inspector-pane')!.style.display).toBe('none')
    expect(q('designer-bottom-minimap')).not.toBeNull()
    q('designer-bottom-tab-inspector')!.click()
    expect(b.select).toHaveBeenCalledWith('p.m.0001', 'inspector')
  })

  function q(test: string) { return document.querySelector<HTMLElement>(`[data-test="${test}"]`) }
})
