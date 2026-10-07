// flowgate.v02.0033 — Git action adapter of the Designer Git part. Every endpoint here is an
// existing server contract the v0.1 GitStatusPanel / GitMergeReviewDialog already use
// (0033-D §2.3, §4.4); this file only adds the request shapes and the snake_case-to-model
// boundary. The status / AI activity / file diff reads live in features/project.
//
//   conflicts   GET   /api/v1/groups/{gid}/git/merge/{merge_id}/conflicts
//   abort       POST  /api/v1/groups/{gid}/git/merge/{merge_id}/abort
//   review      GET   /api/v1/groups/{gid}/git/merge/{merge_id}/review
//   approve     POST  /api/v1/groups/{gid}/git/merge/{merge_id}/approve   { attempt_id, review_fingerprint }
//   reject      POST  /api/v1/groups/{gid}/git/merge/{merge_id}/reject    { reason, provider_id, provider_pinned }
//   finalize    POST  /api/v1/groups/{gid}/git/finalize                   { action, git_target_branch? }
//   push        POST  /api/v1/projects/{pid}/git/push                     { branch }
//   base ops    POST  /api/v1/projects/{pid}/git/base-commit | base-revert | base-remove
//   AI          POST  /api/v1/ai-invoke/start (resolve_conflict) · POST /api/v1/ai-invoke/{run}/cancel
//   providers   GET   /api/v1/ai-invoke/providers?project=
//
// Errors are thrown as GitRequestError carrying `normalizeGitError`'s code / status — the
// server `message` is never surfaced (0033-D §4.5).
import { getRequest, postRequest } from '@shared/api'
import { normalizeGitError, type NormalizedGitError } from '@shared/gitErrors'
import type {
  GitApproveResult,
  GitBaseCommitResult,
  GitBaseFileResult,
  GitBaseRemoveResult,
  GitBaseRevertResult,
  GitConflictSession,
  GitFinalizeAction,
  GitFinalizeResult,
  GitProviders,
  GitRejectResult,
  GitReview,
} from './types'

const API = '/api/v1'
const groupGit = (groupId: string) => `${API}/groups/${encodeURIComponent(groupId)}/git`
const mergePath = (groupId: string, mergeId: number) =>
  `${groupGit(groupId)}/merge/${encodeURIComponent(String(mergeId))}`
const projectGit = (projectId: string) => `${API}/projects/${encodeURIComponent(projectId)}/git`

const toSlashes = (path: string) => path.replace(/\\/g, '/')

/** A failed Git request: the normalized server code / HTTP status (no message text). */
export class GitRequestError extends Error {
  constructor(readonly error: NormalizedGitError) {
    super(error.code ?? (error.network ? 'network' : `http_${error.status ?? 'unknown'}`))
    this.name = 'GitRequestError'
  }
}

/**
 * The normalized Git error of anything a Git call (or a features/project loader, which keeps
 * the transport error as `cause`) threw.
 */
export function gitErrorOf(error: unknown): NormalizedGitError {
  if (error instanceof GitRequestError) return error.error
  const cause = (error as { cause?: unknown } | null)?.cause
  return normalizeGitError(cause ?? error)
}

interface Envelope {
  ok?: boolean
  error?: unknown
}

// Both failure shapes count: a 4xx/5xx and an HTTP 200 `{ ok: false, error }` (§4.5).
async function call<T extends Envelope>(request: () => Promise<{ data: T; status?: number }>): Promise<T> {
  let response
  try {
    response = await request()
  } catch (error) {
    throw new GitRequestError(normalizeGitError(error))
  }
  const data = (response.data ?? {}) as T
  if (data.ok === false && data.error) {
    throw new GitRequestError(normalizeGitError({ response: { status: response.status ?? 200, data } }))
  }
  return data
}

const str = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value.trim() : null)
const strList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string' && !!v).map(toSlashes) : []

// ── Conflict session ───────────────────────────────────────────────────────────────────

interface RawConflicts extends Envelope {
  merge_id?: number
  branch?: string | null
  base_branch?: string | null
  target_branch?: string | null
  kind?: string | null
  tr_conflict?: Record<string, unknown> | null
  files?: Array<{ path?: string; conflict_count?: number }> | null
}

// Session kinds of a TR rewind's revert / a forward restore's reapply (db/git_integration.py).
const TR_SESSION_KINDS: readonly string[] = ['tr_revert', 'tr_reapply']

