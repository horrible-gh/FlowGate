// 내 최대 반복 횟수 / 내 제목 짓기 AI (flowgate.v02test.0096 F96-5): the one load / save logic behind both places that
// edit my own values of GET/PATCH /api/v1/me/ai-preferences — AI 관리 › 기타 설정 (내 값 column) and the ⚙ settings
// window's AI tab. Before F96-5 each screen carried its own copy of the shape check, the optimistic save with rollback,
// the −/＋ bounds, the "back to the default" reset and the title choice; they now share this composable.
// F96-11: my value ranges over repeat_count_min ~ repeat_count_hard_max (1~30); the system value is only the default.
//
// Server calls stay in each feature's own api module (feature boundary rules), so the caller passes its fetch / save
// functions in. The composable keeps no module-wide state: each screen reads the server when it opens, which is how a
// value changed in one screen shows in the other.
import { ref, type Ref } from 'vue'

/** The body of GET/PATCH /me/ai-preferences (v02test.0089). Structural, so both features' own types fit. */
export interface MyAiPreferencesShape {
  title_provider: string
  title_provider_effective: string
  repeat_count_max: number | null
  repeat_count_max_effective: number
  repeat_count_default: number
  repeat_count_min: number
  repeat_count_hard_max: number
}

export interface MyAiPreferencesPatch {
  title_provider?: string
  repeat_count_max?: number | null
}

/** Value of the disabled separator row between the fixed title choices and the AI in a title AI select. */
export const TITLE_SEPARATOR = '__separator__'

/** A body without the contract's fields (an older server) does not count as preferences. */
export function isMyAiPreferences(value: unknown): value is MyAiPreferencesShape {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  return typeof p.title_provider === 'string'
    && typeof p.repeat_count_max_effective === 'number'
    && typeof p.repeat_count_default === 'number'
    && typeof p.repeat_count_min === 'number'
    && typeof p.repeat_count_hard_max === 'number'
    && (p.repeat_count_max === null || p.repeat_count_max === undefined || typeof p.repeat_count_max === 'number')
}

function normalized<T extends MyAiPreferencesShape>(p: T): T {
  return { ...p, repeat_count_max: p.repeat_count_max ?? null }
}

export interface MyAiPreferencesOptions<T extends MyAiPreferencesShape> {
  fetch: () => Promise<T>
  save: (update: MyAiPreferencesPatch) => Promise<T>
  /** Called with the error of a failed save, after the previous value is back on screen. */
  onSaveError?: (err: unknown) => void
}

export interface MyAiPreferencesControl<T extends MyAiPreferencesShape> {
  /** null until a read succeeded (or while the body is not in the contract shape). */
  prefs: Ref<T | null>
  state: Ref<'loading' | 'ready' | 'error'>
  saving: Ref<boolean>
  load: () => Promise<void>
  /** −/＋: moves my value from the effective one, within repeat_count_min ~ repeat_count_hard_max. */
  stepRepeat: (delta: number) => void
  /** Back to "not set": the default applies again. */
  resetRepeat: () => void
  chooseTitle: (value: string) => void
  /** Call from onBeforeUnmount: answers that arrive later are ignored. */
  dispose: () => void
}

export function useMyAiPreferences<T extends MyAiPreferencesShape>(options: MyAiPreferencesOptions<T>): MyAiPreferencesControl<T> {
  const prefs = ref(null) as Ref<T | null>
  const state = ref<'loading' | 'ready' | 'error'>('loading')
  const saving = ref(false)
  let disposed = false

  async function load(): Promise<void> {
    if (!prefs.value) state.value = 'loading'
    try {
      const data = await options.fetch()
      if (disposed) return
      if (!isMyAiPreferences(data)) throw new Error('invalid')
      // A change saved meanwhile is newer than this read.
      if (!saving.value) prefs.value = normalized(data)
      state.value = 'ready'
    } catch {
      if (!disposed && !prefs.value) state.value = 'error'
    }
  }

  // Shown at once and saved at once; the server's answer (same shape as GET) replaces it, a failure puts the previous back.
  async function save(update: MyAiPreferencesPatch, shown: T): Promise<void> {
    const previous = prefs.value
    if (!previous || saving.value) return
    prefs.value = shown
    saving.value = true
    try {
      const data = await options.save(update)
      if (!disposed && isMyAiPreferences(data)) prefs.value = normalized(data)
    } catch (err: unknown) {
      if (disposed) return
      prefs.value = previous
      options.onSaveError?.(err)
    } finally {
      saving.value = false
    }
  }

  function stepRepeat(delta: number): void {
    const p = prefs.value
    if (!p || saving.value) return
    const next = p.repeat_count_max_effective + delta
    if (next < p.repeat_count_min || next > p.repeat_count_hard_max) return
    void save({ repeat_count_max: next }, { ...p, repeat_count_max: next, repeat_count_max_effective: next })
  }

  function resetRepeat(): void {
    const p = prefs.value
    if (!p || saving.value || p.repeat_count_max === null) return
    void save({ repeat_count_max: null }, { ...p, repeat_count_max: null, repeat_count_max_effective: p.repeat_count_default })
  }

  function chooseTitle(value: string): void {
    const p = prefs.value
    if (!p || saving.value || value === p.title_provider || value === TITLE_SEPARATOR) return
    void save({ title_provider: value }, { ...p, title_provider: value })
  }

  return { prefs, state, saving, load, stepRepeat, resetRepeat, chooseTitle, dispose: () => { disposed = true } }
}

export interface TitleChoice {
  value: string
  label: string
  disabled?: boolean
}

/**
 * Title AI select rows: the fixed choices, a separator, then the AI. The stored value is always offered (as its id when
 * no row names it), so the select never claims another value.
 */
export function titleChoices(current: string | null | undefined, fixed: TitleChoice[], ai: TitleChoice[]): TitleChoice[] {
  const rows = [...ai]
  if (current && !fixed.some((o) => o.value === current) && !rows.some((o) => o.value === current)) {
    rows.push({ value: current, label: current })
  }
  return rows.length ? [...fixed, { value: TITLE_SEPARATOR, label: '──────────', disabled: true }, ...rows] : fixed
}
