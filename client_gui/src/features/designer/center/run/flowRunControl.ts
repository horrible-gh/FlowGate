// flowgate.v02.0053 T#9 — the top band's run buttons on the server Graph run (P0008 §7 S5). Owned by the
// Center session, one state per open Flow; the Shell only sends 다음 단계 / 전체 실행 / 중지 here and shows
// what it reads back.
//
// - 전체 실행 (`all`): the run goes on through every card, each with its own card settings (T#2). A Flow
//   without a run starts one (the latest revision); a stopped run is resumed.
// - 다음 단계 (`next`): one step — the run starts (or resumes), opens the cards that are next and hands them
//   out, then stops at once. The open cards keep their work (stop keeps their tokens); nothing after them
//   opens until the next 다음 단계 / 전체 실행 resumes the run. On a run that is already going (started
//   elsewhere), 다음 단계 stops it so that only the cards open now finish.
// - 중지: stops a running run. While 다음 단계 / 전체 실행 is answered its button already shows ■ 중지, so a
//   중지 then is kept, not dropped: the run is stopped as soon as the start / resume answer names it, and
//   nothing is started or resumed at all when the 중지 came before that request went out.
// Before a start or a resume the edited draft is confirmed (`prepare`, TR0020 rev2). After every answer
// the Flow's Canvas is read again (`refresh`): the cards' colour dots are the server's node states. A
// refusal is kept as `error` (the server's code; the Shell turns it into a short toast) and the run is
// read again, so the buttons always show the server's run. No AI delegation engine here (0053 CH).
import type { FlowGraphSource, FlowRunError, FlowRunInfo } from '../graph/flowGraphSource'

export type FlowRunMode = 'next' | 'all'

export interface FlowRunFailure {
  /** The server's error code, or null (transport / unknown failure). */
  readonly code: string | null
  readonly message: string
  /** Grows with every refusal, so the same refusal twice is still told apart. */
  readonly seq: number
}

export interface FlowRunState {
  /** The Flow's running or stopped run as last read (a run that just completed until read again), or null. */
  run: FlowRunInfo | null
  /** What this tab is doing to the run now, or null. */
  action: FlowRunMode | 'stop' | null
  /** The button that set the run going (`all` once 전체 실행 started it; null = not known here). */
  mode: FlowRunMode | null
  error: FlowRunFailure | null
}

export interface FlowRunControlDeps {
  readonly source: FlowGraphSource
  readonly projectId: () => string | null
  /** Whether the Flow is still open in this project (a late answer for a closed tab is dropped). */
  readonly isOpen: (projectId: string, flowId: string) => boolean
  /** Confirms the edited draft first; whether the run may go (FlowCenter `prepareRun`). */
  readonly prepare: (flowId: string) => boolean | Promise<boolean>
  /** Reads the Flow's Canvas again in the background. */
  readonly refresh: (flowId: string) => void
}

export interface FlowRunControl {
  /** The source can run Flows (a sample source cannot). */
  readonly available: boolean
  /** Reads the Flow's current run (after a Canvas read, on a run event). A failed read changes nothing. */
  read(flowId: string): void
  /** 다음 단계 / 전체 실행. Resolves whether the run went. */
  run(flowId: string, mode: FlowRunMode): Promise<boolean>
  /**
   * 중지. Resolves whether the run stopped. Pressed while 다음 단계 / 전체 실행 is answered, it is kept and
   * resolves once that answer is in: whether the run is stopped (or was never started / resumed).
   */
  stop(flowId: string): Promise<boolean>
  forget(flowId: string): void
  clear(): void
}

export const createFlowRunState = (): FlowRunState => ({ run: null, action: null, mode: null, error: null })

