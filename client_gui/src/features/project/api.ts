// Project API adapter (WP0005 T#1 §1/§3). Reuses the existing `@shared/api` transport
// (same helper client/src's v0.1 screens already call) instead of a new client — this file
// only adds the request shape + snake_case-to-frontend-model normalization boundary that
// D0004 §1.2/§4.3 assigns to features/project. WP0005 T#1 §3 scoped it to three endpoints:
// active project list, create, and identifier suggestion. flowgate.v02.0023 adds the
// read-only project group tree (fetchProjectGroupTree) so the Designer and later v0.2 screens
// share one tree request path instead of each growing one. flowgate.v02.0024 T0005 adds the
// read-only Flow file context the Designer File Explorer needs (project Git status, Flow
// tree, Base-relative changes, one-file diff, AI activity) below. flowgate.v02.0033 widens the
// Git status / AI activity normalization with the fields the Designer Git part reads and adds
// the Base file and merge review file diff reads. flowgate.v02.0066 adds the Base remote state
// (ahead/behind, unpushed) of git/status and the scope floor / line counts / tool artifact count
// of the group change list, and keeps the transport error of the change list and file diff.
// archive/restore/source-path and Git mutation endpoints are still not called here (the Git
// part's own api.ts owns those).
// flowgate.v02.0060 T#2 adds the existing server contracts the Designer context menu runs (0006-D rev2 §4.3 2-B):
// Group title (PATCH modules), Flow title (PUT groups), Flow dispose (DC), and the Flow worktree's folder / file
// create, download, upload and delete — each with the Flow's group_id, never a Base fallback. No permanent delete,
// document delete or branch delete is called.

import api, {
  downloadBlobRequest,
  extractApiErrorMessage,
  getRequest,
  patchRequest,
  postFormRequest,
  postRequest,
  putRequest,
} from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'
import { serverDetailOf } from '../../core/i18n/serverDetail'
import type {
  BaseFileDiff,
  CreateFlowInput,
  CreateGroupInput,
  CreateProjectInput,
  CreatedFlow,
  CreatedGroup,
  FlowAiActivity,
  FlowChangeSet,
  FlowFileDiff,
  FlowFileNode,
  FlowFileTree,
  GroupScopeFloor,
  GroupTreeNode,
  GroupTreeNodeKind,
  MergeReviewFileDiff,
  Project,
  ProjectGitPendingPlan,
  ProjectGitPendingRow,
  ProjectGitStatus,
  ProjectGitUnpushed,
  ProjectGitUnpushedMerge,
  ProjectModule,
  SourceDownload,
  SourceMutationRefusal,
  SourceUploadResult,
} from './types'

// flowgate.v02.0088 F10: the Project / Group / Flow list and create calls below throw an Error whose
// message is only the server's own diagnostic ('' when it sent none). The screen that shows the
// failure owns the localized general text (gui.project.* / gui.designer.file_menu.* /
// gui.designer.project_explorer.*), so an English transport line or an English default from this
// adapter is never what a ko/ja person reads first.

interface RawProjectModule {
  name: string
  title?: string | null
}

interface RawProject {
  project_id: string
  project_name: string
  description?: string | null
  color?: string | null
  is_active: number | boolean
  created_at?: string | null
  updated_at?: string | null
  modules?: RawProjectModule[] | null
}

interface RawProjectListResponse {
  projects?: RawProject[]
}

// Every current backend/v0.1 list response filters this row out (NR0003 §3.1) — it is a
// bookkeeping project, not a real one a user can select.
const SYSTEM_PROJECT_ID = '__SYSTEM__'

function toProjectModule(raw: RawProjectModule): ProjectModule {
  return { name: raw.name, title: raw.title || raw.name }
}

function toProject(raw: RawProject): Project {
  return {
    id: raw.project_id,
    name: raw.project_name,
    description: raw.description ?? '',
    color: raw.color ?? null,
    isActive: raw.is_active === 1 || raw.is_active === true,
    createdAt: raw.created_at ?? null,
    updatedAt: raw.updated_at ?? null,
    modules: Array.isArray(raw.modules) ? raw.modules.map(toProjectModule) : [],
  }
}

/** GET /api/v1/projects?status=active, normalized and with the system row dropped. */
export async function fetchActiveProjects(): Promise<Project[]> {
  try {
    const response = await getRequest<RawProjectListResponse | RawProject[]>(
      '/api/v1/projects',
      { status: 'active' },
    )
    const raw = Array.isArray(response.data) ? response.data : response.data?.projects ?? []
    return raw.filter((p) => p.project_id !== SYSTEM_PROJECT_ID).map(toProject)
  } catch (error) {
    throw new Error(serverDetailOf(error))
  }
}

