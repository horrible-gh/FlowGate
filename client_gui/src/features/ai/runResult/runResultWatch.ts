import { reactive } from 'vue'
import type { FlowCommandResult } from '@/features/project'

// flowgate.v02.0113 T#5 (0030-M §5, D 개정 §3.14-1, 7-1) — when the run result windows open. A [다음 단계] / [전체 실행]
// command the server took is watched (`arm`, with the run as this tab knew it before the command) every time its Flow's
// run is idle again (`observe`): the command's result is read (by its own `command_seq` once known), and only a command
// that ended completed (the run completed) or failed opens the 1st window, once. A command that ended waiting (a step
// boundary, a manual write wait, a branch waiting) opens nothing — the Designer's green progress line and the card states
// show it — but while the server says it may still go on (`continues`: a person's save or approval takes the same command
// on) it stays watched, so its real end (completed / failed) still opens the window. Its real boundaries end the watch:
// the next command (`arm` again), a Stop (`stopped`), a Time Machine or another command taking the run (`continues`
// false, or another run), a gone or refused command (flowgate.v02.0143 T#1: not a passing read failure), a closed Flow
// or another Project. AI calls are never counted for it.
//
// flowgate.v02.0117 T#4, flowgate.v02.0124 T#2 — a command that ended waiting for a person (the run idle
// `waiting_approval` / `needs_intervention` / `waiting_confirm`) is told (`tell`) for the Shell's existing toast: once per
// command and end (`runId`, `commandSeq`, `endSeq`, idle reason — the same result read again is not told again, a new
// command or a new end of the same command is), and only while it may still go on (`continues`). The words name the
// first card of the result's `blocking` (title, the server's own reason, `외 N`, how it is released); with none, the
// card of `nodes` that waits as before. A step boundary (`step_done`), the window's completed / failed and the reasons
// not observed (hold, no_targets, …) are never told.
//
// flowgate.v02.0143 T#1 (NR flowgate.v02.0143.0003 §4.2, §5-4·5·6, §7) — the watch keeps the latest run it saw and takes
// a result only while it is still the command's latest word:
// - every observation of a watched Flow's run (running / paused / stopping too) is kept (`latestSeq`, `latestStatus`); a
//   read still starts on an idle run only. A result is taken only for the watch's command (its run, and its
//   `commandSeq` once known). An ended result read while the same run went on again past its end (a non-idle
//   observation with a larger sequence than the result's `endSeq`, else than the sequence the read started at) is a
//   past result: no window and the watch stays — the next idle run is read again, and a failure still there is shown.
// - a read (the first, a retry, a recheck) starts only on the latest run the watch saw, idle (`isLatestIdle`): once the
//   run went on again (a non-idle observation past the idle run read) an older idle run is never read again — its
//   retry is held and the next idle run reads.
// - once the command is known, another run seen (whatever its status, a read going on or not) ends the watch and its
//   timer at once: a late answer of the old run opens nothing.
// - a failed read does not end the watch: a run's sequence counts as read only once a read answered and its answer
//   was taken (its run and command, and still its latest word), so the same sequence is read again. A passing failure
//   (network, 5xx, 408 / 429, no status) and an answer not taken for another run or command are read again a few times
//   (`RUN_RESULT_RETRY`, backing off); past that the watch is `stalled` and reads again only on a newer run or when its
//   run is read again (`recheck`: a reconnect's resync, the tab shown again) — never by itself. A gone command or run
//   (404) and a refused one (401 / 403) end the watch with no window; so do another Project (`clear`) and a closed Flow
//   (`forget`). Every retry timer goes with its watch.
// - an F5 brings the watches back (`persisted` / `restore`, kept in the Designer snapshot) with the results already
//   shown in each Flow (`runId`, `commandSeq`, `endSeq`, the latest few): a shown result is never shown again.

/** The run as this tab sees it: enough to tell a run idle again after the command. */
export interface RunResultRun {
  readonly runId: string
  readonly status: string
  readonly lastEventSeq?: number
}

