import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { getRequest } from '@shared/api'
import { useProjectStore } from './project'

// 0283 T0004 (NR0003 recommendation C): a single transient tree-fetch failure — a client timeout on
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
  // 0325 T0006 — per-file line counts from `git diff --numstat`. null means
  // "unknown" (binary, or an untracked file too large to scan), which the
  // summary must exclude from its totals instead of counting as 0.
  insertions?: number | null
  deletions?: number | null
}

// 0325 TR0007 rev1 — full /changes payload. The changed-file badges only ever needed
// the paths, but the [변경사항 열기] (Open changes) viewer also titles itself "<branch> ↔ <base>".
export interface GroupChangesData {
  group_id: string
  branch: string
  commit: string | null
  base_branch?: string | null
  changes: GroupChangeData[]
  // 0382 NR0003 proposal 3 — the "도구가 남긴 흔적" (traces the tools left behind) that the
  // screen hides. Excluded from the list, but not treated as nonexistent: 261 of them were
  // approved and merged without ever showing on any screen, and that was the core of the
  // incident. Servers (old) without this channel send this back as undefined.
  tool_artifacts?: string[]
}

// flowgate.default.0329 NR0003 — GET /projects/{pid}/git/groups/{gid}/diff
// (read_group_file_diff), unified onto the same old/new-content contract
// flowgate.default.0326 NR0005 §4 uses for the base file explorer's diff viewer
// (read_base_file_diff / FileDiffViewer.vue): the server ships raw content for
// each side and the client derives its own line diff (useFileDiff.ts).
export interface GroupDiffSide {
  exists: boolean
  binary: boolean
  truncated: boolean
  size: number
  content: string | null
}

export interface GroupFileDiffData {
  group_id: string
  branch: string
  commit: string | null
  base_branch?: string | null
  merge_base?: string | null
  path: string
  status: 'M' | 'A' | 'D'
  old: GroupDiffSide
  new: GroupDiffSide
}

export interface GroupBlobData {
  group_id: string
  branch: string
  // 0315 TR (NR0003 recommendation 3) — null for an untracked worktree file: it has no commit
  // object, so it is read straight off disk and carries no point-in-time to pin.
  commit: string | null
  path: string
  size: number
  binary: boolean
  truncated: boolean
  encoding: string | null
  content: string | null
  untracked?: boolean
}

// 0282 NR0003 finding 3 — response shape of GET /projects/{id}/git/status
// (git_service.project_git_status). Typed to what the four consumers read;
// the payload may carry more fields.
export interface GitProjectStatus {
  enabled: boolean
  base_branch: string | null
  base_path_state?: string
  ahead_count: number | null
  behind_count: number | null
  base_dirty?: { dirty: boolean; files: string[] }
  // 0308 T0004 (NR0003 recommendation 1) — new (untracked) base-checkout files. Kept as a
  // SEPARATE channel from base_dirty: folding untracked into base_dirty would widen
  // the E3 merge-finalize guard (git_service.py). Drives the file-tree new-file badge.
  base_untracked?: { count: number; files: string[]; truncated?: boolean }
  // 0327 T0004 (B0001): `writable` = this slot still has a live worktree, so the file
  // explorer can offer create/upload there instead of blanket read-only. Optional so a
  // server that predates the field reads as not-writable (the previous behaviour).
  slots: Array<{ group_id: string; branch: string | null; status: string; merge_id: number | null; writable?: boolean }>
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
  origin_provider_name?: string | null
  origin_ai_run_id?: string | null
}

