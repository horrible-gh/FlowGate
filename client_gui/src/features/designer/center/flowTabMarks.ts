// flowgate.v02test.0035 — the marks a Flow tab keeps after something ended while the user looked at another tab: the
// run's result (its run band tone, `runBandTone`) and the chat answer's end (done / failed). A tab activated clears its
// marks; a closed tab drops them. Kept per Project in the browser's localStorage so a reload or a new browser session
// still shows what was not looked at. Not tied to the notification bell or the run result card.
import { reactive, watch, type WatchStopHandle } from 'vue'
import { runBandTone, type DesignerRunBandTone } from '../top/designerTop'
import { RUN_LOCKED_STATUSES } from './run/flowRunLock'

export type FlowTabRunEndTone = Exclude<DesignerRunBandTone, 'info'>

/** A run that ended while its tab was not the active one: the band tone and the idle reason (open key) behind it. */
export interface FlowTabRunEnd {
  readonly tone: FlowTabRunEndTone
  readonly reason: string | null
}

/** A chat answer that ended while its tab was not the active one. */
export type FlowTabChatEnd = 'done' | 'failed'

export interface FlowTabMark {
  readonly run?: FlowTabRunEnd
  readonly chat?: FlowTabChatEnd
}

export type FlowTabMarks = Readonly<Record<string, FlowTabMark>>

export interface FlowTabMarkStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

export const FLOW_TAB_MARKS_KEY_PREFIX = 'flowgate.designer.flowTabMarks.v1:'

export function flowTabMarksKey(projectId: string): string {
  return `${FLOW_TAB_MARKS_KEY_PREFIX}${projectId}`
}

const TONES: readonly FlowTabRunEndTone[] = ['success', 'warning', 'danger', 'neutral']

function cleanMark(raw: unknown): FlowTabMark | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as { run?: { tone?: unknown; reason?: unknown }; chat?: unknown }
  const run = value.run && TONES.includes(value.run.tone as FlowTabRunEndTone)
    ? { tone: value.run.tone as FlowTabRunEndTone, reason: typeof value.run.reason === 'string' ? value.run.reason : null }
    : undefined
  const chat = value.chat === 'done' || value.chat === 'failed' ? value.chat : undefined
  if (!run && !chat) return null
  return { ...(run ? { run } : {}), ...(chat ? { chat } : {}) }
}

/** The Project's stored marks; anything unreadable is no mark. */
export function readFlowTabMarks(storage: FlowTabMarkStorage | null, projectId: string): Record<string, FlowTabMark> {
  if (!storage) return {}
  try {
    const parsed = JSON.parse(storage.getItem(flowTabMarksKey(projectId)) ?? 'null') as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const marks: Record<string, FlowTabMark> = {}
    for (const [flowId, raw] of Object.entries(parsed as Record<string, unknown>)) {
      const mark = cleanMark(raw)
      if (mark) marks[flowId] = mark
    }
    return marks
  } catch {
    return {}
  }
}

/** Stores the Project's marks (no mark: the entry goes). A storage that refuses keeps the marks in memory only. */
export function writeFlowTabMarks(storage: FlowTabMarkStorage | null, projectId: string, marks: FlowTabMarks): void {
  if (!storage) return
  try {
    if (Object.keys(marks).length === 0) storage.removeItem(flowTabMarksKey(projectId))
    else storage.setItem(flowTabMarksKey(projectId), JSON.stringify(marks))
  } catch { /* quota / disabled storage: memory only */ }
}

/** The mark a run leaves when it goes from a locked status (running / paused / stopping) to idle; null otherwise. */
export function runEndOf(
  before: string | null | undefined,
  after: { status: string; idleReason?: string | null } | null | undefined,
): FlowTabRunEnd | null {
  if (!before || !RUN_LOCKED_STATUSES.includes(before) || !after || RUN_LOCKED_STATUSES.includes(after.status)) return null
  const reason = after.idleReason ?? null
  const tone = runBandTone({ phase: 'idle', idleReason: reason })
  return { tone: tone === 'info' ? 'neutral' : tone, reason }
}

