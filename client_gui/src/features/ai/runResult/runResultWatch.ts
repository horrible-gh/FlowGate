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
// false, or another run), a read failure, a closed Flow or another Project. AI calls are never counted for it.
//
// flowgate.v02.0117 T#4 — a command that ended waiting for a person (the run idle `waiting_approval`; T#1 TR §5: also the
// manual write wait of a role set to 사용하지 않음) is told once (`tell`) for the Shell's existing toast: once per run and
// reason, and only while it may still go on (`continues`). A step boundary (`step_done`), the window's completed / failed
// and the reasons T#1 did not observe (hold, no_targets, …) are never told.

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
  /** The run's last event this watch read the result for. */
  seenSeq: number
  loading: boolean
  /** A newer run state seen while a read was going on. */
  next: RunResultRun | null
}

export interface RunResultWatchDeps {
  /** The run's command result (the store's loader): its latest, or the one starting at `commandSeq`. */
  readonly load: (runId: string, commandSeq?: number) => Promise<FlowCommandResult>
  /** flowgate.v02.0117 T#4: a command that ended waiting for a person (`isToldWait`), told once per run and reason. */
  readonly tell?: (result: FlowCommandResult) => void
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

/** flowgate.v02.0117 T#4: the idle reasons a waiting command is told for — T#1 TR §5's observed waits for a person. */
export const TOLD_WAIT_REASONS: readonly string[] = ['waiting_approval']

/** flowgate.v02.0117 T#4: a command that ended waiting for a person and may still go on with a person's save or approval. */
export function isToldWait(result: FlowCommandResult): boolean {
  return result.ended && result.state === 'waiting' && result.continues && TOLD_WAIT_REASONS.includes(result.idleReason ?? '')
}

/**
 * flowgate.v02.0117 T#4: a told wait in words — the run's idle reason in the existing words (`run_idle_reasons`) and, when a
 * card of the command waits with a card state the Canvas names (a manual write wait, …), that card and its state.
 */
export function runWaitText(result: FlowCommandResult, t: (key: string) => string, te: (key: string) => boolean): string {
  const head = t(`gui.designer.top.run_idle_reasons.${result.idleReason ?? ''}`)
  const card = result.nodes.find((node) => node.outcome === 'waiting' && !!node.reason && te(`gui.designer.center.card_states.${node.reason}`))
  if (!card) return head
  const cardState = t(`gui.designer.center.card_states.${card.reason}`)
  return card.title ? `${head} — ${card.title}: ${cardState}` : `${head} — ${cardState}`
}

export function createRunResultWatch(deps: RunResultWatchDeps) {
  const state = reactive<RunResultWindowsState>({ result: null, resultOpen: false, detailOpen: false, seq: 0 })
  const pending = reactive<Record<string, Pending>>({})
  // flowgate.v02.0117 T#4: the waits told already (run and reason): the same wait of the same run is never told again.
  const told = new Set<string>()

  function tellOnce(result: FlowCommandResult) {
    if (!deps.tell || !isToldWait(result)) return
    const key = `${result.runId}\n${result.idleReason}`
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

  /** A command of `flowId` the server took; `before` is its run as this tab knew it before the command. */
  function arm(flowId: string, before: RunResultRun | null) {
    pending[flowId] = {
      runId: before?.runId ?? null, afterSeq: before?.lastEventSeq ?? 0, commandRunId: null, commandSeq: null,
      seenSeq: -1, loading: false, next: null,
    }
  }

  /** The Flow's run as this tab sees it now: an idle run after the armed command reads the command's result. */
  function observe(flowId: string, run: RunResultRun | null | undefined) {
    const watch = pending[flowId]
    if (!watch || !run || run.status !== 'idle') return
    if (watch.loading) {
      watch.next = run
      return
    }
    const seq = run.lastEventSeq ?? 0
    if (watch.commandSeq === null) {
      if (watch.runId === run.runId && seq <= watch.afterSeq) return              // the run as it was before the command
    } else if (watch.commandRunId !== run.runId) {
      delete pending[flowId]                                                      // the command's run is gone
      return
    } else if (seq <= watch.seenSeq) {
      return                                                                      // nothing new since the last read
    }
    watch.loading = true
    watch.next = null
    watch.seenSeq = seq
    const read = watch.commandSeq === null ? deps.load(run.runId) : deps.load(run.runId, watch.commandSeq)
    read.then(
      (result) => {
        watch.loading = false
        if (pending[flowId] !== watch) return
        const again = () => {
          const next = watch.next
          watch.next = null
          if (next) observe(flowId, next)
        }
        if (watch.commandSeq === null) {
          // An older command answered first: not this tab's command yet.
          if (watch.runId === result.runId && result.commandSeq <= watch.afterSeq) return again()
          watch.commandRunId = result.runId
          watch.commandSeq = result.commandSeq
        }
        if (!result.ended) return again()                                        // it goes on
        if (SHOWN_STATES.includes(result.state)) {
          delete pending[flowId]
          show(result)
          return
        }
        if (result.continues) {                                                  // waiting: no window, still watched
          tellOnce(result)                                                       // T#4: a person's wait, told once
          return again()
        }
        delete pending[flowId]                                                    // a real end with no window
      },
      () => {
        watch.loading = false
        if (pending[flowId] === watch) delete pending[flowId]
      },
    )
  }

  return {
    state,
    pending,
    arm,
    observe,
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
      delete pending[flowId]
    },
    /** Another Project / session: every watch and the windows go. */
    clear: () => {
      for (const flowId of Object.keys(pending)) delete pending[flowId]
      state.result = null
      state.resultOpen = false
      state.detailOpen = false
    },
  }
}

export type RunResultWatch = ReturnType<typeof createRunResultWatch>
