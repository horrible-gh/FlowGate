// flowgate.v02.0060 — item display rules shared by the five parts (0006-D rev2 §3.2, §3.3, §7.3):
// an item whose command is not in the connected list is hidden, a submenu without a visible child
// is hidden (all-disabled children → disabled parent), separators survive only between visible
// items. The target is frozen at build time, so later changes to the caller's state (selection,
// tabs, card definitions) never reach the snapshot a request carries.

import type { ConnectedCommands, MenuAction, MenuCommand, MenuItem, MenuModel, MenuTarget, MenuSubmenu } from './types'

/** Inputs are plain records/arrays. Copy before freezing, including selection and card definitions. */
export function snapshot<T>(value: T): T {
  if (Array.isArray(value)) return Object.freeze(value.map(snapshot)) as T
  if (value && typeof value === 'object') {
    return Object.freeze(Object.fromEntries(Object.entries(value).map(([key, child]) => [key, snapshot(child)]))) as T
  }
  return value
}
export const separator = (key: string): MenuItem => ({ kind: 'separator', key })
export function action(command: MenuCommand, labelKey: string, icon: string, disabled = false, danger = false): MenuAction {
  return { kind: 'action', key: command, command, labelKey, icon, disabled, danger }
}
export function submenu(key: string, labelKey: string, icon: string, children: readonly MenuAction[]): MenuSubmenu {
  return { kind: 'submenu', key, labelKey, icon, children }
}
export function visibleItems(items: readonly MenuItem[], connected: ConnectedCommands = []): MenuItem[] {
  const enabled = new Set(connected)
  const result: MenuItem[] = []
  for (const item of items) {
    if (item.kind === 'separator') {
      if (result.length && result[result.length - 1].kind !== 'separator') result.push(item)
    } else if (item.kind === 'action') {
      if (enabled.has(item.command)) result.push(item)
    } else {
      const children = item.children.filter((child) => enabled.has(child.command))
      if (children.length) result.push({ ...item, children, disabled: !!item.disabled || children.every((child) => child.disabled) })
    }
  }
  if (result[result.length - 1]?.kind === 'separator') result.pop()
  return result
}
export function makeMenu<T extends MenuTarget>(target: T, items: readonly MenuItem[], connected?: ConnectedCommands): MenuModel<T> {
  const savedTarget = snapshot(target)
  const savedItems = snapshot(visibleItems(items, connected))
  return Object.freeze({
    target: savedTarget,
    items: savedItems,
    request(key: string) {
      for (const item of savedItems) {
        if (item.kind === 'separator' || item.disabled) continue
        const selected = item.kind === 'action' ? (item.key === key ? item : undefined) : item.children.find((child) => child.key === key)
        if (selected && !selected.disabled) return snapshot({
          command: selected.command, target: savedTarget,
          ...(selected.definition ? { definition: selected.definition } : {}),
        })
      }
      return null
    },
  })
}

