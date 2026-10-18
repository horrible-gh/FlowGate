import { afterEach, describe, expect, it, vi } from 'vitest'
import { attachRightButtonGuard, isRelayedPress } from '@/features/designer/contextMenu/gesture'
import { createDesignerMenuHost } from '@/features/designer/contextMenu/host'
import { openContextMenu } from '@/features/designer/contextMenu/entry'
import { canvasNodeTarget } from '@/features/designer/contextMenu/canvasMenu'
import { firstConnected, modelFor, targets } from './contextMenuFixtures.0060'

const cleanups: (() => void)[] = []
afterEach(() => {
  cleanups.splice(0).reverse().forEach((cleanup) => cleanup())
  document.body.innerHTML = ''
})
function listen(target: EventTarget, type: string, listener: EventListener, capture = false) {
  target.addEventListener(type, listener, capture)
  cleanups.push(() => target.removeEventListener(type, listener, capture))
}
// jsdom has no PointerEvent on all supported versions. Retain native event dispatch,
// capture/bubble/default prevention and supply only the absent pointer metadata.
function send(target: EventTarget, type: string, button = 2, buttons = 2, pointerType = 'mouse') {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, button, buttons, clientX: 90, clientY: 80 })
  if (type.startsWith('pointer')) Object.defineProperties(event, {
    pointerType: { value: pointerType }, pointerId: { value: 1 }, isPrimary: { value: true },
  })
  target.dispatchEvent(event)
  return event
}
function setup() {
  const canvas = document.createElement('div')
  const inside = document.createElement('div')
  const outside = document.createElement('div')
  canvas.append(inside); document.body.append(canvas, outside)
  const dispose = attachRightButtonGuard(canvas)
  cleanups.push(dispose)
  return { canvas, inside, outside, dispose }
}
const gesture = ['pointerdown', 'mousedown', 'pointermove', 'mousemove', 'pointerup', 'mouseup']
function drag(target: EventTarget, button: number) {
  return gesture.map((type) => send(target, type, type.endsWith('move') ? -1 : button,
    type.endsWith('up') ? 0 : button === 2 ? 2 : button === 1 ? 4 : 1))
}

