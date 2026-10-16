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
// flowgate.v02.0065 — the project Git settings contracts the v0.1 GitSettingsView uses, for the
// Git tab's own `Git 연결` window (no new endpoint, no new field):
//
//   config      GET   /api/v1/projects/{pid}/git/config
//   save        PUT   /api/v1/projects/{pid}/git/config            { repo_url, provider, username, secret, base_branch, enabled: true }
//   test        POST  /api/v1/projects/{pid}/git/test-connection   { repo_url, username, secret, base_branch, provider }
//   provision   GET | POST /api/v1/projects/{pid}/git/provision
//
// flowgate.v02.0066 — the Group worktree contracts (0066 D#1 E1–E12) of the Git tab's worktree
// area, in their own section at the end ("Group worktree"); none of the functions above changes.
//
// Errors are thrown as GitRequestError carrying `normalizeGitError`'s code / status — the
// server `message` is never surfaced (0033-D §4.5).
import { getRequest, postRequest, putRequest } from '@shared/api'
import { normalizeGitError, type NormalizedGitError } from '@shared/gitErrors'
import type {
  GitApproveResult,
  GitBaseCommitResult,
  GitBaseFileResult,
  GitBaseRemoveResult,
  GitBaseRevertResult,
  GitConfigSave,
  GitConfigView,
  GitConflictSession,
  GitConnectionTest,
  GitFinalizeAction,
  GitFinalizeResult,
  GitProviders,
  GitProvisionResult,
  GitProvisionView,
  GitRejectResult,
  GitReview,
  ManualMergeBlock,
  ManualMergePreview,
  ManualMergeResult,
  ManualMergeTarget,
  ManualMergeView,
  MergeResultPushResult,
  MergeResultRemote,
  MergeResultRow,
  MergeResultTarget,
  MergeResultsView,
  RefsFetchBranchState,
  RefsFetchGroup,
  RefsFetchGroupResult,
  RefsFetchResult,
  WorktreeBlocker,
  WorktreeBodyError,
  WorktreeCommitResult,
  WorktreeCommitSuggestion,
  WorktreeErrorDetails,
  WorktreeFile,
  WorktreeFileResult,
  WorktreeFilesResult,
  WorktreeRequestFailure,
  WorktreeSelection,
  WorktreeStageLetter,
  WorktreeStageResult,
  WorktreeStatus,
  WorktreeStatusAfter,
  WorktreeWorkLetter,
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

// ── Project Git settings (flowgate.v02.0065) ───────────────────────────────────────────

interface RawConfig {
  repo_url?: string | null
  provider?: string | null
  username?: string | null
  base_branch?: string | null
  enabled?: boolean
  has_secret?: boolean
}

type RawConfigAnswer = Envelope & { configured?: boolean; config?: RawConfig | null }

// The masked token the server also sends is not kept.
function toConfigView(data: RawConfigAnswer): GitConfigView {
  const raw = data.config ?? null
  return {
    configured: data.configured === true && !!raw,
    repoUrl: str(raw?.repo_url) ?? '',
    provider: str(raw?.provider) ?? 'generic',
    username: str(raw?.username),
    baseBranch: str(raw?.base_branch) ?? 'main',
    enabled: raw?.enabled === true,
    hasSecret: raw?.has_secret === true,
  }
}

/** GET git/config. */
export async function fetchGitConfig(projectId: string): Promise<GitConfigView> {
  return toConfigView(await call(() => getRequest<RawConfigAnswer>(`${projectGit(projectId)}/config`)))
}

/**
 * PUT git/config with Git on. Only the window's fields and `enabled: true` go out; every other
 * settings field is left unsent so the server keeps what is stored (exclude_unset).
 */
export async function saveGitConfig(projectId: string, input: GitConfigSave): Promise<GitConfigView> {
  const data = await call(() =>
    putRequest<RawConfigAnswer>(`${projectGit(projectId)}/config`, {
      repo_url: input.repoUrl,
      provider: input.provider,
      username: input.username || null,
      secret: input.secret || null,
      base_branch: input.baseBranch,
      enabled: true,
    }),
  )
  return toConfigView(data)
}

/**
 * POST git/test-connection with the window's values, before anything is saved. A blank token
 * goes out as null: the server then tests with the stored one. The server's failure text
 * (git's last stderr line, which can hold the URL) is dropped; only its code is kept.
 */
export async function testGitConnection(projectId: string, input: GitConfigSave): Promise<GitConnectionTest> {
  const data = await call(() =>
    postRequest<
      Envelope & {
        result?: {
          reachable?: boolean | null
          authenticated?: boolean | null
          remote_default_branch?: string | null
          failure?: { code?: string | null } | null
        }
      }
    >(`${projectGit(projectId)}/test-connection`, {
      repo_url: input.repoUrl,
      username: input.username || null,
      secret: input.secret || null,
      base_branch: input.baseBranch,
      provider: input.provider,
    }),
  )
  const raw = data.result ?? {}
  return {
    reachable: typeof raw.reachable === 'boolean' ? raw.reachable : null,
    authenticated: typeof raw.authenticated === 'boolean' ? raw.authenticated : null,
    failureCode: str(raw.failure?.code),
    remoteDefaultBranch: str(raw.remote_default_branch),
  }
}

interface RawProvisionView {
  configured?: boolean
  enabled?: boolean
  base_checkout_exists?: boolean
  last_attempt?: { result?: string | null } | null
}

const toProvisionView = (raw: RawProvisionView | null | undefined): GitProvisionView | null =>
  raw && typeof raw === 'object'
    ? {
        configured: raw.configured === true,
        enabled: raw.enabled === true,
        baseCheckoutExists: raw.base_checkout_exists === true,
        lastAttemptResult: str(raw.last_attempt?.result),
      }
    : null

/** GET git/provision. */
export async function fetchGitProvision(projectId: string): Promise<GitProvisionView | null> {
  const data = await call(() => getRequest<Envelope & { provision?: RawProvisionView | null }>(`${projectGit(projectId)}/provision`))
  return toProvisionView(data.provision)
}

/**
 * POST git/provision (synchronous). A preparation that did not work is an HTTP 200 with
 * `result.status: 'failed'` — the caller decides on `status`, never on the HTTP success alone.
 */
export async function provisionGit(projectId: string): Promise<GitProvisionResult> {
  const data = await call(() =>
    postRequest<
      Envelope & {
        result?: { status?: string | null; mode?: string | null; reason?: string | null; provision?: RawProvisionView | null }
      }
    >(`${projectGit(projectId)}/provision`, {}),
  )
  const raw = data.result ?? {}
  return {
    status: str(raw.status) ?? '',
    mode: str(raw.mode),
    busy: raw.reason === 'git_busy',
    provision: toProvisionView(raw.provision),
  }
}

// ── Group worktree (flowgate.v02.0066) ─────────────────────────────────────────────────
// The manual Git contracts of the current Flow's Group worktree (0066 D#1 §5, D#2 §5-1):
//
//   E1  status      GET   /api/v1/groups/{gid}/git/worktree/status
//   E2  stage       POST  /api/v1/groups/{gid}/git/worktree/stage                 { paths | all, worktree_token? }
//   E3  unstage     POST  /api/v1/groups/{gid}/git/worktree/unstage               { paths | all, worktree_token? }
//   E4  commit      POST  /api/v1/groups/{gid}/git/worktree/commit                { message, index_token? }
//   E5  revert      POST  /api/v1/groups/{gid}/git/worktree/revert                { paths, worktree_token? }
//   E6  remove      POST  /api/v1/groups/{gid}/git/worktree/remove                { paths, worktree_token? }
//   E7  suggest     POST  /api/v1/groups/{gid}/git/worktree/commit-message/suggest { provider_id }
//   E8  merge view  GET   /api/v1/groups/{gid}/git/manual-merge[?target=]
//   E9  merge       POST  /api/v1/groups/{gid}/git/manual-merge                   { target_branch?, push }
//   E10 results     GET   /api/v1/groups/{gid}/git/merge-result
//   E11 push        POST  /api/v1/groups/{gid}/git/merge-result/push              { merge_id, merge_commit }
//   E12 fetch-refs  POST  /api/v1/projects/{pid}/git/fetch-refs                   { group_id? }
//
// Every answer is `{ ok: true, result }`. These are not the Base checkout operations above
// (base-commit / base-revert / base-remove) and never call finalize, /git/push or /git/fetch.
// Server `message` and diagnostic text never enter a model; an error detail keeps only the
// keys the screen reads (worktreeErrorDetails).

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)
const flag = (value: unknown): boolean => value === true
const flagOrNull = (value: unknown): boolean | null => (typeof value === 'boolean' ? value : null)
const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const int = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) ? value : null)
const idOrNull = (value: unknown): string | null =>
  typeof value === 'number' && Number.isFinite(value) ? String(value) : str(value)
