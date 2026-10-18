// flowgate.v02.0060 — item display rules shared by the five parts (0006-D rev2 §3.2, §3.3, §7.3), separators
// survive only between visible items (in a submenu too). The target is frozen at build time, so later changes
// to the caller's state (selection, tabs, card definitions) never reach the snapshot a request carries.
// flowgate.v02.0060 T#1 (Q item 459, replacing §3.2-1 "연결되지 않은 명령이면 숨김"): a mockup item whose command
// the area has not connected stays where the mockup puts it, disabled with its reason — 'planned' (연결 예정, the 2-B
// commands) or 'unimplemented' (미구현). Only an item outside the mockup's 40 (the card's run menu) is hidden when not
// connected. A submenu parent is disabled when its connected children are all disabled by their own condition; a
// child that is only not connected yet does not disable it, so the parent still opens and shows the mockup's
// structure (the Git parent is disabled by the Git state the part gives it).

import {
  COMMANDS,
  PLANNED_COMMANDS,
  type ConnectedCommands,
  type DisabledReason,
  type MenuAction,
  type MenuItem,
  type MenuItemCommand,
  type MenuModel,
  type MenuSeparator,
  type MenuSubmenu,
  type MenuTarget,
  type UnconnectedReason,
} from './types'

/** Inputs are plain records/arrays. Copy before freezing, including selection and card definitions. */
export function snapshot<T>(value: T): T {
  if (Array.isArray(value)) return Object.freeze(value.map(snapshot)) as T
  if (value && typeof value === 'object') {
    return Object.freeze(Object.fromEntries(Object.entries(value).map(([key, child]) => [key, snapshot(child)]))) as T
  }
  return value
}
export const separator = (key: string): MenuSeparator => ({ kind: 'separator', key })
export function action(command: MenuItemCommand, labelKey: string, icon: string, disabled = false, danger = false): MenuAction {
  return { kind: 'action', key: command, command, labelKey, icon, disabled, danger }
}
export function submenu(key: string, labelKey: string, icon: string, children: readonly (MenuAction | MenuSeparator)[]): MenuSubmenu {
  return { kind: 'submenu', key, labelKey, icon, children }
}

const MOCKUP_COMMANDS: ReadonlySet<string> = new Set(COMMANDS)
const PLANNED: ReadonlySet<string> = new Set(PLANNED_COMMANDS)
/** Why a mockup command not connected is disabled (Q 459): 연결 예정 for the 2-B commands, 미구현 for the rest. */
export function unconnectedReason(command: MenuItemCommand): UnconnectedReason | null {
  if (!MOCKUP_COMMANDS.has(command)) return null
  return PLANNED.has(command) ? 'planned' : 'unimplemented'
}
export const isUnconnectedReason = (reason: DisabledReason | undefined): reason is UnconnectedReason =>
  reason === 'planned' || reason === 'unimplemented'

/** The item as shown: connected → as built; a mockup item not connected → disabled with its reason; else hidden. */
function shownAction(item: MenuAction, enabled: ReadonlySet<MenuItemCommand>): MenuAction | null {
  if (enabled.has(item.command)) return item
  const reason = unconnectedReason(item.command)
  return reason ? { ...item, disabled: true, disabledReason: reason } : null
}
/** Drops leading, trailing and repeated separators. */
function tidy<T extends MenuItem>(items: readonly T[]): T[] {
  const result: T[] = []
  for (const item of items) {
    if (item.kind !== 'separator' || (result.length && result[result.length - 1].kind !== 'separator')) result.push(item)
  }
  if (result[result.length - 1]?.kind === 'separator') result.pop()
  return result
}
export function visibleItems(items: readonly MenuItem[], connected: ConnectedCommands = []): MenuItem[] {
  const enabled = new Set(connected)
  const result: MenuItem[] = []
  for (const item of items) {
    if (item.kind === 'separator') result.push(item)
    else if (item.kind === 'action') {
      const shown = shownAction(item, enabled)
      if (shown) result.push(shown)
    } else {
      const children = tidy(item.children.flatMap((child): (MenuAction | MenuSeparator)[] => {
        if (child.kind === 'separator') return [child]
        const shown = shownAction(child, enabled)
        return shown ? [shown] : []
      }))
      const leaves = children.filter((child): child is MenuAction => child.kind === 'action')
      if (!leaves.length) continue
      const blocked = leaves.every((child) => child.disabled && !isUnconnectedReason(child.disabledReason))
      result.push({ ...item, children, disabled: !!item.disabled || blocked })
    }
  }
  return tidy(result)
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
        const selected = item.kind === 'action'
          ? (item.key === key ? item : undefined)
          : item.children.find((child): child is MenuAction => child.kind === 'action' && child.key === key)
        if (selected && !selected.disabled) return snapshot({
          command: selected.command, target: savedTarget,
          ...(selected.definition ? { definition: selected.definition } : {}),
          ...(selected.arrange ? { arrange: selected.arrange } : {}),
        })
      }
      return null
    },
  })
}
