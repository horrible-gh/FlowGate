/** flowgate.v02test.0078 (터미널 화면): the toolbar's [터미널] button (hidden / ready / lit; 0015-T C: the system setting
 *  off hides it for admins too), the `>_` mark on
 *  Flow tabs and in the tab list, the three-session notice, and the WebSocket frames. The terminal server may not exist
 *  yet, so the API is mocked; the terminal window is replaced by a stand-in (xterm.js needs a real layout). */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  readTerminalStatus: vi.fn(),
  openTerminalSession: vi.fn(),
  closeTerminalSession: vi.fn(async () => {}),
}))
const auth = vi.hoisted(() => ({ admin: true }))

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
vi.mock('@/features/designer/flowTerminal/FlowTerminalWindow.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'FlowTerminalWindow',
      props: ['flowId', 'title', 'session', 'position', 'visible', 'focused', 'zIndex', 'designer', 'fillTarget'],
      emits: ['focus', 'close', 'reopen', 'designer', 'disabled'],
      setup(props, { expose, emit }) {
        expose({ reveal: () => {}, requestClose: () => emit('close'), dispose: () => {} })
        return () => h('div', {
          'data-test': 'terminal-standin', 'data-flow-id': props.flowId, 'data-visible': String(props.visible), 'data-cwd': props.session?.cwd,
        })
      },
    }),
  }
})

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import FlowTabStrip from '@/features/designer/center/FlowTabStrip.vue'
import FlowTerminalHost from '@/features/designer/flowTerminal/FlowTerminalHost.vue'
import { flowTerminalUi, terminalButtonState } from '@/features/designer/flowTerminal/state'
import { openFailureOf, terminalSocketUrl, type TerminalStatus } from '@/features/designer/flowTerminal/api'
import { inputFrame, parseServerMessage, resizeFrame } from '@/features/designer/flowTerminal/protocol'

registerAppMessages(i18n)

const status = (over: Partial<TerminalStatus> = {}): TerminalStatus => ({
  enabled: true, allowed: true, reason: null, max_sessions: 3, active_sessions: 0, flow_session_id: null, ...over,
})
const NOT_ADMIN = status({ allowed: false, reason: 'not_admin' })
const DISABLED = status({ enabled: false, allowed: false, reason: 'disabled' })
const conflict = (code: string) => Object.assign(new Error(code), { isAxiosError: true, response: { status: 409, data: { detail: { code } } } })

