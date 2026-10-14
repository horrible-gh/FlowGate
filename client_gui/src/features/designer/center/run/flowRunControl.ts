// flowgate.v02.0053 T#9 — the top band's run buttons on the server Graph run (P0008 §7 S5). Owned by the
// Center session, one state per open Flow; the Shell only sends the run commands here and shows what it reads back.
//
// flowgate.v02.0061 T#14 — one run per Flow (`running` | `paused` | `stopping` | `idle`) and one server call per
// button (R, 0002-CH turn 8 §2.3):
// - 다음 단계 (`next`) / 전체 실행 (`all`): the T#2 step / full command (`startRun` with `step` / `full`). The server
//   starts a run of the latest revision or lets the Flow's idle run go on; a step opens the run candidates of the
//   moment and the run settles by itself — nothing is stopped from here afterwards. Only an idle Flow (or one with no
//   run) takes a command, and only then is the edited draft confirmed first (`prepare`, TR0020 rev2).
// - 일시정지 (`pause`): `/pause` (running -> paused). 재개 (`resume`): `/resume`, the paused run's own command.
// - 중지 (`stop`): T#7 Stop (`/stop`): the run is stopping at once and idle (stopped) once its AI processes ended.
// One action per Flow at a time (`action`); the buttons are off meanwhile, so nothing is queued behind it. After every
// answer the Flow's Canvas is read again (`refresh`): the cards' colour dots and the band are the server's. A refusal
// is kept as `error` (the server's code and message; the Shell turns it into a short toast) and the run is read again,
// so the buttons always show the server's run. No AI delegation engine here (0053 CH).
//
// flowgate.v02.0056 F2-1 — a card's [승인] on the Canvas (O1 0005-TR §4-1) goes through here too: it approves
// the card of the Flow's run (read first when this tab does not know it), keeps the run it answers (the band's
// "멈춤 · 다음 카드 N개 대기" follows it) and reads the Canvas again. One approval per Flow at a time
// (`approving`, the card's key); it never touches `action`, so the run buttons stay as they are. A refusal is
// kept as `error` with kind `approval` (the Shell words it by cardStateView's grouping, with the server's own
// message) and the run is read again. What the refusal names in its params (the refused document, why, the
// documents approved before it) is kept with it (`refusal`), so the Shell's toast can say it too.
//
// flowgate.v02.0061 TR0031 rev1 — whether the run status is confirmed (`readState`): a run read going on (`reading`) or
// failed (`failed`), and a Flow not read yet (`unread`), are not "no run": the Center session then keeps the run lock
// (its Canvas read's run status, or the run last known locked) and lets no command, edit or source mode change go until
// it is confirmed (`read`: a run read or a command's answer told it; `run` null = no run).
import { cardApprovalFailureView, cardApprovalRefusalOf, type CardApprovalFailureView, type CardApprovalRefusal } from '../graph/cardStateView'
import type { FlowGraphSource, FlowRunError, FlowRunInfo } from '../graph/flowGraphSource'
import { isRunLocked } from './flowRunLock'

export type FlowRunMode = 'next' | 'all'

/** flowgate.v02.0061 T#14: what this tab is doing to the run — a command, Pause, Resume or Stop. */
export type FlowRunAction = FlowRunMode | 'pause' | 'resume' | 'stop'

export interface FlowRunFailure {
  /** What was refused: a run button (`run`) or a card's [승인] (`approval`, flowgate.v02.0056 F2-1). */
  readonly kind: 'run' | 'approval'
  /** The server's error code, or null (transport / unknown failure). */
  readonly code: string | null
  /** The HTTP status, or null (no answer). */
  readonly status: number | null
  readonly message: string
  /** flowgate.v02.0056 F2-1: how a refused [승인] is worded and handled (cardStateView), else null. */
  readonly approval: CardApprovalFailureView | null
  /** flowgate.v02.0056 F2-1: what a refused [승인] names in its params (cardApprovalRefusalOf), else null. */
  readonly refusal: CardApprovalRefusal | null
  /** Grows with every refusal, so the same refusal twice is still told apart. */
  readonly seq: number
}

