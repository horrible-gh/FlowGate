// Card merge state shared by the document row and its persistent modeless window.
// Existing API actions and run updates remain the source of truth.
import { useAiProvidersChanged } from '@shared/aiProvidersChanged'
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
  mergeAiAtWork,
  mergeAiLeftOff,
  mergeAutoRunning,
  mergeWorkOf,
  type MergeScreen,
  type MergeTone,
} from './mergeModel'

/** The last merge state read per card (the row colour follows it), and the cards whose open window showed work. */
const lastStates = reactive(new Map<string, CardMergeState>())
const shownWork = reactive(new Set<string>())
/**
 * flowgate.v02test.0084 (F84-8): the attempts (card + attempt id) whose merge an AI resolved — the automatic resolution
 * succeeded, or the last thing a window saw before the merge ended was an AI resolver at work (or its finished, fully
 * resolved work). Their window offers [병합 취소], and their merge row stays after the merge so the window can be opened
 * again. A merge a person settled after the AI left off (tries ran out or were stopped, the AI failed, a person's own
 * pick) is not in it.
 */
const aiMerged = reactive(new Set<string>())
const attemptKey = (key: string, attemptId: string | null | undefined) => `${key}\u0000${attemptId ?? ''}`
/** The integrated cards' attempts already read once for aiMerged (one read per attempt). */
const peeked = new Set<string>()

/**
 * The attempts whose conflict a window last saw in an AI's hands (automatic or asked for). Dropped again when the AI
 * left the conflict unsettled or a person picked the files themselves, so only a merge that follows the AI's own work
 * counts as the AI's.
 */
const aiWorked = new Set<string>()

/** How often a progress-only state is read again (ms). */
export const MERGE_POLL_MS = 5000

const keyOf = (target: InspectorTarget | null) => (target ? mergeCardKey(target.flowId, target.nodeId) : null)

/** The card's current attempt was merged by an AI (aiMerged). */
export function mergedByAi(target: InspectorTarget | null): boolean {
  const key = keyOf(target)
  return !!key && !!target?.nodeRun?.attemptId && aiMerged.has(attemptKey(key, target.nodeRun.attemptId))
}

function noteAiMerged(key: string, state: CardMergeState): void {
  if (state.autoResolve?.state === 'succeeded' && state.state === 'hidden') aiMerged.add(attemptKey(key, state.attemptId))
}

/** Row visibility supplier: merge work on the node run, or work this open tab showed (④), or a merge an AI resolved. */
export function useMergeRowVisible(target: Readonly<Ref<InspectorTarget | null>>): Readonly<Ref<boolean>> {
  // An integrated card is read once per attempt to learn whether an AI resolved its merge (the node run cannot tell).
  watch(
    () => {
      const current = target.value
      return current?.nodeRun?.integrationStatus === 'integrated' && current.nodeRun.attemptId ? [keyOf(current), current.nodeRun.attemptId] as const : null
    },
    (peek) => {
      const current = target.value
      if (!peek || !current || !peek[0]) return
      const attempt = attemptKey(peek[0], peek[1])
      if (peeked.has(attempt)) return
      peeked.add(attempt)
      cardMergeRead(current.flowId, current.nodeId).then((state) => noteAiMerged(peek[0]!, state), () => peeked.delete(attempt))
    },
    { immediate: true },
  )
  return computed(() => {
    const key = keyOf(target.value)
    if (!key) return false
    if (shownWork.has(key)) return true
    if (mergedByAi(target.value)) return true
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
  /** flowgate.v02test.0084: the merge shown as ④ was resolved by an AI ([병합 취소] is offered). */
  readonly aiResolved: ComputedRef<boolean>
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
  let providerRequest = 0
  let disposed = false
  useAiProvidersChanged(() => { void loadProviders() })
  let timer: ReturnType<typeof setInterval> | undefined

  function forget(key: string | null): void {
    if (!key) return
    shownWork.delete(key)
    // What this window saw of the AI counts only while it stays open: a conflict settled while no window watched is
    // judged by the server's record (automatic resolution succeeded) alone.
    for (const attempt of [...aiWorked]) if (attempt.startsWith(`${key}\u0000`)) aiWorked.delete(attempt)
  }

  function take(key: string, next: CardMergeState): void {
    if (key !== currentKey) return
    state.value = next
    lastStates.set(key, next)
    // The AI at work on this attempt's conflict (automatic or asked for): a merge that follows is the AI's — unless the
    // AI left it unsettled first (a person then resolves and merges it).
    const attempt = attemptKey(key, next.attemptId)
    if (mergeAiAtWork(next)) aiWorked.add(attempt)
    else if (mergeAiLeftOff(next)) aiWorked.delete(attempt)
    if (next.state === 'hidden' && aiWorked.has(attempt)) aiMerged.add(attempt)
    noteAiMerged(key, next)
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

  /** This window's card merged by an AI: [병합 취소] may be offered (merged screen only). */
  const aiResolved = computed(() => {
    const key = currentKey
    const current = state.value
    return !!key && !!current && current.state === 'hidden' && aiMerged.has(attemptKey(key, current.attemptId))
  })

  const screen = computed(() => {
    const current = state.value
    // Opening the picker is read-only; only submitting a choice stops the server's automatic tries.
    if (picking.value && current?.state === 'catching_up' && mergeAutoRunning(current.autoResolve)
      && current.actions.includes('pick')) return 'pick'
    // A merge an AI resolved shows ④ even when the window opened after it (from the merge row or a notification).
    return mergeScreenOf(current?.state ?? null, hadWork.value || aiResolved.value, picking.value)
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
    disposed = true
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
    if (!current || disposed) return
    const request = ++providerRequest
    try {
      const choices = await settingsChoicesRead(current.projectId)
      if (disposed || request !== providerRequest || target()?.projectId !== current.projectId) return
      providers.value = choices.providers
    } catch {
      if (disposed || request !== providerRequest) return
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
    aiResolved,
    reload,
    aiResolve: (providerId) => act((expectedAttemptId) => ({ action: 'ai_resolve', expectedAttemptId, providerId })),
    pick: (files, providerId) => {
      // A person's own pick (no file left to the AI) settles the conflict: the merge that follows is not the AI's.
      const key = currentKey
      const attemptId = state.value?.attemptId
      if (key && attemptId && !files.some((file) => file.side === 'ai')) aiWorked.delete(attemptKey(key, attemptId))
      return act((expectedAttemptId) => ({ action: 'pick', expectedAttemptId, files, providerId }))
    },
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
  if (mergedByAi(target)) return 'merged'
  return ({ pending: 'waiting', integrating: 'catching_up', conflict: 'needs_choice', failed: 'failed' } as Record<string, string>)[target.nodeRun?.integrationStatus ?? ''] ?? 'none'
}

