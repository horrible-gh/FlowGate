// flowgate.v02gui.0001 — fits a run result window's height to its content: the window opens at the caller's height (the
// screen less a margin), then — once its body is laid out — takes the content's own height plus the shell's titlebar,
// body padding and borders, capped at the screen less `margin`, and is centred vertically again. A longer content than
// the cap scrolls in the shell's body as before. The content is followed (a late line, fonts, cards laid out) until the
// person moves or resizes the window: from then on its place and size are theirs. Uses only ModelessShell's exposed
// geometry() / setGeometry() (setGeometry takes the height as given, below the shell's resize minimum too). Nothing
// is done where nothing is laid out (a minimized window, a test DOM without layout).
import { onBeforeUnmount, watch, type Ref } from 'vue'

interface Geometry {
  x: number
  y: number
  width: number
  height: number
}

export interface FittableShell {
  geometry(): Geometry
  setGeometry(next: Geometry): void
}

const sameGeometry = (a: Geometry, b: Geometry) => a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height

export function useFitModelessHeight(shell: Ref<FittableShell | null>, content: Ref<HTMLElement | null>, margin: number): void {
  let fitted: Geometry | null = null
  let stopped = false
  let observer: ResizeObserver | null = null

  function fit() {
    const handle = shell.value
    const el = content.value
    if (stopped || !handle || !el || typeof window === 'undefined') return
    const body = el.parentElement
    const panel = el.closest<HTMLElement>('.ui-modeless-panel')
    if (!body || !panel || el.offsetHeight === 0 || panel.offsetHeight === 0) return
    const current = handle.geometry()
    // Moved or resized by the person since the last fit: leave it to them.
    if (fitted && !sameGeometry(current, fitted)) {
      stop()
      return
    }
    const style = window.getComputedStyle(body)
    const padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0)
    // Titlebar and borders: the panel less its body (the body fills the rest of the column).
    // Sub-pixel heights (rects, not the rounded offsetHeight) and a ceil, so a fitted body never shows a 1px scrollbar.
    const chrome = panel.getBoundingClientRect().height - body.getBoundingClientRect().height
    const cap = Math.max(120, window.innerHeight - margin)
    const height = Math.min(cap, Math.ceil(chrome + padding + el.getBoundingClientRect().height))
    const y = Math.max(8, Math.round((window.innerHeight - height) / 2))
    if (height !== current.height || y !== current.y) handle.setGeometry({ ...current, y, height })
    fitted = handle.geometry()
  }

  function stop() {
    stopped = true
    observer?.disconnect()
    observer = null
  }

  watch(
    content,
    (el) => {
      observer?.disconnect()
      observer = null
      if (!el || stopped) return
      fit()
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => fit())
        observer.observe(el)
      }
    },
    { flush: 'post', immediate: true },
  )

  onBeforeUnmount(stop)
}