/** flowgate.v02.0061 TR0031 rev1: whether `run` is confirmed — `read` only; the others tell no run status. */
export type FlowRunReadState = 'unread' | 'reading' | 'read' | 'failed'

export interface FlowRunState {
  /** The Flow's run as last read (flowgate.v02.0061 T#14: running / paused / stopping / idle), or null. */
  run: FlowRunInfo | null
  /** flowgate.v02.0061 TR0031 rev1: whether `run` is the confirmed run (`read`) — null then means no run. */
  readState: FlowRunReadState
  /** What this tab is doing to the run now, or null. */
  action: FlowRunAction | null
  /** flowgate.v02.0056 F2-1: the card whose [승인] is being answered (its node key), or null. */
  approving: string | null
  error: FlowRunFailure | null
}

export interface FlowRunControlDeps {
  readonly source: FlowGraphSource
  readonly projectId: () => string | null
  /** Whether the Flow is still open in this project (a late answer for a closed tab is dropped). */
  readonly isOpen: (projectId: string, flowId: string) => boolean
  /** Confirms the edited draft first (an idle Flow only); whether the command may go (FlowCenter `prepareRun`). */
  readonly prepare: (flowId: string) => boolean | Promise<boolean>
  /** Reads the Flow's Canvas again in the background. */
  readonly refresh: (flowId: string) => void
}

export interface FlowRunControl {
  /** The source can run Flows (a sample source cannot). */
  readonly available: boolean
  /** flowgate.v02.0056 F2-1: the source can also approve a card of a run. */
  readonly canApprove: boolean
  /** flowgate.v02.0061 T#14: the source can pause a run. */
  readonly canPause: boolean
  /**
   * Reads the Flow's current run (after a Canvas read, on a run event). A failed read keeps the run last read but marks
   * it unconfirmed (`readState` `failed`).
   */
  read(flowId: string): void
  /** 다음 단계 / 전체 실행: one step / full command on an idle Flow. Resolves whether the server took it. */
  run(flowId: string, mode: FlowRunMode): Promise<boolean>
  /** flowgate.v02.0061 T#14 일시정지: running -> paused. Resolves whether it paused. */
  pause(flowId: string): Promise<boolean>
  /** flowgate.v02.0061 T#14 재개: the paused run goes on with its own command. Resolves whether it went on. */
  resume(flowId: string): Promise<boolean>
  /** 중지 (T#7 Stop) of a running or paused run. Resolves whether the server took it (stopping, or idle at once). */
  stop(flowId: string): Promise<boolean>
  /**
   * flowgate.v02.0056 F2-1: approves the card `nodeKey` of the Flow's run; `resultDocId` is the result the Canvas
   * showed. Resolves whether it was approved. Ignored while another approval of the Flow is answered.
   */
  approve(flowId: string, nodeKey: string, resultDocId: string | null): Promise<boolean>
  forget(flowId: string): void
  clear(): void
}

export const createFlowRunState = (): FlowRunState => ({ run: null, readState: 'unread', action: null, approving: null, error: null })

/** A refused request as the state keeps it (the server's code and status, its message as sent, its params' names). */
function failureOf(kind: FlowRunFailure['kind'], error: unknown, seq: number): FlowRunFailure {
  const refused = error as FlowRunError | null
  const code = typeof refused?.code === 'string' ? refused.code : null
  const status = typeof refused?.status === 'number' ? refused.status : null
  return {
    kind,
    code,
    status,
    message: error instanceof Error ? error.message : '',
    approval: kind === 'approval' ? cardApprovalFailureView(code, status, code === null && status === null) : null,
    refusal: kind === 'approval' ? cardApprovalRefusalOf(refused?.params) : null,
    seq,
  }
}