export function createFlowRunControl(deps: FlowRunControlDeps, runs: Record<string, FlowRunState>): FlowRunControl {
  const { source } = deps
  const available = !!(source.loadRun && source.startRun && source.stopRun && source.resumeRun)
  // Latest read per Flow: an older read answering later is dropped, and an action outdates every read.
  const reads = new Map<string, number>()
  // The 다음 단계 / 전체 실행 being answered per Flow, and the Flows whose 중지 came meanwhile (kept until
  // the run is known, then sent; dropped with the action).
  const going = new Map<string, Promise<boolean>>()
  const stopWanted = new Set<string>()
  let seq = 0
  let failures = 0

  // Read back through `runs` (a reactive record): the object assigned is the raw one, its proxy is what changes show.
  const stateOf = (flowId: string): FlowRunState => {
    if (!runs[flowId]) runs[flowId] = createFlowRunState()
    return runs[flowId]
  }

  function read(flowId: string) {
    const projectId = deps.projectId()
    if (!available || projectId === null || runs[flowId]?.action) return
    const token = ++seq
    reads.set(flowId, token)
    source.loadRun!(projectId, flowId).then(
      (run) => {
        if (reads.get(flowId) !== token || !deps.isOpen(projectId, flowId)) return
        reads.delete(flowId)
        const state = stateOf(flowId)
        if (state.action) return
        state.run = run
        if (run?.status !== 'running') state.mode = null
      },
      () => {
        if (reads.get(flowId) === token) reads.delete(flowId)
      },
    )
  }

  /** Runs one action; a refusal is kept and the run read again. */
  async function act(flowId: string, action: FlowRunState['action'], body: (projectId: string, state: FlowRunState) => Promise<boolean>) {
    const projectId = deps.projectId()
    if (!available || projectId === null) return false
    const state = stateOf(flowId)
    if (state.action) return false
    state.action = action
    state.error = null
    reads.delete(flowId)
    let ok = false
    try {
      ok = await body(projectId, state)
    } catch (error) {
      if (deps.isOpen(projectId, flowId)) {
        const code = (error as FlowRunError | null)?.code ?? null
        state.error = { code: typeof code === 'string' ? code : null, message: error instanceof Error ? error.message : '', seq: ++failures }
      }
    } finally {
      state.action = null
      stopWanted.delete(flowId)
    }
    if (!deps.isOpen(projectId, flowId)) return false
    if (!ok) read(flowId)
    deps.refresh(flowId)
    return ok
  }

  function run(flowId: string, mode: FlowRunMode): Promise<boolean> {
    const busy = !!runs[flowId]?.action
    const done = act(flowId, mode, async (projectId, state) => {
      if (!(await deps.prepare(flowId))) return false
      // The run as the server has it now: a stopped run is resumed, never started twice.
      const current = await source.loadRun!(projectId, flowId)
      let next: FlowRunInfo
      if (current?.status === 'running') {
        // Already going: 전체 실행 keeps it going; 다음 단계 lets the open cards finish and opens nothing more.
        next = current
      } else if (stopWanted.has(flowId)) {
        // 중지 came before the start / resume went out: nothing starts.
        state.run = current
        state.mode = null
        return false
      } else {
        next = current?.status === 'stopped'
          ? await source.resumeRun!(projectId, current.runId)
          : await source.startRun!(projectId, flowId)
      }
      state.run = next
      // 다음 단계 stops at once; so does a 중지 pressed while this was answered, now that the run is known.
      if ((mode === 'next' || stopWanted.has(flowId)) && next.status === 'running') {
        next = await source.stopRun!(projectId, next.runId)
        state.run = next
      }
      state.mode = next.status === 'running' ? mode : null
      return true
    })
    if (busy || runs[flowId]?.action !== mode) return done
    going.set(flowId, done)
    void done.then(() => {
      if (going.get(flowId) === done) going.delete(flowId)
    })
    return done
  }

  function stop(flowId: string): Promise<boolean> {
    const action = runs[flowId]?.action
    const answering = action === 'next' || action === 'all' ? going.get(flowId) : undefined
    if (answering) {
      // The button shows ■ 중지 while 다음 단계 / 전체 실행 is answered: the 중지 is kept and sent by it.
      stopWanted.add(flowId)
      return answering.then(() => {
        const state = runs[flowId]
        return !!state && state.error === null && state.run?.status !== 'running'
      })
    }
    return act(flowId, 'stop', async (projectId, state) => {
      const runId = state.run?.status === 'running' ? state.run.runId : (await source.loadRun!(projectId, flowId))?.runId
      if (!runId) return false
      state.run = await source.stopRun!(projectId, runId)
      state.mode = null
      return true
    })
  }

  return {
    available,
    read,
    run,
    stop,
    forget: (flowId) => {
      reads.delete(flowId)
      going.delete(flowId)
      stopWanted.delete(flowId)
      delete runs[flowId]
    },
    clear: () => {
      reads.clear()
      going.clear()
      stopWanted.clear()
      for (const flowId of Object.keys(runs)) delete runs[flowId]
    },
  }
}