/**
 * POST /api/v1/projects with exactly the fields the backend reads (project_name/
 * project_id/description/color) — the v0.1 create form's unconnected decorative fields
 * (structure/storage type/custom path/members) are not part of this input type at all
 * (D0004 §2.4, NR0003 §3.3).
 */
export async function createProject(input: CreateProjectInput): Promise<Project> {
  try {
    const response = await postRequest<RawProject>('/api/v1/projects', {
      project_name: input.name,
      project_id: input.id,
      description: input.description ?? null,
      color: input.color ?? null,
    })
    return toProject(response.data)
  } catch (error) {
    throw new Error(serverDetailOf(error))
  }
}

/** POST /api/v1/slug/romanize — the existing project-id suggestion endpoint (NR0003 §3.3). */
export async function suggestProjectSlug(text: string): Promise<string | null> {
  const trimmed = text.trim()
  if (!trimmed) return null
  try {
    const response = await postRequest<{ suggested?: string | null }>(
      '/api/v1/slug/romanize',
      { text: trimmed },
    )
    return response.data?.suggested || null
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to suggest a project id.'))
  }
}

// ── Group tree (flowgate.v02.0023) ───────────────────────────────────────────────────
interface RawGroupTreeNode {
  id: string
  parent_id: string | null
  node_type: string
  label?: string | null
  title?: string | null
  number?: string | null
}

interface RawGroupTreeResponse {
  data?: { nodes?: RawGroupTreeNode[] | null } | null
}

const GROUP_TREE_NODE_KINDS: readonly GroupTreeNodeKind[] = [
  'project',
  'module',
  'group',
  'subgroup',
  'orphan',
  'document',
]

function toGroupTreeNode(raw: RawGroupTreeNode): GroupTreeNode | null {
  const kind = GROUP_TREE_NODE_KINDS.find((k) => k === raw.node_type)
  // A node kind this client does not know is skipped rather than guessed into a level.
  if (!kind || !raw.id) return null
  return {
    id: raw.id,
    parentId: raw.parent_id ?? null,
    kind,
    label: raw.label || raw.id,
    title: raw.title || null,
    number: raw.number ?? null,
  }
}

/**
 * GET /api/v1/projects/{id}/groups/tree — the same flat tree the v0.1 Dashboard explorer
 * reads (server `tree_routes.get_groups_tree`). `include_terminal=false` is the pruned
 * variant (final-approved/discarded groups dropped); `force=true` asks the server for a read
 * that does not join an older in-flight one. `branch` is not sent: the server ignores it.
 */