export async function fetchConflicts(groupId: string, mergeId: number): Promise<GitConflictSession> {
  const data = await call(() => getRequest<RawConflicts>(`${mergePath(groupId, mergeId)}/conflicts`))
  return {
    mergeId: typeof data.merge_id === 'number' ? data.merge_id : mergeId,
    branch: str(data.branch),
    baseBranch: str(data.base_branch),
    targetBranch: str(data.target_branch),
    trConflict: (!!data.tr_conflict && typeof data.tr_conflict === 'object') || TR_SESSION_KINDS.includes(str(data.kind) ?? ''),
    // The file content the server also sends is not kept: the part never renders it.
    files: (data.files ?? [])
      .filter((f) => f && typeof f.path === 'string' && f.path)
      .map((f) => ({
        path: toSlashes(f.path as string),
        conflictCount: typeof f.conflict_count === 'number' ? f.conflict_count : 0,
      })),
  }
}

export async function abortMerge(groupId: string, mergeId: number): Promise<void> {
  await call(() => postRequest<Envelope>(`${mergePath(groupId, mergeId)}/abort`, {}))
}

/** Splits a group id into the project / module / group triple ai-invoke wants. */
export function splitGroupId(groupId: string): { project: string; module: string; group: string } | null {
  const parts = groupId.split('.')
  if (parts.length < 3 || parts.some((p) => p === '')) return null
  return { project: parts[0], module: parts[1], group: parts.slice(2).join('.') }
}

/**
 * POST /ai-invoke/start for a group conflict (not the branch-merge `ai-resolve`). No provider
 * (the server's default chain) and no `auto` authority: the screen has no picker for either.
 */
export async function startConflictAi(groupId: string, mergeId: number): Promise<string | null> {
  const parts = splitGroupId(groupId)
  if (!parts) throw new GitRequestError(normalizeGitError({ error: { code: 'invalid_request' } }))
  const data = await call(() =>
    postRequest<Envelope & { run_id?: string | null }>(`${API}/ai-invoke/start`, {
      project: parts.project,
      module: parts.module,
      group: parts.group,
      action_scope: 'resolve_conflict',
      mode: 'single',
      merge_id: mergeId,
    }),
  )
  return str(data.run_id)
}

/** POST /ai-invoke/{run}/cancel — the server status after the request (e.g. `finished`). */
export async function cancelAiRun(runId: string): Promise<string | null> {
  const data = await call(() =>
    postRequest<Envelope & { status?: string | null }>(`${API}/ai-invoke/${encodeURIComponent(runId)}/cancel`, {}),
  )
  return str(data.status)
}

// ── Merge review ───────────────────────────────────────────────────────────────────────

interface RawReview {
  review_state?: string | null
  review_fingerprint?: string | null
  changes?: Array<{ path?: string; status?: string | null }> | null
  resolver_provider?: string | null
  reconciliation_kind?: string | null
  can_approve?: boolean
  can_reject?: boolean
}

export async function fetchReview(groupId: string, mergeId: number): Promise<GitReview> {
  const data = await call(() => getRequest<Envelope & { result?: RawReview }>(`${mergePath(groupId, mergeId)}/review`))
  const raw = data.result ?? {}
  return {
    mergeId,
    reviewState: str(raw.review_state),
    fingerprint: str(raw.review_fingerprint),
    changes: (raw.changes ?? [])
      .filter((c) => c && typeof c.path === 'string' && c.path)
      .map((c) => ({ path: toSlashes(c.path as string), status: str(c.status) ?? '' })),
    resolverProvider: str(raw.resolver_provider),
    reconciliationKind: str(raw.reconciliation_kind),
    canApprove: raw.can_approve === true,
    canReject: raw.can_reject === true,
  }
}

interface RawApproveResult {
  status?: string | null
  review_state?: string | null
  reconciliation_kind?: string | null
  errors?: unknown[] | null
}

export async function approveReview(
  groupId: string,
  mergeId: number,
  attemptId: string,
  fingerprint: string,
): Promise<GitApproveResult> {
  const data = await call(() =>
    postRequest<Envelope & { result?: RawApproveResult }>(`${mergePath(groupId, mergeId)}/approve`, {
      attempt_id: attemptId,
      review_fingerprint: fingerprint,
    }),
  )
  const raw = data.result ?? {}
  return {
    status: str(raw.status) ?? '',
    reviewState: str(raw.review_state),
    reconciliationKind: str(raw.reconciliation_kind),
    errorCount: Array.isArray(raw.errors) ? raw.errors.length : 0,
  }
}