export function createFlowRunControl(deps: FlowRunControlDeps, runs: Record<string, FlowRunState>): FlowRunControl {
  const { source } = deps
  const available = !!(source.loadRun && source.startRun && source.stopRun && source.resumeRun)
  // Latest read per Flow: an older read answering later is dropped, and an action outdates every read.
  const reads = new Map<string, number>()
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
    stateOf(flowId).readState = 'reading'
    source.loadRun!(projectId, flowId).then(
      (run) => {
        if (reads.get(flowId) !== token || !deps.isOpen(projectId, flowId)) return
        reads.delete(flowId)
        const state = stateOf(flowId)
        if (state.action) return
        state.run = run
        state.readState = 'read'
      },
      () => {
        if (reads.get(flowId) !== token) return
        reads.delete(flowId)
        // Not "no run": the run stays unconfirmed (the session keeps its lock) until a later read answers.
        if (deps.isOpen(projectId, flowId) && runs[flowId]) runs[flowId].readState = 'failed'
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
      if (deps.isOpen(projectId, flowId)) state.error = failureOf('run', error, ++failures)
    } finally {
      state.action = null
    }
    if (!deps.isOpen(projectId, flowId)) return false
    if (!ok) read(flowId)
    deps.refresh(flowId)
    return ok
  }

  /** The run this tab knows, else the server's current one (null: none). */
  async function runIdOf(projectId: string, flowId: string, state: FlowRunState): Promise<string | null> {
    return state.run?.runId ?? (await source.loadRun!(projectId, flowId))?.runId ?? null
  }

  function run(flowId: string, mode: FlowRunMode): Promise<boolean> {
    return act(flowId, mode, async (projectId, state) => {
      // A locked run takes no command (its buttons are 일시정지 / 재개 / 중지); the draft is confirmed on an idle Flow only.
      if (isRunLocked(state.run)) return false
      if (!(await deps.prepare(flowId))) return false
      // One server call: the step / full command. Nothing is stopped from here after it (T#2 settles the step).
      state.run = await source.startRun!(projectId, flowId, mode === 'next' ? 'step' : 'full')
      state.readState = 'read'
      return true
    })
  }

  /** Pause / resume / stop of the Flow's run: one server call on the run this tab knows (or the current one). */
  function control(flowId: string, action: 'pause' | 'resume' | 'stop', call: (projectId: string, runId: string) => Promise<FlowRunInfo>) {
    return act(flowId, action, async (projectId, state) => {
      const runId = await runIdOf(projectId, flowId, state)
      if (!runId) return false
      state.run = await call(projectId, runId)
      state.readState = 'read'
      return true
    })
  }

  function pause(flowId: string): Promise<boolean> {
    if (!source.pauseRun) return Promise.resolve(false)
    return control(flowId, 'pause', (projectId, runId) => source.pauseRun!(projectId, runId))
  }

  function resume(flowId: string): Promise<boolean> {
    return control(flowId, 'resume', (projectId, runId) => source.resumeRun!(projectId, runId))
  }

  function stop(flowId: string): Promise<boolean> {
    return control(flowId, 'stop', (projectId, runId) => source.stopRun!(projectId, runId))
  }

  async function approve(flowId: string, nodeKey: string, resultDocId: string | null): Promise<boolean> {
    const projectId = deps.projectId()
    if (!available || !source.approveNode || projectId === null) return false
    const state = stateOf(flowId)
    if (state.approving !== null) return false
    state.approving = nodeKey
    state.error = null
    reads.delete(flowId)
    let ok = false
    try {
      // The run the card belongs to: the Flow's one run (read when this tab does not know it).
      const runId = await runIdOf(projectId, flowId, state)
      if (runId === null) throw Object.assign(new Error(''), { code: 'run_not_active' })
      const next = await source.approveNode(projectId, runId, nodeKey, resultDocId)
      if (deps.isOpen(projectId, flowId) && !state.action) {
        state.run = next
        state.readState = 'read'
      }
      ok = true
    } catch (error) {
      if (deps.isOpen(projectId, flowId)) state.error = failureOf('approval', error, ++failures)
    } finally {
      state.approving = null
    }
    if (!deps.isOpen(projectId, flowId)) return false
    if (!ok) read(flowId)
    deps.refresh(flowId)
    return ok
  }

  return {
    available,
    canApprove: available && !!source.approveNode,
    canPause: available && !!source.pauseRun,
    read,
    run,
    pause,
    resume,
    stop,
    approve,
    forget: (flowId) => {
      reads.delete(flowId)
      delete runs[flowId]
    },
    clear: () => {
      reads.clear()
      for (const flowId of Object.keys(runs)) delete runs[flowId]
    },
  }
}
