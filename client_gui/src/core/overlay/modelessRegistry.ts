import { computed, reactive, ref } from 'vue'

// What a window is about. The taskbar draws one line icon per kind so icon-only styles (modern, dock) can tell
// windows apart; a window that names no kind gets one from its kind colour, and 'window' when that is unknown too.
export const MODELESS_KINDS = ['document', 'history', 'change', 'note', 'question', 'git', 'merge', 'ai', 'test', 'plan', 'settings', 'project', 'window'] as const
export type ModelessKind = typeof MODELESS_KINDS[number]
const KIND_BY_COLOR: Record<string, ModelessKind> = { '#4ba3ff': 'document', '#8b9bb2': 'history', '#52b788': 'git', '#a78bfa': 'ai', '#f0a23a': 'test', '#e7a244': 'plan', '#94a3b8': 'settings' }

export interface ModelessEntry {
  id: symbol
  title: string
  color: string
  kind?: ModelessKind
  // A short mark (e.g. a document number) that tells apart windows of the same kind on the taskbar.
  badge?: string
  minimized: boolean
  restore: () => void
}
export function modelessKindOf(entry: Pick<ModelessEntry, 'kind' | 'color'>): ModelessKind {
  return entry.kind ?? KIND_BY_COLOR[String(entry.color ?? '').trim().toLowerCase()] ?? 'window'
}
/** The number of a document id (`….0012-CH` → `12`), used as the taskbar mark of its window. */
export function documentNumberBadge(docId: string | null | undefined): string | undefined {
  const match = /(\d+)-[A-Za-z]+$/.exec(docId ?? '')
  return match ? String(Number(match[1])) : undefined
}

// Shell instances own registration, including callers with only local minimized state.
const windows = reactive(new Map<symbol, ModelessEntry>())
const bars = reactive(new Map<symbol, number>())
export const modelessWindows = computed(() => [...windows.values()])
export const modelessTaskbarHeight = computed(() => Math.max(0, ...bars.values()))
export function registerModeless(entry: ModelessEntry): void { windows.set(entry.id, entry) }
export function unregisterModeless(id: symbol): void { windows.delete(id) }
export function setModelessTaskbar(id: symbol, height: number): void { bars.set(id, height) }
export function removeModelessTaskbar(id: symbol): void { bars.delete(id) }

export const TASKBAR_STYLE_FIELD = 'designer_taskbar_style'
export const TASKBAR_STYLES = ['flowgate', 'modern', 'dock', 'classic'] as const
export type TaskbarStyle = typeof TASKBAR_STYLES[number]
export function normalizeTaskbarStyle(value: unknown): TaskbarStyle {
  return TASKBAR_STYLES.includes(value as TaskbarStyle) ? value as TaskbarStyle : 'flowgate'
}
export const modelessStyle = ref<TaskbarStyle>('flowgate')

// Fixed item widths make overflow independent of title length and language.
// Modern shows every item as a 44px icon tile (menu, designer, separator take about 120px).
// Dock uses the same 44px tiles inside a floating group whose padding and wider separator add about 40px.
// Classic shows names: the menu button and the designer item take about 250px, each window 180px plus a gap.
// Pinned buttons are icon-only in every style (pin = one button plus its gap); with pins a separator follows them.
// Below TASKBAR_COMPACT_WIDTH the [Menu] shows its icon only and the designer item is hidden (compact fixed width).
export const TASKBAR_COMPACT_WIDTH = 640
const CAPACITY: Record<TaskbarStyle, [fixed: number, item: number, more: number, pin: number, compact: number]> = { flowgate: [260, 226, 80, 46, 90], modern: [120, 48, 52, 48, 80], dock: [160, 48, 52, 48, 110], classic: [250, 186, 80, 42, 90] }
export function taskbarCapacity(width: number, count: number, style: TaskbarStyle = 'flowgate', pins = 0): number {
  const [fixed, item, more, pin, compact] = CAPACITY[style] ?? CAPACITY.flowgate
  const room = Math.max(0, width - (width < TASKBAR_COMPACT_WIDTH ? compact : fixed) - (pins > 0 ? pins * pin + 16 : 0))
  if (count * item <= room) return count
  return Math.max(0, Math.floor((room - more) / item))
}
