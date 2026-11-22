// State of the chat quick message row (flowgate.v02test.0072 card 2): the project's buttons, which ones are on, and
// the text a send carries. The composer owns one instance; the row component only renders it.
//
// - 'sticky' (계속) buttons stay on until pressed again. Their on-state lives in the browser (localStorage), per user
//   and project — never on the server — so it survives F5, and a failed load does not clear it.
// - 'once' (1회) buttons are on for the next send only; the composer calls `consume` after the server accepted it.
// - A failed load (403 without project.settings.read included) hides the row: nothing is appended then.

import { computed, ref, shallowRef } from 'vue'
import { fetchChatQuickMessages, type QuickMessage } from './api'

export type QuickRowStatus = 'idle' | 'loading' | 'ready' | 'hidden'

export const stickyStorageKey = (userId: string, projectId: string) =>
  `flowgate.user.${userId}.chat.quick-messages.${encodeURIComponent(projectId)}.sticky`

export function loadStickyIds(userId: string, projectId: string | null | undefined): number[] {
  if (!projectId) return []
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(stickyStorageKey(userId, projectId)) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter((id): id is number => typeof id === 'number' && Number.isInteger(id)) : []
  } catch {
    return []
  }
}

export function saveStickyIds(userId: string, projectId: string, ids: Iterable<number>): void {
  const list = [...new Set(ids)]
  try {
    if (list.length) localStorage.setItem(stickyStorageKey(userId, projectId), JSON.stringify(list))
    else localStorage.removeItem(stickyStorageKey(userId, projectId))
  } catch { /* best effort: storage failures never block the row */ }
}

/** The outgoing text: the typed message, then each quick message on its own line (empty parts are left out). */
export function buildOutgoingText(text: string, phrases: readonly string[]): string {
  return [text, ...phrases].map((part) => part.trim()).filter(Boolean).join('\n')
}

export interface QuickMessagesOptions {
  projectId: () => string | null | undefined
  userId: () => string
  fetch?: (projectId: string) => Promise<QuickMessage[]>
}

export function useQuickMessages(options: QuickMessagesOptions) {
  const fetchList = options.fetch ?? fetchChatQuickMessages
  const items = shallowRef<readonly QuickMessage[]>([])
  const status = ref<QuickRowStatus>('idle')
  const stickyOn = shallowRef<ReadonlySet<number>>(new Set())
  const onceOn = shallowRef<ReadonlySet<number>>(new Set())
  let scope: { userId: string; projectId: string } | null = null
  let generation = 0

  const visible = computed(() => status.value === 'ready')

  function isOn(item: QuickMessage): boolean {
    return item.kind === 'sticky' ? stickyOn.value.has(item.id) : onceOn.value.has(item.id)
  }

  /** On buttons in row order; empty while the row is hidden (nothing is appended then). */
  const active = computed<QuickMessage[]>(() => (visible.value ? items.value.filter(isOn) : []))

  async function load(): Promise<void> {
    const gen = ++generation
    const projectId = options.projectId() || null
    const userId = options.userId()
    scope = projectId ? { userId, projectId } : null
    onceOn.value = new Set()
    stickyOn.value = new Set(loadStickyIds(userId, projectId))
    if (!projectId) {
      items.value = []
      status.value = 'hidden'
      return
    }
    status.value = 'loading'
    try {
      const list = await fetchList(projectId)
      if (gen !== generation) return
      items.value = list
      status.value = 'ready'
    } catch {
      if (gen !== generation) return
      items.value = []
      status.value = 'hidden'
    }
  }

  function toggle(id: number): void {
    if (!visible.value) return
    const item = items.value.find((candidate) => candidate.id === id)
    if (!item) return
    if (item.kind === 'sticky') {
      const next = new Set(stickyOn.value)
      if (!next.delete(id)) next.add(id)
      stickyOn.value = next
      if (scope) saveStickyIds(scope.userId, scope.projectId, next)
    } else {
      const next = new Set(onceOn.value)
      if (!next.delete(id)) next.add(id)
      onceOn.value = next
    }
  }

  /** Alt+1..9: toggles the button at `index` (0-based); false when there is none. */
  function toggleAt(index: number): boolean {
    const item = visible.value ? items.value[index] : undefined
    if (!item) return false
    toggle(item.id)
    return true
  }

  /** After an accepted send: the once buttons that went with it turn off (sticky ones stay on). */
  function consume(sent: readonly QuickMessage[]): void {
    const ids = sent.filter((item) => item.kind === 'once').map((item) => item.id)
    if (!ids.length) return
    const next = new Set(onceOn.value)
    for (const id of ids) next.delete(id)
    onceOn.value = next
  }

  return { items, status, visible, active, isOn, load, toggle, toggleAt, consume }
}

export type QuickMessagesState = ReturnType<typeof useQuickMessages>
