// Card merge state shared by the document row and its persistent modeless window.
// Existing API actions and run updates remain the source of truth.
import { computed, inject, onBeforeUnmount, reactive, ref, watch, type ComputedRef, type Ref } from 'vue'
import {
  cardMergeAct,
  cardMergeErrorOf,
  cardMergeRead,
  settingsChoicesRead,
  type CardMergeError,
  type CardMergeRequest,
  type CardMergeSide,
  type CardMergeState,
} from '@/features/document'
import { INSPECTOR_HOST } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'
import {
  OPEN_CARD_MERGE_EVENT,
  mergeCardKey,
  mergeScreenOf,
  mergeToneOfState,
  mergeToneOfStatus,
  mergeAutoRunning,
  mergeWorkOf,
  type MergeScreen,
  type MergeTone,
} from './mergeModel'

/** The last merge state read per card (the row colour follows it), and the cards whose open window showed work. */
const lastStates = reactive(new Map<string, CardMergeState>())
const shownWork = reactive(new Set<string>())

/** How often a progress-only state is read again (ms). */
export const MERGE_POLL_MS = 5000

const keyOf = (target: InspectorTarget | null) => (target ? mergeCardKey(target.flowId, target.nodeId) : null)

/** Row visibility supplier: merge work on the node run, or work this open tab showed (④). */
export function useMergeRowVisible(target: Readonly<Ref<InspectorTarget | null>>): Readonly<Ref<boolean>> {
  return computed(() => {
    const key = keyOf(target.value)
    if (!key) return false
    if (shownWork.has(key)) return true
    const read = lastStates.get(key)
    // A completed window may close before the Canvas run refresh arrives.
    if (read?.state === 'hidden' && read.attemptId && read.attemptId === target.value?.nodeRun?.attemptId) return false
    return mergeWorkOf(target.value)
  })
}

/** Row colour supplier: the last read state when there is one, else the node run's integration status. */
export function useMergeRowTone(target: Readonly<Ref<InspectorTarget | null>>): Readonly<Ref<MergeTone | null>> {
  return computed(() => {
    const key = keyOf(target.value)
    if (!key) return null
    const read = lastStates.get(key)
    if (read && shownWork.has(key)) return mergeToneOfState(read.state)
    return mergeToneOfStatus(target.value?.nodeRun?.integrationStatus)
  })
}

/** Asks the Designer to select a card and open its merge window. */
export function openCardMerge(flowId: string, nodeKey: string): void {
  window.dispatchEvent(new CustomEvent(OPEN_CARD_MERGE_EVENT, { detail: { flowId, nodeKey } }))
}

export interface MergeProvider {
  readonly id: string
  readonly name: string
}

export interface MergeWindowState {
  readonly state: Readonly<Ref<CardMergeState | null>>
  readonly screen: ComputedRef<MergeScreen>
  readonly readFailed: Readonly<Ref<boolean>>
  readonly error: Readonly<Ref<CardMergeError | null>>
  readonly busy: Readonly<Ref<boolean>>
  readonly picking: Ref<boolean>
  readonly providers: Readonly<Ref<readonly MergeProvider[]>>
  reload(): Promise<void>
  aiResolve(providerId: string | null): Promise<void>
  pick(files: readonly { path: string; side: CardMergeSide }[], providerId: string | null): Promise<void>
  merge(): Promise<void>
  discardRerun(): Promise<void>
  retry(): Promise<void>
  loadProviders(): Promise<void>
}

