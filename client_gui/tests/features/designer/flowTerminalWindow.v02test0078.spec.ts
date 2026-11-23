/** flowgate.v02test.0078 (터미널 테스트): the floating terminal window itself — the part the screen card replaced with a
 *  stand-in. xterm.js and the WebSocket are replaced by recording fakes, so this checks what the window does with the
 *  agreed frames: the ticket-only address, the title-bar folder, input / resize frames, Hangul output, the card band,
 *  the close confirmation while a command runs (DELETE once), and "세션이 끝났습니다" + [새로 열기] after an end. */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ closeTerminalSession: vi.fn(async () => {}) }))
const xterm = vi.hoisted(() => ({ terminals: [] as any[], fits: 0 }))

vi.mock('@/features/designer/flowTerminal/api', async (original) => ({
  ...(await original<typeof import('@/features/designer/flowTerminal/api')>()),
  closeTerminalSession: api.closeTerminalSession,
}))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 97
    rows = 31
    written: string[] = []
    focused = 0
    disposed = false
    selection = ''
    dataHandler: ((data: string) => void) | null = null
    resizeHandler: ((size: { cols: number; rows: number }) => void) | null = null
    keyHandler: ((event: KeyboardEvent) => boolean) | null = null
    constructor(public options: Record<string, unknown>) { xterm.terminals.push(this) }
    loadAddon() {}
    open() {}
    attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean) { this.keyHandler = handler }
    onData(handler: (data: string) => void) { this.dataHandler = handler }
    onResize(handler: (size: { cols: number; rows: number }) => void) { this.resizeHandler = handler }
    write(data: string) { this.written.push(data) }
    focus() { this.focused++ }
    dispose() { this.disposed = true }
    hasSelection() { return !!this.selection }
    getSelection() { return this.selection }
    clearSelection() { this.selection = '' }
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() { xterm.fits++ } } }))

class FakeSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static all: FakeSocket[] = []
  readyState = FakeSocket.CONNECTING
  sent: string[] = []
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) { FakeSocket.all.push(this) }
  send(frame: string) { this.sent.push(frame) }
  close() {
    if (this.closed) return
    this.closed = true
    this.readyState = FakeSocket.CLOSED
    this.onclose?.()
  }
  // test side
  open() { this.readyState = FakeSocket.OPEN; this.onopen?.() }
  frame(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }) }
  drop() { this.readyState = FakeSocket.CLOSED; this.onclose?.() }
  frames() { return this.sent.map(raw => JSON.parse(raw)) }
}

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import FlowTerminalWindow from '@/features/designer/flowTerminal/FlowTerminalWindow.vue'

registerAppMessages(i18n)

const HANGUL = '한글'
const SESSION = { session_id: 'ts_1', cwd: '/srv/flowgate/worktrees/p/0078', ticket: 'tk-123', ticket_expires_in: 30 }

let wrapper: VueWrapper<any> | null = null
const q = (test: string) => document.body.querySelector<HTMLElement>(`[data-test="${test}"]`)

