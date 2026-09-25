import { onBeforeUnmount, onMounted, watch } from 'vue'
import { useProjectStore } from '../stores/project'
import { useSnapshotRequestsStore } from '../stores/snapshotRequests'

// flowgate.default.0517 T0026 §1 — keeps the durable Snapshot Pending list in sync while the
// header is up. This used to live in SnapshotPendingCenter (its own header icon); it moved
// with the list into the notification panel, but the fetching must not depend on the panel
// being open: the badge count and the one-time auto-open of a NEW request (store's
// maybeAutoOpen, T0012 §10) both run off these fetches.
//
// T0012 §12 durable restore: SSE reconnect and the server's snapshot-lifecycle refresh
// signal both just mean "go re-read the pending list" (D0007 §4.1) — never trusted as the
// list itself. Unlike the document feed, this is fetched on the overview route too: a
// pending approval blocks an AI run wherever the user happens to be.
export function useSnapshotPendingSync() {
  const projectStore = useProjectStore()
  const store = useSnapshotRequestsStore()

  function refresh(): void {
    const pid = projectStore.currentProjectId
    if (pid) void store.fetchPending(pid)
  }

  let refetchTimer: ReturnType<typeof setTimeout> | null = null
  function onRefreshSignal(): void {
    if (refetchTimer !== null) clearTimeout(refetchTimer)
    refetchTimer = setTimeout(() => {
      refetchTimer = null
      refresh()
    }, 300)
  }

  watch(() => projectStore.currentProjectId, (pid) => {
    store.reset()
    if (pid) void store.fetchPending(pid)
  })

  onMounted(() => {
    refresh()
    window.addEventListener('fg:sse_reconnected', onRefreshSignal)
    window.addEventListener('fg:snapshot_refresh', onRefreshSignal)
  })

  onBeforeUnmount(() => {
    if (refetchTimer !== null) clearTimeout(refetchTimer)
    window.removeEventListener('fg:sse_reconnected', onRefreshSignal)
    window.removeEventListener('fg:snapshot_refresh', onRefreshSignal)
  })

  return { refresh }
}