function defaultStorage(): FlowTabMarkStorage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null }
}

export interface FlowTabRunFacts {
  /** False while the run is not confirmed by a read: nothing is judged from it. */
  readonly known: boolean
  readonly status: string | null
  readonly idleReason: string | null
}

export interface FlowTabMarksOptions {
  readonly projectId: () => string | null
  readonly tabs: () => readonly string[]
  readonly activeFlowId: () => string | null
  readonly runs: () => Readonly<Record<string, FlowTabRunFacts>>
  /** Whether each tab's chat AI is answering now. */
  readonly chats: () => Readonly<Record<string, boolean>>
  /** How the tab's last chat answer ended (consulted when its chat stops answering); absent = done. */
  readonly chatEnd?: (flowId: string) => FlowTabChatEnd | null
  readonly storage?: () => FlowTabMarkStorage | null
}

/**
 * The open tabs' marks. A run or chat that ends while its tab is the active one leaves nothing (as before); activating a
 * tab clears its marks; closing a tab drops them. Call inside a component's setup (the watches end with it).
 */
export function useFlowTabMarks(options: FlowTabMarksOptions): { marks: FlowTabMarks; stop: WatchStopHandle } {
  const storage = options.storage ?? defaultStorage
  const marks = reactive<Record<string, FlowTabMark>>({})
  let projectId: string | null = null
  let lastRuns: Record<string, string | null> = {}
  let lastChats: Record<string, boolean> = {}

  const save = () => { if (projectId !== null) writeFlowTabMarks(storage(), projectId, { ...marks }) }
  const set = (flowId: string, change: Partial<FlowTabMark>) => {
    const next = cleanMark({ ...marks[flowId], ...change })
    if (next) marks[flowId] = next
    else delete marks[flowId]
    save()
  }
  const clear = (flowId: string) => {
    if (!(flowId in marks)) return
    delete marks[flowId]
    save()
  }

  const stops: WatchStopHandle[] = []

  // Another Project: its own stored marks; nothing seen in the old one carries over.
  stops.push(watch(options.projectId, (pid) => {
    projectId = pid
    lastRuns = {}
    lastChats = {}
    for (const key of Object.keys(marks)) delete marks[key]
    if (pid !== null) Object.assign(marks, readFlowTabMarks(storage(), pid))
  }, { immediate: true }))

  // A closed tab drops its marks (a Project change empties the tabs without dropping the stored ones).
  stops.push(watch(() => [options.projectId(), options.tabs().join('\n')] as const, (now, before) => {
    if (!before || now[0] !== before[0] || now[0] === null) return
    const open = new Set(options.tabs())
    for (const flowId of before[1] ? before[1].split('\n') : []) if (!open.has(flowId)) clear(flowId)
  }))

  // The tab the user opens is looked at: its marks go.
  stops.push(watch(options.activeFlowId, (flowId) => { if (flowId !== null) clear(flowId) }, { immediate: true }))

  stops.push(watch(() => ({ ...options.runs() }), (runs) => {
    const active = options.activeFlowId()
    const next: Record<string, string | null> = {}
    for (const flowId of options.tabs()) {
      const facts = runs[flowId]
      if (!facts || !facts.known) {
        if (flowId in lastRuns) next[flowId] = lastRuns[flowId]
        continue
      }
      const end = runEndOf(lastRuns[flowId], facts.status === null ? null : { status: facts.status, idleReason: facts.idleReason })
      if (end && flowId !== active) set(flowId, { run: end })
      next[flowId] = facts.status
    }
    lastRuns = next
  }, { immediate: true, deep: true }))

  stops.push(watch(() => ({ ...options.chats() }), (chats) => {
    const active = options.activeFlowId()
    const next: Record<string, boolean> = {}
    for (const flowId of options.tabs()) {
      const answering = chats[flowId] === true
      if (lastChats[flowId] === true && !answering && flowId !== active) {
        set(flowId, { chat: options.chatEnd?.(flowId) ?? 'done' })
      }
      next[flowId] = answering
    }
    lastChats = next
  }, { immediate: true }))

  return { marks, stop: () => { for (const stop of stops) stop() } }
}
