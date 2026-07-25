import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getRequest } from '@shared/api'
import { useProjectStore } from './project'

// 0283 T0004 (NR0003 권고 C): a single transient tree-fetch failure — a client timeout on
// a slow remote-storage directory walk, or a momentary 5xx — used to surface
// "트리를 불러오지 못했습니다." immediately and force a manual page reload. Retry once after a
// short backoff before giving up, so a one-off blip self-heals; a second failure still
// propagates to the existing catch/error UI unchanged.
const TREE_RETRY_DELAY_MS = 800
const getTreeWithRetry = async <T>(url: string) => {
  try {
    return await getRequest<T>(url)
  } catch {
    await new Promise((resolve) => setTimeout(resolve, TREE_RETRY_DELAY_MS))
    return await getRequest<T>(url)
  }
}

export interface WorkflowNodeState {
  nodeStatus: 'ns-pending' | 'ns-approved' | 'ns-advanced' | 'ns-next-act' | 'ns-done'
  docClass: 'R' | 'Q' | 'B' | null
}

export interface FileNode {
  id: string
  parent_id: string | null
  type: 'folder' | 'file'
  name: string
  label: string
  path: string
  permissions: string[]
}

// 0186 P0005 §3 — group-branch blob read (checkout-free). Mirrors read_group_blob.
export interface GroupChangeData {
  path: string
  status: string
}

export interface GroupBlobData {
  group_id: string
  branch: string
  commit: string
  path: string
  size: number
  binary: boolean
  truncated: boolean
  encoding: string | null
  content: string | null
}

// 0282 NR0003 발견 3 — response shape of GET /projects/{id}/git/status
// (git_service.project_git_status). Typed to what the four consumers read;
// the payload may carry more fields.
export interface GitProjectStatus {
  enabled: boolean
  base_branch: string | null
  base_path_state?: string
  ahead_count: number | null
  behind_count: number | null
  base_dirty?: { dirty: boolean; files: string[] }
  // 0308 T0004 (NR0003 권고 1) — new (untracked) base-checkout files. Kept as a
  // SEPARATE channel from base_dirty: folding untracked into base_dirty would widen
  // the E3 merge-finalize guard (git_service.py). Drives the file-tree new-file badge.
  base_untracked?: { count: number; files: string[]; truncated?: boolean }
  slots: Array<{ group_id: string; branch: string | null; status: string; merge_id: number | null }>
  pending: Array<{
    group_id: string
    branch: string | null
    status: string
    default_action: string
    merge_id: number | null
    ac_doc_id?: string | null
    conflict_since?: string | null
  }>
  pending_count: number
  cleanable_count?: number
  provision_failures?: Array<{ group_id: string; error: string; failed_at: string | null }>
  unpushed?: { count: number; commit_count: number; merges: unknown[] }
}

export interface GroupNode {
  id: string
  parent_id: string | null
  node_type: 'project' | 'module' | 'group' | 'subgroup' | 'orphan' | 'document'
  type_code: string | null
  number: string | null
  filename: string | null
  label: string
  title?: string
  has_md: boolean
  md_path: string | null
  is_final_approved?: boolean
  is_discarded?: boolean
}

const isMockMode = (): boolean => {
  if (import.meta.env.VITE_MOCK_MODE === 'true') return true
  if (typeof window !== 'undefined') {
    return new URLSearchParams(window.location.search).get('mock') === 'true'
  }
  return false
}

const MOCK_FILE_NODES: FileNode[] = []

const MOCK_GROUP_NODES: GroupNode[] = []

