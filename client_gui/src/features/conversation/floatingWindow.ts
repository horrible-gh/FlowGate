// flowgate.v02test.0096 ④: place, size and last tab of the ⚙ settings window that floats near the composer.
//
// The window is modeless: it stays open while the person keeps reading and typing in the chat, moves by its title bar,
// resizes by its corner and is always pulled back fully inside the viewport (on open, while dragging / resizing and
// when the browser window shrinks). The last place, size and tab are kept in this browser's localStorage, so the
// window opens again where it was left. Nothing here talks to the server.
//
// flowgate.v02test.0096 F96-8: the height follows the content by default (the window is as tall as the open tab, capped
// at the viewport, where the body scrolls). Only a size the person set with the corner grip is kept as a fixed height
// (`sized`); a height stored before this (the old fixed 400) is ignored, so a short tab never leaves an empty bottom.
// Double-clicking the grip goes back to the content height.
import { onBeforeUnmount, reactive, ref } from 'vue'

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
  /** True only when the person resized the window by hand; otherwise the stored height is not used. */
  sized: boolean
}

export interface FloatingWindowOptions {
  storageKey?: string
  width: number
  /** The height used to place the window before its content has been measured. */
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
  if (!raw) return { rect: null, tab: null, sized: false }
  try {
    const data = JSON.parse(raw) as Record<string, unknown> | null
    if (!data || typeof data !== 'object') return { rect: null, tab: null, sized: false }
    const r = data.rect as Record<string, unknown> | null | undefined
    const rect = r && finite(r.x) && finite(r.y) && finite(r.width) && finite(r.height) && r.width > 0 && r.height > 0
      ? { x: r.x, y: r.y, width: r.width, height: r.height }
      : null
    return { rect, tab: typeof data.tab === 'string' && data.tab ? data.tab : null, sized: !!rect && data.sized === true }
  } catch {
    return { rect: null, tab: null, sized: false }
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
  const rect = reactive<WindowRect>({ x: 0, y: 0, width: options.width, height: options.height })
  /** A height the person chose with the grip; false = the height follows the content. */
  const sized = ref(false)
  /** The tallest the window may be (viewport less the margins); the content scrolls past it. */
  const maxHeight = ref(Math.max(1, viewport().height - margin * 2))
  // Set while the first place above the ⚙ button still uses the estimated height.
  let firstAnchor: { left: number; right: number; top: number; bottom: number } | null = null

  function load(): StoredWindow {
    try {
      return parseStoredWindow(typeof localStorage === 'undefined' ? null : localStorage.getItem(key))
    } catch {
      return { rect: null, tab: null, sized: false }
    }
  }

  let tab: string | null = load().tab

  function persist(): void {
    try {
      localStorage.setItem(key, JSON.stringify({ rect: { ...rect }, tab, sized: sized.value }))
    } catch {
      // Best effort: the window still works for this page.
    }
  }

  function apply(next: WindowRect): void {
    const view = viewport()
    maxHeight.value = Math.max(1, view.height - margin * 2)
    // A content-height window has no minimum height of its own: its measured height is what it is.
    Object.assign(rect, clampRect(next, view, { width: options.minWidth, height: sized.value ? options.minHeight : 0 }, margin))
  }

  /** On open: the remembered place (and size, if set by hand), or a first place near the anchor; pulled inside the viewport. */
  function openAt(anchor: { left: number; right: number; top: number; bottom: number } | null): void {
    const stored = load()
    tab = stored.tab ?? tab
    sized.value = stored.sized
    firstAnchor = stored.rect ? null : anchor
    const estimate = { width: options.width, height: options.height }
    apply(stored.rect
      ? { ...stored.rect, height: stored.sized ? stored.rect.height : Math.min(rect.height, estimate.height) }
      : placeNear(anchor, estimate, viewport(), margin))
    if (typeof window !== 'undefined') window.addEventListener('resize', onViewportResize)
  }

  /**
   * The window's rendered height (content height while not sized by hand). The first place above the ⚙ button is
   * redone with the real height; afterwards a taller tab only pulls the window back inside the screen.
   */
  function measure(height: number): void {
    if (sized.value || !(height > 0) || mode === 'resize') return
    if (firstAnchor) {
      const anchor = firstAnchor
      firstAnchor = null
      apply(placeNear(anchor, { width: rect.width, height }, viewport(), margin))
      return
    }
    if (height !== rect.height) apply({ ...rect, height })
  }

  /** Back to the content height (double-click on the grip). */
  function resetSize(): void {
    if (!sized.value) return
    sized.value = false
    persist()
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
    else {
      // The first drag of the grip fixes the height: from here on the window keeps the size the person gives it.
      sized.value = true
      firstAnchor = null
      apply({ ...origin, width: Math.max(options.minWidth, origin.width + dx), height: Math.max(options.minHeight, origin.height + dy) })
    }
  }

  function onPointerUp(): void {
    const was = mode
    stopPointer()
    if (was) {
      if (was === 'move') firstAnchor = null
      persist()
    }
  }

  function stopPointer(): void {
    mode = null
    document.removeEventListener('pointermove', onPointerMove)
    document.removeEventListener('pointerup', onPointerUp)
    document.removeEventListener('pointercancel', onPointerUp)
  }

  onBeforeUnmount(onClose)

  return { rect, sized, maxHeight, openAt, onClose, measure, resetSize, startMove, startResize, storedTab, rememberTab, persist }
}
