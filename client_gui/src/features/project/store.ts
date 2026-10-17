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
//
// flowgate.v02.0043 adds what the Designer File menu needs: the 최근 Project / Group / Flow
// lists (recentStorage.ts), closing the current Project, opening a recent Project the way the
// Dialog's "열기" does, a Dialog open that asks for the create child, and the Group / Flow
// creates (api.ts) that invalidate the cached group tree of their Project.

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  BaseFileDiff,
  CreateFlowInput,
  CreateGroupInput,
  CreateProjectInput,
  CreatedFlow,
  CreatedGroup,
  FetchGroupTreeOptions,
  FlowAiActivity,
  FlowChangeSet,
  FlowFileDiff,
  FlowFileTree,
  GroupTreeNode,
  FlowCanvas,
  FlowCanvasLayoutInput,
  FlowCanvasLayoutResult,
  FlowCanvasTitleState,
  FlowGraphConfirmResult,
  FlowGraphDefinition,
  FlowGraphDraftInput,
  FlowGraphDraftSaveResult,
  FlowGraphValidation,
  FlowRun,
  FlowRunApproval,
  FlowRunTimeMachine,
  MergeReviewFileDiff,
  Project,
  ProjectGitStatus,
  RecentFlow,
  RecentGroup,
  RecentProject,
} from './types'
import {
  createFlow as createFlowApi,
  createGroup as createGroupApi,
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
  suggestProjectSlug,
} from './api'
import { fetchFlowCanvas, nodeTitleSave, saveFlowCanvasLayout as saveFlowCanvasLayoutApi } from './flowCanvasApi'
import {
  confirmFlowGraphRevision as confirmFlowGraphRevisionApi,
  fetchFlowGraph,
  saveFlowGraphDraft as saveFlowGraphDraftApi,
  validateFlowGraphDraft as validateFlowGraphDraftApi,
} from './flowGraphApi'
import {
  approveFlowRunNode as approveFlowRunNodeApi,
  fetchCurrentFlowRun,
  pauseFlowRun as pauseFlowRunApi,
  resumeFlowRun as resumeFlowRunApi,
  startFlowRun as startFlowRunApi,
  stopFlowRun as stopFlowRunApi,
  timeMachineFlowRunNode as timeMachineFlowRunNodeApi,
  type FlowRunCommand,
} from './flowRunApi'
import { subscribeFlowCanvasChanges as subscribeFlowCanvasChangesApi, type FlowCanvasChangeHandlers } from './flowCanvasEvents'
import { subscribeProjectEvents as subscribeProjectEventsApi, type ProjectEventSubscription } from './projectEvents'
import { clearStoredCurrentProjectId, writeStoredCurrentProjectId } from './currentProjectStorage'
import {
  pushRecent,
  readRecentFlows,
  readRecentGroups,
  readRecentProjects,
  writeRecentFlows,
  writeRecentGroups,
  writeRecentProjects,
} from './recentStorage'

