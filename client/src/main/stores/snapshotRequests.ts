import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getRequest, postRequest } from '@shared/api'
import { dialogStackEntries } from '../composables/useDialogStack'

// flowgate.default.0517 T0012 — durable AI Scratch Source Snapshot approval UX.
//
// D0007 §4.1 / §3.3: the server's `requested`-status list is the single source of truth
// for the Pending count/list. SSE (and every other client signal) is treated as nothing
// but "go re-read it" — this store never derives the list or the badge count from an
// event payload, only from the response of `fetchPending()`.
export interface SnapshotRequestRow {
  snapshot_id: string
  project_id: string
  group_id: string
  run_id: string
  token_id: string
  provider_id: string
  reason: string
  scope: 'single_file' | 'selected_files' | 'directory' | 'whole_source'
  requested_paths: string[]
  purpose: string
  source_kind: 'current_worktree'
  status: string
  requested_at: string
}

export const useSnapshotRequestsStore = defineStore('snapshotRequests', () => {
  const pending = ref<SnapshotRequestRow[]>([])
  const loading = ref(false)
  const error = ref(false)

  // T0012 §10: "이번 수신에서 아직 자동 표시하지 않음". A request id is marked seen the
  // FIRST time this store observes it in a pending fetch — including the very first
  // fetch after mount/reload/relogin, which must NOT reopen a dialog for every request
  // that was already sitting there (§12 durable restore is about the list, not about
  // replaying "new request" auto-opens). Only a request that shows up in a LATER fetch,
  // absent from every prior one, is "new" and gets the one-time auto-open attempt.
  const seenIds = new Set<string>()
  let initialized = false

  // Rejection round 1 (C5/C9/C11): server durable state is the single source of truth for
  // `pending`, but `fetchPending` calls can complete out of order — an SSE-triggered refresh
  // racing a manual refresh in the same project, or a call issued for a project the user has
  // since switched away from. `fetchSeq` is bumped on every call (and on `reset()`); a
  // response is only applied if it is still the most recently ISSUED call by the time it
  // resolves, so an older response can never overwrite state a newer call already produced.
  // `activeProjectId` additionally records which project the store is currently scoped to,
  // so a stale-project caller (see `settleDecision`) can be refused before it ever fetches.
  let fetchSeq = 0
  const activeProjectId = ref<string | null>(null)

  // The single detail-dialog target, shared by the auto-open path and the Pending list's
  // [자세히] button — one Vue instance, so "동시에 snapshot approval dialog 1개 이하"
  // (T0012 §11) holds structurally instead of by convention.
  const detailTarget = ref<SnapshotRequestRow | null>(null)
  const detailOpen = computed(() => detailTarget.value !== null)

  const pendingCount = computed(() => pending.value.length)

  function anyDialogOpen(): boolean {
    // Our own detail dialog (if open) is itself on this stack, so this one check covers
    // both "another blocking dialog is up" and "a snapshot dialog is already open"
    // (T0012 §11's two auto-open preconditions collapse into one test).
    return dialogStackEntries().length > 0
  }

  function openDetail(row: SnapshotRequestRow): void {
    detailTarget.value = row
  }

  // X/ESC: the request stays `requested` — this never calls approve/reject, it only
  // drops the client-side "currently showing" pointer (T0012 §7/§10).
  function closeDetail(): void {
    detailTarget.value = null
  }

  function maybeAutoOpen(rows: SnapshotRequestRow[]): void {
    if (!initialized) {
      for (const row of rows) seenIds.add(row.snapshot_id)
      initialized = true
      return
    }
    const freshlySeen: SnapshotRequestRow[] = []
    for (const row of rows) {
      if (!seenIds.has(row.snapshot_id)) {
        seenIds.add(row.snapshot_id)
        freshlySeen.push(row)
      }
    }
    if (freshlySeen.length === 0) return
    // §10 forbids opening more than one dialog for a burst of new requests; the rest
    // simply accumulate in Pending (§11) and are never retried once evaluated here.
    if (detailTarget.value !== null) return
    if (anyDialogOpen()) return
    openDetail(freshlySeen[0])
  }

  async function fetchPending(projectId: string): Promise<void> {
    if (!projectId) return
    activeProjectId.value = projectId
    const seq = ++fetchSeq
    loading.value = true
    error.value = false
    try {
      const response = await getRequest<{ ok: boolean; requests: SnapshotRequestRow[] }>(
        '/api/v1/snapshots/pending',
        { project_id: projectId },
      )
      // Stale-response guard: a call issued after this one (a newer refresh, a decision's
      // re-fetch, or a project switch) already owns `pending` — drop this response instead
      // of overwriting fresher (or differently-scoped) durable state with old rows.
      if (seq !== fetchSeq) return
      const rows = response.data?.requests ?? []
      pending.value = rows
      maybeAutoOpen(rows)
    } catch {
      if (seq !== fetchSeq) return
      error.value = true
    } finally {
      if (seq === fetchSeq) loading.value = false
    }
  }

  // T0012 §14: on success, close the dialog and re-read the durable list from the
  // server — never just drop the row locally. A row already removed by the POST is
  // simply absent from the re-fetch; the optimistic removal is a fallback for when the
  // owning project can no longer be determined (defensive, not the primary path).
  async function settleDecision(snapshotId: string, path: 'approve' | 'reject'): Promise<void> {
    const projectId = pending.value.find((row) => row.snapshot_id === snapshotId)?.project_id
    await postRequest(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}/${path}`, {})
    if (detailTarget.value?.snapshot_id === snapshotId) detailTarget.value = null
    // A decision's own re-fetch must not resurrect a project the user has since switched
    // away from: `activeProjectId` only changes via an explicit `fetchPending`/`reset()`
    // call, so if it no longer matches the project this decision belongs to, a switch (which
    // always resets and re-fetches on its own) has already taken over — refetching here would
    // pull the OLD project's list back into `pending` on top of the new project's screen.
    if (projectId && projectId === activeProjectId.value) await fetchPending(projectId)
    else pending.value = pending.value.filter((row) => row.snapshot_id !== snapshotId)
  }

  async function approve(snapshotId: string): Promise<void> {
    await settleDecision(snapshotId, 'approve')
  }

  async function reject(snapshotId: string): Promise<void> {
    await settleDecision(snapshotId, 'reject')
  }

  function reset(): void {
    // Bump the generation so any fetch still in flight for the previous scope (or project)
    // fails the `seq !== fetchSeq` check in `fetchPending` and is dropped on arrival.
    fetchSeq += 1
    activeProjectId.value = null
    pending.value = []
    loading.value = false
    error.value = false
    seenIds.clear()
    initialized = false
    detailTarget.value = null
  }

  return {
    pending, loading, error, pendingCount, detailTarget, detailOpen,
    fetchPending, approve, reject, openDetail, closeDetail, reset,
  }
})