const pathOrNull = (value: unknown): string | null => (typeof value === 'string' && value ? toSlashes(value) : null)
const records = (value: unknown): Record<string, unknown>[] => (Array.isArray(value) ? value.filter(isRecord) : [])
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.map(str).filter((v): v is string => v !== null) : []

function countMap(value: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (!isRecord(value)) return out
  for (const [key, count] of Object.entries(value)) {
    const n = num(count)
    if (n !== null) out[key] = n
  }
  return out
}

/** Only the detail keys the screen reads (0066 D#2 §5-1); paths and texts are dropped. */
export function worktreeErrorDetails(value: unknown): WorktreeErrorDetails {
  const raw = isRecord(value) ? value : {}
  return {
    reason: str(raw.reason),
    jobId: idOrNull(raw.job_id),
    mergeId: int(raw.merge_id),
    latestMergeId: int(raw.latest_merge_id),
    sessionKind: str(raw.session_kind),
    targetFastForwarded: flagOrNull(raw.target_fast_forwarded),
    pushVia: str(raw.push_via),
    count: num(raw.count),
    counts: countMap(raw.counts),
    protectedUncommitted: num(raw.protected_uncommitted),
  }
}

/** The code and the allowed details of what a Group worktree call threw. */
export function worktreeErrorOf(error: unknown): WorktreeRequestFailure {
  const normalized = gitErrorOf(error)
  return {
    code: normalized.code,
    httpStatus: normalized.status,
    network: normalized.network,
    details: worktreeErrorDetails(normalized.details),
  }
}

