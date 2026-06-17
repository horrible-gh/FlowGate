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
  }

  function getCachedFileTree(pid: string): FileNode[] | undefined {
    return fileTreeCache.value[cacheKey(pid)]
  }

  function getCachedGroupTree(pid: string): GroupNode[] | undefined {
    return groupTreeCache.value[cacheKey(pid)]
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
    fetchFileTree, fetchGroupTree, invalidateProject,
    getCachedFileTree, getCachedGroupTree,
    setWorkflowNodeState, clearWorkflowNodeState,
  }
})
