// flowgate.v02test.0096 ④: place, size and last tab of the ⚙ settings window that floats near the composer.
//
// The window is modeless: it stays open while the person keeps reading and typing in the chat, moves by its title bar,
// resizes by its corner and is always pulled back fully inside the viewport (on open, while dragging / resizing and
// when the browser window shrinks). The last place, size and tab are kept in this browser's localStorage, so the
// window opens again where it was left. Nothing here talks to the server.
import { onBeforeUnmount, reactive } from 'vue'

export const SETTINGS_WINDOW_KEY = 'fg.composer_settings_window.v1'

export interface WindowRect {
  x: number
  y: number
  width: number
  height: number
}

export interface StoredWindow {
  rect: WindowRect | null
  tab: string | null
}

export interface FloatingWindowOptions {
  storageKey?: string
  width: number
  height: number
  minWidth: number
  minHeight: number
  /** Gap kept between the window and the viewport edge / the anchor. */
  margin?: number
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** A stored value as rect + tab; anything unreadable reads as nothing stored. */
export function parseStoredWindow(raw: string | null): StoredWindow {
  if (!raw) return { rect: null, tab: null }
  try {
    const data = JSON.parse(raw) as Record<string, unknown> | null
    if (!data || typeof data !== 'object') return { rect: null, tab: null }
    const r = data.rect as Record<string, unknown> | null | undefined
    const rect = r && finite(r.x) && finite(r.y) && finite(r.width) && finite(r.height) && r.width > 0 && r.height > 0
      ? { x: r.x, y: r.y, width: r.width, height: r.height }
      : null
    return { rect, tab: typeof data.tab === 'string' && data.tab ? data.tab : null }
  } catch {
    return { rect: null, tab: null }
  }
}

function viewport(): { width: number; height: number } {
  if (typeof window === 'undefined') return { width: 1024, height: 768 }
  return { width: window.innerWidth || 1024, height: window.innerHeight || 768 }
}

/**
 * The rect pulled fully inside a viewport: never smaller than the minimum (unless the viewport itself is smaller),
 * never larger than the viewport less the margins, and placed so no edge leaves the screen.
 */
export function clampRect(rect: WindowRect, view: { width: number; height: number }, min: { width: number; height: number }, margin = 4): WindowRect {
  const maxWidth = Math.max(1, view.width - margin * 2)
  const maxHeight = Math.max(1, view.height - margin * 2)
  const width = Math.min(Math.max(rect.width, Math.min(min.width, maxWidth)), maxWidth)
  const height = Math.min(Math.max(rect.height, Math.min(min.height, maxHeight)), maxHeight)
  const x = Math.min(Math.max(rect.x, margin), Math.max(margin, view.width - width - margin))
  const y = Math.min(Math.max(rect.y, margin), Math.max(margin, view.height - height - margin))
  return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) }
}

/** First place: right edges lined up with the anchor (the ⚙ button), just above it — or below when there is no room. */
export function placeNear(anchor: { left: number; right: number; top: number; bottom: number } | null, size: { width: number; height: number }, view: { width: number; height: number }, margin = 4): WindowRect {
  if (!anchor || (anchor.right === 0 && anchor.bottom === 0)) {
    return { x: view.width - size.width - 16, y: view.height - size.height - 16, width: size.width, height: size.height }
  }
  const above = anchor.top - size.height - 6
  const y = above >= margin ? above : anchor.bottom + 6
  return { x: anchor.right - size.width, y, width: size.width, height: size.height }
}

export function useFloatingWindow(options: FloatingWindowOptions) {
  const key = options.storageKey ?? SETTINGS_WINDOW_KEY
  const margin = options.margin ?? 4
  const min = { width: options.minWidth, height: options.minHeight }
  const rect = reactive<WindowRect>({ x: 0, y: 0, width: options.width, height: options.height })

  function load(): StoredWindow {
    try {
      return parseStoredWindow(typeof localStorage === 'undefined' ? null : localStorage.getItem(key))
    } catch {
      return { rect: null, tab: null }
    }
  }

  let tab: string | null = load().tab

  function persist(): void {
    try {
      localStorage.setItem(key, JSON.stringify({ rect: { ...rect }, tab }))
    } catch {
      // Best effort: the window still works for this page.
    }
  }

  function apply(next: WindowRect): void {
    Object.assign(rect, clampRect(next, viewport(), min, margin))
  }

  /** On open: the remembered rect, or a first place near the anchor; both pulled inside the viewport. */
  function openAt(anchor: { left: number; right: number; top: number; bottom: number } | null): void {
    const stored = load()
    tab = stored.tab ?? tab
    apply(stored.rect ?? placeNear(anchor, { width: options.width, height: options.height }, viewport(), margin))
    if (typeof window !== 'undefined') window.addEventListener('resize', onViewportResize)
  }

  function onClose(): void {
    stopPointer()
    if (typeof window !== 'undefined') window.removeEventListener('resize', onViewportResize)
  }

  function onViewportResize(): void {
    apply({ ...rect })
  }

  function storedTab(): string | null {
    return tab
  }

  function rememberTab(value: string): void {
    tab = value
    persist()
  }

  // ── drag (title bar) and resize (corner) ─────────────────────────────────────────────
  let mode: 'move' | 'resize' | null = null
  let startX = 0
  let startY = 0
  let origin: WindowRect = { ...rect }

  function begin(event: PointerEvent, next: 'move' | 'resize'): void {
    if (event.button !== undefined && event.button !== 0) return
    mode = next
    startX = event.clientX
    startY = event.clientY
    origin = { ...rect }
    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', onPointerUp)
    document.addEventListener('pointercancel', onPointerUp)
    event.preventDefault()
  }

  /** Title bar press: buttons inside the title bar (×) keep their own click. */
  function startMove(event: PointerEvent): void {
    const target = event.target as HTMLElement | null
    if (target?.closest?.('button, input, select, a')) return
    begin(event, 'move')
  }

  function startResize(event: PointerEvent): void {
    begin(event, 'resize')
  }

  function onPointerMove(event: PointerEvent): void {
    if (!mode) return
    const dx = event.clientX - startX
    const dy = event.clientY - startY
    if (mode === 'move') apply({ ...origin, x: origin.x + dx, y: origin.y + dy })
    else apply({ ...origin, width: Math.max(min.width, origin.width + dx), height: Math.max(min.height, origin.height + dy) })
  }

  function onPointerUp(): void {
    const was = mode
    stopPointer()
    if (was) persist()
  }

  function stopPointer(): void {
    mode = null
    document.removeEventListener('pointermove', onPointerMove)
    document.removeEventListener('pointerup', onPointerUp)
    document.removeEventListener('pointercancel', onPointerUp)
  }

  onBeforeUnmount(onClose)

  return { rect, openAt, onClose, startMove, startResize, storedTab, rememberTab, persist }
}
