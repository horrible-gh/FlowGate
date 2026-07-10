import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getRequest } from '@shared/api'
import { useProjectStore } from './project'

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
      const res = await getRequest<{ nodes: FileNode[] }>(`/api/v1/projects/${pid}/files/tree?branch=${encodeURIComponent(currentBranch.value)}`)
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
      const res = await getRequest<{ nodes: GroupNode[] }>(`/api/v1/projects/${pid}/groups/tree?branch=${encodeURIComponent(currentBranch.value)}`)
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
      const res = await getRequest<{ data: { branch: string; commit: string; nodes: FileNode[] } }>(
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
    baseDirtyFiles, setBaseDirtyFiles, isBaseDirtyPath,
    fetchFileTree, fetchGroupTree, invalidateProject,
    getCachedFileTree, getCachedGroupTree,
    activeGroupBranch, fetchGroupBranchTree, fetchGroupBranchChanges, fetchGroupBranchBlob,
    currentGroupCommit, groupChangedFiles, isGroupChangedPath,
    setWorkflowNodeState, clearWorkflowNodeState,
  }
})
