// Project store (WP0005 T#1 §5). Minimal responsibilities only: projects, loading/error,
// current project, Dialog open/close state, list fetch, current-project change, create
// orchestration, and the public interface App/future Dialog UI consumes. Dialog-local state
// (search text, hover, create-form input, row selection) does NOT belong here — that is
// T#2's ProjectDialog/ProjectCreateDialog concern (D0004 §2.1/§5.2).
//
// Stale-response guard: every write this store performs because of an async result is
// gated by `requestEpoch`, a counter bumped by every new `fetchProjects()` call and by
// `closeDialog()`. A response only applies if its captured epoch still matches the current
// one, so a slow earlier fetch that resolves after a newer fetch (or after the Dialog
// closed) can never revert `loading`/`error`/`projects`/`currentProjectId` to a stale state,
// and it never reopens a closed Dialog (WP0005 T#1 §5, flowgate.v02.0008.0001-R §13).
//
// flowgate.v02.0023 adds the current project's group tree (fetch/cache/inflight-join) here
// rather than in a Designer-only store: it is project context that the Designer Project
// Explorer and File Explorer (0024) both read. Only the canonical flat node list is cached;
// each consumer derives its own display shape and keeps its own view state (expansion,
// selection, search) locally.

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  BaseFileDiff,
  CreateProjectInput,
  FetchGroupTreeOptions,
  FlowAiActivity,
  FlowChangeSet,
  FlowFileDiff,
  FlowFileTree,
  GroupTreeNode,
  MergeReviewFileDiff,
  Project,
  ProjectGitStatus,
} from './types'
import {
  createProject as createProjectApi,
  fetchActiveProjects as fetchActiveProjectsApi,
  fetchBaseFileDiff,
  fetchFlowAiActivity,
  fetchFlowChangeSet,
  fetchFlowFileDiff,
  fetchFlowFileTree,
  fetchMergeReviewFileDiff,
  fetchProjectGitStatus,
  fetchProjectGroupTree as fetchProjectGroupTreeApi,
} from './api'
import { clearStoredCurrentProjectId, writeStoredCurrentProjectId } from './currentProjectStorage'