export async function fetchProjectGroupTree(
  projectId: string,
  options: { includeTerminal: boolean; force: boolean },
): Promise<GroupTreeNode[]> {
  try {
    const response = await getRequest<RawGroupTreeResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/groups/tree`,
      {
        include_terminal: options.includeTerminal ? 'true' : 'false',
        force: options.force ? 'true' : 'false',
      },
    )
    const raw = response.data?.data?.nodes ?? []
    return raw.map(toGroupTreeNode).filter((n): n is GroupTreeNode => n !== null)
  } catch (error) {
    // flowgate.v02.0105 T#1: the HTTP status and a network failure stay on the error (`status` / `network`, the
    // transport error as `cause`), so the Designer's F5 restore tells a refused Project (403 / 404) apart from a
    // passing failure. The message is unchanged.
    const { status, network } = normalizeApiError(error)
    throw Object.assign(new Error(serverDetailOf(error)), { status, network, cause: error })
  }
}

// ── Flow file context (flowgate.v02.0024 T0005) ─────────────────────────────────────────
// Same server contracts the v0.1 Dashboard explorer reads (git/status, group tree,
// group changes/diff, ai-invoke/active); only the transport shapes and normalization live
// here. In-flight GET coalescing comes from `@shared/api` getRequest itself.

interface RawGitSlot {
  group_id: string
  branch?: string | null
  status?: string | null
  writable?: boolean
}

interface RawGitPendingRow {
  group_id: string
  status?: string | null
  merge_id?: number | null
  ac_doc_id?: string | null
  review_state?: string | null
  final_approval_bound?: boolean | null
  // S1 (flowgate.v02.0033-D §4.6) working names; absent on a server without S1.
  planned_action?: string | null
  planned_target_branch?: string | null
}

interface RawGitStatus {
  enabled?: boolean
  // The server fills an unset Base with its legacy main branch; base_branch_configured says
  // whether base_branch is the stored Project Base or that fallback (0024 TR0006).
  base_branch?: string | null
  base_branch_configured?: boolean | null
  slots?: RawGitSlot[] | null
  pending?: RawGitPendingRow[] | null
  base_dirty?: {
    files?: string[] | null
    merge_in_progress?: unknown
    ai_run?: unknown
  } | null
  base_untracked?: {
    files?: string[] | null
    truncated?: boolean | null
    // S2 working name; absent on a server without S2.
    total?: number | null
  } | null
  // flowgate.v02.0066: Base against its remote (local refs only).
  ahead_count?: number | null
  behind_count?: number | null
  unpushed?: RawGitUnpushed | null
}

interface RawGitUnpushed {
  count?: number | null
  commit_count?: number | null
  measured?: boolean | null
  remote_branch_missing?: boolean | null
  local_commit_count?: number | null
  merges?: Array<RawGitUnpushedMerge | null> | null
}

interface RawGitUnpushedMerge {
  merge_commit?: string | null
  group_id?: string | null
  subject?: string | null
  merged_at?: string | null
  can_unmerge?: boolean | null
  blocked_reason?: string | null
}

interface RawFileNode {
  id: string
  parent_id: string | null
  type: 'folder' | 'file'
  name: string
  label?: string | null
  path: string
  permissions?: string[] | null
}

interface RawFileTree {
  branch: string
  commit?: string | null
  nodes?: RawFileNode[] | null
}

interface RawChangeSet {
  group_id: string
  branch: string
  commit?: string | null
  base_branch?: string | null
  changes?: { path: string; status: string; insertions?: number | null; deletions?: number | null }[] | null
  // flowgate.v02.0066: the scope floor and the tool artifacts left out of the list.
  work_base_ref?: string | null
  scope_base_sha?: string | null
  work_base_sha?: string | null
  work_base_sync_sha?: string | null
  work_base_state?: string | null
  tool_artifacts?: string[] | null
}

interface RawFileDiff {
  group_id: string
  branch: string
  commit?: string | null
  base_branch?: string | null
  merge_base?: string | null
  path: string
  status: 'M' | 'A' | 'D'
  old: FlowFileDiff['old']
  new: FlowFileDiff['new']
}

const projectGitPath = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/git`
const groupGitPath = (projectId: string, groupId: string) =>
  `${projectGitPath(projectId)}/groups/${encodeURIComponent(groupId)}`

const toSlashes = (path: string) => path.replace(/\\/g, '/')

// A branch name the server reported, trimmed; blank/missing is "unknown" (null). There is
// deliberately no default branch here — callers must treat null as unresolved.
function branchOrNull(value: string | null | undefined): string | null {
  const trimmed = typeof value === 'string' ? value.trim() : ''
  return trimmed || null
}

// S1 is detected by the field being there at all, not by its value (0033-D §3.4).
function toPendingPlan(raw: RawGitPendingRow): ProjectGitPendingPlan | null {
  if (!('planned_action' in raw) && !('planned_target_branch' in raw)) return null
  return {
    action: branchOrNull(raw.planned_action),
    targetBranch: branchOrNull(raw.planned_target_branch),
  }
}

function toPendingRow(raw: RawGitPendingRow): ProjectGitPendingRow {
  return {
    groupId: raw.group_id,
    status: raw.status ?? '',
    mergeId: typeof raw.merge_id === 'number' ? raw.merge_id : null,
    acDocId: raw.ac_doc_id || null,
    reviewState: raw.review_state || null,
    finalApprovalBound: raw.final_approval_bound === true,
    plan: toPendingPlan(raw),
  }
}

const pathList = (files: string[] | null | undefined) =>
  (files ?? []).filter((f) => typeof f === 'string' && f).map(toSlashes)

const countOrNull = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

// flowgate.v02.0066 — an unmeasured Base keeps its counts null, never 0 (0066 D#2 §4-2).
function toUnpushed(raw: RawGitUnpushed | null | undefined): ProjectGitUnpushed | null {
  if (!raw || typeof raw !== 'object') return null
  const measured = raw.measured === true
  return {
    count: measured ? countOrNull(raw.count) : null,
    commitCount: measured ? countOrNull(raw.commit_count) : null,
    measured,
    remoteBranchMissing: typeof raw.remote_branch_missing === 'boolean' ? raw.remote_branch_missing : null,
    localCommitCount: countOrNull(raw.local_commit_count),
    merges: (raw.merges ?? [])
      .filter((m): m is RawGitUnpushedMerge => !!m && !!branchOrNull(m.merge_commit))
      .map(
        (m): ProjectGitUnpushedMerge => ({
          mergeCommit: branchOrNull(m.merge_commit) as string,
          groupId: branchOrNull(m.group_id),
          subject: typeof m.subject === 'string' ? m.subject : null,
          mergedAt: branchOrNull(m.merged_at),
          canUnmerge: m.can_unmerge === true,
          blockedReason: branchOrNull(m.blocked_reason),
        }),
      ),
  }
}

function toGitStatus(raw: RawGitStatus): ProjectGitStatus {
  const untracked = raw.base_untracked ?? {}
  return {
    enabled: raw.enabled === true,
    // Only a Base the server confirms as configured counts. Its main-branch fallback for an
    // unset Base — or a server too old to say — is unresolved, never a comparison base.
    baseBranch: raw.base_branch_configured === true ? branchOrNull(raw.base_branch) : null,
    slots: (raw.slots ?? []).map((slot) => ({
      groupId: slot.group_id,
      branch: slot.branch ?? null,
      status: slot.status ?? '',
      writable: slot.writable === true,
    })),
    pending: (raw.pending ?? []).filter((row) => row && row.group_id).map(toPendingRow),
    baseDirty: {
      files: pathList(raw.base_dirty?.files),
      mergeInProgress: !!raw.base_dirty?.merge_in_progress,
      aiRun: !!raw.base_dirty?.ai_run,
    },
    baseUntracked: {
      files: pathList(untracked.files),
      truncated: untracked.truncated === true,
      total: typeof untracked.total === 'number' && Number.isFinite(untracked.total) ? untracked.total : null,
    },
    // flowgate.v02.0066: only what the server sends; an absent field stays out of the model.
    ...('ahead_count' in raw ? { aheadCount: countOrNull(raw.ahead_count) } : {}),
    ...('behind_count' in raw ? { behindCount: countOrNull(raw.behind_count) } : {}),
    ...('unpushed' in raw ? { unpushed: toUnpushed(raw.unpushed) } : {}),
  }
}

const FLOOR_FIELDS = ['work_base_ref', 'scope_base_sha', 'work_base_sha', 'work_base_sync_sha', 'work_base_state'] as const

// flowgate.v02.0066 — the group scope floor of a /changes answer; undefined when the server
// sends none of its fields (a server before the floor).
function toScopeFloor(raw: RawChangeSet): GroupScopeFloor | undefined {
  if (!FLOOR_FIELDS.some((key) => key in raw)) return undefined
  return {
    workBaseRef: branchOrNull(raw.work_base_ref),
    scopeBaseSha: branchOrNull(raw.scope_base_sha),
    workBaseSha: branchOrNull(raw.work_base_sha),
    workBaseSyncSha: branchOrNull(raw.work_base_sync_sha),
    workBaseState: branchOrNull(raw.work_base_state),
  }
}

// Keeps the transport error on the thrown one, so a consumer that has to tell a 403/404 or
// a network failure apart (the Designer Git part, 0033-D §4.5) can read the server code.
function loadError(error: unknown, fallback: string): Error {
  const wrapped = new Error(extractApiErrorMessage(error, fallback)) as Error & { cause?: unknown }
  wrapped.cause = error
  return wrapped
}

function toFileNode(raw: RawFileNode): FlowFileNode {
  return {
    id: raw.id,
    parentId: raw.parent_id ?? null,
    type: raw.type,
    name: raw.name,
    label: raw.label || raw.name,
    path: toSlashes(raw.path),
    permissions: raw.permissions ?? [],
  }
}

/** GET /api/v1/projects/{pid}/git/status — null when the server has no status for it. */
export async function fetchProjectGitStatus(projectId: string): Promise<ProjectGitStatus | null> {
  try {
    const response = await getRequest<{ status?: RawGitStatus | null }>(`${projectGitPath(projectId)}/status`)
    const raw = response.data?.status
    return raw ? toGitStatus(raw) : null
  } catch (error) {
    throw loadError(error, 'Failed to load the project Git status.')
  }
}

/**
 * GET /api/v1/projects/{pid}/git/groups/{gid}/tree — the Flow worktree tree, read from Git
 * objects plus untracked entries (no checkout switch). One retry, like the v0.1 explorer's
 * tree read, because a worktree mid-write can fail a single read. Only readable nodes are
 * returned.
 */
export async function fetchFlowFileTree(projectId: string, groupId: string): Promise<FlowFileTree> {
  const path = `${groupGitPath(projectId, groupId)}/tree`
  try {
    let response
    try {
      response = await getRequest<{ data: RawFileTree }>(path)
    } catch {
      response = await getRequest<{ data: RawFileTree }>(path)
    }
    const data = response.data.data
    return {
      branch: data.branch,
      commit: data.commit ?? null,
      nodes: (data.nodes ?? []).filter((n) => (n.permissions ?? []).includes('read')).map(toFileNode),
    }
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load the Flow file tree.'))
  }
}

/**
 * GET /api/v1/projects/{pid}/git/groups/{gid}/changes — committed + worktree + untracked
 * ('?') changes of the Flow since its merge-base. `base` asks the server to compare against
 * that branch instead of the group's own work base (server `base` parameter, 0024 TR0004
 * rev2); without it the server answers against the group work base. `baseBranch` in the
 * result is what the server says it actually compared against.
 */
export async function fetchFlowChangeSet(
  projectId: string,
  groupId: string,
  options: { base?: string } = {},
): Promise<FlowChangeSet> {
  try {
    const params = options.base ? { base: options.base } : {}
    const response = await getRequest<{ data: RawChangeSet }>(`${groupGitPath(projectId, groupId)}/changes`, params)
    const data = response.data.data
    const floor = toScopeFloor(data)
    return {
      groupId: data.group_id,
      branch: data.branch,
      commit: data.commit ?? null,
      baseBranch: branchOrNull(data.base_branch),
      changes: (data.changes ?? []).map((c) => ({
        path: toSlashes(c.path),
        status: c.status,
        ...('insertions' in c ? { insertions: countOrNull(c.insertions) } : {}),
        ...('deletions' in c ? { deletions: countOrNull(c.deletions) } : {}),
      })),
      ...(floor ? { floor } : {}),
      // Only the count: the artifact paths are not kept.
      ...(Array.isArray(data.tool_artifacts) ? { toolArtifactCount: data.tool_artifacts.length } : {}),
    }
  } catch (error) {
    // flowgate.v02.0066: the transport error stays as `cause` (a floor 409, 403, 404 or a
    // network failure must be told apart); the message is unchanged.
    throw loadError(error, 'Failed to load the Flow changes.')
  }
}

/**
 * GET /api/v1/projects/{pid}/git/groups/{gid}/diff — old/new content of one Flow file.
 * Pass the same `base` the change list used so the diff and the list share one merge-base;
 * `ref` pins the read to a tree commit.
 */
export async function fetchFlowFileDiff(
  projectId: string,
  groupId: string,
  path: string,
  options: { base?: string; ref?: string } = {},
): Promise<FlowFileDiff> {
  try {
    const params: Record<string, string> = { path }
    if (options.base) params.base = options.base
    if (options.ref) params.ref = options.ref
    const response = await getRequest<{ data: RawFileDiff }>(`${groupGitPath(projectId, groupId)}/diff`, params)
    const data = response.data.data
    return {
      groupId: data.group_id,
      branch: data.branch,
      commit: data.commit ?? null,
      baseBranch: branchOrNull(data.base_branch),
      mergeBase: data.merge_base ?? null,
      path: toSlashes(data.path),
      status: data.status,
      old: data.old,
      new: data.new,
    }
  } catch (error) {
    // flowgate.v02.0066: `cause` keeps the transport error (floor 409 / 404 / 403 / network).
    throw loadError(error, 'Failed to load the file diff.')
  }
}

/** GET /api/v1/ai-invoke/active?group_id= — whether an AI run currently owns the group. */
export async function fetchFlowAiActivity(groupId: string): Promise<FlowAiActivity> {
  try {
    const response = await getRequest<{
      active?: boolean
      status?: string | null
      run_id?: string | null
      action_scope?: string | null
    }>('/api/v1/ai-invoke/active', { group_id: groupId })
    const data = response.data ?? {}
    const active = data.active === true && data.status !== 'finished'
    return {
      active,
      runId: active ? data.run_id || null : null,
      status: active ? data.status || null : null,
      actionScope: active ? data.action_scope || null : null,
    }
  } catch (error) {
    throw loadError(error, 'Failed to load the Flow AI activity.')
  }
}

// ── Base / merge review file diff (flowgate.v02.0033) ─────────────────────────────────

interface RawBaseFileDiff {
  branch?: string | null
  commit?: string | null
  path: string
  status: 'M' | 'A' | 'D'
  old: BaseFileDiff['old']
  new: BaseFileDiff['new']
}

/**
 * GET /api/v1/projects/{pid}/git/diff?path= — HEAD blob vs working tree of one Base
 * checkout file. `status` (A: not in HEAD / D: not on disk / M) is the server's answer; a
 * hidden or vanished path is a 404.
 */
export async function fetchBaseFileDiff(projectId: string, path: string): Promise<BaseFileDiff> {
  try {
    const response = await getRequest<{ data: RawBaseFileDiff }>(`${projectGitPath(projectId)}/diff`, { path })
    const data = response.data.data
    return {
      branch: branchOrNull(data.branch),
      commit: data.commit ?? null,
      path: toSlashes(data.path),
      status: data.status,
      old: data.old,
      new: data.new,
    }
  } catch (error) {
    throw loadError(error, 'Failed to load the file diff.')
  }
}

interface RawReviewFileDiff {
  path: string
  status: 'M' | 'A' | 'D'
  old: MergeReviewFileDiff['old']
  new: MergeReviewFileDiff['new']
}

/**
 * GET /api/v1/groups/{gid}/git/merge/{merge_id}/review-diff?path= — old/new of one file of
 * the frozen review candidate (not the working tree).
 */
export async function fetchMergeReviewFileDiff(
  groupId: string,
  mergeId: number,
  path: string,
): Promise<MergeReviewFileDiff> {
  try {
    const response = await getRequest<{ data: RawReviewFileDiff }>(
      `/api/v1/groups/${encodeURIComponent(groupId)}/git/merge/${encodeURIComponent(String(mergeId))}/review-diff`,
      { path },
    )
    const data = response.data.data
    return { groupId, mergeId, path: toSlashes(data.path), status: data.status, old: data.old, new: data.new }
  } catch (error) {
    throw loadError(error, 'Failed to load the file diff.')
  }
}

// ── Group / Flow creation (flowgate.v02.0043) ──────────────────────────────────────────
// The same two server contracts the v0.1 explorer create modal calls
// (client/src/main/components/CreateEditGroupModal.vue): module create (T505) and group create
// (T191). Only the request shape and the id normalization live here.

interface RawCreatedModule {
  ok?: boolean
  module_id?: string
  name?: string | null
  title?: string | null
}

/**
 * POST /api/v1/projects/{pid}/modules — creates a Group (v0.1 module). The returned id is the
 * group tree node id (`module:<project>:<name>`), the one the Project Explorer shows.
 */
export async function createGroup(projectId: string, input: CreateGroupInput): Promise<CreatedGroup> {
  try {
    const response = await postRequest<RawCreatedModule>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/modules`,
      { name: input.name, title: input.title },
    )
    const name = response.data?.name || input.name
    return { id: `module:${projectId}:${name}`, name, title: response.data?.title || input.title }
  } catch (error) {
    throw new Error(serverDetailOf(error))
  }
}

interface RawCreatedGroup {
  group_id: string
  created_at?: string | null
}

/** POST /api/v1/groups — creates a Flow (v0.1 group) under a Group; the server reserves the id. */
export async function createFlow(input: CreateFlowInput): Promise<CreatedFlow> {
  try {
    const response = await postRequest<RawCreatedGroup>('/api/v1/groups', {
      project_id: input.projectId,
      title: input.title,
      module: input.moduleName,
      parent_id: null,
      priority: null,
    })
    return { id: response.data.group_id, title: input.title }
  } catch (error) {
    throw new Error(serverDetailOf(error))
  }
}

// ── Group / Flow / source file commands (flowgate.v02.0060 T#2, 0006-D rev2 §4.3 2-B) ──────────────────────────
// The same server contracts the v0.1 explorer calls (CreateFileFolderModal, FileTreeNode, useFileUpload,
// GroupTreeNode / DocHeader dispose, module / group edit). Each Flow worktree call sends the Flow's group_id, so
// the server resolves that worktree or refuses (no silent Base fallback). A refusal is thrown as an Error whose
// `refusal` says what the screen shows; `cause` keeps the transport error.

const projectPath = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}`

/** The server code and message of a refused call, from the shapes these routes answer with. */
function refusalParts(error: unknown): { status: number | null; code: string | null; text: string } {
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response
  const data = response?.data && typeof response.data === 'object' ? (response.data as Record<string, any>) : null
  const nested = data?.error && typeof data.error === 'object' ? data.error : data?.detail && typeof data.detail === 'object' ? data.detail : null
  const code = nested?.code ?? data?.code ?? null
  const text = [nested?.message, typeof data?.detail === 'string' ? data.detail : null, data?.message]
    .filter((part): part is string => typeof part === 'string')
    .join(' ')
  return { status: typeof response?.status === 'number' ? response.status : null, code: typeof code === 'string' ? code : null, text }
}

/**
 * The transport error under a thrown one: itself when it has the HTTP response, else the first `cause` that has it
 * (the loaders' `loadError` keeps the transport error there). The error itself when none has a response (network).
 */
function transportError(error: unknown): unknown {
  let current: unknown = error
  for (let depth = 0; depth < 5 && current && typeof current === 'object'; depth += 1) {
    if ((current as { response?: unknown }).response) return current
    current = (current as { cause?: unknown }).cause
  }
  return error
}

/**
 * What a refused Group / Flow / source command (or a failed read of the 수정 확인 window) means on screen. The server's
 * text is only read, never shown.
 */
export function sourceMutationRefusal(error: unknown): SourceMutationRefusal {
  const own = (error as { refusal?: SourceMutationRefusal } | null)?.refusal
  if (own) return own
  const transport = transportError(error)
  const hasResponse = !!(transport as { response?: unknown } | null)?.response
  const { status, code, text } = refusalParts(transport)
  const lower = text.toLowerCase()
  const reason: SourceMutationRefusal['reason'] = !hasResponse
    ? 'network'
    : code === 'TR2_MANAGED_FILE'
      ? 'managed'
      : code === 'SOURCE_MUTATION_BUSY' || code === 'GROUP_AI_RUN_LOCKED' || status === 423
        ? 'locked'
        : code === 'SOURCE_WORKTREE_UNAVAILABLE' || code === 'WORKTREE_UNAVAILABLE' || lower.includes('worktree is unavailable') || lower.includes('worktree is not available')
          ? 'worktree'
          : lower.includes('group_disposed') || lower.includes('already disposed')
            ? 'disposed'
            : status === 403 || code === 'FORBIDDEN'
              ? 'forbidden'
              : status === 413 || code === 'PAYLOAD_TOO_LARGE'
                ? 'too_large'
                : lower.includes('already exists')
                  ? 'exists'
                  : status === 404 || /NOT_FOUND$/.test(code ?? '') || lower.includes('not found')
                    ? 'not_found'
                    : status === 400 || status === 409 || status === 422
                      ? 'invalid'
                      : 'failed'
  return { reason, status, code }
}

function refused(error: unknown, fallback: string): Error {
  const wrapped = new Error(extractApiErrorMessage(error, fallback)) as Error & { cause?: unknown; refusal?: SourceMutationRefusal }
  wrapped.cause = error
  wrapped.refusal = sourceMutationRefusal(error)
  return wrapped
}

/**
 * PATCH /api/v1/projects/{pid}/modules/{name} — a Group's display name (v0.1 module title). The module key
 * (`name`) and so the Group id stay as they are (server T559: slug immutable).
 */
export async function renameGroup(projectId: string, moduleName: string, title: string): Promise<void> {
  try {
    await patchRequest(`${projectPath(projectId)}/modules/${encodeURIComponent(moduleName)}`, { title })
  } catch (error) {
    throw refused(error, 'Failed to rename the group.')
  }
}

/** PUT /api/v1/groups/{gid} with the title only — a Flow's display name (its id stays; a disposed Flow is 409). */
export async function renameFlow(flowId: string, title: string): Promise<void> {
  try {
    await putRequest(`/api/v1/groups/${encodeURIComponent(flowId)}`, { title })
  } catch (error) {
    throw refused(error, 'Failed to rename the flow.')
  }
}

/**
 * POST /api/v1/groups/{gid}/dispose — the existing Flow dispose (DC): a file-less DC document and the
 * group_disposed event, then the server cleans the Flow's worktree / work branch. The reason goes as
 * `reason_detail`, as the v0.1 dispose confirm sends it. The route answers 200 with `status: 'error'` for a Flow
 * that is gone or already disposed; that is a refusal too.
 */
export async function disposeFlow(flowId: string, reason: string): Promise<void> {
  let answer: { status?: string; message?: string } | null = null
  try {
    answer = (await postRequest<{ status?: string; message?: string }>(`/api/v1/groups/${encodeURIComponent(flowId)}/dispose`, {
      reason_detail: reason,
    })).data ?? null
  } catch (error) {
    throw refused(error, 'Failed to dispose the flow.')
  }
  if (answer?.status === 'error') {
    const message = (answer.message ?? '').toLowerCase()
    const reasonOf: SourceMutationRefusal['reason'] = message.includes('already disposed')
      ? 'disposed'
      : message.includes('not found') ? 'not_found' : 'failed'
    const wrapped = new Error('Failed to dispose the flow.') as Error & { refusal?: SourceMutationRefusal }
    wrapped.refusal = { reason: reasonOf, status: 200, code: null }
    throw wrapped
  }
}

/**
 * POST /api/v1/storage/folder | /storage/file — an empty folder / file named `name` under `parentPath` ('' = the
 * root) of the Flow's worktree. Refusals come back as HTTP 400 with `{status: 'error', message, code?}`.
 */
export async function createSourceEntry(
  projectId: string,
  flowId: string,
  kind: 'folder' | 'file',
  parentPath: string,
  name: string,
): Promise<void> {
  try {
    await postRequest(`/api/v1/storage/${kind}`, { project_id: projectId, parent_path: parentPath, name, group_id: flowId })
  } catch (error) {
    throw refused(error, kind === 'folder' ? 'Failed to create the folder.' : 'Failed to create the file.')
  }
}

function filenameOf(disposition: unknown, fallback: string): string {
  if (typeof disposition !== 'string') return fallback
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      // A malformed encoded name falls through to the plain one.
    }
  }
  const plain = /filename="([^"]+)"/i.exec(disposition)
  return plain ? plain[1] : fallback
}