interface Pending {
  /** The run and its last event as this tab knew them before the command. */
  readonly runId: string | null
  readonly afterSeq: number
  /** This tab's command once the server told it (its run and start sequence). */
  commandRunId: string | null
  commandSeq: number | null
  /** The run's last event whose result this watch took (flowgate.v02.0143 T#1: raised only once a read answered). */
  seenSeq: number
  loading: boolean
  /** A newer idle run seen while a read was going on. */
  next: RunResultRun | null
  /** flowgate.v02.0143 T#1: the latest run this watch observed, whatever its status. */
  latestRunId: string | null
  latestSeq: number
  latestStatus: string | null
  /** flowgate.v02.0143 T#1: the read going on (its run, the sequence it started at) and the largest sequence of a
   *  non-idle observation of that run since it started (the run went on again), else null. */
  readRunId: string | null
  readSeq: number
  resumedSeq: number | null
  /** flowgate.v02.0143 T#1: passing read failures (and answers not taken for another run or command) of one sequence
   *  in a row; once they ran out the watch waits for a newer run or a recheck (`stalled`, at `stalledSeq`). */
  failSeq: number
  failures: number
  stalled: boolean
  stalledSeq: number
}

/** flowgate.v02.0143 T#1: what an F5 brings back of a watch — its Flow, the command's run (before the command is known:
 *  the run as known before it), the command's start sequence once known and the run's last event before it. */
export interface RunResultWatchSnapshot {
  readonly flowId: string
  readonly runId: string | null
  readonly commandSeq: number | null
  readonly afterSeq: number
}

/** flowgate.v02.0143 T#1: the results shown in a Flow (`runResultKey`), the latest RUN_RESULT_SEEN_LIMIT. */
export interface RunResultSeenSnapshot {
  readonly flowId: string
  readonly keys: readonly string[]
}

export const RUN_RESULT_SEEN_LIMIT = 20

/** flowgate.v02.0143 T#1: a passing read failure is read again `limit` times, `delayMs(n)` after the n-th failure. */
export interface RunResultRetry {
  readonly limit: number
  readonly delayMs: (failures: number) => number
}

export const RUN_RESULT_RETRY: RunResultRetry = { limit: 3, delayMs: (failures) => 1000 * 2 ** (failures - 1) }

/**
 * flowgate.v02.0143 T#1: a failed command result read — `final` (the command or its run is gone: 404 `CommandNotFound`;
 * refused: 401 / 403) ends the watch; anything else (network, 5xx, 408 / 429, no status) is `passing`. Told by the
 * error's HTTP `status` and `network` mark, the way the Center session reads a failed read (`readFailure`).
 */
export function readFailureKind(error: unknown): 'final' | 'passing' {
  const mark = error as { status?: unknown; network?: unknown } | null
  if (mark?.network === true) return 'passing'
  const status = typeof mark?.status === 'number' ? mark.status : null
  return status === 401 || status === 403 || status === 404 ? 'final' : 'passing'
}

/** flowgate.v02.0143 T#1: the key of a shown result (its run, command and end). */
export function runResultKey(result: Pick<FlowCommandResult, 'runId' | 'commandSeq' | 'endSeq'>): string {
  return `${result.runId}\n${result.commandSeq}\n${result.endSeq ?? ''}`
}

export interface RunResultWatchDeps {
  /** The run's command result (the store's loader): its latest, or the one starting at `commandSeq`. */
  readonly load: (runId: string, commandSeq?: number) => Promise<FlowCommandResult>
  /** flowgate.v02.0117 T#4 / 0124 T#2: a command that ended waiting for a person (`isToldWait`), told once per command and end. */
  readonly tell?: (result: FlowCommandResult) => void
  /** flowgate.v02.0143 T#1: runs `run` after `ms` (a retry) and returns its cancel. Default: a real timer. */
  readonly schedule?: (run: () => void, ms: number) => () => void
  /** flowgate.v02.0143 T#1: how a passing read failure is read again (default `RUN_RESULT_RETRY`). */
  readonly retry?: RunResultRetry
}