async function mountWindow() {
  wrapper = mount(FlowTerminalWindow, {
    props: { flowId: 'p.m.0078', title: '터미널 플로우', session: SESSION, position: 0, visible: true, focused: true, zIndex: 10 },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  await vi.waitFor(() => expect(xterm.terminals).toHaveLength(1))
  await flushPromises()
  const socket = FakeSocket.all.at(-1)!
  return { socket, term: xterm.terminals[0] }
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  xterm.terminals.length = 0
  xterm.fits = 0
  FakeSocket.all.length = 0
  api.closeTerminalSession.mockClear()
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('FlowTerminalWindow — connection and frames', () => {
  it('connects at once with only the one-time ticket in the address and shows the folder in the title bar', async () => {
    const { socket } = await mountWindow()
    expect(FakeSocket.all).toHaveLength(1)
    const url = new URL(socket.url)
    expect(url.protocol).toMatch(/^wss?:$/)
    expect(url.pathname.endsWith('/api/v1/terminal/ws')).toBe(true)
    expect([...url.searchParams.keys()]).toEqual(['ticket'])
    expect(url.searchParams.get('ticket')).toBe('tk-123')
    expect(socket.url).not.toMatch(/token|bearer|authorization/i)
    expect(q('flow-terminal-cwd')!.textContent).toContain(SESSION.cwd)
    expect(q('flow-terminal-connecting')).not.toBeNull()
  })

  it('sends its real size on open and on every resize, and typed text as input frames', async () => {
    const { socket, term } = await mountWindow()
    socket.open()
    await nextTick()
    expect(q('flow-terminal-connecting')).toBeNull()
    expect(socket.frames()).toContainEqual({ type: 'resize', cols: 97, rows: 31 })
    term.resizeHandler({ cols: 120, rows: 40 })
    expect(socket.frames().at(-1)).toEqual({ type: 'resize', cols: 120, rows: 40 })
    term.dataHandler(`echo ${HANGUL}\r`)
    expect(socket.frames().at(-1)).toEqual({ type: 'input', data: `echo ${HANGUL}\r` })
  })

  it('writes output (Hangul included) to the screen and ignores frames outside the contract', async () => {
    const { socket, term } = await mountWindow()
    socket.open()
    socket.frame({ type: 'output', data: `${HANGUL}\r\n` })
    socket.frame({ type: 'nonsense', data: 'x' })
    expect(term.written).toEqual([`${HANGUL}\r\n`])
  })

  it('shows the card band while a card runs in the group and hides it when it stops', async () => {
    const { socket } = await mountWindow()
    socket.open()
    socket.frame({ type: 'card_running', value: true })
    await nextTick()
    expect(q('flow-terminal-card-running')!.textContent).toBe('카드 실행 중 — 같은 폴더를 고치면 충돌할 수 있음')
    socket.frame({ type: 'card_running', value: false })
    await nextTick()
    expect(q('flow-terminal-card-running')).toBeNull()
  })
})

describe('FlowTerminalWindow — close', () => {
  it('closes at once when nothing runs: DELETE once, socket closed, close emitted', async () => {
    const { socket, term } = await mountWindow()
    socket.open()
    q('flow-terminal-close')!.click()
    await flushPromises()
    expect(q('flow-terminal-confirm')).toBeNull()
    expect(api.closeTerminalSession).toHaveBeenCalledTimes(1)
    expect(api.closeTerminalSession).toHaveBeenCalledWith('ts_1')
    expect(socket.closed).toBe(true)
    expect(term.disposed).toBe(true)
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })

  it('asks once while a command runs; cancel keeps the session, confirm ends it', async () => {
    const { socket } = await mountWindow()
    socket.open()
    socket.frame({ type: 'busy', value: true })
    await nextTick()
    q('flow-terminal-close')!.click()
    await nextTick()
    expect(q('flow-terminal-confirm')!.textContent).toContain('실행 중인 명령이 있습니다')
    q('flow-terminal-confirm-cancel')!.click()
    await nextTick()
    expect(q('flow-terminal-confirm')).toBeNull()
    expect(api.closeTerminalSession).not.toHaveBeenCalled()
    expect(socket.closed).toBe(false)
    q('flow-terminal-close')!.click()
    await nextTick()
    q('flow-terminal-confirm-close')!.click()
    await flushPromises()
    expect(api.closeTerminalSession).toHaveBeenCalledTimes(1)
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })

  it('does not ask again once the command finished (busy false)', async () => {
    const { socket } = await mountWindow()
    socket.open()
    socket.frame({ type: 'busy', value: true })
    socket.frame({ type: 'busy', value: false })
    await nextTick()
    q('flow-terminal-close')!.click()
    await flushPromises()
    expect(q('flow-terminal-confirm')).toBeNull()
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })
})

describe('FlowTerminalWindow — ended session', () => {
  it('shows "세션이 끝났습니다" with the reason after an idle timeout, and [새로 열기] asks for a new session', async () => {
    const { socket } = await mountWindow()
    socket.open()
    socket.frame({ type: 'exit', reason: 'idle_timeout', code: null })
    await nextTick()
    const ended = q('flow-terminal-ended')!
    expect(ended.dataset.reason).toBe('idle_timeout')
    expect(ended.textContent).toContain('세션이 끝났습니다')
    expect(ended.textContent).toContain('30분 동안 쓰지 않아 끝났습니다')
    expect(socket.closed).toBe(true)
    q('flow-terminal-reopen')!.click()
    expect(wrapper!.emitted('reopen')).toHaveLength(1)
  })

  it('a dropped connection ends the window too (no reattach), and closing it then sends no DELETE', async () => {
    const { socket } = await mountWindow()
    socket.open()
    socket.frame({ type: 'busy', value: true })
    socket.drop()
    await nextTick()
    expect(q('flow-terminal-ended')!.dataset.reason).toBe('closed')
    expect(q('flow-terminal-reopen')).not.toBeNull()
    q('flow-terminal-close')!.click()          // busy was cleared by the end: no confirmation
    await flushPromises()
    expect(q('flow-terminal-confirm')).toBeNull()
    expect(api.closeTerminalSession).not.toHaveBeenCalled()
    expect(wrapper!.emitted('close')).toHaveLength(1)
  })

  it('shows the exit code of a shell that exited', async () => {
    const { socket } = await mountWindow()
    socket.open()
    socket.frame({ type: 'exit', reason: 'shell_exit', code: 3 })
    await nextTick()
    expect(q('flow-terminal-ended')!.textContent).toContain('셸이 끝났습니다 · 종료 코드 3')
  })
})