// An error the server put inside an answer body ({ code, message, details }).
function bodyError(value: unknown): WorktreeBodyError | null {
  if (!isRecord(value)) return null
  const code = str(value.code)
  return code ? { code, details: worktreeErrorDetails(value.details) } : null
}

const worktreePath = (groupId: string) => `${groupGit(groupId)}/worktree`

type ResultEnvelope = Envelope & { result?: unknown }

async function resultOf(request: () => Promise<{ data: ResultEnvelope; status?: number }>): Promise<Record<string, unknown>> {
  const data = await call(request)
  return isRecord(data.result) ? data.result : {}
}

const STAGE_LETTERS: readonly string[] = ['A', 'M', 'D', 'T', 'U']
const WORK_LETTERS: readonly string[] = ['M', 'D', 'T']

function toWorktreeFile(raw: Record<string, unknown>): WorktreeFile | null {
  const path = pathOrNull(raw.path)
  if (!path) return null
  const staged = str(raw.staged)
  const unstaged = str(raw.unstaged)
  return {
    path,
    inHead: flag(raw.in_head),
    staged: staged && STAGE_LETTERS.includes(staged) ? (staged as WorktreeStageLetter) : null,
    unstaged: unstaged && WORK_LETTERS.includes(unstaged) ? (unstaged as WorktreeWorkLetter) : null,
    untracked: flag(raw.untracked),
    fileClass: str(raw.class),
    protectedReason: str(raw.protected_reason),
    unsupported: str(raw.unsupported),
    canStage: flag(raw.can_stage),
    canUnstage: flag(raw.can_unstage),
    canRevert: flag(raw.can_revert),
    canRemove: flag(raw.can_remove),
    insertions: num(raw.insertions),
    deletions: num(raw.deletions),
  }
}