let wrapper: VueWrapper<any> | null = null
beforeEach(() => {
  i18n.global.locale.value = 'ko'
  auth.admin = true
  api.readTerminalStatus.mockReset()
  api.openTerminalSession.mockReset()
  api.closeTerminalSession.mockClear()
  flowTerminalUi.status = null
  flowTerminalUi.openFlows = {}
  flowTerminalUi.currentOpen = false
  flowTerminalUi.open = null
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('terminalButtonState', () => {
  it('hides for a non-admin, an unknown status or the system setting off (0015-T C-1), lights an open one', () => {
    expect(terminalButtonState(null, false)).toBe('hidden')
    expect(terminalButtonState(NOT_ADMIN, false)).toBe('hidden')
    expect(terminalButtonState(DISABLED, false)).toBe('hidden')
    expect(terminalButtonState(DISABLED, true)).toBe('hidden')
    expect(terminalButtonState(status(), false)).toBe('ready')
    expect(terminalButtonState(status(), true)).toBe('on')
  })
})

describe('toolbar [터미널] button', () => {
  const mountToolbar = (props: Record<string, unknown> = {}) => {
    wrapper = mount(DesignerToolbar, { props: { runPhase: 'idle', density: 'free', zoom: 100, ...props }, global: { plugins: [i18n] } })
    return wrapper
  }
  const button = () => wrapper!.find('[data-test="flow-terminal-open"]')

  it('is not drawn at all for a non-admin (no gap) or before the status is known', async () => {
    mountToolbar()
    expect(button().exists()).toBe(false)
    flowTerminalUi.status = NOT_ADMIN
    await nextTick()
    expect(button().exists()).toBe(false)
    // The attachments button is still the last one of its group.
    const group = wrapper!.get('[data-test="flow-attachments-open"]').element.parentElement!
    expect(group.lastElementChild).toBe(wrapper!.get('[data-test="flow-attachments-open"]').element)
  })

  it('sits right after [첨부파일] in the same group (no separator) with the same shape', async () => {
    flowTerminalUi.status = status()
    mountToolbar()
    await nextTick()
    const attachment = wrapper!.get('[data-test="flow-attachments-open"]').element
    expect(attachment.nextElementSibling).toBe(button().element)
    expect(button().classes()).toEqual(expect.arrayContaining(['designer-tool', 'designer-tool-attachment']))
    expect(button().find('svg').exists()).toBe(true)
    expect(button().find('span').text()).toBe('터미널')
  })

  it('is not drawn at all (no gap, no dimmed button) when the system setting is off, for an admin too', async () => {
    flowTerminalUi.status = DISABLED
    mountToolbar()
    await nextTick()
    expect(button().exists()).toBe(false)
    expect(wrapper!.find('.designer-tool-terminal').exists()).toBe(false)
    const group = wrapper!.get('[data-test="flow-attachments-open"]').element.parentElement!
    expect(group.lastElementChild).toBe(wrapper!.get('[data-test="flow-attachments-open"]').element)
    // Turned on again: it comes back.
    flowTerminalUi.status = status()
    await nextTick()
    expect(button().exists()).toBe(true)
  })

  it('opens the terminal (view.terminal) and is lit with a dot while the window is open', async () => {
    flowTerminalUi.status = status()
    mountToolbar()
    await nextTick()
    expect(button().classes()).not.toContain('has-window')
    expect(wrapper!.find('[data-test="flow-terminal-dot"]').exists()).toBe(false)
    await button().trigger('click')
    expect(wrapper!.emitted('command')).toEqual([['view.terminal']])
    flowTerminalUi.currentOpen = true
    await nextTick()
    expect(button().classes()).toContain('has-window')
    expect(wrapper!.find('[data-test="flow-terminal-dot"]').exists()).toBe(true)
  })

  it('speaks English too', async () => {
    i18n.global.locale.value = 'en'
    flowTerminalUi.status = status()
    mountToolbar()
    await nextTick()
    expect(button().find('span').text()).toBe('Terminal')
    expect(button().attributes('title')).toBe('Terminal')
  })
})

describe('Flow tab `>_` mark', () => {
  const TABS = [{ flowId: 'a', name: '서버' }, { flowId: 'b', name: '화면' }, { flowId: 'c', name: '채팅' }]
  const mountStrip = (props: Record<string, unknown> = {}) => {
    wrapper = mount(FlowTabStrip, { props: { tabs: TABS, activeFlowId: 'a', ...props }, global: { plugins: [i18n] }, attachTo: document.body })
    return wrapper
  }
  async function overflow(w: VueWrapper<any>) {
    const strip = w.get('[data-test="designer-flow-tabs"]').element as HTMLElement
    const bar = w.get('[data-test="designer-flow-tab-bar"]').element as HTMLElement
    Object.defineProperty(strip, 'scrollWidth', { configurable: true, get: () => 900 })
    Object.defineProperty(strip, 'clientWidth', { configurable: true, get: () => 300 })
    Object.defineProperty(bar, 'clientWidth', { configurable: true, get: () => 300 })
    strip.dispatchEvent(new Event('scroll'))
    await nextTick(); await nextTick()
  }

  it('shows `>_` before the name of a Flow with an open terminal, next to the chat bubble when both apply', () => {
    const w = mountStrip({ terminalStates: { b: true, c: true }, chatStates: { c: true } })
    const marks = w.findAll('[data-test="designer-flow-tab-terminal"]')
    expect(marks).toHaveLength(2)
    expect(marks[0].text()).toBe('>_')
    expect(marks[0].attributes('aria-label')).toBe('터미널 열림')
    // Both marks show on the tab that has both (the chat bubble keeps its place right after `>_`).
    const chat = w.get('[data-test="designer-flow-tab-chat"]').element
    expect(chat.previousElementSibling).toBe(marks[1].element)
  })

  it('goes away when the terminal closes', async () => {
    const w = mountStrip({ terminalStates: { a: true } })
    expect(w.find('[data-test="designer-flow-tab-terminal"]').exists()).toBe(true)
    await w.setProps({ terminalStates: {} })
    expect(w.find('[data-test="designer-flow-tab-terminal"]').exists()).toBe(false)
  })

  it('is in the tab list too, without a notice dot on the list button', async () => {
    const w = mountStrip({ terminalStates: { c: true } })
    await overflow(w)
    const listButton = w.get('[data-test="designer-flow-tab-list-button"]')
    expect(listButton.attributes('data-has-mark')).toBeUndefined()
    expect(listButton.classes()).not.toContain('has-mark')
    await listButton.trigger('click')
    await nextTick(); await nextTick()
    const items = [...document.body.querySelectorAll<HTMLElement>('[data-test="designer-flow-tab-list-item"]')]
    expect(items.map(item => !!item.querySelector('[data-test="designer-flow-tab-list-terminal"]'))).toEqual([false, false, true])
  })
})

describe('terminal Host', () => {
  const TABS = [{ flowId: 'p.m.0001', name: '플로우 1' }, { flowId: 'p.m.0002', name: '플로우 2' }]
  const mountHost = (props: Record<string, unknown> = {}) => {
    wrapper = mount(FlowTerminalHost, { props: { flowId: 'p.m.0001', tabs: TABS, ...props }, global: { plugins: [i18n] }, attachTo: document.body })
    return wrapper
  }
  const session = (id = 's1') => ({ session_id: id, cwd: 'C:/work/p/0001', ticket: `t-${id}`, ticket_expires_in: 30 })
  const standins = () => wrapper!.findAll('[data-test="terminal-standin"]')

  it('reads GET /terminal/status of the open Flow for an admin, and never for a non-admin', async () => {
    api.readTerminalStatus.mockResolvedValue(status())
    mountHost()
    await flushPromises()
    expect(api.readTerminalStatus).toHaveBeenCalledWith('p.m.0001')
    expect(flowTerminalUi.status).toEqual(status())
    wrapper!.unmount(); wrapper = null
    api.readTerminalStatus.mockClear()
    auth.admin = false
    mountHost()
    await flushPromises()
    expect(api.readTerminalStatus).not.toHaveBeenCalled()
    expect(flowTerminalUi.status).toBeNull()
  })

  it('a failed status read hides the button (status null)', async () => {
    api.readTerminalStatus.mockRejectedValue(new Error('404'))
    mountHost()
    await flushPromises()
    expect(flowTerminalUi.status).toBeNull()
  })

  it('opens one window per Flow, marks its tab, keeps it (hidden) on another tab and brings it back', async () => {
    api.readTerminalStatus.mockResolvedValue(status())
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    await flushPromises()
    flowTerminalUi.open!()
    await flushPromises()
    expect(api.openTerminalSession).toHaveBeenCalledWith('p.m.0001', expect.any(Number), expect.any(Number))
    expect(standins()).toHaveLength(1)
    expect(standins()[0].attributes('data-cwd')).toBe('C:/work/p/0001')
    expect(flowTerminalUi.openFlows).toEqual({ 'p.m.0001': true })
    expect(flowTerminalUi.currentOpen).toBe(true)
    // Pressing again brings the same window up: no second session.
    flowTerminalUi.open!()
    await flushPromises()
    expect(api.openTerminalSession).toHaveBeenCalledTimes(1)
    expect(standins()).toHaveLength(1)
    // Another tab: the window stays (hidden), the tab keeps its mark, the button is not lit.
    await wrapper!.setProps({ flowId: 'p.m.0002' })
    await flushPromises()
    expect(standins()[0].attributes('data-visible')).toBe('false')
    expect(flowTerminalUi.openFlows).toEqual({ 'p.m.0001': true })
    expect(flowTerminalUi.currentOpen).toBe(false)
    await wrapper!.setProps({ flowId: 'p.m.0001' })
    expect(standins()[0].attributes('data-visible')).toBe('true')
    expect(flowTerminalUi.currentOpen).toBe(true)
  })

  it('closing the window removes the tab mark; closing the Flow tab closes its terminal', async () => {
    api.readTerminalStatus.mockResolvedValue(status())
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    wrapper!.findComponent({ name: 'FlowTerminalWindow' }).vm.$emit('close')
    await nextTick()
    expect(standins()).toHaveLength(0)
    expect(flowTerminalUi.openFlows).toEqual({})
    flowTerminalUi.open!()
    await flushPromises()
    expect(standins()).toHaveLength(1)
    await wrapper!.setProps({ tabs: [TABS[1]], flowId: 'p.m.0002' })
    await nextTick()
    expect(standins()).toHaveLength(0)
    expect(flowTerminalUi.openFlows).toEqual({})
  })

  it('shows "터미널은 3개까지 …" when the server refuses a fourth session', async () => {
    api.readTerminalStatus.mockResolvedValue(status({ active_sessions: 3 }))
    api.openTerminalSession.mockRejectedValue(conflict('terminal_session_limit'))
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    expect(standins()).toHaveLength(0)
    expect(document.body.textContent).toContain('터미널은 3개까지 열 수 있습니다 — 다른 탭의 터미널을 닫아 주세요')
    expect(flowTerminalUi.openFlows).toEqual({})
  })

  it('says the working folder is missing (not a generic retry) when the server answers terminal_cwd_unavailable', async () => {
    api.readTerminalStatus.mockResolvedValue(status())
    api.openTerminalSession.mockRejectedValue(conflict('terminal_cwd_unavailable'))
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    expect(standins()).toHaveLength(0)
    expect(document.body.textContent).toContain('이 플로우의 작업 폴더가 서버에 없습니다')
    expect(document.body.textContent).not.toContain('다시 시도해 주세요')
  })

  it('[새로 열기] starts a new session for the same Flow', async () => {
    api.readTerminalStatus.mockResolvedValue(status())
    api.openTerminalSession.mockResolvedValueOnce(session('s1')).mockResolvedValueOnce(session('s2'))
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    wrapper!.findComponent({ name: 'FlowTerminalWindow' }).vm.$emit('reopen')
    await flushPromises()
    expect(api.openTerminalSession).toHaveBeenCalledTimes(2)
    expect(standins()).toHaveLength(1)
    expect(wrapper!.findComponent({ name: 'FlowTerminalWindow' }).props('session')).toMatchObject({ session_id: 's2' })
  })

  it('the setting turned off: a status read with enabled=false closes the open terminals (no DELETE) and hides the button', async () => {
    api.readTerminalStatus.mockResolvedValue(status())
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    expect(standins()).toHaveLength(1)
    api.readTerminalStatus.mockResolvedValue(DISABLED)
    await wrapper!.vm.refreshStatus()
    await flushPromises()
    expect(standins()).toHaveLength(0)
    expect(flowTerminalUi.openFlows).toEqual({})
    expect(terminalButtonState(flowTerminalUi.status, flowTerminalUi.currentOpen)).toBe('hidden')
    expect(api.closeTerminalSession).not.toHaveBeenCalled()
  })

  it('a WebSocket closed for terminal_disabled (the window\'s `disabled`) closes that terminal without a notice and reads the status again', async () => {
    api.readTerminalStatus.mockResolvedValue(status())
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    api.readTerminalStatus.mockClear()
    api.readTerminalStatus.mockResolvedValue(DISABLED)
    wrapper!.findComponent({ name: 'FlowTerminalWindow' }).vm.$emit('disabled')
    await flushPromises()
    expect(standins()).toHaveLength(0)
    expect(api.readTerminalStatus).toHaveBeenCalledWith('p.m.0001')
    expect(flowTerminalUi.status).toEqual(DISABLED)
    expect(document.body.textContent).not.toContain('세션이 끝났습니다')
  })

  it('opening refused with terminal_disabled shows no notice (the button hides after the status read)', async () => {
    api.readTerminalStatus.mockResolvedValue(DISABLED)
    api.openTerminalSession.mockRejectedValue(conflict('terminal_disabled'))
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    expect(standins()).toHaveLength(0)
    expect(document.body.textContent).not.toContain('다시 시도해 주세요')
    expect(terminalButtonState(flowTerminalUi.status, false)).toBe('hidden')
  })

  it('clears the shared state on unmount (sign-out / another Project)', async () => {
    api.readTerminalStatus.mockResolvedValue(status())
    api.openTerminalSession.mockResolvedValue(session())
    mountHost()
    flowTerminalUi.open!()
    await flushPromises()
    wrapper!.unmount(); wrapper = null
    expect(flowTerminalUi).toMatchObject({ status: null, openFlows: {}, currentOpen: false, open: null })
  })
})

describe('terminal contract helpers', () => {
  it('maps the POST /sessions refusals', () => {
    expect(openFailureOf(conflict('terminal_session_limit'))).toBe('limit')
    expect(openFailureOf(conflict('terminal_flow_session_exists'))).toBe('exists')
    expect(openFailureOf(conflict('terminal_not_admin'))).toBe('not_admin')
    expect(openFailureOf(conflict('terminal_disabled'))).toBe('disabled')
    expect(openFailureOf(conflict('terminal_cwd_unavailable'))).toBe('no_cwd')
    expect(openFailureOf(new Error('boom'))).toBe('failed')
  })

  it('puts only the one-time ticket in the WebSocket address, ws/wss after the API base', () => {
    expect(terminalSocketUrl('a b', 'http://127.0.0.1:8088/flowgate', 'http://127.0.0.1:3001/app/'))
      .toBe('ws://127.0.0.1:8088/flowgate/api/v1/terminal/ws?ticket=a%20b')
    expect(terminalSocketUrl('t', 'https://fg.example/flowgate/', 'https://fg.example/app/'))
      .toBe('wss://fg.example/flowgate/api/v1/terminal/ws?ticket=t')
    expect(terminalSocketUrl('t', '/flowgate', 'https://fg.example/app/')).toBe('wss://fg.example/flowgate/api/v1/terminal/ws?ticket=t')
  })

  it('reads the server frames and writes the screen frames', () => {
    expect(parseServerMessage('{"type":"output","data":"안녕\\r\\n"}')).toEqual({ type: 'output', data: '안녕\r\n' })
    expect(parseServerMessage('{"type":"busy","value":true}')).toEqual({ type: 'busy', value: true })
    expect(parseServerMessage('{"type":"card_running","value":false}')).toEqual({ type: 'card_running', value: false })
    expect(parseServerMessage('{"type":"exit","reason":"idle_timeout","code":null}')).toEqual({ type: 'exit', reason: 'idle_timeout', code: null })
    expect(parseServerMessage('{"type":"exit","reason":"weird","code":1}')).toEqual({ type: 'exit', reason: 'error', code: 1 })
    expect(parseServerMessage('not json')).toBeNull()
    expect(parseServerMessage('{"type":"busy","value":"yes"}')).toBeNull()
    expect(JSON.parse(inputFrame('ㅎ'))).toEqual({ type: 'input', data: 'ㅎ' })
    expect(JSON.parse(resizeFrame(120.7, 0))).toEqual({ type: 'resize', cols: 120, rows: 1 })
  })
})