export const useProjectStore = defineStore('project', () => {
  const projects = ref<Project[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const dialogOpen = ref(false)
  // flowgate.v02.0040 (0001-R §1/§2): the current Project is the one the person opened with
  // the Dialog's "열기" in this app session — nothing else. It is not seeded from the stored
  // `fg_current_project_id` (written by v0.1 and by an earlier session): that id is not a
  // choice the person made here, and the Designer, which shows the current Project's tree,
  // must start without a Project until "열기". The key is still written on "열기" so v0.1
  // keeps reading it as before.
  const currentProjectId = ref<string | null>(null)

  const requestEpoch = ref(0)

  const currentProject = computed<Project | null>(() => {
    if (!currentProjectId.value) return null
    return projects.value.find((p) => p.id === currentProjectId.value) ?? null
  })

  function bumpEpoch(): number {
    requestEpoch.value += 1
    return requestEpoch.value
  }

  function applyCurrentProjectId(projectId: string | null): void {
    currentProjectId.value = projectId
    if (projectId) writeStoredCurrentProjectId(projectId)
    else clearStoredCurrentProjectId()
  }

  // flowgate.v02.0040 (0001-R §1): the list refresh never picks a Project on its own. A current
  // Project that is still in the active list stays; one that has left it is cleared (its stored
  // id too); with no current Project there is nothing to do — the v0.1 fallback to the first
  // active project is gone, because that made a Project current (and so shown in the Designer)
  // without the person ever pressing "열기".
  function reconcileCurrentProject(): void {
    if (currentProjectId.value === null) return
    if (projects.value.some((p) => p.id === currentProjectId.value)) return
    applyCurrentProjectId(null)
  }

  // Shared by fetchProjects() and createProject()'s post-create refresh. `epoch` is the
  // generation the caller already owns (fetchProjects mints a fresh one; createProject
  // reuses its own so the refresh can't outlive a close/reopen that happened after create
  // resolved but before this refresh finishes). `reconcile` is false for the post-create
  // refresh: creating a Project must never set currentProjectId as a side effect (D0004
  // §3.6/§5.4), even when the active list was empty beforehand.
  async function refreshProjects(epoch: number, reconcile: boolean): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const result = await fetchActiveProjectsApi()
      if (epoch !== requestEpoch.value) return
      projects.value = result
      if (reconcile) reconcileCurrentProject()
    } catch (err) {
      if (epoch !== requestEpoch.value) return
      projects.value = []
      error.value = err instanceof Error ? err.message : 'Failed to load projects.'
    } finally {
      if (epoch === requestEpoch.value) loading.value = false
    }
  }

  async function fetchProjects(): Promise<void> {
    const epoch = bumpEpoch()
    await refreshProjects(epoch, true)
  }

  // Create orchestration only (WP0005 T#1 §5). Whether/how a caller turns the created
  // Project into a Dialog-local "selected" candidate is T#2's ProjectCreateDialog concern
  // (D0004 §3.6) — this store never sets currentProjectId as a side effect of creating.
  //
  // Shares `requestEpoch`/`loading` with fetchProjects so the two request kinds cannot
  // corrupt each other's generation: bumping the epoch here invalidates an in-flight fetch's
  // own cleanup (its `finally` epoch check will now fail), so this function's `finally` takes
  // over clearing `loading` whenever it still owns the current epoch when it settles — success
  // or failure. If a newer fetch/create/closeDialog has since taken over the epoch, this call
  // was superseded and must not touch `loading`/`error`, nor hand the caller a create result
  // the caller could mistake for the current session's outcome — it returns `null` instead.
  // This applies symmetrically to a superseded call's own createProjectApi() rejection: a
  // stale session's failure must not reach whichever Dialog session now owns the epoch, so it
  // also resolves to `null` rather than throwing. Only a rejection observed while this call
  // still owns the current epoch is a real failure and is thrown to the caller.
  //
  // The post-create list refresh reuses this same epoch (instead of letting fetchProjects
  // mint its own) so that a close/reopen landing while the refresh is still in flight is
  // detected here too: the refresh's own epoch check only protects `projects`/`loading`/
  // `error` from being overwritten, it cannot tell this function whether `created` is still
  // safe to hand back. That final safety check happens explicitly below, after the refresh
  // settles, before `created` is ever returned to the caller.
  async function createProject(input: CreateProjectInput): Promise<Project | null> {
    const epoch = bumpEpoch()
    try {
      let created: Project
      try {
        created = await createProjectApi(input)
      } catch (err) {
        // A rejection from a superseded call (close/reopen or a newer fetch/create already
        // took the epoch) is not this session's failure to surface — it must end in the same
        // invalidated-result `null` outcome as a superseded success, not throw into whichever
        // Dialog session now owns the epoch.
        if (epoch !== requestEpoch.value) return null
        throw err
      }
      if (epoch !== requestEpoch.value) return null
      await refreshProjects(epoch, false)
      if (epoch !== requestEpoch.value) return null
      return created
    } finally {
      if (epoch === requestEpoch.value) loading.value = false
    }
  }

  // The only place currentProjectId is ever assigned from a Dialog-driven "open" action
  // (D0004 §3.4). Row selection alone must never call this — that distinction lives in the
  // future ProjectDialog's Dialog-local `selected` state, not in this store.
  function setCurrentProject(project: Project): void {
    applyCurrentProjectId(project.id)
  }

  // flowgate.v02.0040: one-shot "this Project was explicitly opened" signal for the Designer.
  // Only the Dialog's "열기" raises it (right after setCurrentProject); the Designer's Project
  // Explorer takes it on entry to start with that Project selected. Taking it clears it, so a
  // later Designer entry from the Sidebar/Dashboard card (which never raises it) still starts
  // unselected (0039 T#3). Not persisted: it only bridges the Dialog -> Designer move.
  const explicitOpenProjectId = ref<string | null>(null)

  function requestExplicitOpen(projectId: string): void {
    explicitOpenProjectId.value = projectId
  }

  function takeExplicitOpen(): string | null {
    const projectId = explicitOpenProjectId.value
    explicitOpenProjectId.value = null
    return projectId
  }

  // ── Group tree (flowgate.v02.0023) ───────────────────────────────────────────────
  // Keyed per project + variant (pruned vs include_terminal): the two variants are different
  // node lists and must never answer for each other. Kept apart from `requestEpoch`, which
  // belongs to the Project list/Dialog session and is bumped by closeDialog().
  const groupTrees = ref<Record<string, GroupTreeNode[]>>({})
  const groupTreeInflight = new Map<string, { force: boolean; promise: Promise<GroupTreeNode[]> }>()
  const groupTreeCommitted = new Map<string, number>()
  let groupTreeOrder = 0

  const groupTreeKey = (projectId: string, includeTerminal: boolean) =>
    `${projectId}:${includeTerminal ? 'full' : 'pruned'}`

  function getCachedGroupTree(projectId: string, includeTerminal = false): GroupTreeNode[] | undefined {
    return groupTrees.value[groupTreeKey(projectId, includeTerminal)]
  }

  // Answers from the cache unless `force`. A non-forced read joins any read already in flight
  // for the same key; a forced read only joins another forced one (an older non-forced read
  // may predate the change the caller wants to see). A response only replaces the cached
  // list if no later-started read already committed one, so a slow stale response can never
  // overwrite a fresher tree; it still resolves its own nodes to its own caller. A failure
  // leaves the cached list untouched (callers keep showing the last good tree).
  function fetchGroupTree(projectId: string, options: FetchGroupTreeOptions = {}): Promise<GroupTreeNode[]> {
    const force = options.force ?? false
    const includeTerminal = options.includeTerminal ?? false
    const key = groupTreeKey(projectId, includeTerminal)
    const cached = groupTrees.value[key]
    if (!force && cached) return Promise.resolve(cached)
    const inflight = groupTreeInflight.get(key)
    if (inflight && (!force || inflight.force)) return inflight.promise
    const order = ++groupTreeOrder
    // Assigned synchronously below, before the IIFE's first await suspends it, so the
    // `finally` (which only runs after that) always sees it.
    let promise!: Promise<GroupTreeNode[]>
    promise = (async () => {
      try {
        const nodes = await fetchProjectGroupTreeApi(projectId, { includeTerminal, force })
        if (order >= (groupTreeCommitted.get(key) ?? 0)) {
          groupTreeCommitted.set(key, order)
          groupTrees.value = { ...groupTrees.value, [key]: nodes }
          // The cached (reactive) list, so this caller and later cache reads hold the same
          // reference.
          return groupTrees.value[key]
        }
        return nodes
      } finally {
        if (groupTreeInflight.get(key)?.promise === promise) groupTreeInflight.delete(key)
      }
    })()
    groupTreeInflight.set(key, { force, promise })
    return promise
  }

  // Flow file context (flowgate.v02.0024 T0005). The Designer File Explorer reaches the
  // project Git status and the active Flow's tree / Base-relative changes / diff / AI
  // activity only through these loaders, so features/designer owns no API or store of its
  // own. They are stateless on purpose: Flow-scoped staleness (seq guard, scope switch) is
  // the consumer's concern, the Dialog's `requestEpoch` must not gate them, and duplicate
  // in-flight GETs are already coalesced by `@shared/api`.
  function loadProjectGitStatus(projectId: string): Promise<ProjectGitStatus | null> {
    return fetchProjectGitStatus(projectId)
  }

  function loadFlowFileTree(projectId: string, groupId: string): Promise<FlowFileTree> {
    return fetchFlowFileTree(projectId, groupId)
  }

  function loadFlowChanges(projectId: string, groupId: string, options: { base?: string } = {}): Promise<FlowChangeSet> {
    return fetchFlowChangeSet(projectId, groupId, options)
  }

  function loadFlowFileDiff(
    projectId: string,
    groupId: string,
    path: string,
    options: { base?: string; ref?: string } = {},
  ): Promise<FlowFileDiff> {
    return fetchFlowFileDiff(projectId, groupId, path, options)
  }

  function loadFlowAiActivity(groupId: string): Promise<FlowAiActivity> {
    return fetchFlowAiActivity(groupId)
  }

  // flowgate.v02.0033: the Designer Git part's file reads. A conflict / uncommitted file is a
  // Base checkout file (HEAD vs working tree); a merge review file is read from the frozen
  // review candidate. Stateless like the loaders above.
  function loadBaseFileDiff(projectId: string, path: string): Promise<BaseFileDiff> {
    return fetchBaseFileDiff(projectId, path)
  }

  function loadMergeReviewFileDiff(groupId: string, mergeId: number, path: string): Promise<MergeReviewFileDiff> {
    return fetchMergeReviewFileDiff(groupId, mergeId, path)
  }

  function openDialog(): void {
    dialogOpen.value = true
    void fetchProjects()
  }

  function closeDialog(): void {
    dialogOpen.value = false
    // Invalidate any fetch/create still in flight from this session and drop any loading/
    // error state it left behind — a closed Dialog shows nothing.
    bumpEpoch()
    loading.value = false
    error.value = null
  }

  return {
    projects,
    loading,
    error,
    dialogOpen,
    currentProject,
    // Read-only id view: `currentProject` needs the active list loaded, but a screen that
    // only needs "which project" (e.g. the Designer) must not wait for or trigger it.
    currentProjectId: computed(() => currentProjectId.value),
    fetchProjects,
    createProject,
    setCurrentProject,
    requestExplicitOpen,
    takeExplicitOpen,
    openDialog,
    closeDialog,
    getCachedGroupTree,
    fetchGroupTree,
    loadProjectGitStatus,
    loadFlowFileTree,
    loadFlowChanges,
    loadFlowFileDiff,
    loadFlowAiActivity,
    loadBaseFileDiff,
    loadMergeReviewFileDiff,
  }
})