describe('independent right-button canvas guard (0060)', () => {
  it.each([false, true].flatMap((mode) => ['canvas', 'node', 'multi', 'handle'].map((kind) => [mode, kind] as const)))(
    'preserves all state in selectMode=%s on %s during right drag and menu open/close', (selectMode, kind) => {
      const { inside } = setup()
      const state = { activeProject: 'p', activeFlow: 'f', activeTab: 'f', expanded: ['group'],
        selectedNodeIds: ['n2', 'n'], selectedNodeId: null, selectedEdgeId: 'e', selectMode,
        palette: 'task', placement: true, viewport: { x: 10, y: 20, zoom: 1.2 },
        positions: [{ id: 'n', x: 10, y: 20 }], density: 'normal', moveStep: 16,
        undo: ['edit'], redo: ['move'], draftQueue: [], layoutQueue: [] }
      const before = JSON.stringify(state)
      const graphHandler = vi.fn(() => { state.viewport.x++; state.selectedNodeIds.reverse(); state.undo.push('unexpected') })
      for (const type of gesture) listen(inside, type, graphHandler)
      const popupPointer = vi.fn()
      const popupMouse = vi.fn()
      listen(document, 'pointerdown', popupPointer)
      listen(document, 'mousedown', popupMouse)
      const host = createDesignerMenuHost()
      const target = kind === 'canvas' ? targets[6] : kind === 'handle' ? targets[9]
        : canvasNodeTarget('f', kind === 'multi' ? 'n' : 'outside', state.selectedNodeIds)
      listen(inside, 'contextmenu', (event) => { openContextMenu(event as MouseEvent, host, modelFor(target, firstConnected)) })
      const events = drag(inside, 2)
      expect(graphHandler).not.toHaveBeenCalled()
      expect(events.every((event) => !event.defaultPrevented)).toBe(true)
      expect(popupPointer).toHaveBeenCalledTimes(1)
      expect(popupMouse).toHaveBeenCalledTimes(1)
      expect(isRelayedPress(popupPointer.mock.calls[0][0])).toBe(true)
      const menuEvent = send(inside, 'contextmenu', 2, 0)
      expect(menuEvent.defaultPrevented).toBe(kind !== 'multi')
      expect(!!host.current.value).toBe(kind !== 'multi')
      host.close()
      expect(JSON.stringify(state)).toBe(before)
    },
  )
  it.each([0, 1])('preserves every left/middle gesture (button=%s)', (button) => {
    const { inside } = setup()
    const received = vi.fn()
    for (const type of gesture) listen(inside, type, received)
    const observer = vi.fn()
    listen(document, 'pointerdown', observer)
    const events = drag(inside, button)
    expect(received.mock.calls.map(([e]) => e.type)).toEqual(gesture)
    expect(events.every((event) => !event.defaultPrevented)).toBe(true)
    expect(observer).toHaveBeenCalledTimes(1)
    expect(isRelayedPress(observer.mock.calls[0][0])).toBe(false)
  })
  it.each(['touch', 'pen'])('preserves %s pointer and compatibility mouse events, including barrel button', (pointerType) => {
    const { inside } = setup()
    const received = vi.fn()
    for (const type of gesture) listen(inside, type, received)
    for (const type of gesture) send(inside, type, type.endsWith('move') ? -1 : 2, type.endsWith('up') ? 0 : 2, pointerType)
    expect(received.mock.calls.map(([event]) => event.type)).toEqual(gesture)
    received.mockClear()
    drag(inside, 2) // a later real mouse still gets guarded
    expect(received).not.toHaveBeenCalled()
  })
  it('leaves wheel, touch, keyboard and unrelated outside presses alone', () => {
    const { inside, outside } = setup()
    const received = vi.fn()
    for (const type of ['wheel', 'touchstart', 'touchmove', 'touchend', 'keydown']) {
      listen(inside, type, received)
      const event = new Event(type, { bubbles: true, cancelable: true })
      inside.dispatchEvent(event)
      expect(event.defaultPrevented).toBe(false)
    }
    listen(outside, 'pointerdown', received)
    send(outside, 'pointerdown')
    expect(received).toHaveBeenCalledTimes(6)
  })
  it.each(['input', 'textarea', 'select', 'editable', 'excluded', 'menu'])(
    'preserves native handling for %s within the guarded region', (kind) => {
      const { inside } = setup()
      const child = document.createElement(['input', 'textarea', 'select'].includes(kind) ? kind : 'div')
      if (kind === 'editable') child.setAttribute('contenteditable', 'true')
      if (kind === 'excluded') child.setAttribute('data-context-menu-exclude', '')
      if (kind === 'menu') child.setAttribute('data-designer-context-menu', '')
      inside.append(child)
      const received = vi.fn()
      for (const type of gesture) listen(child, type, received)
      drag(child, 2)
      expect(received.mock.calls.map(([event]) => event.type)).toEqual(gesture)
    },
  )
  it('continues guarding a held gesture outside the element and releases it after mouseup', () => {
    const { inside, outside } = setup()
    const received = vi.fn()
    for (const type of ['pointermove', 'mousemove', 'pointerup', 'mouseup']) listen(outside, type, received)
    send(inside, 'pointerdown'); send(inside, 'mousedown')
    send(outside, 'pointermove', -1); send(outside, 'mousemove', -1)
    send(outside, 'pointerup', 2, 0); send(outside, 'mouseup', 2, 0)
    expect(received).not.toHaveBeenCalled()
    send(outside, 'pointermove', -1, 0); send(outside, 'mousemove', -1, 0)
    expect(received).toHaveBeenCalledTimes(2)
  })
  it.each(['contextmenu', 'blur', 'lost-buttons', 'cancel'])('releases held state on %s', (reason) => {
    const { inside } = setup()
    send(inside, 'pointerdown'); send(inside, 'mousedown')
    if (reason === 'blur') window.dispatchEvent(new Event('blur'))
    else if (reason === 'lost-buttons') { send(inside, 'pointermove', -1, 0); send(inside, 'mousemove', -1, 0) }
    else if (reason === 'cancel') { send(inside, 'pointercancel', 2, 0); send(inside, 'mouseup', 2, 0) }
    else send(inside, 'contextmenu', 2, 0)
    const received = vi.fn()
    listen(inside, 'pointermove', received); listen(inside, 'mousemove', received)
    send(inside, 'pointermove', -1, 0); send(inside, 'mousemove', -1, 0)
    expect(received).toHaveBeenCalledTimes(2)
  })
  it('does not swallow unrelated button releases during a right-button chord', () => {
    const { inside } = setup()
    const received = vi.fn()
    listen(inside, 'pointerup', received); listen(inside, 'mouseup', received)
    send(inside, 'pointerdown'); send(inside, 'mousedown')
    send(inside, 'pointerup', 0, 2); send(inside, 'mouseup', 0, 2)
    expect(received).toHaveBeenCalledTimes(2)
    send(inside, 'pointerup', 2, 0); send(inside, 'mouseup', 2, 0)
    expect(received).toHaveBeenCalledTimes(2)
  })
  it('removes every handler on dispose, including during a held gesture', () => {
    const { inside, dispose } = setup()
    send(inside, 'pointerdown'); send(inside, 'mousedown')
    dispose(); dispose()
    const received = vi.fn()
    for (const type of gesture) listen(inside, type, received)
    const observer = vi.fn()
    listen(document, 'pointerdown', observer)
    drag(inside, 2)
    expect(received).toHaveBeenCalledTimes(6)
    expect(observer).toHaveBeenCalledTimes(1)
    expect(isRelayedPress(observer.mock.calls[0][0])).toBe(false)
  })
})
