// State of the chat quick message row (flowgate.v02test.0072 card 2): the project's buttons, which ones are on, and
// the text a send carries. The composer owns one instance; the row component only renders it.
//
// - 'sticky' (계속) buttons stay on until pressed again. Their on-state lives in the browser (localStorage), per user
//   and project — never on the server — so it survives F5, and a failed load does not clear it.
// - 'once' (1회) buttons are on for the next send only; the composer calls `consume` after the server accepted it.
// - A failed load (403 without project.settings.read included) hides the row: nothing is appended then.
// - Card 3: with project.settings.edit the same row adds / edits / deletes / reorders its buttons. While editing, the
//   buttons do not turn on or off. A deleted sticky button is also dropped from the stored on-state; a reorder that
//   cannot be saved goes back to the previous order — on the server too. Only one reorder is saved at a time: a move
//   made while the previous one is still being saved is ignored ('busy'), so the server always ends with the last order shown.

import { computed, ref, shallowRef } from 'vue'
import {
  createChatQuickMessage,
  deleteChatQuickMessage,
  fetchCanEditQuickMessages,
  fetchChatQuickMessages,
  restoreChatQuickMessageOrder,
  saveChatQuickMessageOrder,
  updateChatQuickMessage,
  type QuickMessage,
  type QuickMessageInput,
} from './api'

export type QuickRowStatus = 'idle' | 'loading' | 'ready' | 'hidden'

/**
 * What a move came to:
 * - 'moved'      the new order is saved (or the button was already there: nothing to save)
 * - 'busy'       the previous order is still being saved; this move was ignored
 * - 'restored'   saving failed and the previous order is back, on the server as well as in the row
 * - 'unrestored' saving failed and the previous order could not be written back either; the row shows the server's order
 * - 'denied'     no edit permission or no such button
 */
export type QuickMoveResult = 'moved' | 'busy' | 'restored' | 'unrestored' | 'denied'

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
  /** Whether the user may manage the row (project.settings.edit). */
  canEdit?: (projectId: string) => Promise<boolean>
  create?: (projectId: string, input: QuickMessageInput, sortOrder: number) => Promise<QuickMessage>
  update?: (projectId: string, id: number, input: QuickMessageInput) => Promise<QuickMessage>
  remove?: (projectId: string, id: number) => Promise<void>
  saveOrder?: (projectId: string, before: readonly QuickMessage[], order: readonly number[]) => Promise<void>
  /** Writes the old sort_order of every row in `before` back (after a failed saveOrder). */
  restoreOrder?: (projectId: string, before: readonly QuickMessage[]) => Promise<void>
}

const byRow = (a: QuickMessage, b: QuickMessage) => a.sortOrder - b.sortOrder || a.id - b.id

