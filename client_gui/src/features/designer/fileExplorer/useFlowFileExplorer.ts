import { ref, type Ref } from 'vue'
import {
  useProjectStore,
  type FlowAiActivity,
  type FlowFileNode,
  type FlowRef,
  type ProjectGitStatus,
} from '@/features/project'
import { toStatusMap, type ChangeState } from './fileExplorerModel'

// flowgate.v02.0024 T0005 — data adapter between the Designer File Explorer and the
// features/project store. Nothing here talks to the server directly:
//   tree      -> loadFlowFileTree     (Flow worktree, untracked included)
//   Base/slot -> loadProjectGitStatus (project Base branch + writable slot)
//   changes   -> loadFlowChanges      (merge-base..worktree, '?' = new, compared against
//                                      the project Base passed as `base`)
//   AI        -> loadFlowAiActivity   (an AI run owns the Flow's group)
//
// Base branch contract (0024-D §5.2/§12): the comparison base is read from the project's
// Git status, never assumed — there is no default branch name anywhere in this feature.
// The change set is requested against that Base explicitly, so a Flow whose group is pinned
// to another work base is still listed against the project Base. When the Base cannot be
// established, or the answer does not name it (a server that ignores `base`), the change
// state becomes `unresolved` and the UI must not present it as a comparison result.

export function useFlowFileExplorer(projectId: Ref<string | null>, flow: Ref<FlowRef | null>) {
  const projectStore = useProjectStore()

  const nodes = ref<FlowFileNode[]>([])
  const commit = ref<string | null>(null)
  const loading = ref(false)
  // Blocking (nothing rendered yet) vs non-blocking (a refresh failed over a rendered tree).
  const error = ref(false)
  const refreshError = ref(false)
  const changes = ref<ChangeState>({ kind: 'idle' })
  const projectBaseBranch = ref<string | null>(null)
  const slotWritable = ref(false)
  // `aiActivityKnown=false` (the lookup failed) locks mutation just like a running AI does:
  // write access has to be proven for the current scope, never assumed.
  const aiBusy = ref(false)
  const aiActivityKnown = ref(false)

  // Which project+flow the rendered `nodes` belong to. A reload for a DIFFERENT scope must
  // not keep the previous scope's tree on failure (stale-tree rule).
  const renderedKey = ref<string | null>(null)
  let seq = 0

  const scopeKey = (pid: string, gid: string) => `${pid}:${gid}`

  // Fail closed: anything but an explicit writable slot for THIS group is readonly.
  function readSlot(status: ProjectGitStatus | null, gid: string) {
    const slot = status?.slots.find((s) => s.groupId === gid)
    slotWritable.value = slot?.writable === true
  }

  function readActivity(result: PromiseSettledResult<FlowAiActivity>) {
    aiActivityKnown.value = result.status === 'fulfilled'
    aiBusy.value = result.status === 'fulfilled' && result.value.active
  }

  function resetScope() {
    nodes.value = []
    commit.value = null
    renderedKey.value = null
    slotWritable.value = false
    aiBusy.value = false
    aiActivityKnown.value = false
    projectBaseBranch.value = null
  }

  async function loadChanges(
    pid: string,
    gid: string,
    statusResult: PromiseSettledResult<ProjectGitStatus | null>,
    stale: () => boolean,
  ) {
    if (changes.value.kind !== 'ready') changes.value = { kind: 'loading' }
    if (statusResult.status === 'rejected') {
      projectBaseBranch.value = null
      changes.value = { kind: 'unresolved', reason: 'base_unavailable', expected: null, actual: null }
      return
    }
    const projectBase = statusResult.value?.baseBranch ?? null
    projectBaseBranch.value = projectBase
    if (!projectBase) {
      changes.value = { kind: 'unresolved', reason: 'base_unconfigured', expected: null, actual: null }
      return
    }
    try {
      const set = await projectStore.loadFlowChanges(pid, gid, { base: projectBase })
      if (stale()) return
      const actual = set.baseBranch
      if (!actual) {
        changes.value = { kind: 'unresolved', reason: 'base_unknown', expected: projectBase, actual: null }
        return
      }
      // The project Base is the single comparison standard (D §12). A server that does not
      // know `base` answers against the group's own work base, so never trust an answer
      // that names another branch.
      if (actual !== projectBase) {
        changes.value = { kind: 'unresolved', reason: 'base_mismatch', expected: projectBase, actual }
        return
      }
      changes.value = { kind: 'ready', baseBranch: actual, statuses: toStatusMap(set.changes) }
    } catch {
      if (stale()) return
      changes.value = { kind: 'error' }
    }
  }

  async function reload() {
    const pid = projectId.value
    const gid = flow.value?.groupId ?? null
    const mySeq = ++seq
    const stale = () => mySeq !== seq || pid !== projectId.value || gid !== (flow.value?.groupId ?? null)
    if (!pid || !gid) {
      resetScope()
      changes.value = { kind: 'idle' }
      error.value = false
      refreshError.value = false
      loading.value = false
      return
    }
    const key = scopeKey(pid, gid)
    const silent = nodes.value.length > 0 && renderedKey.value === key
    if (!silent) {
      // A new scope inherits nothing from the previous one — least of all write access.
      resetScope()
      changes.value = { kind: 'loading' }
      loading.value = true
    }
    error.value = false

    const [treeResult, statusResult, activityResult] = await Promise.allSettled([
      projectStore.loadFlowFileTree(pid, gid),
      projectStore.loadProjectGitStatus(pid),
      projectStore.loadFlowAiActivity(gid),
    ])
    if (stale()) return
    readSlot(statusResult.status === 'fulfilled' ? statusResult.value : null, gid)
    readActivity(activityResult)
    if (treeResult.status === 'fulfilled') {
      nodes.value = treeResult.value.nodes
      commit.value = treeResult.value.commit
      renderedKey.value = key
      refreshError.value = false
    } else if (silent) {
      // Keep the rendered tree; report the failed refresh non-blockingly.
      refreshError.value = true
    } else {
      error.value = true
    }
    loading.value = false
    if (treeResult.status === 'rejected' && !silent) {
      changes.value = { kind: 'idle' }
      return
    }
    await loadChanges(pid, gid, statusResult, stale)
  }

  return {
    nodes, commit, loading, error, refreshError, changes,
    projectBaseBranch, slotWritable, aiBusy, aiActivityKnown, reload,
  }
}
