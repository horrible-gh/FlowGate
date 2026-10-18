// flowgate.v02.0060 — the single menu owner of the Designer (0006-D rev2 §2.1 메뉴 호스트, §3.4, §7.2):
// current snapshot + items, position, open generation and the focus to return to. Opening again
// replaces the menu (one menu only); a selection or invalidation carrying an older generation is
// ignored so a stale target can never run. Reuses the core overlay z-index / focus-return helpers.

import { readonly, shallowRef } from 'vue'
import { allocateOverlayZIndex } from '../../../core/overlay/zIndex'
import { captureFocusedElement, returnFocusTo } from '../../../core/overlay/focusReturn'
import { makeMenu, snapshot } from './model'
import { COMMANDS, type MenuModel, type Point } from './types'

export interface MenuSession {
  readonly generation: number
  readonly model: MenuModel
  readonly position: Point
  readonly zIndex: number
  readonly focusOwner?: string
}
/** One controller per Designer. Connectors invalidate the captured generation on state changes. */
export function createDesignerMenuHost() {
  const current = shallowRef<MenuSession | null>(null)
  let generation = 0
  let trigger: HTMLElement | null = null
  let fallback: HTMLElement | null = null

  function close(expected = current.value?.generation): boolean {
    if (!current.value || expected !== current.value.generation) return false
    current.value = null
    returnFocusTo(trigger?.isConnected ? trigger : fallback)
    trigger = fallback = null
    return true
  }
  return {
    current: readonly(current),
    open(model: MenuModel, position: Point, fallbackFocus: HTMLElement | null = null): number | null {
      const saved = makeMenu(model.target, model.items, COMMANDS)
      if (!saved.items.some((item) => item.kind !== 'separator')) { close(); return null }
      // A replacement must not capture the old menu button as its return target.
      const focused = captureFocusedElement()
      if (!current.value || !focused?.closest('[data-designer-context-menu]')) trigger = focused
      fallback = fallbackFocus
      const focusOwner = trigger?.closest<HTMLElement>('[data-modal-focus-scope]')?.dataset.modalFocusScope
      const next = ++generation
      current.value = Object.freeze({ generation: next, model: saved, position: snapshot(position), zIndex: allocateOverlayZIndex(), focusOwner })
      return next
    },
    close,
    invalidate: close,
    select(key: string, expected: number) {
      if (!current.value || current.value.generation !== expected) return null
      const request = current.value.model.request(key)
      if (!request) return null
      close(expected)
      return request
    },
  }
}
export type DesignerMenuHost = ReturnType<typeof createDesignerMenuHost>

