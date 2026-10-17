// flowgate.v02.0066 — Center Diff: the single owner of the one group file diff on screen (0066 D#2
// §2-2, §3-10), not a Pinia store. It opens only worktree intents of the current project and Flow
// group, reads them through the group diff loader (GET .../groups/{gid}/diff) and drops any answer
// that is not for the latest request of the diff still open.
// flowgate.v02.0067 T#4 — it is the detail of the Git modeless `Diff` tab (0067-R §6), no longer a
// Designer Center screen: DesignerGitPanel owns it for its lifetime and hands it the GitSource read
// (`worktree.loadFileDiff`).
import { reactive, readonly, type DeepReadonly } from 'vue'
import type { FlowFileDiff } from '@/features/project'
import { gitErrorOf } from './api'
import type { CenterDiffError, CenterDiffLoad, CenterDiffTarget, GitCenterDiffRefresh, GitDiffIntent } from './types'

/** Reads one group file diff; `compareBase` null = the work base floor (no `base` sent). */
export type CenterDiffLoader = (
  projectId: string,
  groupId: string,
  path: string,
  compareBase: string | null,
) => Promise<FlowFileDiff>

export interface CenterDiffState {
  // The panel's project and active Flow group; an intent of another one is not opened.
  projectId: string | null
  groupId: string | null
  open: CenterDiffTarget | null
  load: CenterDiffLoad
  diff: FlowFileDiff | null
  error: CenterDiffError | null
}

export interface GitCenterDiffSession {
  readonly state: DeepReadonly<CenterDiffState>
  /** The panel's work target; a change of either closes the diff. */
  setContext(projectId: string | null, groupId: string | null): void
  /** An `open-diff` intent: only origin worktree / diffSource group of the current context opens. */
  open(intent: GitDiffIntent): void
  reload(): void
  /** `git-center-diff-refresh`: the same group reads again, or a target comparison diff closes. */
  refresh(request: GitCenterDiffRefresh): void
  close(): void
  dispose(): void
}

export interface GitCenterDiffOptions {
  loadFileDiff: CenterDiffLoader
}

const FLOOR_CODES: readonly string[] = ['group_work_base_unverified', 'group_work_base_diverged']

/** Which line a failed read shows (0066 D#2 §3-10(3)); the server message is never kept. */
export function centerDiffError(error: unknown): CenterDiffError {
  const err = gitErrorOf(error)
  const code = err.code
  if (err.network) return { kind: 'network', code: null }
  if (code && FLOOR_CODES.includes(code)) return { kind: 'floor', code }
  if (err.status === 404) return { kind: 'not_found', code }
  if (err.status === 403 || code === 'forbidden') return { kind: 'forbidden', code }
  if (err.status === 400 || err.status === 422) return { kind: 'compare', code }
  return { kind: 'failed', code: code ?? (err.status !== null ? String(err.status) : null) }
}

export function createGitCenterDiff(options: GitCenterDiffOptions): GitCenterDiffSession {
  const { loadFileDiff } = options

  const state = reactive<CenterDiffState>({
    projectId: null,
    groupId: null,
    open: null,
    load: 'idle',
    diff: null,
    error: null,
  })
  // Numbers reads; only the latest one may apply, and a close voids every one before it.
  let seq = 0
  let disposed = false

  function close(): void {
    seq += 1
    state.open = null
    state.load = 'idle'
    state.diff = null
    state.error = null
  }

  async function read(): Promise<void> {
    const target = state.open
    if (!target || disposed) return
    const mySeq = ++seq
    state.load = 'loading'
    state.error = null
    try {
      const diff = await loadFileDiff(target.projectId, target.groupId, target.path, target.compareBase)
      if (mySeq !== seq || disposed) return
      state.diff = diff
      state.load = 'ready'
    } catch (error) {
      if (mySeq !== seq || disposed) return
      state.diff = null
      state.error = centerDiffError(error)
      state.load = 'error'
    }
  }

  function setContext(projectId: string | null, groupId: string | null): void {
    if (projectId === state.projectId && groupId === state.groupId) return
    state.projectId = projectId
    state.groupId = groupId
    close()
  }

  function open(intent: GitDiffIntent): void {
    // 0033 intents (dirty / conflict / review) open nothing here (0066 D#2 §[DEFERRED]).
    if (disposed || intent.origin !== 'worktree' || intent.diffSource !== 'group') return
    if (!intent.groupId || intent.projectId !== state.projectId || intent.groupId !== state.groupId) return
    const current = state.open
    const target: CenterDiffTarget = {
      projectId: intent.projectId,
      groupId: intent.groupId,
      path: intent.path,
      compareBase: intent.compareBase ?? null,
      status: intent.status,
    }
    // Another file replaces the open one; the same one simply reads again.
    if (!current || current.path !== target.path || current.compareBase !== target.compareBase) state.diff = null
    state.open = target
    void read()
  }

  function reload(): void {
    void read()
  }

  function refresh(request: GitCenterDiffRefresh): void {
    const current = state.open
    if (!current || current.groupId !== request.groupId) return
    if (request.closeCompare && current.compareBase !== null) return close()
    void read()
  }

  function dispose(): void {
    disposed = true
    seq += 1
  }

  return {
    state: readonly(state) as DeepReadonly<CenterDiffState>,
    setContext,
    open,
    reload,
    refresh,
    close,
    dispose,
  }
}