function toWorktreeStatus(raw: Record<string, unknown>, groupId: string): WorktreeStatus {
  const state = isRecord(raw.git_state) ? raw.git_state : {}
  const floor = isRecord(raw.floor) ? raw.floor : {}
  const committed = isRecord(raw.committed) ? raw.committed : null
  const counts = isRecord(raw.counts) ? raw.counts : {}
  const ownership = isRecord(raw.ownership) ? raw.ownership : {}
  return {
    groupId: str(raw.group_id) ?? groupId,
    branch: str(raw.branch),
    head: str(raw.head),
    worktreeAvailable: flag(raw.worktree_available),
    worktreeToken: str(raw.worktree_token),
    indexToken: str(raw.index_token),
    gitState: {
      status: str(state.status),
      rootWfDone: flag(state.root_wf_done),
      finalApprovalBound: flag(state.final_approval_bound),
    },
    floor: {
      workBaseRef: str(floor.work_base_ref),
      scopeBaseSha: str(floor.scope_base_sha),
      workBaseSha: str(floor.work_base_sha),
      workBaseSyncSha: str(floor.work_base_sync_sha),
      workBaseState: str(floor.work_base_state),
      error: bodyError(floor.error),
    },
    committed: committed
      ? { commitCount: num(committed.commit_count), changedFileCount: num(committed.changed_file_count) }
      : null,
    files: records(raw.files)
      .map(toWorktreeFile)
      .filter((f): f is WorktreeFile => f !== null),
    // Exactly the server values: not filled with 0, not recounted from `files`.
    counts: {
      changed: num(counts.changed),
      staged: num(counts.staged),
      unstaged: num(counts.unstaged),
      untracked: num(counts.untracked),
      excludedNewUntracked: num(counts.excluded_new_untracked),
      hidden: num(counts.hidden),
      managedDirty: num(counts.managed_dirty),
      unsupported: num(counts.unsupported),
    },
    ownership: { available: flag(ownership.available), reason: str(ownership.reason) },
    blockers: records(raw.blockers)
      .map((b) => ({ code: str(b.code), operations: strings(b.operations) }))
      .filter((b): b is WorktreeBlocker => b.code !== null),
  }
}

// The refreshed E1 status of an E2–E6 answer; null with the server's code when it failed.
function statusAfter(raw: Record<string, unknown>, groupId: string): WorktreeStatusAfter {
  const status = isRecord(raw.status) ? toWorktreeStatus(raw.status, groupId) : null
  return { status, statusError: status ? null : str(raw.status_error) ?? 'status_unavailable' }
}

/** E1 — the worktree files split into Stage / working changes, tokens, floor and blockers. */
export async function fetchWorktreeStatus(groupId: string): Promise<WorktreeStatus> {
  const raw = await resultOf(() => getRequest<ResultEnvelope>(`${worktreePath(groupId)}/status`))
  return toWorktreeStatus(raw, groupId)
}

// `paths` and `all` never go out together (the server refuses that with 422).
function selectionBody(selection: WorktreeSelection, worktreeToken: string | null): Record<string, unknown> {
  const body: Record<string, unknown> = 'all' in selection ? { all: true } : { paths: selection.paths.map(toSlashes) }
  if (worktreeToken) body.worktree_token = worktreeToken
  return body
}

async function changeIndex(
  groupId: string,
  op: 'stage' | 'unstage',
  selection: WorktreeSelection,
  worktreeToken: string | null,
): Promise<WorktreeStageResult> {
  const raw = await resultOf(() =>
    postRequest<ResultEnvelope>(`${worktreePath(groupId)}/${op}`, selectionBody(selection, worktreeToken)),
  )
  const skipped = isRecord(raw.skipped) ? raw.skipped : {}
  return {
    applied: strList(raw.applied),
    appliedHiddenCount: num(raw.applied_hidden_count) ?? 0,
    skipped: {
      managed: num(skipped.managed) ?? 0,
      hidden: num(skipped.hidden) ?? 0,
      excludedNew: num(skipped.excluded_new) ?? 0,
      unsupported: num(skipped.unsupported) ?? 0,
    },
    ...statusAfter(raw, groupId),
  }
}