/** A refusal of a blob request arrives as a blob too: read it back into the JSON envelope before classifying. */
async function readBlobRefusal(error: unknown): Promise<void> {
  const response = (error as { response?: { data?: unknown } } | null)?.response
  const data = response?.data
  if (!response || typeof Blob === 'undefined' || !(data instanceof Blob)) return
  try {
    response.data = JSON.parse(await blobText(data))
  } catch {
    // Not JSON: the status alone is read.
  }
}

function blobText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

/**
 * GET /api/v1/projects/{pid}/files/download (file) | /files/download-zip (folder, empty folders not included) —
 * from the Flow's worktree (`group_id`). `path` '.' is the worktree root.
 */
export async function downloadSourceEntry(
  projectId: string,
  flowId: string,
  kind: 'folder' | 'file',
  path: string,
  fallbackName: string,
): Promise<SourceDownload> {
  try {
    const response = await downloadBlobRequest(`${projectPath(projectId)}/files/${kind === 'file' ? 'download' : 'download-zip'}`, {
      path,
      group_id: flowId,
    })
    return { filename: filenameOf(response.headers?.['content-disposition'], fallbackName), blob: response.data }
  } catch (error) {
    await readBlobRefusal(error)
    throw refused(error, 'Failed to download.')
  }
}