export const useExplorerStore = defineStore('explorer', () => {
  const projectStore = useProjectStore()
  const currentBranch = computed(() => projectStore.currentBranch || 'main')
  const cacheKey = (pid: string, branch = currentBranch.value) => `${pid}:${branch}`
  const fileTreeCache = ref<Record<string, FileNode[]>>({})
  const groupTreeCache = ref<Record<string, GroupNode[]>>({})
  const workflowNodeStates = ref<Record<string, WorkflowNodeState>>({})
  const selectedFileNodeId = ref<string | null>(null)
  const selectedGroupNodeId = ref<string | null>(null)
  const pendingSelectFilePath = ref<string | null>(null)
  // flowgate.default.0177 L0002 §2.6-a: per-project set of base-checkout files
  // with uncommitted (tracked) changes — drives the "modified" badge in the file
  // tree. Refreshed by its four triggers: git/status fetch, src-content save
  // response, base-commit/base-revert response, finalize base_dirty 409.
  const baseDirtyFiles = ref<Record<string, string[]>>({})
  // 0308 T0004 (NR0003 권고 1) — per-project set of new (untracked) base-checkout
  // files, the exact complement of baseDirtyFiles. The server already emits it as
  // base_untracked; it now drives a distinct file-tree new-file badge instead of being
  // consumed only by GitStatusPanel. Refreshed alongside baseDirtyFiles on every
  // git/status fetch. Never merged into baseDirtyFiles (E3 guard — git_service.py).
  const baseUntrackedFiles = ref<Record<string, string[]>>({})
  // 0282 NR0003 발견 3: project git/status was fetched independently by four
  // components (FileExplorer, GitActionMenu, GitStatusPanel, GitBaseDirtyDialog)
  // — every mount race or SSE trigger multiplied the server's aggregation work.
  // The store now owns the fetch: concurrent callers coalesce onto one request,
  // the latest payload stays readable per project, and the §2.6-a base-dirty
  // badge sync happens here once instead of in each component.
  const gitStatus = ref<Record<string, GitProjectStatus | null>>({})
  const gitStatusInflight = new Map<string, Promise<GitProjectStatus | null>>()
  // 0186 L0006 §2.4 — checkout-free group-branch explorer caches, keyed by the
  // branch HEAD commit so a branch advance auto-invalidates the stale snapshot.
  // activeGroupBranch: currently viewed group_id in the file explorer (null = base).
  const activeGroupBranch = ref<string | null>(null)
  const groupBranchCommit = ref<Record<string, string>>({})       // `${pid}:${gid}` -> commit
  const groupBranchTreeCache = ref<Record<string, FileNode[]>>({}) // `${pid}:${gid}:${commit}` -> nodes
  const groupBlobCache = ref<Record<string, GroupBlobData>>({})    // `${pid}:${gid}:${commit}:${path}` -> blob
  const groupChangedFiles = ref<Record<string, string[]>>({})      // `${pid}:${gid}` -> normalized paths
  const loadingFile = ref(false)
  const loadingGroup = ref(false)
  const fileError = ref<string | null>(null)
  const groupError = ref<string | null>(null)
  // 0245 R0001 / NR0003 §1 — tree expansion state for both explorers. It lives here
  // rather than in the recursive node components because a node's children are only
  // mounted while that node is expanded: a header "expand all" button can therefore
  // never reach a collapsed node's descendants by prop or event, and would open just
  // one level per press. A child instead reads its own expansion from this store as
  // it mounts, so expanding a parent cascades into the subtree that appears with it.
  // Keys are `${projectId}:${nodeId}`.
  const expandedFileNodes = ref<Record<string, boolean>>({})
  const expandedGroupNodes = ref<Record<string, boolean>>({})

  async function fetchFileTree(pid: string, force = false): Promise<FileNode[]> {
    const key = cacheKey(pid)
    if (!force && fileTreeCache.value[key]) return fileTreeCache.value[key]
    if (isMockMode()) {
      await new Promise((r) => setTimeout(r, 100))
      fileTreeCache.value[key] = MOCK_FILE_NODES
      return MOCK_FILE_NODES
    }
    loadingFile.value = true
    fileError.value = null
    try {
      const res = await getTreeWithRetry<{ nodes: FileNode[] }>(`/api/v1/projects/${pid}/files/tree?branch=${encodeURIComponent(currentBranch.value)}`)
      const nodes = (res.data as any).data.nodes as FileNode[]
      fileTreeCache.value[key] = nodes.filter((n) => n.permissions.includes('read'))
      return fileTreeCache.value[key]
    } catch (e) {
      fileError.value = 'tree_load_failed'
      throw e
    } finally {
      loadingFile.value = false
    }
  }

  async function fetchGroupTree(pid: string, force = false): Promise<GroupNode[]> {
    const key = cacheKey(pid)
    if (!force && groupTreeCache.value[key]) return groupTreeCache.value[key]
    if (isMockMode()) {
      await new Promise((r) => setTimeout(r, 100))
      groupTreeCache.value[key] = MOCK_GROUP_NODES
      return MOCK_GROUP_NODES
    }
    loadingGroup.value = true
    groupError.value = null
    try {
      const res = await getTreeWithRetry<{ nodes: GroupNode[] }>(`/api/v1/projects/${pid}/groups/tree?branch=${encodeURIComponent(currentBranch.value)}`)
      const nodes = (res.data as any).data.nodes as GroupNode[]
      groupTreeCache.value[key] = nodes
      return groupTreeCache.value[key]
    } catch (e) {
      groupError.value = 'tree_load_failed'
      throw e
    } finally {
      loadingGroup.value = false
    }
  }

  /** Fetch (and share) a project's git/status. Always hits the server — the
   *  status changes under finalize/SSE flows, so freshness wins — but every
   *  caller arriving while a fetch is in flight joins that fetch instead of
   *  issuing another one. Errors propagate to each joined caller. */
  async function fetchGitStatus(pid: string): Promise<GitProjectStatus | null> {
    const inflight = gitStatusInflight.get(pid)
    if (inflight) return inflight
    const request = (async () => {
      try {
        const res = await getRequest<{ ok: boolean; status: GitProjectStatus }>(
          `/api/v1/projects/${encodeURIComponent(pid)}/git/status`,
        )
        const status = ((res.data as any)?.status ?? null) as GitProjectStatus | null
        gitStatus.value = { ...gitStatus.value, [pid]: status }
        // 0177 §2.6-a badge trigger 1/4 (moved here from GitStatusPanel): every
        // status fetch refreshes the file-tree "modified" badges.
        if (status) setBaseDirtyFiles(pid, status.base_dirty?.files ?? [])
        // 0308 T0004 (NR0003 권고 1) badge trigger — refresh the file-tree new-file
        // badges from the same status payload, on a channel separate from base-dirty.
        if (status) setBaseUntrackedFiles(pid, status.base_untracked?.files ?? [])
        return status
      } finally {
        gitStatusInflight.delete(pid)
      }
    })()
    gitStatusInflight.set(pid, request)
    return request
  }

  function invalidateProject(pid: string) {
    for (const key of Object.keys(fileTreeCache.value)) {
      if (key === pid || key.startsWith(`${pid}:`)) delete fileTreeCache.value[key]
    }
    for (const key of Object.keys(groupTreeCache.value)) {
      if (key === pid || key.startsWith(`${pid}:`)) delete groupTreeCache.value[key]
    }
    for (const key of Object.keys(groupBranchTreeCache.value)) {
      if (key.startsWith(`${pid}:`)) delete groupBranchTreeCache.value[key]
    }
    for (const key of Object.keys(groupBlobCache.value)) {
      if (key.startsWith(`${pid}:`)) delete groupBlobCache.value[key]
    }
    for (const key of Object.keys(groupBranchCommit.value)) {
      if (key.startsWith(`${pid}:`)) delete groupBranchCommit.value[key]
    }
    for (const key of Object.keys(groupChangedFiles.value)) {
      if (key.startsWith(`${pid}:`)) delete groupChangedFiles.value[key]
    }
  }

  // ── Group-branch (checkout-free) explorer (0186 P0005 §2·§3) ────────────────

  const groupKey = (pid: string, gid: string) => `${pid}:${gid}`

  function purgeGroupCommit(pid: string, gid: string, commit: string) {
    const prefix = `${groupKey(pid, gid)}:${commit}`
    for (const k of Object.keys(groupBranchTreeCache.value)) {
      if (k === prefix) delete groupBranchTreeCache.value[k]
    }
    for (const k of Object.keys(groupBlobCache.value)) {
      if (k.startsWith(`${prefix}:`)) delete groupBlobCache.value[k]
    }
  }

  function currentGroupCommit(pid: string, gid: string): string | undefined {
    return groupBranchCommit.value[groupKey(pid, gid)]
  }

  /** Fetch a group branch's tree straight from Git objects (no checkout switch).
   *  DEVIATION from P0005 §4 / L0006 §2.4 (which contract a cache-hit read on group
   *  re-selection with force-refresh as the only bypass): tree reads always hit the
   *  server (freshness-first). A read-only explorer must never show a stale snapshot
   *  of a branch that AI workers are actively advancing, and this makes scenario 5
   *  (branch-advance detection) hold unconditionally without depending on a refresh
   *  trigger firing. The blob cache below is kept (needed for the §2.3 point-in-time
   *  pin). On a commit change the previous commit's tree/blob caches are purged. */
  async function fetchGroupBranchTree(
    pid: string,
    gid: string,
  ): Promise<{ branch: string; commit: string; nodes: FileNode[] }> {
    loadingFile.value = true
    fileError.value = null
    try {
      const res = await getTreeWithRetry<{ data: { branch: string; commit: string; nodes: FileNode[] } }>(
        `/api/v1/projects/${encodeURIComponent(pid)}/git/groups/${encodeURIComponent(gid)}/tree`,
      )
      const data = (res.data as any).data as { branch: string; commit: string; nodes: FileNode[] }
      const key = groupKey(pid, gid)
      const prev = groupBranchCommit.value[key]
      if (prev && prev !== data.commit) purgeGroupCommit(pid, gid, prev)
      groupBranchCommit.value = { ...groupBranchCommit.value, [key]: data.commit }
      const nodes = data.nodes.filter((n) => n.permissions.includes('read'))
      groupBranchTreeCache.value[`${key}:${data.commit}`] = nodes
      return { branch: data.branch, commit: data.commit, nodes }
    } catch (e) {
      fileError.value = 'tree_load_failed'
      throw e
    } finally {
      loadingFile.value = false
    }
  }

  async function fetchGroupBranchChanges(pid: string, gid: string): Promise<GroupChangeData[]> {
    const res = await getRequest<{ data: { changes: GroupChangeData[] } }>(
      `/api/v1/projects/${encodeURIComponent(pid)}/git/groups/${encodeURIComponent(gid)}/changes`,
    )
    const changes = (res.data as any).data.changes as GroupChangeData[]
    groupChangedFiles.value = {
      ...groupChangedFiles.value,
      [groupKey(pid, gid)]: changes.map((change) => change.path.replace(/\\/g, '/')),
    }
    return changes
  }

  function isGroupChangedPath(pid: string, gid: string, path: string): boolean {
    return (groupChangedFiles.value[groupKey(pid, gid)] ?? []).includes(path.replace(/\\/g, '/'))
  }

  // 0192 T0005 §1 — folder-level "modified" propagation. The dirty/changed marker
  // ('>') was file-only, so an edit under a folder (especially a COLLAPSED one,
  // whose children are not rendered) left no trace on any ancestor. A folder is
  // "dirty" when it contains at least one changed file: any tracked path that lives
  // under `folderPath + '/'`. Both the full file lists are already in the store, so
  // this is a pure prefix scan — no new API or state.
  function _anyUnder(files: string[] | undefined, folderPath: string): boolean {
    if (!files || !files.length) return false
    const prefix = folderPath.replace(/\\/g, '/').replace(/\/+$/, '') + '/'
    return files.some((f) => f.startsWith(prefix))
  }

  function isGroupChangedDir(pid: string, gid: string, folderPath: string): boolean {
    return _anyUnder(groupChangedFiles.value[groupKey(pid, gid)], folderPath)
  }

  /** Fetch a single file from a group branch, pinned to the tree's commit so tree
   *  and blob never disagree on point-in-time (L0006 §2.3·§2.4). Blob responses
   *  are cached by (pid, gid, commit, path). */
  async function fetchGroupBranchBlob(pid: string, gid: string, path: string): Promise<GroupBlobData> {
    const commit = currentGroupCommit(pid, gid)
    if (commit) {
      const cached = groupBlobCache.value[`${groupKey(pid, gid)}:${commit}:${path}`]
      if (cached) return cached
    }
    const refQ = commit ? `&ref=${encodeURIComponent(commit)}` : ''
    const res = await getRequest<{ data: GroupBlobData }>(
      `/api/v1/projects/${encodeURIComponent(pid)}/git/groups/${encodeURIComponent(gid)}/blob` +
        `?path=${encodeURIComponent(path)}${refQ}`,
    )
    const data = (res.data as any).data as GroupBlobData
    groupBlobCache.value[`${groupKey(pid, gid)}:${data.commit}:${path}`] = data
    return data
  }

  function getCachedFileTree(pid: string): FileNode[] | undefined {
    return fileTreeCache.value[cacheKey(pid)]
  }

  function getCachedGroupTree(pid: string): GroupNode[] | undefined {
    return groupTreeCache.value[cacheKey(pid)]
  }

  function setBaseDirtyFiles(pid: string, files: string[]) {
    baseDirtyFiles.value = {
      ...baseDirtyFiles.value,
      [pid]: files.map((f) => f.replace(/\\/g, '/')),
    }
  }

  function isBaseDirtyPath(pid: string, path: string): boolean {
    const files = baseDirtyFiles.value[pid]
    if (!files || !files.length) return false
    return files.includes(path.replace(/\\/g, '/'))
  }

  // 0192 T0005 §1 — see isGroupChangedDir. Folder is dirty when any base-checkout
  // dirty file lives under it.
  function isBaseDirtyDir(pid: string, folderPath: string): boolean {
    return _anyUnder(baseDirtyFiles.value[pid], folderPath)
  }

  // 0308 T0004 (NR0003 권고 1·2·3) — new (untracked) base-checkout files, a channel
  // parallel to the base-dirty one above. Same normalization and the same _anyUnder
  // folder propagation, so a new file inside a collapsed folder still marks its ancestors.
  function setBaseUntrackedFiles(pid: string, files: string[]) {
    baseUntrackedFiles.value = {
      ...baseUntrackedFiles.value,
      [pid]: files.map((f) => f.replace(/\\/g, '/')),
    }
  }

  function isBaseUntrackedPath(pid: string, path: string): boolean {
    const files = baseUntrackedFiles.value[pid]
    if (!files || !files.length) return false
    return files.includes(path.replace(/\\/g, '/'))
  }

  function isBaseUntrackedDir(pid: string, folderPath: string): boolean {
    return _anyUnder(baseUntrackedFiles.value[pid], folderPath)
  }

  // ── Explorer tree expansion (0245 R0001 / NR0003 §1) ────────────────────────

  const expandKey = (pid: string, nodeId: string) => `${pid}:${nodeId}`
  // The document tree keeps its established per-node localStorage key, so expansion
  // still survives a reload and the ancestor-reveal writers below stay compatible.
  const groupExpandStorageKey = (pid: string, nodeId: string) => `flowgate:grp-exp:${pid}:${nodeId}`

  // The file tree was never persisted (FileTreeNode held a plain ref(false)); that
  // session-only behaviour is kept deliberately — only the owner of the state moved.
  function isFileNodeExpanded(pid: string, nodeId: string): boolean {
    return expandedFileNodes.value[expandKey(pid, nodeId)] === true
  }

  function setFileNodesExpanded(pid: string, nodeIds: string[], expanded: boolean) {
    const next = { ...expandedFileNodes.value }
    for (const nodeId of nodeIds) next[expandKey(pid, nodeId)] = expanded
    expandedFileNodes.value = next
  }

  function setFileNodeExpanded(pid: string, nodeId: string, expanded: boolean) {
    setFileNodesExpanded(pid, [nodeId], expanded)
  }

  /** Reads the store first and falls back to localStorage for a node this session
   *  has not touched. The store read is what Vue tracks, so a later set re-renders
   *  the node; no write happens here (a side effect in a getter would be a reactivity
   *  trap). Every writer goes through setGroupNodesExpanded, which updates both, so
   *  the cached value can never drift from the stored one. */
  function isGroupNodeExpanded(pid: string, nodeId: string): boolean {
    const cached = expandedGroupNodes.value[expandKey(pid, nodeId)]
    if (cached !== undefined) return cached
    try {
      return localStorage.getItem(groupExpandStorageKey(pid, nodeId)) === '1'
    } catch {
      return false
    }
  }

  function setGroupNodesExpanded(pid: string, nodeIds: string[], expanded: boolean) {
    const next = { ...expandedGroupNodes.value }
    for (const nodeId of nodeIds) {
      next[expandKey(pid, nodeId)] = expanded
      try {
        localStorage.setItem(groupExpandStorageKey(pid, nodeId), expanded ? '1' : '0')
      } catch { /* ignore — e.g. private mode quota */ }
    }
    expandedGroupNodes.value = next
  }

  function setGroupNodeExpanded(pid: string, nodeId: string, expanded: boolean) {
    setGroupNodesExpanded(pid, [nodeId], expanded)
  }

  /** Reveal a node by expanding every ancestor group. Previously each caller wrote
   *  the localStorage keys by hand, which only took effect once the explorer
   *  remounted ("...so they're open after remount"). Going through the store makes
   *  the reveal apply immediately, and keeps the store and localStorage in step. */
  function expandGroupAncestors(
    pid: string,
    nodes: Array<{ id: string; parent_id: string | null }>,
    nodeId: string,
  ) {
    const ancestors: string[] = []
    let node = nodes.find((n) => n.id === nodeId)
    let parentId = node?.parent_id ?? null
    while (parentId) {
      ancestors.push(parentId)
      node = nodes.find((n) => n.id === parentId)
      parentId = node?.parent_id ?? null
    }
    if (ancestors.length) setGroupNodesExpanded(pid, ancestors, true)
  }

  /** Reveal + select a document node in the group tree by doc id — the single
   *  implementation shared by dashboard/notification navigation, the AI run
   *  cards' "문서 열기", and the auto-advance SSE select intent (0316 T0004 /
   *  NR0003 권고 1·2·3). Ensures the tree is loaded (force-refetching once when the
   *  id is absent, e.g. a just-created auto-advance head that the cached tree has
   *  not caught up to), expands the ancestor groups so the node is revealed, and
   *  sets it as the selected node — the same reveal the dashboard cards already
   *  perform, now reachable from the AI-work open paths that previously only opened
   *  a tab. Best-effort: a tree-load failure resolves to null instead of throwing,
   *  so a caller that also opens a tab still opens it. Returns the node when found.
   *
   *  `switchProject` (0316 TR0005 rev1 반려 — "문서열기 해도 해당 프로젝트로 안가잖아"):
   *  when the target document lives in a project OTHER than the one on screen, the
   *  reveal/select below would land on a group tree that isn't displayed and nothing
   *  would move — the explorer stayed on the old project. The previous revision made
   *  this worse by having every caller pass the *current* project id, so a cross-project
   *  "문서 열기" silently no-op'd. With `switchProject`, the active project is switched to
   *  `pid` first, so the explorer actually shows the document's project and the reveal
   *  lands on the tree now on screen. Off by default: only the explicit user-driven open
   *  paths opt in — a background SSE follow must never yank the user's view to another
   *  project (that guard stays in useFlowGateSse). Selection/expansion are store (and
   *  localStorage) state, so they survive the explorer remount the switch triggers. */
  async function revealDocInGroupTree(
    pid: string | null,
    docId: string | null,
    options: { switchProject?: boolean } = {},
  ): Promise<GroupNode | null> {
    if (!pid || !docId) return null
    try {
      if (options.switchProject && pid !== projectStore.currentProjectId) {
        projectStore.setCurrentProject(pid)
      }
      let nodes = getCachedGroupTree(pid)
      if (!nodes) nodes = await fetchGroupTree(pid, true)
      let node = nodes.find((n) => n.id === docId)
      if (!node) {
        nodes = await fetchGroupTree(pid, true)
        node = nodes.find((n) => n.id === docId)
      }
      if (!node || node.node_type !== 'document') return null
      expandGroupAncestors(pid, nodes, node.id)
      selectedGroupNodeId.value = node.id
      return node
    } catch {
      return null
    }
  }

  function setWorkflowNodeState(docId: string, state: WorkflowNodeState) {
    workflowNodeStates.value[docId] = state
  }

  function clearWorkflowNodeState(docId: string) {
    delete workflowNodeStates.value[docId]
  }

  return {
    currentBranch,
    fileTreeCache, groupTreeCache, workflowNodeStates,
    selectedFileNodeId, selectedGroupNodeId, pendingSelectFilePath,
    loadingFile, loadingGroup, fileError, groupError,
    baseDirtyFiles, setBaseDirtyFiles, isBaseDirtyPath, isBaseDirtyDir,
    baseUntrackedFiles, setBaseUntrackedFiles, isBaseUntrackedPath, isBaseUntrackedDir,
    gitStatus, fetchGitStatus,
    fetchFileTree, fetchGroupTree, invalidateProject,
    getCachedFileTree, getCachedGroupTree,
    activeGroupBranch, fetchGroupBranchTree, fetchGroupBranchChanges, fetchGroupBranchBlob,
    currentGroupCommit, groupChangedFiles, isGroupChangedPath, isGroupChangedDir,
    expandedFileNodes, expandedGroupNodes,
    isFileNodeExpanded, setFileNodeExpanded, setFileNodesExpanded,
    isGroupNodeExpanded, setGroupNodeExpanded, setGroupNodesExpanded, expandGroupAncestors,
    revealDocInGroupTree,
    setWorkflowNodeState, clearWorkflowNodeState,
  }
})