/** E2 — Stage the given paths, or every stageable entry (`all`). */
export function stageWorktree(
  groupId: string,
  selection: WorktreeSelection,
  worktreeToken: string | null,
): Promise<WorktreeStageResult> {
  return changeIndex(groupId, 'stage', selection, worktreeToken)
}

/** E3 — Unstage the given paths, or everything staged (`all`, hidden entries included). */
export function unstageWorktree(
  groupId: string,
  selection: WorktreeSelection,
  worktreeToken: string | null,
): Promise<WorktreeStageResult> {
  return changeIndex(groupId, 'unstage', selection, worktreeToken)
}

/**
 * E4 — commit exactly what is staged in the Group worktree (no Base, no merge, no push). The
 * message goes out as typed: the server normalizes it and is the one that refuses a blank one.
 */
export async function commitWorktree(groupId: string, message: string, indexToken: string | null): Promise<WorktreeCommitResult> {
  const body: Record<string, unknown> = { message }
  if (indexToken) body.index_token = indexToken
  const raw = await resultOf(() => postRequest<ResultEnvelope>(`${worktreePath(groupId)}/commit`, body))
  const paths = strList(raw.committed_paths)
  return {
    commitSha: str(raw.commit_sha),
    commit: str(raw.commit),
    subject: str(raw.subject),
    committedPaths: paths,
    committedCount: num(raw.committed_count) ?? paths.length,
    ...statusAfter(raw, groupId),
  }
}

async function changeFiles(
  groupId: string,
  op: 'revert' | 'remove',
  paths: string[],
  worktreeToken: string | null,
): Promise<WorktreeFilesResult> {
  const body: Record<string, unknown> = { paths: paths.map(toSlashes) }
  if (worktreeToken) body.worktree_token = worktreeToken
  const raw = await resultOf(() => postRequest<ResultEnvelope>(`${worktreePath(groupId)}/${op}`, body))
  return {
    results: records(raw.results)
      .map((r) => ({ path: pathOrNull(r.path), result: str(r.result) ?? '' }))
      .filter((r): r is WorktreeFileResult => r.path !== null),
    ...statusAfter(raw, groupId),
  }
}

/** E5 — put existing paths back to their HEAD content. */
export function revertWorktree(groupId: string, paths: string[], worktreeToken: string | null): Promise<WorktreeFilesResult> {
  return changeFiles(groupId, 'revert', paths, worktreeToken)
}

/** E6 — delete new paths (there is no committed copy to go back to). */
export function removeWorktreeFiles(groupId: string, paths: string[], worktreeToken: string | null): Promise<WorktreeFilesResult> {
  return changeFiles(groupId, 'remove', paths, worktreeToken)
}

/** E7 — an AI draft of the commit subject from the staged snapshot. Commits nothing. */
export async function suggestWorktreeCommitMessage(groupId: string, providerId: string): Promise<WorktreeCommitSuggestion> {
  const raw = await resultOf(() =>
    postRequest<ResultEnvelope>(`${worktreePath(groupId)}/commit-message/suggest`, { provider_id: providerId }),
  )
  return {
    suggested: str(raw.suggested) ?? '',
    providerId: str(raw.provider_id),
    stagedCount: num(raw.staged_count),
    inputTruncated: flag(raw.input_truncated),
    head: str(raw.head),
    indexToken: str(raw.index_token),
  }
}

// ── Manual merge (E8 / E9) ──

function toManualMergePreview(value: unknown): ManualMergePreview | null {
  if (!isRecord(value)) return null
  return {
    targetBranch: str(value.target_branch),
    workBaseRef: str(value.work_base_ref),
    projectBaseBranch: str(value.project_base_branch),
    isWorkBase: flag(value.is_work_base),
    isProjectBase: flag(value.is_project_base),
    incomingWorkBaseCommits: num(value.incoming_work_base_commits),
    warnings: strings(value.warnings),
    error: bodyError(value.error),
  }
}

