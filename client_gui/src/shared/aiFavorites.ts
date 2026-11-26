// 자주 쓰는 AI (flowgate.v02test.0096 ①): the AI I pinned (📌) and the AI I used last, per signed-in person.
//
// Both lists are NEW values of their own. They never touch the AI list data (system AI, my AI, my order, my default
// AI / the system default star): the AI 관리 list toggles a pin, the composer's AI picker reads both lists to build its
// 자주 쓰는 AI section and records an AI as used when it is picked. The next card (④, ⚙ 설정 › AI 탭 "자주 쓰는 AI 고정
// 관리") manages the same pins through this composable.
//
// Storage: this browser's localStorage, one key per person. The personal stores the server has (/me/ai-preferences,
// /me/ui-settings) are fixed columns, so a list there needs a schema change; a pin is a display preference that may
// stay per browser. State is shared module-wide, so a pin toggled in one screen shows in every other mounted screen
// at once, and a change made in another tab arrives through the `storage` event.
import { computed, reactive, type ComputedRef } from 'vue'

export const AI_FAVORITES_KEY_PREFIX = 'fg.ai_favorites.v1.'
/** Recent AI shown in the 자주 쓰는 AI section. */
export const RECENT_SHOWN = 3
/** Recent AI kept, so a few pinned / default ones can be skipped and three still remain. */
export const RECENT_KEPT = 10
/** Upper bound of the pin list (a corrupt or huge stored value is cut to this). */
export const PINNED_MAX = 50

export interface AiFavoritesState {
  pinned: string[]
  recent: string[]
}

// Plain map of reactive per-person states: creating one inside a computed must not trigger that computed again.
const states = new Map<string, AiFavoritesState>()

function ownerKey(owner: string | null | undefined): string {
  return owner && owner.trim() ? owner.trim() : '_'
}

function storageKey(owner: string): string {
  return AI_FAVORITES_KEY_PREFIX + owner
}

function cleanIds(value: unknown, max: number): string[] {
  if (!Array.isArray(value)) return []
  const ids = value.filter((id): id is string => typeof id === 'string' && id !== '')
  return [...new Set(ids)].slice(0, max)
}

/** A stored value as the two lists; anything unreadable reads as empty lists. */
export function parseAiFavorites(raw: string | null): AiFavoritesState {
  if (!raw) return { pinned: [], recent: [] }
  try {
    const data: unknown = JSON.parse(raw)
    if (!data || typeof data !== 'object') return { pinned: [], recent: [] }
    const value = data as Record<string, unknown>
    return { pinned: cleanIds(value.pinned, PINNED_MAX), recent: cleanIds(value.recent, RECENT_KEPT) }
  } catch {
    return { pinned: [], recent: [] }
  }
}

function read(owner: string): AiFavoritesState {
  try {
    return parseAiFavorites(typeof localStorage === 'undefined' ? null : localStorage.getItem(storageKey(owner)))
  } catch {
    return { pinned: [], recent: [] }
  }
}

function stateOf(owner: string): AiFavoritesState {
  let state = states.get(owner)
  if (!state) {
    state = reactive(read(owner))
    states.set(owner, state)
  }
  return state
}

function write(owner: string, next: AiFavoritesState): void {
  const state = stateOf(owner)
  state.pinned = next.pinned
  state.recent = next.recent
  try {
    localStorage.setItem(storageKey(owner), JSON.stringify({ pinned: next.pinned, recent: next.recent }))
  } catch {
    // Best effort: the change still holds for this page.
  }
}

let listening = false
function listen(): void {
  if (listening || typeof window === 'undefined') return
  listening = true
  window.addEventListener('storage', (event: StorageEvent) => {
    if (event.key === null) {
      // localStorage.clear() in another tab: every list read again (now empty).
      for (const [owner, state] of states) Object.assign(state, read(owner))
      return
    }
    if (!event.key.startsWith(AI_FAVORITES_KEY_PREFIX)) return
    const owner = event.key.slice(AI_FAVORITES_KEY_PREFIX.length)
    if (!states.has(owner)) return
    const next = parseAiFavorites(event.newValue)
    const state = states.get(owner)!
    state.pinned = next.pinned
    state.recent = next.recent
  })
}

/**
 * The 자주 쓰는 AI order: my default AI, then the pinned AI (in pin order), then up to {@link RECENT_SHOWN} recent AI
 * not already named — each id once and only ids the list offers (`available`, in any order).
 */
export function frequentAiIds(input: {
  defaultId?: string | null
  pinned: readonly string[]
  recent: readonly string[]
  available: Iterable<string>
}): string[] {
  const offered = new Set(input.available)
  const out: string[] = []
  const add = (id: string | null | undefined) => {
    if (id && offered.has(id) && !out.includes(id)) out.push(id)
  }
  add(input.defaultId)
  input.pinned.forEach(add)
  let recent = 0
  for (const id of input.recent) {
    if (recent >= RECENT_SHOWN) break
    if (id && offered.has(id) && !out.includes(id)) {
      out.push(id)
      recent += 1
    }
  }
  return out
}

export interface AiFavorites {
  pinned: ComputedRef<readonly string[]>
  recent: ComputedRef<readonly string[]>
  isPinned(id: string): boolean
  togglePin(id: string): void
  setPinned(id: string, on: boolean): void
  /** Replace the whole pin list (order kept, duplicates dropped) — for a screen that reorders pins. */
  replacePinned(ids: readonly string[]): void
  /** Mark an AI as just used: it moves to the front of the recent list. */
  recordUse(id: string): void
}

/** `owner` names the signed-in person (read on every call, so a sign-in change switches the lists). */
export function useAiFavorites(owner: () => string | null | undefined): AiFavorites {
  listen()
  const key = () => ownerKey(owner())
  const pinned = computed<readonly string[]>(() => stateOf(key()).pinned)
  const recent = computed<readonly string[]>(() => stateOf(key()).recent)

  function setPinned(id: string, on: boolean): void {
    if (!id) return
    const k = key()
    const state = stateOf(k)
    const has = state.pinned.includes(id)
    if (on === has) return
    const next = on ? [...state.pinned, id].slice(0, PINNED_MAX) : state.pinned.filter((p) => p !== id)
    write(k, { pinned: next, recent: state.recent })
  }

  return {
    pinned,
    recent,
    isPinned: (id) => stateOf(key()).pinned.includes(id),
    togglePin: (id) => setPinned(id, !stateOf(key()).pinned.includes(id)),
    setPinned,
    replacePinned(ids) {
      const k = key()
      write(k, { pinned: cleanIds([...ids], PINNED_MAX), recent: stateOf(k).recent })
    },
    recordUse(id) {
      if (!id) return
      const k = key()
      const state = stateOf(k)
      if (state.recent[0] === id) return
      write(k, { pinned: state.pinned, recent: [id, ...state.recent.filter((r) => r !== id)].slice(0, RECENT_KEPT) })
    },
  }
}

/** Test hook: forget the in-memory lists so the next read comes from localStorage again. */
export function resetAiFavoritesCache(): void {
  for (const [owner, state] of states) Object.assign(state, read(owner))
}
