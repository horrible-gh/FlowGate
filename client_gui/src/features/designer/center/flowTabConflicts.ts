// flowgate.v02test.0066 (#36-1): what each open Flow tab shows about a merge conflict — 병합 충돌, or AI 해결 중 with its
// progress — judged from the same answers as the Git panel, for every open tab (not only the shown Flow).
//
//   git/status `pending` row   one read for the whole Project (never one per tab). A group's row with status
//                              `conflict` decides its tab the way the panel's card does (gitSession `groupCard`):
//                              a resolver run → AI 해결 중, a merge review state (해결 끝 · 승인 대기) → no mark,
//                              anything else → 병합 충돌.
//   ai-invoke/active           read only for tabs whose row is a conflict with a merge id, with `conflict_progress`
//                              (done / total files, counted by the server) so the tooltip needs no file list.
//   the Graph's card facts     (`graphConflicts`, what the tab showed before) still decide a tab that has no conflict
//                              row — Git off, status unreadable or not read yet — so a conflict shows as fast as before.
//
// Reads happen on the Git ledger events of an open tab's group, when a tab is opened, when the Graph's answer changes
// and on a stream resync. Only while a tab is AI 해결 중 is it polled (TAB_CONFLICT_POLL_MS), one request per such tab.
import { computed, getCurrentInstance, onScopeDispose, reactive, watch, type ComputedRef } from 'vue'
import { useProjectStore, type FlowAiActivity, type ProjectGitStatus } from '@/features/project'
import type { ProjectEventSubscription } from '@/features/project/projectEvents'
import { REVIEW_STATES, isResolving } from '@/features/git'

/** A tab's conflict mark; a tab with none is left out. `done` / `total` are null while the server gave no count. */
export type FlowTabConflict =
  | { readonly state: 'conflict' }
  | { readonly state: 'resolving'; readonly done: number | null; readonly total: number | null }

/** Slower than the panel's own poll (2.5 s): the tooltip's count may lag, the end arrives by event anyway. */
export const TAB_CONFLICT_POLL_MS = 5000

/** The panel's Git ledger events (DesignerGitPanel GIT_LEDGER_EVENTS) plus the start of an AI run. */
export const TAB_CONFLICT_EVENTS: readonly string[] = [
  'git_pending_changed', 'git_merge_conflict', 'git_finalize_done', 'git_approval_job_changed',
  'gate_publish_job_changed', 'git_status_changed', 'ai_invoke_started', 'ai_invoke_finished',
]

export interface FlowTabConflictSource {
  loadStatus(projectId: string): Promise<ProjectGitStatus | null>
  /** ai-invoke/active with the resolver's progress. */
  loadAiActivity(groupId: string): Promise<FlowAiActivity>
  subscribe(projectId: string, subscription: ProjectEventSubscription): () => void
}

export interface FlowTabConflictOptions {
  projectId: () => string | null
  /** the open tabs' Flow (group) ids. */
  flows: () => readonly string[]
  /** each open tab's conflict as its Graph says (absent = Graph not read yet). */
  graphConflicts: () => Readonly<Record<string, boolean | undefined>>
  /** tests pass one; otherwise the Project store of the running app (none without Pinia: the Graph alone decides). */
  source?: FlowTabConflictSource | null
  pollMs?: number
}

interface ConflictRow { mergeId: number | null; review: boolean }
interface Progress { done: number | null; total: number | null }

function storeSource(): FlowTabConflictSource | null {
  const pinia = getCurrentInstance()?.appContext.config.globalProperties.$pinia
  if (!pinia) return null
  const store = useProjectStore(pinia)
  return {
    loadStatus: (projectId) => store.loadProjectGitStatus(projectId),
    loadAiActivity: (groupId) => store.loadFlowAiActivity(groupId, { conflictProgress: true }),
    subscribe: (projectId, subscription) => store.subscribeProjectEvents(projectId, subscription),
  }
}