/** E8 — whether a merge would be admitted now, into which targets, and what it would carry. */
export async function fetchManualMerge(groupId: string, target: string | null): Promise<ManualMergeView> {
  const raw = await resultOf(() =>
    target
      ? getRequest<ResultEnvelope>(`${groupGit(groupId)}/manual-merge`, { target })
      : getRequest<ResultEnvelope>(`${groupGit(groupId)}/manual-merge`),
  )
  const approval = isRecord(raw.approval) ? raw.approval : {}
  const uncommitted = isRecord(raw.uncommitted) ? raw.uncommitted : null
  const graph = isRecord(raw.graph) ? raw.graph : null
  return {
    canMerge: flag(raw.can_merge),
    blocked: records(raw.blocked)
      .map((b) => {
        const error = bodyError(b)
        return error ? { code: error.code, reason: error.details.reason, details: error.details } : null
      })
      .filter((b): b is ManualMergeBlock => b !== null),
    admissionBasis: str(raw.admission_basis),
    approval: {
      acDocId: str(approval.ac_doc_id),
      status: str(approval.status),
      finalApprovalBound: flag(approval.final_approval_bound),
    },
    defaultTarget: str(raw.default_target),
    targets: records(raw.targets)
      .map((t) => ({
        name: str(t.name),
        kind: str(t.kind),
        eligible: flag(t.eligible),
        blockedReason: str(t.blocked_reason),
        isWorkBase: flag(t.is_work_base),
      }))
      .filter((t): t is ManualMergeTarget => t.name !== null),
    preview: toManualMergePreview(raw.preview),
    commitCount: num(raw.commit_count),
    floorError: bodyError(raw.floor_error),
    aheadOfTarget: num(raw.ahead_of_target),
    mergeNeeded: flag(raw.merge_needed),
    recovery: flag(raw.recovery),
    uncommitted: uncommitted
      ? {
          blocking: num(uncommitted.blocking),
          counts: countMap(uncommitted.counts),
          protectedUncommitted: num(uncommitted.protected_uncommitted),
          reason: str(uncommitted.reason),
        }
      : null,
    graph: graph
      ? { runPresent: flagOrNull(graph.run_present), blocker: str(graph.blocker), gateOpen: flagOrNull(graph.gate_open) }
      : null,
  }
}

/**
 * E9 — merge the group into `target` (null = its work base), and push that target when `push`.
 * The branch name is only ever one the server listed in E8 `targets`.
 */
export async function requestManualMerge(groupId: string, target: string | null, push: boolean): Promise<ManualMergeResult> {
  const body: Record<string, unknown> = { push }
  if (target) body.target_branch = target
  const raw = await resultOf(() => postRequest<ResultEnvelope>(`${groupGit(groupId)}/manual-merge`, body))
  const visibleConflicts = Array.isArray(raw.conflict_files) ? raw.conflict_files.length : 0
  return {
    status: str(raw.status) ?? '',
    groupStatus: str(raw.group_status),
    worktreeKept: flag(raw.worktree_kept),
    action: str(raw.action),
    targetBranch: str(raw.target_branch),
    mergeCommit: str(raw.merge_commit),
    mergeCommitSha: str(raw.merge_commit_sha),
    pushed: flag(raw.pushed),
    recovered: flag(raw.recovered),
    mergeId: int(raw.merge_id),
    // The paths are not kept: the conflict card reads its own session.
    conflictFileCount: visibleConflicts + (num(raw.hidden_conflict_count) ?? 0),
    excludedArtifactCount: num(raw.excluded_artifact_count),
    jobId: idOrNull(raw.job_id),
  }
}

// ── Merge results and follow-up push (E10 / E11) ──

function toMergeResultTarget(value: unknown): MergeResultTarget | null {
  if (!isRecord(value)) return null
  const targetBranch = str(value.target_branch)
  return targetBranch ? { targetBranch, isProjectBase: flag(value.is_project_base) } : null
}

function toMergeResultRemote(value: unknown): MergeResultRemote {
  const raw = isRecord(value) ? value : {}
  return {
    measured: flag(raw.measured),
    remoteBranchMissing: flagOrNull(raw.remote_branch_missing),
    localAheadCount: num(raw.local_ahead_count),
    containsMergeCommit: flagOrNull(raw.contains_merge_commit),
  }
}