function realTimer(run: () => void, ms: number): () => void {
  const id = setTimeout(run, ms)
  return () => clearTimeout(id)
}

export interface RunResultWindowsState {
  /** The result the windows show (null: both closed). */
  result: FlowCommandResult | null
  resultOpen: boolean
  detailOpen: boolean
  /** Grows with every result shown, so each opening starts a fresh window. */
  seq: number
}

export const SHOWN_STATES: readonly string[] = ['completed', 'failed']

/** flowgate.v02.0117 T#4 / 0124 T#2: the idle reasons a waiting command is told for — a person's waits. */
export const TOLD_WAIT_REASONS: readonly string[] = ['waiting_approval', 'needs_intervention', 'waiting_confirm']

/** flowgate.v02.0117 T#4: a command that ended waiting for a person and may still go on with a person's save or approval. */
export function isToldWait(result: FlowCommandResult): boolean {
  return result.ended && result.state === 'waiting' && result.continues && TOLD_WAIT_REASONS.includes(result.idleReason ?? '')
}

/**
 * flowgate.v02.0117 T#4: a told wait in words — the run's idle reason in the existing words (`run_idle_reasons`) and, when a
 * card of the command waits with a card state the Canvas names (a manual write wait, …), that card and its state.
 */
export function runWaitText(
  result: FlowCommandResult,
  t: (key: string, values?: Record<string, unknown>) => string,
  te: (key: string) => boolean,
): string {
  const head = t(`gui.designer.top.run_idle_reasons.${result.idleReason ?? ''}`)
  // flowgate.v02.0124 T#2 (D8): the first blocking card first — its title, the server's reason as it is, `외 N`, and how it
  // is released (the server's `release` code, when the words exist).
  const first = result.blocking[0]
  if (first) {
    const who = first.title ?? first.nodeKey
    let text = first.reason ? `${head} — ${who}: ${first.reason}` : `${head} — ${who}`
    if (result.blocking.length > 1) text += ` (${t('gui.designer.top.run_wait_more', { n: result.blocking.length - 1 })})`
    const release = first.release ? `gui.designer.top.run_wait_release.${first.release}` : null
    if (release && te(release)) text += ` · ${t(release)}`
    return text
  }
  const card = result.nodes.find((node) => node.outcome === 'waiting' && !!node.reason && te(`gui.designer.center.card_states.${node.reason}`))
  if (!card) return head
  const cardState = t(`gui.designer.center.card_states.${card.reason}`)
  return card.title ? `${head} — ${card.title}: ${cardState}` : `${head} — ${cardState}`
}

