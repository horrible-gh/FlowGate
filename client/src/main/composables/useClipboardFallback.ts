// Manual-copy fallback state (B0001 / group 0221). This deploy is served over plain HTTP on
// a LAN origin, so `navigator.clipboard` does not exist and every clipboard write rides on
// `execCommand('copy')`, which intermittently fails once the click's transient activation has
// lapsed (token round-trip, Alt-Tab blur). When that happens the ONLY 100%-reliable path is
// the user selecting the text and copying it themselves — this module carries the failed text
// to a modal (ClipboardFallbackModal, mounted once in App.vue) that offers exactly that.
//
// Module-level state on purpose: copy failures surface from many components (MainPanel,
// FileTreeNode, MdViewer, dialogs) while the modal is mounted once at the app root.
import { reactive } from 'vue'
import { consumeLastFailedCopyText } from '../utils/clipboard'

const state = reactive({
  visible: false,
  text: '',
})

/**
 * Open the manual-copy fallback modal for a failed clipboard write.
 *
 * Pass the text when the caller still has it; otherwise the text of the last failed copy is
 * pulled from utils/clipboard (set whenever a resolved text failed to land). Returns whether
 * the modal could be opened — `false` means no text is known (e.g. the producer itself
 * failed), so the caller should fall back to a plain failure toast.
 */
export function openClipboardFallback(text?: string | null): boolean {
  const resolved = text ?? consumeLastFailedCopyText()
  if (!resolved) return false
  state.text = resolved
  state.visible = true
  return true
}

export function closeClipboardFallback() {
  state.visible = false
  state.text = ''
}

export function useClipboardFallback() {
  return { state, open: openClipboardFallback, close: closeClipboardFallback }
}