// 0454 T0007 rev5 — project-wide totals for the MainPanel overview cards. Fetched by passing
// `includeSummary: true` to fetchGroupTree below, which rides the SAME `/groups/tree` request
// GroupExplorer already makes rather than a separate route: rev1's dedicated
// `/groups/tree/overview_summary` route and rev3's revival of it both left this cache filled by
// a SECOND, independently-triggered request that was not guaranteed to overlap the tree fetch —
// a sequential (non-overlapping) arrival cost a second full `process_service.get_group_tree()`
// call every time (rev4 review finding). `includeSummary` is opt-in and defaults to false on the
// server, so this does not repeat rev2's mistake of changing what `include_terminal=true`
// callers receive by default — see tree_routes.get_groups_tree's docstring.
export interface GroupOverviewSummary {
  total_documents: number
  working_groups: number
  type_distribution: Array<{ type: string; count: number }>
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
  // 0454 T0006 §2.2 — the group tree now has TWO server variants per project+branch
  // (`include_terminal=true|false`), so it needs its own key: the file tree's cacheKey()
  // meaning is untouched. `full` / `pruned` rather than `true` / `false` so a key read out
  // of a debugger or a test failure says which payload it holds. A pruned response must
  // never be handed to a caller that asked for the full tree, or the other way round —
  // hence the variant is part of the key, not a flag checked after the fact.
  const groupTreeKey = (
    pid: string,
    includeTerminal: boolean,
    branch = currentBranch.value,
  ) => `${pid}:${branch}:${includeTerminal ? 'full' : 'pruned'}`
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
  // 0308 T0004 (NR0003 recommendation 1) — per-project set of new (untracked) base-checkout
  // files, the exact complement of baseDirtyFiles. The server already emits it as
  // base_untracked; it now drives a distinct file-tree new-file badge instead of being
  // consumed only by GitStatusPanel. Refreshed alongside baseDirtyFiles on every
  // git/status fetch. Never merged into baseDirtyFiles (E3 guard — git_service.py).
  const baseUntrackedFiles = ref<Record<string, string[]>>({})
  // 0282 NR0003 finding 3: project git/status was fetched independently by four
  // components (FileExplorer, GitActionMenu, GitStatusPanel, GitBaseDirtyDialog)
  // — every mount race or SSE trigger multiplied the server's aggregation work.
  // The store now owns the fetch: concurrent callers coalesce onto one request,
  // the latest payload stays readable per project, and the §2.6-a base-dirty
  // badge sync happens here once instead of in each component.
  const gitStatus = ref<Record<string, GitProjectStatus | null>>({})
  const gitStatusInflight = new Map<string, Promise<GitProjectStatus | null>>()
  // 0449 T0004 item 2 (NR0003 E2 fallout): the same inflight-join contract for the group
  // tree. Two refreshes a few hundred ms apart (a reject's git_worktree_ready, then the
  // re-approval's doc_review_status_changed) each owned a first GET *and* a retry GET, so a
  // single reopen could put four multi-MB tree requests on the wire. Keyed by cacheKey(pid),
  // which already carries the branch — requests for a different branch never join.
  //
  // 0454 T0007 rev5: each entry also remembers whether IT asked the server for a forced
  // (never-join) read — see fetchGroupTree's docstring. A force=true caller must not silently
  // join a NON-forced in-flight request (whose URL never carries `force=true`, so the server
  // could hand it back a fetch that predates a write this caller already knows completed) — it
  // is only allowed to join another force=true fetch.
  //
  // 0454 T0007 rev6 (rev5 review finding 1): "only join another force=true fetch" is not
  // enough on its own — GroupExplorer's own reload() (initial load, SSE refresh, the hide-toggle)
  // ALSO always calls with force=true, so a force=true reveal-after-create call could still join
  // one of THOSE, and that fetch may have started before the write this caller needs reflected.
  // `order` timestamps each entry with its position in `groupTreeOrder` (see below) so a joiner
  // can tell whether the in-flight fetch is new enough to trust.
  const groupTreeInflight = new Map<string, { force: boolean; order: number; promise: Promise<GroupNode[]> }>()
  // 0454 T0007 rev6 — a single ever-increasing counter shared by every key/variant. Two roles:
  // (1) `groupTreeWriteOrder` below records the order value as of the last known write
  // (invalidateProject), so a force=true join can refuse an in-flight fetch that predates it
  // (rev5 review finding 1); (2) each own (non-joining) fetch's `order` also gates which
  // response is allowed to WIN the cache write (finding 2) — see groupTreeCommittedOrder.
  // Global rather than per-key/per-project: a stray extra fetch on an unrelated key after some
  // other project's write is a wasted request, not a correctness bug: the alternative (a
  // per-key/per-project marker) adds bookkeeping for a race this codebase has not hit in
  // practice across projects.
  let groupTreeOrder = 0
  let groupTreeWriteOrder = 0
  // 0454 T0007 rev6 (rev5 review finding 2) — `fetchGroupTree`'s cache write used to be
  // unconditional: a request that started before a write, but which happens to RESOLVE after a
  // request that started after that write, would overwrite the newer cache with its own stale
  // result. Deleting a settled inflight entry already checks ownership (line ~363 below) — that
  // only stops a stale promise from evicting a fresher one from the inflight map, it says
  // nothing about the CACHE the stale promise is about to write into. These two maps hold the
  // `order` of whichever response last won each cache slot, so a later-resolving but
  // earlier-started response can never regress it. Kept outside groupTreeCache/
  // groupOverviewSummaryCache (and NOT cleared by invalidateProject) because the ordering must
  // survive a cache clear.
  //
  // On its own this map does NOT stop a stale response from repopulating a slot
  // invalidateProject just emptied: a key nothing has committed to since the clear (first load,
  // or the clear happened before anything else committed) still reads as `?? 0`, so a pre-write
  // response's `order` can satisfy `order >= committedOrder` even though it predates the write.
  // `groupTreeWriteOrder` above is the other half of that check (rev7, see fetchGroupTree's
  // docstring) — every commit also requires `order >= groupTreeWriteOrder`.
  const groupTreeCommittedOrder = new Map<string, number>()
  const overviewSummaryCommittedOrder = new Map<string, number>()
  // 0454 T0007 — the overview-summary aggregate, populated as a side effect of fetchGroupTree
  // (rev5: every call now asks for it). Keyed like the file tree (pid+branch only): the summary
  // describes the whole project and does not vary by include_terminal, so whichever variant a
  // caller happens to fetch (full or pruned — the sidebar's default hidden load included) still
  // refreshes this cache.
  const groupOverviewSummaryCache = ref<Record<string, GroupOverviewSummary>>({})
  // 0186 L0006 §2.4 — checkout-free group-branch explorer caches, keyed by the
  // branch HEAD commit so a branch advance auto-invalidates the stale snapshot.
  // activeGroupBranch: currently viewed group_id in the file explorer (null = base).
  const activeGroupBranch = ref<string | null>(null)
  const groupBranchCommit = ref<Record<string, string>>({})       // `${pid}:${gid}` -> commit
  const groupBranchTreeCache = ref<Record<string, FileNode[]>>({}) // `${pid}:${gid}:${commit}` -> nodes
  const groupBlobCache = ref<Record<string, GroupBlobData>>({})    // `${pid}:${gid}:${commit}:${path}` -> blob
  const groupChangedFiles = ref<Record<string, string[]>>({})      // `${pid}:${gid}` -> normalized tracked-change paths
  // 0340 T0004 (B0001 / NR0003 §1) — keep the Git status that accompanies each
  // changed path. The path-only list above remains the compatibility channel for
  // ancestor-folder dirty propagation; this parallel map lets file rows distinguish
  // a deletion from an ordinary modification without changing the server contract.
  const groupChangeStatuses = ref<Record<string, Record<string, string>>>({})
  // 0315 TR (NR0003 recommendation 1·2·4) — new (untracked) files in a group worktree, the
  // group-branch analogue of baseUntrackedFiles. The tree read returns them on a
  // separate `worktree_untracked` channel because they change without advancing the
  // branch commit (so they must NOT be cached under groupBranchTreeCache's commit key).
  // Drives the new-file badge in the read-only group-branch explorer.
  const groupUntrackedFiles = ref<Record<string, string[]>>({})    // `${pid}:${gid}` -> normalized paths
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

