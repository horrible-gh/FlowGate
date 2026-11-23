import { computed, reactive, ref } from 'vue'

export interface ModelessEntry {
  id: symbol
  title: string
  color: string
  minimized: boolean
  restore: () => void
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
export const TASKBAR_STYLES = ['flowgate', 'modern'] as const
export type TaskbarStyle = typeof TASKBAR_STYLES[number]
export function normalizeTaskbarStyle(value: unknown): TaskbarStyle {
  return TASKBAR_STYLES.includes(value as TaskbarStyle) ? value as TaskbarStyle : 'flowgate'
}
export const modelessStyle = ref<TaskbarStyle>('flowgate')

// Fixed item widths make overflow independent of title length and language.
// Modern shows every item as a 44px icon tile (menu, designer, separator take about 120px).
export function taskbarCapacity(width: number, count: number, style: TaskbarStyle = 'flowgate'): number {
  const [fixed, item, more] = style === 'modern' ? [120, 48, 52] : [260, 226, 80]
  const room = Math.max(0, width - fixed)
  if (count * item <= room) return count
  return Math.max(0, Math.floor((room - more) / item))
}