/**
 * POST /api/v1/projects/{pid}/files/upload — multipart `files[]` under `target_path` ('' = the root) of the Flow's
 * worktree. The server overwrites an existing file and checks every path before writing any (one batch).
 */
export async function uploadSourceFiles(
  projectId: string,
  flowId: string,
  targetPath: string,
  files: readonly File[],
): Promise<SourceUploadResult> {
  const form = new FormData()
  form.append('target_path', targetPath)
  form.append('group_id', flowId)
  for (const file of files) form.append('files[]', file, file.name)
  try {
    const response = await postFormRequest<Partial<SourceUploadResult>>(`${projectPath(projectId)}/files/upload`, form)
    const data = response.data ?? {}
    return {
      uploaded: Array.isArray(data.uploaded) ? data.uploaded.map((u) => ({ path: toSlashes(u.path), size: u.size })) : [],
      skipped: Array.isArray(data.skipped) ? data.skipped.map((u) => ({ path: toSlashes(u.path), reason: u.reason })) : [],
    }
  } catch (error) {
    throw refused(error, 'Failed to upload.')
  }
}

/**
 * DELETE /api/v1/projects/{pid}/files {path, type, group_id} — one file, or a folder with everything under it, in
 * the Flow's worktree. The server refuses the root, a type mismatch, a managed path under it and a busy group.
 */
export async function deleteSourceEntry(projectId: string, flowId: string, kind: 'folder' | 'file', path: string): Promise<void> {
  try {
    await api.delete(`${projectPath(projectId)}/files`, { data: { path, type: kind, group_id: flowId } })
  } catch (error) {
    throw refused(error, 'Failed to delete.')
  }
}