  /** Fetch a project's group tree. `force` means "bypass the completed cache" — it does NOT
   *  ALWAYS mean "own a private request": a NON-forced caller arriving while a fetch for the
   *  same project+branch+variant is still running joins that fetch (and the single
   *  getTreeWithRetry retry inside it), and receives the same resolved nodes or the same error
   *  (0449 T0004 item 2).
   *
   *  0454 T0007 rev5: `force` now ALSO rides the request itself (`&force=true`) and changes what
   *  a caller is willing to JOIN, not only what it reads from the local cache. A `force=true`
   *  call means "I need a read that cannot predate this call" — the shape DashboardView.vue's
   *  two reveal-after-create paths need, since they call this right after awaiting the create
   *  request's own response, specifically to find the document that create just made. Joining an
   *  already in-flight request that (for all this caller knows) started BEFORE that create would
   *  silently defeat the guarantee, so a `force=true` caller only joins another `force=true`
   *  fetch — never a plain one — and always sends `force=true` to the server, which applies the
   *  same rule to `_get_raw_tree_nodes` (tree_routes.py) so the DB read itself cannot predate the
   *  request either. A forced fetch still BECOMES the shared in-flight entry once it starts, so
   *  later callers (forced or not) may join it — only the "am I allowed to join" direction is
   *  restricted, not sharing in general.
   *
   *  `includeTerminal` (0454 T0006 §2.1) picks the SERVER variant: `true` (default, so every
   *  pre-existing caller keeps its full-tree meaning) returns the whole flat tree; `false`
   *  asks the server to prune final-approved/discarded groups and their descendants, which is
   *  what the sidebar's default hidden state actually needs. It is always spelled out in the
   *  URL — never left to the server default — so a request and its cache variant are
   *  identifiable in a network log and in a test's captured URL. Cache AND inflight registry
   *  are keyed per variant, so the two never share a completed array or join one another's
   *  request; two callers on the SAME variant (and the same `force` eligibility) join as before.
   *
   *  Every call also asks the server for the project-wide `overview_summary` aggregate, folded
   *  into the same response (tree_routes.py's `include_summary` query flag) — a response that
   *  carries it updates groupOverviewSummaryCache as a side effect (0454 T0007 rev5). This is
   *  UNCONDITIONAL, not opt-in per caller: `include_summary` only ever adds a few hundred bytes
   *  of already-in-memory aggregation (no extra DB call — see `_get_raw_tree_nodes`), and making
   *  it conditional per call site would reopen the exact bug rev5 removes. Every joiner of a
   *  given in-flight request gets the SAME response by construction, so (unlike a per-call
   *  opt-in would) it can never depend on which caller happened to win the join race.
   *
   *  0454 T0007 rev6 (rev5 review, both findings): rev5 gated the join purely on `force`
   *  matching, and let whichever response happened to RESOLVE last win the cache write. Neither
   *  is enough:
   *
   *  - A force=true caller could still join an existing force=true in-flight fetch that started
   *    BEFORE the write this caller needs reflected — e.g. GroupExplorer's own reload() (initial
   *    load / SSE refresh / hide-toggle) is also always force=true, so it can already be running
   *    when DashboardView's reveal-after-create fires. Joining it defeats force=true's entire
   *    purpose. Fixed by `order`/`groupTreeWriteOrder`: a force=true join is only allowed onto an
   *    in-flight fetch whose start-order is at or after the last known write (invalidateProject).
   *  - Two overlapping fetches for the same key that do NOT join each other (a non-forced one
   *    superseded by a forced one — see the join check below) both write `groupTreeCache[key]`
   *    unconditionally on success. Whichever happens to finish LAST wins, regardless of which one
   *    started (and therefore read the server's state) more recently — an older, stale response
   *    can overwrite a newer one just by resolving later. Fixed by `groupTreeCommittedOrder`
   *    (and `overviewSummaryCommittedOrder`): a response may only write into a cache slot if its
   *    own `order` is at least as high as whatever last won that slot.
   *
   *  0454 T0007 rev7 (rev6 review): `order >= committedOrder` only defends a slot that something
   *  has already committed to. A slot invalidateProject just emptied (or one nothing has ever
   *  committed to — e.g. the very first load) still reads as `committedOrder ?? 0`, so a fetch
   *  that STARTED before the invalidating write but resolves after it can pass that check and
   *  refill the just-cleared slot with pre-write data — even though a fresh, later-started fetch
   *  has not committed anything yet (still in flight, or failed). Both commit checks below now
   *  also require `order >= groupTreeWriteOrder`: since every write bumps that floor to a value
   *  higher than any fetch already running at that moment, no pre-write response can ever commit
   *  again, regardless of whether anything newer has committed in its place yet. A response that
   *  loses this way still returns its own `data.nodes`/`data.overview_summary` to its own caller
   *  — only the shared cache write is refused. */
  async function fetchGroupTree(pid: string, force = false, includeTerminal = true): Promise<GroupNode[]> {
    const key = groupTreeKey(pid, includeTerminal)
    if (!force && groupTreeCache.value[key]) return groupTreeCache.value[key]
    if (isMockMode()) {
      await new Promise((r) => setTimeout(r, 100))
      groupTreeCache.value[key] = MOCK_GROUP_NODES
      return MOCK_GROUP_NODES
    }
    const inflight = groupTreeInflight.get(key)
    // A force=true caller may only join another force=true fetch that is itself new enough
    // (started at or after the last known write) — see the docstring above.
    if (inflight && (!force || (inflight.force && inflight.order >= groupTreeWriteOrder))) return inflight.promise
    const order = ++groupTreeOrder
    // Definite-assignment: `request` is assigned synchronously below, before the IIFE's first
    // `await` suspends it — the `finally` block (which closes over `request`) only runs after
    // that suspension resumes, by which time the assignment has long since completed.
    let request!: Promise<GroupNode[]>
    request = (async () => {
      loadingGroup.value = true
      groupError.value = null
      try {
        const res = await getTreeWithRetry<{ nodes: GroupNode[]; overview_summary?: GroupOverviewSummary }>(`/api/v1/projects/${pid}/groups/tree?branch=${encodeURIComponent(currentBranch.value)}&include_terminal=${includeTerminal ? 'true' : 'false'}&include_summary=true&force=${force ? 'true' : 'false'}`)
        const data = (res.data as any).data as { nodes: GroupNode[]; overview_summary?: GroupOverviewSummary }
        const summaryKey = cacheKey(pid)
        // 0454 T0007 rev7 (rev6 review) — `order >= committedOrder` alone only defends a cache
        // slot against an EARLIER-started response that is still resolving; it says nothing
        // about a slot that invalidateProject just EMPTIED without anything having committed to
        // it yet (first load, or the only prior commit was itself for the pre-write data).
        // committedOrder for such a key can sit below this response's `order` even though this
        // response started before the write — so it must also clear the global write floor
        // (`groupTreeWriteOrder`, bumped by invalidateProject) or a request that started before
        // an invalidation, but only resolves after it, refills the just-cleared slot with
        // pre-write data.
        if (data.overview_summary && order >= (overviewSummaryCommittedOrder.get(summaryKey) ?? 0) && order >= groupTreeWriteOrder) {
          overviewSummaryCommittedOrder.set(summaryKey, order)
          groupOverviewSummaryCache.value[summaryKey] = data.overview_summary
        }
        // A response only overwrites the cache if no later-started fetch already committed one
        // for this key — a stale response finishing last must not undo a fresher one that
        // finished first. A response that loses this race still returns ITS OWN data.nodes: the
        // caller that made this exact call gets what it asked for either way. A response that
        // wins returns `groupTreeCache.value[key]` (not the local `data.nodes`) so that a caller
        // reading the reactive cache afterwards gets back the identical (===) reference this
        // call resolved with, exactly as callers of getCachedGroupTree already expect.
        if (order >= (groupTreeCommittedOrder.get(key) ?? 0) && order >= groupTreeWriteOrder) {
          groupTreeCommittedOrder.set(key, order)
          groupTreeCache.value[key] = data.nodes
          return groupTreeCache.value[key]
        }
        return data.nodes
      } catch (e) {
        groupError.value = 'tree_load_failed'
        throw e
      } finally {
        // Cleared on BOTH outcomes, before any joined caller is resumed, so the next
        // reload starts a fresh request instead of re-joining a settled one. Only OUR OWN
        // entry is retired: a force=true call that arrived while a non-forced fetch was still
        // running (and therefore could not join it) may already have installed a fresher entry
        // in our place — deleting unconditionally would strand its own joiners.
        loadingGroup.value = false
        if (groupTreeInflight.get(key)?.promise === request) groupTreeInflight.delete(key)
      }
    })()
    groupTreeInflight.set(key, { force, order, promise: request })
    return request
  }

