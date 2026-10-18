// flowgate.v02.0060 — right-click entry rule (0006-D rev2 §3.1 (나)): input / textarea / select /
// editable areas keep the native menu; a right click inside the open menu is ignored; the innermost
// handler claims the event so an outer area does not handle it again; the menu opens — and the
// signal is consumed once — only when at least one item is visible (disabled items count).
// flowgate.v02.0060 0017-T: blocking the browser's own menu is a separate rule (the mockup v2 contextmenu rule):
// inside the Designer a right click never shows it — a target opens the Designer menu, a place without one (or a
// target with no item, or one excluded from the menu) shows nothing — and only editable areas keep it.

import type { DesignerMenuHost } from './host'
import type { MenuModel } from './types'

const claimed = new WeakSet<Event>()
export function isEditableTarget(target: EventTarget | null): boolean {
  const element = target instanceof Element ? target : target instanceof Node ? target.parentElement : null
  if (!element) return false
  if (element.closest('input, textarea, select')) return true
  const editable = element.closest('[contenteditable]')
  return editable !== null && editable.getAttribute('contenteditable')?.toLowerCase() !== 'false'
}
/** Call from the innermost resolved target's bubbling contextmenu handler.
 * Claim even an empty model: an outer canvas must never replace a hidden multi menu.
 */
export function openContextMenu(event: MouseEvent, host: DesignerMenuHost, model: MenuModel | null, fallback?: HTMLElement): boolean {
  if (claimed.has(event) || event.defaultPrevented) return false
  if (isEditableTarget(event.target)) return false
  const element = event.target instanceof Element ? event.target : null
  if (element?.closest('[data-designer-context-menu]')) {
    event.preventDefault()
    event.stopPropagation()
    return false
  }
  claimed.add(event)
  if (!model || !model.items.some((item) => item.kind !== 'separator')) { host.close(); return false }
  const generation = host.open(model, { x: event.clientX, y: event.clientY }, fallback)
  if (generation === null) return false
  event.preventDefault()
  event.stopPropagation()
  return true
}

/** The block itself: whatever the areas did, the browser's menu stays shut unless the target is editable. */
export function blockNativeContextMenu(event: MouseEvent): void {
  if (!event.defaultPrevented && !isEditableTarget(event.target)) event.preventDefault()
}
/** Installs the block on the document in the bubble phase, after the areas' own handlers and the entry rule. */
export function attachNativeMenuBlock(doc: Document): () => void {
  const block = (event: Event) => blockNativeContextMenu(event as MouseEvent)
  doc.addEventListener('contextmenu', block)
  return () => doc.removeEventListener('contextmenu', block)
}