export function useMerge(target: () => InspectorTarget | null, refresh?: () => void): MergeWindowState {
  const host = inject(INSPECTOR_HOST, null)
  const state = ref<CardMergeState | null>(null)
  const readFailed = ref(false)
  const error = ref<CardMergeError | null>(null)
  const busy = ref(false)
  const picking = ref(false)
  const providers = ref<readonly MergeProvider[]>([])
  const hadWork = ref(false)
  let currentKey: string | null = null
  let ticket = 0
  let timer: ReturnType<typeof setInterval> | undefined

  function forget(key: string | null): void {
    if (key) shownWork.delete(key)
  }

  function take(key: string, next: CardMergeState): void {
    if (key !== currentKey) return
    state.value = next
    lastStates.set(key, next)
    if (next.state !== 'hidden') {
      hadWork.value = true
      shownWork.add(key)
    }
  }

  async function reload(): Promise<void> {
    const current = target()
    const key = keyOf(current)
    if (!current || !key) return
    const mine = ++ticket
    try {
      const next = await cardMergeRead(current.flowId, current.nodeId)
      if (mine !== ticket) return
      readFailed.value = false
      take(key, next)
    } catch {
      if (mine === ticket) readFailed.value = true
    }
  }

  watch(
    () => {
      const current = target()
      const run = current?.nodeRun
      return [keyOf(current), run?.integrationStatus ?? null, run?.integrationReason ?? null,
        run?.integrationBlockedBy ?? null, run?.attemptId ?? null, run?.status ?? null].join('|')
    },
    () => {
      const key = keyOf(target())
      if (key !== currentKey) {
        forget(currentKey)
        currentKey = key
        state.value = null
        hadWork.value = mergeWorkOf(target())
        picking.value = false
        error.value = null
        readFailed.value = false
        if (key && hadWork.value) shownWork.add(key)
      }
      void reload()
    },
    { immediate: true },
  )

  const screen = computed(() => {
    const current = state.value
    // Opening the picker is read-only; only submitting a choice stops the server's automatic tries.
    if (picking.value && current?.state === 'catching_up' && mergeAutoRunning(current.autoResolve)
      && current.actions.includes('pick')) return 'pick'
    return mergeScreenOf(current?.state ?? null, hadWork.value, picking.value)
  })

  // Progress is read again while the server works on its own: a merge in progress, a waiting one, or an automatic resolution.
  // Opening the window only reads; nothing here asks the server to stop or start the automatic tries.
  watch(
    () => screen.value === 'catching_up' || screen.value === 'waiting' || mergeAutoRunning(state.value?.autoResolve),
    (progress) => {
      clearInterval(timer)
      timer = progress ? setInterval(() => void reload(), MERGE_POLL_MS) : undefined
    },
    { immediate: true },
  )

  onBeforeUnmount(() => {
    clearInterval(timer)
    ++ticket
    forget(currentKey)
    currentKey = null
  })

  async function act(build: (attemptId: string) => CardMergeRequest): Promise<void> {
    const current = target()
    const key = keyOf(current)
    const attemptId = state.value?.attemptId
    if (!current || !key || !attemptId || busy.value) return
    busy.value = true
    error.value = null
    try {
      const next = await cardMergeAct(current.flowId, current.nodeId, build(attemptId))
      picking.value = false
      take(key, next)
    } catch (thrown) {
      const failure = cardMergeErrorOf(thrown)
      if (key === currentKey) error.value = failure
      if (failure.code === 'merge_state_changed') await reload()
    } finally {
      busy.value = false
      if (refresh) refresh()
      else host?.request({ kind: 'refresh-flow', flowId: current.flowId })
    }
  }

  async function loadProviders(): Promise<void> {
    const current = target()
    if (!current || providers.value.length) return
    try {
      providers.value = (await settingsChoicesRead(current.projectId)).providers
    } catch {
      providers.value = []
    }
  }

  return {
    state,
    screen,
    readFailed,
    error,
    busy,
    picking,
    providers,
    reload,
    aiResolve: (providerId) => act((expectedAttemptId) => ({ action: 'ai_resolve', expectedAttemptId, providerId })),
    pick: (files, providerId) => act((expectedAttemptId) => ({ action: 'pick', expectedAttemptId, files, providerId })),
    merge: () => act((expectedAttemptId) => ({ action: 'merge', expectedAttemptId })),
    discardRerun: () => act((expectedAttemptId) => ({ action: 'discard_rerun', expectedAttemptId })),
    retry: () => act((expectedAttemptId) => ({ action: 'retry', expectedAttemptId })),
    loadProviders,
  }
}

/** Last known state for the document row; reading remains owned by the window. */
export function mergeRowState(target: InspectorTarget): string {
  const state = lastStates.get(mergeCardKey(target.flowId, target.nodeId))?.state
  if (state && shownWork.has(mergeCardKey(target.flowId, target.nodeId))) return state === 'hidden' ? 'merged' : state
  return ({ pending: 'waiting', integrating: 'catching_up', conflict: 'needs_choice', failed: 'failed' } as Record<string, string>)[target.nodeRun?.integrationStatus ?? ''] ?? 'none'
}
