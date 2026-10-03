import { computed, ref, type Ref } from 'vue'
import { useExplorerStore, type FileNode, type GitProjectStatus } from '../../../stores/explorer'
import { useAiInvokeRunsStore } from '../../../stores/aiInvokeRuns'
import { toStatusMap, type ChangeState, type DesignerFlowScope } from './fileExplorerModel'

// flowgate.v02.0024 T0003 — data adapter between the Designer File Explorer and the
// EXISTING explorer store contracts. Nothing here talks to the server directly:
//   tree      -> explorerStore.fetchGroupBranchTree   (Flow worktree, untracked included)
//   changes   -> explorerStore.fetchGroupBranchChangeSet (merge-base..worktree, '?' = new,
//                compared against the project Base passed as `base`)
//   Base/slot -> explorerStore.fetchGitStatus          (project Base branch + writable slot)
// so caching, retry and in-flight coalescing stay exactly what the Dashboard explorer uses.
//
// Base branch contract (0024-D §5.2/§12): the comparison base is read from the project's
// Git status, never assumed. No `|| 'main'` anywhere. The change set is requested
// against that Base explicitly, so a Flow whose group is pinned to another work base
// (e.g. project Base `main`, group forked from `v0.2`) is still listed against the
// project Base. When the Base cannot be established, or the answer does not name it (a
// server that ignores `base`), the change state becomes `unresolved` and the UI must not
// present it as a comparison result.

export function useFlowFileExplorer(
  projectId: Ref<string | null>,
  flow: Ref<DesignerFlowScope | null>,
) {
  const explorerStore = useExplorerStore()
  const aiInvokeRunsStore = useAiInvokeRunsStore()

  const nodes = ref<FileNode[]>([])
  const commit = ref<string | null>(null)
  const loading = ref(false)
  // Blocking (nothing rendered yet) vs non-blocking (a refresh failed over a rendered
  // tree) — the same split FileExplorer/GroupExplorer use (0449 T0004 item 1.2).
  const error = ref(false)
  const refreshError = ref(false)
  const changes = ref<ChangeState>({ kind: 'idle' })
  const projectBaseBranch = ref<string | null>(null)
  const slotWritable = ref(false)

  // Which project+flow the rendered `nodes` belong to. A reload for a DIFFERENT scope
  // must not keep the previous scope's tree on failure (stale-tree rule, 0615 T0004).
  const renderedKey = ref<string | null>(null)
  let seq = 0

  const scopeKey = (pid: string, gid: string) => `${pid}:${gid}`

  const busy = computed(() => {
    const gid = flow.value?.groupId
    return !!gid && (aiInvokeRunsStore.isGroupRunning(gid) || aiInvokeRunsStore.isGroupInlineVisible(gid))
  })

  // Fail closed: anything but an explicit writable slot for THIS group is readonly.
  function readSlot(status: GitProjectStatus | null, gid: string) {
    const slot = status?.slots?.find((s) => s.group_id === gid)
    slotWritable.value = slot?.writable === true
  }

  async function loadChanges(
    pid: string,
    gid: string,
    statusResult: PromiseSettledResult<GitProjectStatus | null>,
    stale: () => boolean,
  ) {
    if (changes.value.kind !== 'ready') changes.value = { kind: 'loading' }
    const status = statusResult.status === 'fulfilled' ? statusResult.value : null
    if (statusResult.status === 'rejected') {
      projectBaseBranch.value = null
      changes.value = { kind: 'unresolved', reason: 'base_unavailable', expected: null, actual: null }
      return
    }
    const projectBase = (status?.base_branch ?? '').trim() || null
    projectBaseBranch.value = projectBase
    if (!projectBase) {
      changes.value = { kind: 'unresolved', reason: 'base_unconfigured', expected: null, actual: null }
      return
    }
    try {
      const set = await explorerStore.fetchGroupBranchChangeSet(pid, gid, { base: projectBase })
      if (stale()) return
      const actual = (set.base_branch ?? '').trim() || null
      if (!actual) {
        changes.value = { kind: 'unresolved', reason: 'base_unknown', expected: projectBase, actual: null }
        return
      }
      // The project Base is the single comparison standard (D §12). Without `base`,
      // `/changes` falls back to the group's own work base, so never trust an answer that
      // names another branch.
      if (actual !== projectBase) {
        changes.value = { kind: 'unresolved', reason: 'base_mismatch', expected: projectBase, actual }
        return
      }
      changes.value = { kind: 'ready', baseBranch: actual, statuses: toStatusMap(set.changes ?? []) }
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
      nodes.value = []
      commit.value = null
      renderedKey.value = null
      slotWritable.value = false
      projectBaseBranch.value = null
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
      nodes.value = []
      renderedKey.value = null
      slotWritable.value = false
      projectBaseBranch.value = null
      changes.value = { kind: 'loading' }
      loading.value = true
    }
    error.value = false

    const [treeResult, statusResult] = await Promise.allSettled([
      explorerStore.fetchGroupBranchTree(pid, gid),
      explorerStore.fetchGitStatus(pid),
    ])
    if (stale()) return
    readSlot(statusResult.status === 'fulfilled' ? statusResult.value : null, gid)
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
    projectBaseBranch, slotWritable, busy, reload,
  }
}