export function createRunResultWatch(deps: RunResultWatchDeps) {
  const state = reactive<RunResultWindowsState>({ result: null, resultOpen: false, detailOpen: false, seq: 0 })
  const pending = reactive<Record<string, Pending>>({})
  // flowgate.v02.0143 T#1: the results shown per Flow (runResultKey), the latest RUN_RESULT_SEEN_LIMIT — kept for an F5.
  const seen = reactive<Record<string, string[]>>({})
  // flowgate.v02.0124 T#2 (D9): the waits told already (run, command, end, idle reason): the same result read again is not
  // told again; a new command or a new end of the same command is.
  const told = new Set<string>()
  const schedule = deps.schedule ?? realTimer
  const retry = deps.retry ?? RUN_RESULT_RETRY
  // flowgate.v02.0143 T#1: each Flow's retry timer (one at most), cancelled with its watch.
  const timers = new Map<string, () => void>()
  let disposed = false

  function cancelTimer(flowId: string) {
    const cancel = timers.get(flowId)
    if (!cancel) return
    timers.delete(flowId)
    cancel()
  }

  function drop(flowId: string) {
    cancelTimer(flowId)
    delete pending[flowId]
  }

  function tellOnce(result: FlowCommandResult) {
    if (!deps.tell || !isToldWait(result)) return
    const key = `${result.runId}\n${result.commandSeq}\n${result.endSeq ?? ''}\n${result.idleReason ?? ''}`
    if (told.has(key)) return
    told.add(key)
    deps.tell(result)
  }

  function show(result: FlowCommandResult) {
    state.result = result
    state.resultOpen = true
    state.detailOpen = false
    state.seq += 1
  }

  function markShown(flowId: string, result: FlowCommandResult): boolean {
    const key = runResultKey(result)
    const keys = seen[flowId] ?? []
    if (keys.includes(key)) return false
    seen[flowId] = [...keys, key].slice(-RUN_RESULT_SEEN_LIMIT)
    return true
  }

  function freshPending(runId: string | null, afterSeq: number): Pending {
    return {
      runId, afterSeq, commandRunId: null, commandSeq: null, seenSeq: -1, loading: false, next: null,
      latestRunId: null, latestSeq: -1, latestStatus: null, readRunId: null, readSeq: -1, resumedSeq: null,
      failSeq: -1, failures: 0, stalled: false, stalledSeq: -1,
    }
  }

  /** A command of `flowId` the server took; `before` is its run as this tab knew it before the command. */
  function arm(flowId: string, before: RunResultRun | null) {
    cancelTimer(flowId)
    pending[flowId] = freshPending(before?.runId ?? null, before?.lastEventSeq ?? 0)
  }

  /** flowgate.v02.0143 T#1: the run as observed, whatever its status; a non-idle one during a read is the run going on. */
  function note(watch: Pending, run: RunResultRun, seq: number) {
    if (watch.latestRunId !== run.runId || seq >= watch.latestSeq) {
      watch.latestRunId = run.runId
      watch.latestSeq = seq
      watch.latestStatus = run.status
    }
    if (run.status === 'idle') return
    if (watch.loading && watch.readRunId === run.runId && seq > watch.readSeq) {
      watch.resumedSeq = Math.max(watch.resumedSeq ?? seq, seq)
    }
    // An idle run waiting for its read is older than this one: the run went on since.
    if (watch.next && watch.next.runId === run.runId && (watch.next.lastEventSeq ?? 0) < seq) watch.next = null
  }

  /**
   * flowgate.v02.0143 T#1 (TR rework P1): `run` at `seq` is still the latest run this watch saw, idle — the only run a
   * read (the first, a retry, a recheck) may start on. Once the run went on (a non-idle observation past it), another
   * run came or a newer idle run was seen, it is a past run: the next idle run reads instead.
   */
  function isLatestIdle(watch: Pending, run: RunResultRun, seq: number): boolean {
    return watch.latestRunId === run.runId && watch.latestStatus === 'idle' && watch.latestSeq <= seq
  }

  /** The Flow's run as this tab sees it now: an idle run after the armed command reads the command's result. */
  function observe(flowId: string, run: RunResultRun | null | undefined, force = false) {
    const watch = pending[flowId]
    if (disposed || !watch || !run) return
    const seq = run.lastEventSeq ?? 0
    note(watch, run, seq)
    // flowgate.v02.0143 T#1 (TR rework P2): once the command is known, another run (whatever its status, a read going on
    // or not) ends the watch and its timer at once — the command's run is gone; a late answer of it finds no watch.
    if (watch.commandSeq !== null && watch.commandRunId !== run.runId) {
      drop(flowId)
      return
    }
    if (run.status !== 'idle') {
      // flowgate.v02.0143 T#1 (TR rework P1): the run went on past the idle run a retry waits to read again (or another
      // run came): the retry is held — the next idle run reads.
      if (timers.has(flowId) && (run.runId !== watch.readRunId || seq > watch.failSeq)) cancelTimer(flowId)
      return
    }
    if (watch.loading) {
      watch.next = run
      return
    }
    if (watch.commandSeq === null) {
      if (watch.runId === run.runId && seq <= watch.afterSeq) return              // the run as it was before the command
    } else if (seq <= watch.seenSeq) {
      return                                                                      // nothing new since the last read
    }
    if (timers.has(flowId) && seq <= watch.failSeq) return                       // T#1: its retry reads it
    if (watch.stalled && seq <= watch.stalledSeq && !force) return                // T#1: a newer run or a recheck only
    if (!isLatestIdle(watch, run, seq)) return                                    // T#1: a past run (seen out of order)
    read(flowId, watch, run, seq, force)
  }

  function read(flowId: string, watch: Pending, run: RunResultRun, seq: number, forced: boolean) {
    cancelTimer(flowId)
    watch.loading = true
    watch.next = null
    watch.readRunId = run.runId
    watch.readSeq = seq
    watch.resumedSeq = null
    const load = watch.commandSeq === null ? deps.load(run.runId) : deps.load(run.runId, watch.commandSeq)
    load.then(
      (result) => {
        watch.loading = false
        if (pending[flowId] !== watch || disposed) return
        const resumed = watch.resumedSeq
        watch.resumedSeq = null
        const again = () => {
          const next = watch.next
          watch.next = null
          if (next) observe(flowId, next)
        }
        if (watch.commandSeq === null) {
          // An older command answered first: not this tab's command yet.
          if (watch.runId === result.runId && result.commandSeq <= watch.afterSeq) return again()
          if (result.runId !== run.runId) return missed(flowId, watch, run, seq, forced)  // T#1: not the run that was read
          // T#1 (TR rework P2): another run was seen while this one was read — its own read decides the command.
          if (watch.latestRunId !== run.runId) return again()
          watch.commandRunId = result.runId
          watch.commandSeq = result.commandSeq
        } else if (result.runId !== watch.commandRunId || result.commandSeq !== watch.commandSeq) {
          return missed(flowId, watch, run, seq, forced)                         // T#1: another command's answer
        }
        // flowgate.v02.0143 T#1: the run went on again past this end while it was read — a past result; the sequence
        // is not taken (TR rework P2), the next idle run reads.
        if (result.ended && resumed !== null && resumed > (result.endSeq ?? seq)) return again()
        // flowgate.v02.0143 T#1 (TR rework P2): the sequence counts as read only now that its answer was taken.
        watch.seenSeq = Math.max(watch.seenSeq, seq)
        watch.failSeq = -1
        watch.failures = 0
        watch.stalled = false
        if (!result.ended) return again()                                        // it goes on
        if (SHOWN_STATES.includes(result.state)) {
          drop(flowId)
          if (markShown(flowId, result)) show(result)                            // T#1: never a result shown already
          return
        }
        if (result.continues) {                                                  // waiting: no window, still watched
          tellOnce(result)                                                       // T#4: a person's wait, told once
          return again()
        }
        drop(flowId)                                                              // a real end with no window
      },
      (error) => {
        watch.loading = false
        if (pending[flowId] !== watch || disposed) return
        watch.resumedSeq = null
        // flowgate.v02.0143 T#1: a gone or refused command ends the watch; a passing failure keeps it.
        if (readFailureKind(error) === 'final') {
          drop(flowId)
          return
        }
        missed(flowId, watch, run, seq, forced)
      },
    )
  }

  /**
   * flowgate.v02.0143 T#1: the read of `run` at `seq` gave nothing to take — a passing failure, or (TR rework P2) an
   * answer of another run or command. The sequence stays unread and is read again: a newer run seen meanwhile at once;
   * else the same run a few times (`retry`, backing off) while it is still the latest idle run — past that, or after a
   * recheck, the watch is stalled (no timer of its own). A run that went on meanwhile holds it: the next idle run reads.
   */
  function missed(flowId: string, watch: Pending, run: RunResultRun, seq: number, forced: boolean) {
    if (watch.failSeq !== seq) {
      watch.failSeq = seq
      watch.failures = 0
    }
    watch.failures += 1
    const next = watch.next
    watch.next = null
    if (next && (next.lastEventSeq ?? 0) > seq) {                                // a newer run came meanwhile
      observe(flowId, next)
      return
    }
    if (!isLatestIdle(watch, run, seq)) return                                    // TR rework P1: the run went on
    if (forced || watch.failures > retry.limit) {                                // no timer of its own any more
      watch.stalled = true
      watch.stalledSeq = Math.max(watch.stalledSeq, seq)
      return
    }
    timers.set(flowId, schedule(() => {
      timers.delete(flowId)
      if (pending[flowId] !== watch || disposed || watch.loading) return
      // TR rework P1: the run went on (or another run came) while the retry waited — held for the next idle run.
      if (!isLatestIdle(watch, run, seq)) return
      read(flowId, watch, run, seq, false)
    }, retry.delayMs(watch.failures)))
  }

  /**
   * flowgate.v02.0143 T#1: the Flow's run was read again (a reconnect's resync, the tab shown again): a stalled watch reads
   * its run once more (no timer follows a failure of it); any other watch is observed as usual.
   */
  function recheck(flowId: string, run: RunResultRun | null | undefined) {
    const watch = pending[flowId]
    if (!watch || !run) return
    observe(flowId, run, watch.stalled && !watch.loading)
  }

  /** flowgate.v02.0143 T#1: what an F5 brings back — the watches and the results shown, per Flow. */
  function persisted(): { watches: RunResultWatchSnapshot[]; seen: RunResultSeenSnapshot[] } {
    return {
      watches: Object.entries(pending).map(([flowId, watch]) => ({
        flowId,
        runId: watch.commandSeq !== null ? watch.commandRunId : watch.runId,
        commandSeq: watch.commandSeq,
        afterSeq: watch.afterSeq,
      })),
      seen: Object.entries(seen).filter(([, keys]) => keys.length > 0).map(([flowId, keys]) => ({ flowId, keys: [...keys] })),
    }
  }

  /** flowgate.v02.0143 T#1: an F5 — the snapshot's watches armed again (a known command is read by its own sequence). */
  function restore(watches: readonly RunResultWatchSnapshot[], shown: readonly RunResultSeenSnapshot[]) {
    for (const entry of shown) {
      seen[entry.flowId] = [...new Set([...(seen[entry.flowId] ?? []), ...entry.keys])].slice(-RUN_RESULT_SEEN_LIMIT)
    }
    for (const entry of watches) {
      if (pending[entry.flowId]) continue                                       // a command of this tab since
      const watch = freshPending(entry.runId, entry.afterSeq)
      if (entry.commandSeq !== null && entry.runId !== null) {
        watch.commandRunId = entry.runId
        watch.commandSeq = entry.commandSeq
      }
      pending[entry.flowId] = watch
    }
  }

  return {
    state,
    pending,
    seen,
    arm,
    observe: (flowId: string, run: RunResultRun | null | undefined) => observe(flowId, run),
    recheck,
    persisted,
    restore,
    show,
    openDetail: () => {
      if (state.result) state.detailOpen = true
    },
    closeResult: () => {
      state.resultOpen = false
      if (!state.detailOpen) state.result = null
    },
    closeDetail: () => {
      state.detailOpen = false
      if (!state.resultOpen) state.result = null
    },
    /** The Flow's tab closed: its watch goes. */
    forget: (flowId: string) => {
      drop(flowId)
      delete seen[flowId]
    },
    /** Another Project / session: every watch and the windows go. */
    clear: () => {
      for (const flowId of Object.keys(pending)) drop(flowId)
      for (const flowId of Object.keys(seen)) delete seen[flowId]
      state.result = null
      state.resultOpen = false
      state.detailOpen = false
    },
    /** flowgate.v02.0143 T#1: the Shell goes — no retry runs after it. */
    dispose: () => {
      disposed = true
      for (const flowId of [...timers.keys()]) cancelTimer(flowId)
    },
  }
}

export type RunResultWatch = ReturnType<typeof createRunResultWatch>