/** Reject with the reason and the pinned default AI the server requires (§3.7). */
export async function rejectReview(
  groupId: string,
  mergeId: number,
  reason: string,
  providerId: string,
): Promise<GitRejectResult> {
  const data = await call(() =>
    postRequest<Envelope & { result?: { status?: string | null; resolver_run_id?: string | null } }>(
      `${mergePath(groupId, mergeId)}/reject`,
      { reason, provider_id: providerId, provider_pinned: true },
    ),
  )
  return { status: str(data.result?.status) ?? '', resolverRunId: str(data.result?.resolver_run_id) }
}

export async function fetchProviders(projectId: string): Promise<GitProviders> {
  const data = await call(() =>
    getRequest<Envelope & { providers?: Array<{ id?: string | null } | null> | null; default_provider_id?: string | null }>(
      `${API}/ai-invoke/providers`,
      { project: projectId },
    ),
  )
  return {
    providerIds: (data.providers ?? []).map((p) => str(p?.id)).filter((id): id is string => !!id),
    defaultProviderId: str(data.default_provider_id),
  }
}

// ── Remote apply ───────────────────────────────────────────────────────────────────────

/**
 * POST /groups/{gid}/git/finalize with an explicit action — never the project default and
 * never an omitted action (§3.4). `targetBranch` only goes out for merge / merge_only.
 */
export async function finalizeGroup(
  groupId: string,
  action: GitFinalizeAction,
  targetBranch: string | null,
): Promise<GitFinalizeResult> {
  const body: Record<string, string> = { action }
  if (targetBranch && (action === 'merge' || action === 'merge_only')) body.git_target_branch = targetBranch
  const data = await call(() => postRequest<Envelope & { result?: { status?: string | null } }>(`${groupGit(groupId)}/finalize`, body))
  return { status: str(data.result?.status) ?? '' }
}

export async function pushBranch(projectId: string, branch: string | null): Promise<GitFinalizeResult> {
  const data = await call(() =>
    postRequest<Envelope & { result?: { status?: string | null } }>(`${projectGit(projectId)}/push`, branch ? { branch } : {}),
  )
  return { status: str(data.result?.status) ?? '' }
}

// ── Base checkout changes ──────────────────────────────────────────────────────────────

/** POST base-commit with the explicit path list (new files need it). Blank message is omitted. */
export async function baseCommit(projectId: string, message: string, paths: string[]): Promise<GitBaseCommitResult> {
  const body: Record<string, unknown> = { paths }
  if (message.trim()) body.message = message.trim()
  const data = await call(() =>
    postRequest<Envelope & { result?: { committed?: boolean; remaining?: string[]; remaining_untracked?: string[] } }>(
      `${projectGit(projectId)}/base-commit`,
      body,
    ),
  )
  return {
    committed: data.result?.committed === true,
    remaining: strList(data.result?.remaining),
    remainingUntracked: strList(data.result?.remaining_untracked),
  }
}

type RawFileResults = Envelope & {
  result?: { results?: Array<{ path?: string; result?: string }>; remaining?: string[]; remaining_untracked?: string[] }
}

const fileResults = (raw: RawFileResults): GitBaseFileResult[] =>
  (raw.result?.results ?? [])
    .filter((r) => r && typeof r.path === 'string')
    .map((r) => ({ path: toSlashes(r.path as string), result: str(r.result) ?? '' }))

// base-revert / base-remove answer `ok: false` together with a per-file `result` when one
// of the files failed — that is a result to report, not a request failure.
async function fileCall(request: () => Promise<{ data: RawFileResults; status?: number }>): Promise<RawFileResults> {
  let response
  try {
    response = await request()
  } catch (error) {
    throw new GitRequestError(normalizeGitError(error))
  }
  const data = response.data ?? {}
  if (data.ok === false && !data.result) {
    throw new GitRequestError(normalizeGitError({ response: { status: response.status ?? 200, data } }))
  }
  return data
}

export async function baseRevert(projectId: string, files: string[]): Promise<GitBaseRevertResult> {
  const data = await fileCall(() => postRequest<RawFileResults>(`${projectGit(projectId)}/base-revert`, { files }))
  return { results: fileResults(data), remaining: strList(data.result?.remaining) }
}

/** Deletes untracked Base files for good (there is no committed copy). */
export async function baseRemove(projectId: string, files: string[]): Promise<GitBaseRemoveResult> {
  const data = await fileCall(() => postRequest<RawFileResults>(`${projectGit(projectId)}/base-remove`, { files }))
  return { results: fileResults(data), remainingUntracked: strList(data.result?.remaining_untracked) }
}