/** E10 — the latest completed manual merge of each target, and which can still be pushed. */
export async function fetchMergeResults(groupId: string): Promise<MergeResultsView> {
  const raw = await resultOf(() => getRequest<ResultEnvelope>(`${groupGit(groupId)}/merge-result`))
  return {
    groupStatus: str(raw.group_status),
    results: records(raw.results)
      .map((r): MergeResultRow | null => {
        const mergeId = int(r.merge_id)
        const target = toMergeResultTarget(r.target)
        if (mergeId === null || !target) return null
        return {
          mergeId,
          target,
          mergeCommit: str(r.merge_commit),
          pushed: flag(r.pushed),
          recovered: flag(r.recovered),
          completedAt: str(r.completed_at),
          remote: toMergeResultRemote(r.remote),
          pushAvailable: flag(r.push_available),
          pushBlockedReason: str(r.push_blocked_reason),
        }
      })
      .filter((r): r is MergeResultRow => r !== null),
    pushCandidates: num(raw.push_candidates) ?? 0,
    groupBlocker: str(raw.group_blocker),
  }
}

/**
 * E11 — push the target of one recorded merge. Only the record (`merge_id`) and its merge
 * commit go out: the server settles the branch from the record, never from the client.
 */
export async function pushMergeResult(groupId: string, mergeId: number, mergeCommit: string): Promise<MergeResultPushResult> {
  const raw = await resultOf(() =>
    postRequest<ResultEnvelope>(`${groupGit(groupId)}/merge-result/push`, { merge_id: mergeId, merge_commit: mergeCommit }),
  )
  return {
    pushedRef: str(raw.pushed_ref),
    pushedHead: str(raw.pushed_head),
    targetBranch: str(raw.target_branch),
    isProjectBase: flag(raw.is_project_base),
    mergeId: int(raw.merge_id),
    mergeCommit: str(raw.merge_commit),
    alreadyPublished: flag(raw.already_published),
    jobId: idOrNull(raw.job_id),
    phase: str(raw.phase),
  }
}

// ── Fetch-only refresh (E12) ──

function toBranchState(raw: Record<string, unknown>): RefsFetchBranchState {
  return {
    branch: str(raw.branch),
    remoteBranchMissing: flagOrNull(raw.remote_branch_missing),
    aheadCount: num(raw.ahead_count),
    behindCount: num(raw.behind_count),
  }
}

function toRefsFetchGroup(value: unknown): RefsFetchGroup | null {
  if (!isRecord(value)) return null
  const groupId = str(value.group_id)
  if (!groupId) return null
  const workBase = isRecord(value.work_base) ? value.work_base : {}
  return {
    groupId,
    workBaseRef: str(workBase.work_base_ref),
    workBase: toBranchState(workBase),
    results: records(value.results)
      .map((r): RefsFetchGroupResult | null => {
        const mergeId = int(r.merge_id)
        const target = toMergeResultTarget(r.target)
        if (mergeId === null || !target) return null
        return {
          mergeId,
          target,
          remote: toMergeResultRemote(r.remote),
          pushAvailable: flag(r.push_available),
          pushBlockedReason: str(r.push_blocked_reason),
        }
      })
      .filter((r): r is RefsFetchGroupResult => r !== null),
    pushCandidates: num(value.push_candidates) ?? 0,
    groupBlocker: str(value.group_blocker),
  }
}

/**
 * E12 — refresh the remote-tracking refs only (no local branch, Base checkout or worktree
 * moves). With a group id the answer also carries that group's remote state.
 */
export async function fetchRefs(projectId: string, groupId: string | null): Promise<RefsFetchResult> {
  const raw = await resultOf(() => postRequest<ResultEnvelope>(`${projectGit(projectId)}/fetch-refs`, groupId ? { group_id: groupId } : {}))
  return {
    fetched: flag(raw.fetched),
    baseBranch: str(raw.base_branch),
    aheadCount: num(raw.ahead_count),
    behindCount: num(raw.behind_count),
    group: toRefsFetchGroup(raw.group),
  }
}