export function useQuickMessages(options: QuickMessagesOptions) {
  const fetchList = options.fetch ?? fetchChatQuickMessages
  const fetchCanEdit = options.canEdit ?? fetchCanEditQuickMessages
  const apiCreate = options.create ?? createChatQuickMessage
  const apiUpdate = options.update ?? updateChatQuickMessage
  const apiRemove = options.remove ?? deleteChatQuickMessage
  const apiSaveOrder = options.saveOrder ?? saveChatQuickMessageOrder
  const apiRestoreOrder = options.restoreOrder ?? restoreChatQuickMessageOrder
  const items = shallowRef<readonly QuickMessage[]>([])
  const status = ref<QuickRowStatus>('idle')
  const canEditRow = ref(false)
  const editing = ref(false)
  /** True while a reorder is being saved (or undone): further moves wait for it to finish by being ignored. */
  const ordering = ref(false)
  const stickyOn = shallowRef<ReadonlySet<number>>(new Set())
  const onceOn = shallowRef<ReadonlySet<number>>(new Set())
  let scope: { userId: string; projectId: string } | null = null
  let generation = 0

  const visible = computed(() => status.value === 'ready')
  /** ＋ 추가 / ✎ 편집 / right-click edit are shown only when the row is shown and the user may edit. */
  const canEdit = computed(() => visible.value && canEditRow.value)

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
    canEditRow.value = false
    editing.value = false
    if (!projectId) {
      items.value = []
      status.value = 'hidden'
      return
    }
    status.value = 'loading'
    const editAnswer = Promise.resolve().then(() => fetchCanEdit(projectId)).catch(() => false)
    try {
      const list = await fetchList(projectId)
      if (gen !== generation) return
      items.value = list
      // A sticky button whose row was deleted elsewhere is not kept on in the browser either.
      const known = new Set(list.map((item) => item.id))
      const kept = [...stickyOn.value].filter((id) => known.has(id))
      if (kept.length !== stickyOn.value.size) {
        stickyOn.value = new Set(kept)
        saveStickyIds(userId, projectId, kept)
      }
      status.value = 'ready'
    } catch {
      if (gen !== generation) return
      items.value = []
      status.value = 'hidden'
      return
    }
    const mayEdit = await editAnswer
    if (gen === generation) canEditRow.value = mayEdit === true
  }

  function toggle(id: number): void {
    if (!visible.value || editing.value) return
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

  /** ✎ 편집 / ✓ 완료. Only with edit permission. */
  function setEditing(value: boolean): void {
    editing.value = value && canEdit.value
  }

  /** The row's scope while a change is under way; null when nothing is shown or the project changed meanwhile. */
  function begin(): { projectId: string; gen: number } | null {
    if (!canEdit.value || !scope) return null
    return { projectId: scope.projectId, gen: generation }
  }

  /** ＋ 추가: the new button goes to the end of the row. Resolves true when it was saved. */
  async function create(input: QuickMessageInput): Promise<boolean> {
    const ctx = begin()
    if (!ctx) return false
    try {
      const sortOrder = items.value.reduce((max, item) => Math.max(max, item.sortOrder), 0) + 1
      const saved = await apiCreate(ctx.projectId, input, sortOrder)
      if (ctx.gen === generation) items.value = [...items.value, saved].sort(byRow)
      return true
    } catch {
      return false
    }
  }

  /** Changes a button's title / text / kind. Its on-state is dropped when the kind changed (it has a new kind of state). */
  async function update(id: number, input: QuickMessageInput): Promise<boolean> {
    const ctx = begin()
    if (!ctx) return false
    try {
      const saved = await apiUpdate(ctx.projectId, id, input)
      if (ctx.gen !== generation) return true
      const old = items.value.find((item) => item.id === id)
      items.value = items.value.map((item) => (item.id === id ? saved : item)).sort(byRow)
      if (old && old.kind !== saved.kind) dropOnState(id)
      return true
    } catch {
      return false
    }
  }

  /** Deletes a button; a sticky one is also removed from the stored on-state. Resolves true when it is gone. */
  async function remove(id: number): Promise<boolean> {
    const ctx = begin()
    if (!ctx) return false
    try {
      await apiRemove(ctx.projectId, id)
    } catch {
      return false
    }
    if (ctx.gen === generation) {
      items.value = items.value.filter((item) => item.id !== id)
      dropOnState(id)
    }
    return true
  }

  function dropOnState(id: number): void {
    if (stickyOn.value.has(id)) {
      const next = new Set(stickyOn.value)
      next.delete(id)
      stickyOn.value = next
      if (scope) saveStickyIds(scope.userId, scope.projectId, next)
    }
    if (onceOn.value.has(id)) {
      const next = new Set(onceOn.value)
      next.delete(id)
      onceOn.value = next
    }
  }

  /**
   * Drag: puts button `id` at 0-based `toIndex` of the row. The new order shows at once and is saved. Only one reorder
   * is in flight at a time — a move made meanwhile is ignored ('busy') — so two saves never overlap and the server
   * ends with the order the row last showed.
   *
   * When saving fails, the saver has already waited for every request of that save to finish, so nothing is still on
   * its way. The previous order is then written back to the server (every row's old sort_order) and shown again
   * ('restored'). Only when even that fails the row shows what the server holds ('unrestored'), never a guess.
   */
  async function move(id: number, toIndex: number): Promise<QuickMoveResult> {
    if (ordering.value) return 'busy'
    const ctx = begin()
    const before = items.value
    const from = before.findIndex((item) => item.id === id)
    if (!ctx || from < 0) return 'denied'
    const target = Math.max(0, Math.min(before.length - 1, toIndex))
    if (target === from) return 'moved'
    const next = [...before]
    next.splice(target, 0, next.splice(from, 1)[0])
    ordering.value = true
    try {
      items.value = next.map((item, index) => ({ ...item, sortOrder: index + 1 }))
      try {
        await apiSaveOrder(ctx.projectId, before, next.map((item) => item.id))
        return 'moved'
      } catch { /* part of the order may be stored: undo it below */ }
      try {
        await apiRestoreOrder(ctx.projectId, before)
        if (ctx.gen === generation) {
          // Rows added / removed while saving stay; every other row gets its old position back.
          const old = new Map(before.map((item) => [item.id, item.sortOrder]))
          items.value = items.value.map((item) => ({ ...item, sortOrder: old.get(item.id) ?? item.sortOrder })).sort(byRow)
        }
        return 'restored'
      } catch { /* the server's order is unknown: read it */ }
      try {
        const truth = await fetchList(ctx.projectId)
        if (ctx.gen === generation) items.value = truth
      } catch { /* the row keeps what it shows; a reload reads the server's order */ }
      return 'unrestored'
    } finally {
      ordering.value = false
    }
  }

  return {
    items, status, visible, active, isOn, load, toggle, toggleAt, consume,
    canEdit, editing, ordering, setEditing, create, update, remove, move,
  }
}

export type QuickMessagesState = ReturnType<typeof useQuickMessages>
