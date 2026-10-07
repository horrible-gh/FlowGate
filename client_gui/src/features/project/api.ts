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
// the Base file and merge review file diff reads. archive/restore/source-path and Git
// mutation endpoints are still not called here (the Git part's own api.ts owns those).

import { extractApiErrorMessage, getRequest, postRequest } from '@shared/api'
import type {
  BaseFileDiff,
  CreateProjectInput,
  FlowAiActivity,
  FlowChangeSet,
  FlowFileDiff,
  FlowFileNode,
  FlowFileTree,
  GroupTreeNode,
  GroupTreeNodeKind,
  MergeReviewFileDiff,
  Project,
  ProjectGitPendingPlan,
  ProjectGitPendingRow,
  ProjectGitStatus,
  ProjectModule,
} from './types'

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
    throw new Error(extractApiErrorMessage(error, 'Failed to load projects.'))
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
    throw new Error(extractApiErrorMessage(error, 'Failed to create project.'))
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
    throw new Error(extractApiErrorMessage(error, 'Failed to load the project tree.'))
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
  changes?: { path: string; status: string }[] | null
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
    return {
      groupId: data.group_id,
      branch: data.branch,
      commit: data.commit ?? null,
      baseBranch: branchOrNull(data.base_branch),
      changes: (data.changes ?? []).map((c) => ({ path: toSlashes(c.path), status: c.status })),
    }
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load the Flow changes.'))
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
    throw new Error(extractApiErrorMessage(error, 'Failed to load the file diff.'))
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