export function useFlowTabConflicts(options: FlowTabConflictOptions): ComputedRef<Record<string, FlowTabConflict>> {
  const source = options.source === undefined ? storeSource() : options.source
  const pollMs = options.pollMs ?? TAB_CONFLICT_POLL_MS
  /** the open tabs' conflict rows of the last git/status answer. */
  const rows = reactive<Record<string, ConflictRow>>({})
  /** the tabs a resolver run owns now, with its last counted progress. */
  const resolving = reactive<Record<string, Progress>>({})
  let disposed = false
  /** bumped per Project: an answer for an older one is dropped. */
  let gen = 0
  let statusReading = false
  let statusAgain = false
  const aiReading = new Set<string>()
  const aiAgain = new Set<string>()
  let timer: ReturnType<typeof setTimeout> | null = null

  function clear(target: Record<string, unknown>) {
    for (const key of Object.keys(target)) delete target[key]
  }

  async function readStatus(): Promise<void> {
    if (!source || disposed) return
    if (statusReading) { statusAgain = true; return }
    const pid = options.projectId()
    if (!pid || options.flows().length === 0) return
    statusReading = true
    const mine = gen
    try {
      const status = await source.loadStatus(pid)
      if (disposed || mine !== gen) return
      const open = new Set(options.flows())
      const next: Record<string, ConflictRow> = {}
      for (const row of status?.enabled ? status.pending : []) {
        if (row.status !== 'conflict' || !open.has(row.groupId)) continue
        next[row.groupId] = { mergeId: row.mergeId, review: !!row.reviewState && REVIEW_STATES.includes(row.reviewState) }
      }
      for (const id of Object.keys(rows)) if (!next[id]) delete rows[id]
      for (const [id, row] of Object.entries(next)) rows[id] = row
      // The panel shows AI 해결 중 only for a conflict row that has its merge id; so does the tab.
      for (const id of Object.keys(resolving)) if (!rows[id] || rows[id].mergeId === null) delete resolving[id]
      for (const [id, row] of Object.entries(next)) if (row.mergeId !== null) void readAi(id)
    } catch {
      // Keep the last answer; the next event or resync reads again.
    } finally {
      statusReading = false
      if (statusAgain) { statusAgain = false; if (!disposed) void readStatus() }
    }
  }

  async function readAi(id: string): Promise<void> {
    if (!source || disposed) return
    if (aiReading.has(id)) { aiAgain.add(id); return }
    if (!rows[id] || rows[id].mergeId === null) { delete resolving[id]; return }
    aiReading.add(id)
    const mine = gen
    try {
      const activity = await source.loadAiActivity(id)
      if (disposed || mine !== gen || !options.flows().includes(id) || !rows[id] || rows[id].mergeId === null) return
      const was = id in resolving
      if (isResolving(activity)) {
        resolving[id] = { done: activity.conflictProgress?.done ?? null, total: activity.conflictProgress?.total ?? null }
      } else {
        delete resolving[id]
        // The resolver ended or was stopped: the row may have moved on (review state, or gone).
        if (was) void readStatus()
      }
    } catch {
      // A failed read is no evidence the resolver ended: keep the mark, the poll asks again.
    } finally {
      aiReading.delete(id)
      if (aiAgain.delete(id) && !disposed) void readAi(id)
      schedulePoll()
    }
  }

  function schedulePoll() {
    if (timer !== null || disposed || Object.keys(resolving).length === 0) return
    timer = setTimeout(() => {
      timer = null
      for (const id of Object.keys(resolving)) void readAi(id)
    }, pollMs)
  }

  function stopPoll() {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  function onEvent(type: string, id: string) {
    if (!options.flows().includes(id)) return
    if (type === 'ai_invoke_started' || type === 'ai_invoke_finished') {
      // An AI run of a tab without any conflict is not read at all (chat, card work …).
      if (rows[id]) {
        if (rows[id].mergeId !== null) void readAi(id)
        // A run that ended on a conflict tab the poll did not know as resolving may still have changed the row.
        if (type === 'ai_invoke_finished' && !(id in resolving)) void readStatus()
      } else if (options.graphConflicts()[id]) void readStatus()
      return
    }
    void readStatus()
  }

  watch(options.projectId, (pid, _old, cleanup) => {
    gen += 1
    stopPoll()
    clear(rows)
    clear(resolving)
    if (!pid || !source) return
    cleanup(source.subscribe(pid, {
      events: TAB_CONFLICT_EVENTS,
      event: (frame) => onEvent(frame.type, frame.groupId ?? String(frame.payload.group_id ?? '')),
      resync: () => void readStatus(),
    }))
    void readStatus()
  }, { immediate: true })

  // A newly opened tab is read; a closed one only drops what was kept for it.
  watch(() => [...options.flows()], (now, before) => {
    const open = new Set(now)
    for (const id of Object.keys(rows)) if (!open.has(id)) delete rows[id]
    for (const id of Object.keys(resolving)) if (!open.has(id)) delete resolving[id]
    if (now.some((id) => !(before ?? []).includes(id))) void readStatus()
  })

  // The Graph saw a conflict appear or go (its own events, within seconds): ask Git what it is now.
  watch(() => {
    const graph = options.graphConflicts()
    return options.flows().filter((id) => graph[id] === true).join('\n')
  }, () => void readStatus())

  onScopeDispose(() => {
    disposed = true
    stopPoll()
  })

  return computed(() => {
    const marks: Record<string, FlowTabConflict> = {}
    const graph = options.graphConflicts()
    for (const id of options.flows()) {
      const row = rows[id]
      if (row) {
        const progress = resolving[id]
        if (progress && row.mergeId !== null) marks[id] = { state: 'resolving', done: progress.done, total: progress.total }
        else if (!row.review) marks[id] = { state: 'conflict' }
      } else if (graph[id] === true) marks[id] = { state: 'conflict' }
    }
    return marks
  })
}