  function getCachedGroupOverviewSummary(pid: string): GroupOverviewSummary | undefined {
    return groupOverviewSummaryCache.value[cacheKey(pid)]
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
        // 0308 T0004 (NR0003 recommendation 1) badge trigger — refresh the file-tree new-file
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
    // 0454 T0007 rev6 (rev5 review finding 1) — record that a write happened, so any force=true
    // fetchGroupTree call arriving after this line refuses to join an in-flight fetch that
    // started before it (see fetchGroupTree's docstring and groupTreeWriteOrder above). Every
    // production write path that needs a subsequent force=true reveal/refresh to be trustworthy
    // already calls invalidateProject first (DashboardView.vue's handleRequirementCreated /
    // handleRelatedDocCreated do so before their reveal fetch as of this revision; refreshAll /
    // manualRefresh already called it before bumping the refresh tokens that trigger reload()).
    //
    // rev7 (rev6 review) — this same bump is also the commit floor fetchGroupTree's two
    // `order >= groupTreeWriteOrder` checks read. Any fetch already running when this line
    // executes has an `order` strictly below the new value (it was assigned earlier, from the
    // same monotonic counter), so it can never win a cache write after this point even if the
    // key it targets has no committed entry to compare against (a cleared or never-populated
    // slot). The clears below intentionally do NOT try to also bump groupTreeCommittedOrder /
    // overviewSummaryCommittedOrder per key — this single global counter already covers every
    // key of every project, including ones with no entry in those maps yet.
    groupTreeOrder += 1
    groupTreeWriteOrder = groupTreeOrder
    for (const key of Object.keys(fileTreeCache.value)) {
      if (key === pid || key.startsWith(`${pid}:`)) delete fileTreeCache.value[key]
    }
    // 0454 T0006 §2.2 — group-tree keys are `${pid}:${branch}:full|pruned`, so this one
    // prefix sweep drops BOTH display variants across EVERY branch of the project. Leaving
    // one variant behind would let a stale full tree answer a cache read taken right after
    // an invalidation the caller made precisely because the tree changed.
    for (const key of Object.keys(groupTreeCache.value)) {
      if (key === pid || key.startsWith(`${pid}:`)) delete groupTreeCache.value[key]
    }
    // 0454 T0007 review fix — the overview-summary cache is keyed like the file tree
    // (pid+branch), so it needs the same sweep as groupTreeCache: a stale summary must not
    // outlive the invalidation that caused it (git archive refresh, project-tree change, ...).
    for (const key of Object.keys(groupOverviewSummaryCache.value)) {
      if (key === pid || key.startsWith(`${pid}:`)) delete groupOverviewSummaryCache.value[key]
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
    for (const key of Object.keys(groupChangeStatuses.value)) {
      if (key.startsWith(`${pid}:`)) delete groupChangeStatuses.value[key]
    }
    for (const key of Object.keys(groupUntrackedFiles.value)) {
      if (key.startsWith(`${pid}:`)) delete groupUntrackedFiles.value[key]
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
      type GroupTreePayload = {
        branch: string
        commit: string
        nodes: FileNode[]
        worktree_untracked?: string[]
        // 0327 T0004 (B0001): worktree folders that hold no file yet — git reports
        // them nowhere else, so they ride their own channel.
        worktree_untracked_dirs?: string[]
      }
      const res = await getTreeWithRetry<{ data: GroupTreePayload }>(
        `/api/v1/projects/${encodeURIComponent(pid)}/git/groups/${encodeURIComponent(gid)}/tree`,
      )
      const data = (res.data as any).data as GroupTreePayload
      const key = groupKey(pid, gid)
      const prev = groupBranchCommit.value[key]
      if (prev && prev !== data.commit) purgeGroupCommit(pid, gid, prev)
      groupBranchCommit.value = { ...groupBranchCommit.value, [key]: data.commit }
      const nodes = data.nodes.filter((n) => n.permissions.includes('read'))
      groupBranchTreeCache.value[`${key}:${data.commit}`] = nodes
      // 0315 TR (NR0003 recommendation 1) — untracked files ride a channel separate from the
      // commit-keyed tree cache, since they change without advancing the commit.
      setGroupUntrackedFiles(pid, gid, [
        ...(data.worktree_untracked ?? []),
        ...(data.worktree_untracked_dirs ?? []),
      ])
      return { branch: data.branch, commit: data.commit, nodes }
    } catch (e) {
      fileError.value = 'tree_load_failed'
      throw e
    } finally {
      loadingFile.value = false
    }
  }

  /** Full /changes payload (branch names included). `fetchGroupBranchChanges` is the
   *  paths-only view of this same call, kept as-is for the change-badge callers. */
  async function fetchGroupBranchChangeSet(pid: string, gid: string): Promise<GroupChangesData> {
    const res = await getRequest<{ data: GroupChangesData }>(
      `/api/v1/projects/${encodeURIComponent(pid)}/git/groups/${encodeURIComponent(gid)}/changes`,
    )
    const data = (res.data as any).data as GroupChangesData
    const changes = (data.changes ?? []) as GroupChangeData[]
    const trackedChanges = changes
      .filter((change) => change.status !== '?')
      .map((change) => ({
        path: change.path.replace(/\\/g, '/'),
        status: change.status,
      }))
    // 0315 TR (NR0003 recommendation 2) — the server now also lists untracked files here with a
    // '?' status. Those drive the NEW badge (via the tree's worktree_untracked channel),
    // not the MODIFIED ('>') badge, so keep only tracked changes in groupChangedFiles;
    // otherwise a brand-new file would light up as "modified" instead of "new".
    groupChangedFiles.value = {
      ...groupChangedFiles.value,
      [groupKey(pid, gid)]: trackedChanges.map((change) => change.path),
    }
    groupChangeStatuses.value = {
      ...groupChangeStatuses.value,
      [groupKey(pid, gid)]: Object.fromEntries(
        trackedChanges.map((change) => [change.path, change.status]),
      ),
    }
    return { ...data, changes }
  }

  async function fetchGroupBranchChanges(pid: string, gid: string): Promise<GroupChangeData[]> {
    return (await fetchGroupBranchChangeSet(pid, gid)).changes
  }

  /** Unified diff of ONE changed file in a group branch (0325 R0001). Not cached:
   *  the group worktree is live, so a diff read after an edit must show the edit. */
  async function fetchGroupBranchDiff(pid: string, gid: string, path: string): Promise<GroupFileDiffData> {
    const res = await getRequest<{ data: GroupFileDiffData }>(
      `/api/v1/projects/${encodeURIComponent(pid)}/git/groups/${encodeURIComponent(gid)}/diff` +
        `?path=${encodeURIComponent(path)}`,
    )
    return (res.data as any).data as GroupFileDiffData
  }

  function isGroupChangedPath(pid: string, gid: string, path: string): boolean {
    return (groupChangedFiles.value[groupKey(pid, gid)] ?? []).includes(path.replace(/\\/g, '/'))
  }

  function groupChangeStatus(pid: string, gid: string, path: string): string | undefined {
    return groupChangeStatuses.value[groupKey(pid, gid)]?.[path.replace(/\\/g, '/')]
  }

  function isGroupDeletedPath(pid: string, gid: string, path: string): boolean {
    return groupChangeStatus(pid, gid, path) === 'D'
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

  // 0315 TR (NR0003 recommendation 4) — new (untracked) files in a group worktree, the
  // group-branch analogue of the isBaseUntracked* helpers. Same normalization and
  // the same _anyUnder folder propagation, so a new file inside a collapsed folder
  // still marks its ancestors in the read-only group-branch tree.
  function setGroupUntrackedFiles(pid: string, gid: string, files: string[]) {
    groupUntrackedFiles.value = {
      ...groupUntrackedFiles.value,
      [groupKey(pid, gid)]: files.map((f) => f.replace(/\\/g, '/')),
    }
  }

  function isGroupUntrackedPath(pid: string, gid: string, path: string): boolean {
    const files = groupUntrackedFiles.value[groupKey(pid, gid)]
    if (!files || !files.length) return false
    return files.includes(path.replace(/\\/g, '/'))
  }

  function isGroupUntrackedDir(pid: string, gid: string, folderPath: string): boolean {
    const files = groupUntrackedFiles.value[groupKey(pid, gid)]
    // 0327 T0004 (B0001): a folder just created in the worktree holds no file, so the
    // prefix scan alone would never mark it — the very folder the user made would be
    // the one node without a "new" badge. Those paths are in the list on their own
    // (worktree_untracked_dirs), so an exact match counts too.
    if (files && files.includes(folderPath.replace(/\\/g, '/').replace(/\/+$/, ''))) return true
    return _anyUnder(files, folderPath)
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
    // 0315 TR (NR0003 recommendation 3) — an untracked file is read off disk with commit=null and
    // no point-in-time; caching it by commit would pin stale content across edits, so
    // it is served fresh every time. Committed reads still cache by (pid, gid, commit, path).
    if (!data.untracked && data.commit) {
      groupBlobCache.value[`${groupKey(pid, gid)}:${data.commit}:${path}`] = data
    }
    return data
  }

  function getCachedFileTree(pid: string): FileNode[] | undefined {
    return fileTreeCache.value[cacheKey(pid)]
  }

  /** 0454 T0006 §2.3 — read ONE display variant of the cached group tree. Returns exactly the
   *  variant asked for (or undefined): a pruned tree is never returned to a caller that wants
   *  the full one, so "the document isn't in the tree" can't come from reading the wrong copy.
   *  Defaults to `true` for consumer compatibility, but every production caller passes its
   *  intent explicitly rather than leaning on the default. */
  function getCachedGroupTree(pid: string, includeTerminal = true): GroupNode[] | undefined {
    return groupTreeCache.value[groupTreeKey(pid, includeTerminal)]
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

  // 0308 T0004 (NR0003 recommendation 1·2·3) — new (untracked) base-checkout files, a channel
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
   *  cards' "문서 열기" (Open document), and the auto-advance SSE select intent (0316 T0004 /
   *  NR0003 recommendation 1·2·3). Ensures the tree is loaded (force-refetching once when the
   *  id is absent, e.g. a just-created auto-advance head that the cached tree has
   *  not caught up to), expands the ancestor groups so the node is revealed, and
   *  sets it as the selected node — the same reveal the dashboard cards already
   *  perform, now reachable from the AI-work open paths that previously only opened
   *  a tab. Best-effort: a tree-load failure resolves to null instead of throwing,
   *  so a caller that also opens a tab still opens it. Returns the node when found.
   *
   *  `switchProject` (0316 TR0005 rev1 rejection — "문서열기 해도 해당 프로젝트로 안가잖아"
   *  [opening the document doesn't even take you to that project]):
   *  when the target document lives in a project OTHER than the one on screen, the
   *  reveal/select below would land on a group tree that isn't displayed and nothing
   *  would move — the explorer stayed on the old project. The previous revision made
   *  this worse by having every caller pass the *current* project id, so a cross-project
   *  "문서 열기" (Open document) silently no-op'd. With `switchProject`, the active project is switched to
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
      // 0454 T0006 §3.3 — a reveal outranks the sidebar's hide setting, so it works on the
      // FULL variant throughout. The pruned variant is missing every terminal group by
      // construction; looking the target up there and finding nothing would report "document
      // not found" for a document that exists and is merely hidden.
      let nodes = getCachedGroupTree(pid, true)
      if (!nodes) nodes = await fetchGroupTree(pid, true, true)
      let node = nodes.find((n) => n.id === docId)
      if (!node) {
        nodes = await fetchGroupTree(pid, true, true)
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
    groupOverviewSummaryCache, getCachedGroupOverviewSummary,
    activeGroupBranch, fetchGroupBranchTree, fetchGroupBranchChanges, fetchGroupBranchBlob,
    fetchGroupBranchChangeSet, fetchGroupBranchDiff,
    currentGroupCommit, groupChangedFiles, groupChangeStatuses,
    groupChangeStatus, isGroupDeletedPath, isGroupChangedPath, isGroupChangedDir,
    groupUntrackedFiles, setGroupUntrackedFiles, isGroupUntrackedPath, isGroupUntrackedDir,
    expandedFileNodes, expandedGroupNodes,
    isFileNodeExpanded, setFileNodeExpanded, setFileNodesExpanded,
    isGroupNodeExpanded, setGroupNodeExpanded, setGroupNodesExpanded, expandGroupAncestors,
    revealDocInGroupTree,
    setWorkflowNodeState, clearWorkflowNodeState,
  }
})