export interface OpenDialogOptions {
  /** Open the create child right away (the Designer's File › 새 Project). Admin-only downstream. */
  create?: boolean
}

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

  // ── Recent Project / Group / Flow (flowgate.v02.0043) ───────────────────────────────
  // Newest first, one entry per id, capped (recentStorage.ts). A Project is recorded when it
  // is opened ("열기" / a recent entry); Groups and Flows are recorded by the Designer when it
  // opens them. Nothing here is ever derived from the stored current Project id.
  const recentProjects = ref<RecentProject[]>(readRecentProjects())
  const recentGroups = ref<RecentGroup[]>(readRecentGroups())
  const recentFlows = ref<RecentFlow[]>(readRecentFlows())

  function recordRecentProject(entry: RecentProject): void {
    recentProjects.value = pushRecent(recentProjects.value, { id: entry.id, name: entry.name })
    writeRecentProjects(recentProjects.value)
  }

  function recordRecentGroup(entry: RecentGroup): void {
    recentGroups.value = pushRecent(recentGroups.value, { id: entry.id, name: entry.name, projectId: entry.projectId })
    writeRecentGroups(recentGroups.value)
  }

  function recordRecentFlow(entry: RecentFlow): void {
    recentFlows.value = pushRecent(recentFlows.value, {
      id: entry.id,
      name: entry.name,
      projectId: entry.projectId,
      groupId: entry.groupId,
    })
    writeRecentFlows(recentFlows.value)
  }

  // The only place currentProjectId is ever assigned from a Dialog-driven "open" action
  // (D0004 §3.4). Row selection alone must never call this — that distinction lives in the
  // future ProjectDialog's Dialog-local `selected` state, not in this store.
  function setCurrentProject(project: Project): void {
    applyCurrentProjectId(project.id)
    recordRecentProject({ id: project.id, name: project.name })
  }

  // flowgate.v02.0043: File › 최근 Project. The same "열기" as the Dialog's — the Project becomes
  // current and the one-shot explicit-open signal below is raised, so the Project Explorer
  // selects it (0040) — without needing the active list to be loaded.
  function openRecentProject(entry: RecentProject): void {
    applyCurrentProjectId(entry.id)
    requestExplicitOpen(entry.id)
    recordRecentProject(entry)
  }

  // flowgate.v02.0043: File › Project 닫기. The Designer loses its Project context (its Explorers
  // and Center clear); the stored id goes too, as when a current Project leaves the active list.
  function closeCurrentProject(): void {
    applyCurrentProjectId(null)
  }

  // flowgate.v02.0040: one-shot "this Project was explicitly opened" signal for the Designer.
  // Only the Dialog's "열기" raises it (right after setCurrentProject); the Designer's Project
  // Explorer takes it on entry to start with that Project selected. Taking it clears it, so a
  // later Designer entry from the Sidebar/Dashboard card (which never raises it) still starts
  // unselected (0039 T#3). Not persisted: it only bridges the Dialog -> Designer move.
  // flowgate.v02.0043: readable, so an Explorer already showing that Project can react to a
  // "열기" of the same Project (File › Project 열기… / 최근 Project from inside the Designer).
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

  // flowgate.v02.0043: a Group / Flow was created, so every cached tree of that Project is
  // stale. Dropping them makes the next non-forced read go to the server; a consumer that
  // already shows the tree re-reads with `force` on its own (the Explorer's refreshToken).
  function invalidateGroupTree(projectId: string): void {
    const next = { ...groupTrees.value }
    for (const includeTerminal of [false, true]) delete next[groupTreeKey(projectId, includeTerminal)]
    groupTrees.value = next
  }

  // ── Group / Flow creation (flowgate.v02.0043) ──────────────────────────────────────
  // Stateless like the loaders below: the create dialogs own their own submit / stale guards.
  // The Project list session's `requestEpoch` does not gate these.
  async function createGroup(projectId: string, input: CreateGroupInput): Promise<CreatedGroup> {
    const created = await createGroupApi(projectId, input)
    invalidateGroupTree(projectId)
    return created
  }

  async function createFlow(input: CreateFlowInput): Promise<CreatedFlow> {
    const created = await createFlowApi(input)
    invalidateGroupTree(input.projectId)
    return created
  }

  /** The existing identifier suggestion (POST /slug/romanize) for a Group key. */
  function suggestSlug(text: string): Promise<string | null> {
    return suggestProjectSlug(text)
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

  // flowgate.v02.0045: the Designer Canvas Graph loader / saver (P0008 §3 / §4). Stateless like
  // the loaders above: per-Flow staleness and save batching belong to the Center session.
  function loadFlowCanvas(flowId: string, view?: string, collapse?: readonly string[]): Promise<FlowCanvas> {
    return fetchFlowCanvas(flowId, view, collapse)
  }

  function saveFlowCanvasLayout(flowId: string, input: FlowCanvasLayoutInput): Promise<FlowCanvasLayoutResult> {
    return saveFlowCanvasLayoutApi(flowId, input)
  }

  // flowgate.v02.0064 T#4 (P0006 §4.2.2): one card's title save; the version order of what is shown is the Center's.
  function saveFlowNodeTitle(flowId: string, nodeKey: string, title: string): Promise<FlowCanvasTitleState> {
    return nodeTitleSave(flowId, nodeKey, title)
  }

  // flowgate.v02.0045 S3+S4: the Graph definition saver (P0008 §5 / §6). Draft batching, version
  // tracking and key remapping belong to the Center session's draft editor.
  function loadFlowGraph(flowId: string): Promise<FlowGraphDefinition> {
    return fetchFlowGraph(flowId)
  }

  function saveFlowGraphDraft(flowId: string, input: FlowGraphDraftInput): Promise<FlowGraphDraftSaveResult> {
    return saveFlowGraphDraftApi(flowId, input)
  }

  function validateFlowGraphDraft(flowId: string, draftVersion: number): Promise<FlowGraphValidation> {
    return validateFlowGraphDraftApi(flowId, draftVersion)
  }

  function confirmFlowGraphRevision(flowId: string, draftVersion: number, baseRevisionNo: number | null): Promise<FlowGraphConfirmResult> {
    return confirmFlowGraphRevisionApi(flowId, draftVersion, baseRevisionNo)
  }

  // flowgate.v02.0053 T#9: the Graph run of a Flow (P0008 §7 S5). Stateless like the loaders above: which
  // run the top band shows, and the 다음 단계 / 전체 실행 / 중지 sequences, belong to the Center session.
  function loadFlowRun(flowId: string): Promise<FlowRun | null> {
    return fetchCurrentFlowRun(flowId)
  }

  // flowgate.v02.0061 T#14: the step / full command (다음 단계 / 전체 실행) and Pause.
  function startFlowRun(flowId: string, mode?: FlowRunCommand): Promise<FlowRun> {
    return startFlowRunApi(flowId, mode)
  }

  function pauseFlowRun(runId: string): Promise<FlowRun> {
    return pauseFlowRunApi(runId)
  }

  function stopFlowRun(runId: string): Promise<FlowRun> {
    return stopFlowRunApi(runId)
  }

  function resumeFlowRun(runId: string): Promise<FlowRun> {
    return resumeFlowRunApi(runId)
  }

  // flowgate.v02.0056 F2-1: a card's [승인] on the Canvas (O1 0005-TR §4-1); the run it answers goes to the band.
  function approveFlowRunNode(runId: string, nodeKey: string, resultDocId: string | null): Promise<{ run: FlowRun; approval: FlowRunApproval }> {
    return approveFlowRunNodeApi(runId, nodeKey, resultDocId)
  }

  // flowgate.v02.0061 T#15: the Graph Time Machine of a done card (its context menu); the run it answers goes to the band.
  function timeMachineFlowRunNode(runId: string, nodeKey: string): Promise<{ run: FlowRun; timeMachine: FlowRunTimeMachine }> {
    return timeMachineFlowRunNodeApi(runId, nodeKey)
  }

  // Other tabs' / devices' Canvas changes of one project (P0008 §8). Returns the unsubscribe.
  function subscribeFlowCanvasChanges(projectId: string, handlers: FlowCanvasChangeHandlers): () => void {
    return subscribeFlowCanvasChangesApi(projectId, handlers)
  }

  // flowgate.v02.0048 T#2: any event of one project over the same shared stream as the Canvas
  // (projectEvents.ts, one EventSource per project). Returns the unsubscribe.
  function subscribeProjectEvents(projectId: string, subscription: ProjectEventSubscription): () => void {
    return subscribeProjectEventsApi(projectId, subscription)
  }

  // flowgate.v02.0043: set by openDialog({ create: true }) and read once by ProjectDialog when it
  // opens. Not persisted; a Dialog open without the option clears it.
  const createRequested = ref(false)

  function openDialog(options: OpenDialogOptions = {}): void {
    createRequested.value = options.create === true
    dialogOpen.value = true
    void fetchProjects()
  }

  function takeCreateRequest(): boolean {
    const requested = createRequested.value
    createRequested.value = false
    return requested
  }

  function closeDialog(): void {
    dialogOpen.value = false
    createRequested.value = false
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
    // Read-only views of the recent lists and the explicit-open signal (flowgate.v02.0043).
    recentProjects: computed(() => recentProjects.value),
    recentGroups: computed(() => recentGroups.value),
    recentFlows: computed(() => recentFlows.value),
    explicitOpenProjectId: computed(() => explicitOpenProjectId.value),
    fetchProjects,
    createProject,
    setCurrentProject,
    openRecentProject,
    closeCurrentProject,
    recordRecentGroup,
    recordRecentFlow,
    requestExplicitOpen,
    takeExplicitOpen,
    openDialog,
    takeCreateRequest,
    closeDialog,
    getCachedGroupTree,
    fetchGroupTree,
    createGroup,
    createFlow,
    suggestSlug,
    loadProjectGitStatus,
    loadFlowFileTree,
    loadFlowChanges,
    loadFlowFileDiff,
    loadFlowAiActivity,
    loadBaseFileDiff,
    loadMergeReviewFileDiff,
    loadFlowCanvas,
    saveFlowCanvasLayout,
    saveFlowNodeTitle,
    loadFlowGraph,
    saveFlowGraphDraft,
    validateFlowGraphDraft,
    confirmFlowGraphRevision,
    loadFlowRun,
    startFlowRun,
    pauseFlowRun,
    stopFlowRun,
    resumeFlowRun,
    approveFlowRunNode,
    timeMachineFlowRunNode,
    subscribeFlowCanvasChanges,
    subscribeProjectEvents,
  }
})
