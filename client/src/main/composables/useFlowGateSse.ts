import { onBeforeUnmount, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useExplorerStore } from '../stores/explorer'
import { useProjectStore } from '../stores/project'
import { useDashboardStore } from '../stores/dashboard'
import { useToast } from '../components/common/useToast'

function getSseUrl(): string {
  const base =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
    'http://127.0.0.1:8088/flowgate'

  const token =
    (window as any).__accessToken__ ||
    sessionStorage.getItem('fg_access_token') ||
    ''
  const tokenParam = token ? `?token=${encodeURIComponent(token)}` : ''

  return `${base}/api/v1/events/stream${tokenParam}`
}

// Decode the current access token to obtain this client's own user id, matching
// the server's JWT subject (`sub`) and the helper in stores/tabs.ts. Used to
// recognise the decider's own SSE self-echo so the redundant info toast can be
// suppressed (R0001/NR0003). Returns null when no/invalid token (guest) so the
// caller falls back to the previous behaviour (info shown).
function getOwnUserId(): string | null {
  try {
    const token =
      (window as any).__accessToken__ ||
      sessionStorage.getItem('fg_access_token') ||
      ''
    if (!token) return null
    const payload = JSON.parse(atob(token.split('.')[1]))
    const uid = payload.sub ?? payload.user_id
    return uid != null ? String(uid) : null
  } catch {
    return null
  }
}

export function useFlowGateSse(refreshAll: () => void) {
  const explorerStore = useExplorerStore()
  const projectStore = useProjectStore()
  const dashboardStore = useDashboardStore()
  const { t } = useI18n()
  const { showToast } = useToast()

  let es: EventSource | null = null
  let hadPreviousConnection = false

  // Manual reconnection state. The native EventSource auto-reconnect reuses the
  // ORIGINAL url — i.e. the access token captured at first connect — and treats a
  // 401 response as fatal (no retry). After the access token rotates (Axios refresh)
  // or the connection drops past token expiry, that leaves the stream permanently
  // dead with no recovery, so open documents stop receiving workflow-decision
  // events. We take over reconnection: tear down and rebuild the url with the
  // freshest token on every attempt. (group 0021 / NR0003 items 1, 2)
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectAttempts = 0
  let closedByUs = false
  const RECONNECT_BASE_MS = 1000
  const RECONNECT_MAX_MS = 30000

  // Liveness watchdog (R0001 / group 0025 TR). The NR0003 reconnection only fires on an
  // explicit EventSource `error` or an Axios token rotation. A connection that dies
  // *silently* — proxy idle-timeout, laptop sleep/resume, Wi-Fi↔LAN switch — leaves the
  // EventSource nominally OPEN (no `error`), so the stream becomes a zombie and the
  // client stops receiving workflow-decision events ("decided but view never refreshes",
  // the recurring regression). We detect this by watching the server heartbeat: the
  // stream now emits a named `ping` event every ~30s, and we force a reconnect if no
  // traffic arrives within a tolerance window. On reconnect-open the existing resync
  // (invalidateAndRefresh) replays any missed state.
  let lastSeenAt = 0
  let livenessTimer: ReturnType<typeof setInterval> | null = null
  const LIVENESS_STALE_MS = 75000 // > 2 missed 30s heartbeats + margin
  const LIVENESS_CHECK_MS = 15000

  function log(msg: string, ...args: unknown[]) {
    // SSE lifecycle diagnostics (NR0003 item 5): connection state + reconnect reason.
    // Debug level so it stays out of the way in normal operation.
    if (import.meta.env.DEV) console.debug(`[FlowGateSse] ${msg}`, ...args)
  }

  function clearReconnectTimer() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function scheduleReconnect(reason: string) {
    if (closedByUs) return
    clearReconnectTimer()
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** reconnectAttempts, RECONNECT_MAX_MS)
    reconnectAttempts += 1
    log(`scheduling reconnect in ${delay}ms (reason=${reason}, attempt=${reconnectAttempts})`)
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function reconnectNow(reason: string) {
    log(`forced reconnect (reason=${reason})`)
    clearReconnectTimer()
    reconnectAttempts = 0
    if (es) {
      try { es.close() } catch { /* ignore */ }
      es = null
    }
    connect()
  }

  function markAlive() {
    lastSeenAt = Date.now()
  }

  function startLivenessWatch() {
    stopLivenessWatch()
    livenessTimer = setInterval(() => {
      if (closedByUs) return
      // Only police a connection we believe is established. While a reconnect is already
      // scheduled (es === null + a pending backoff timer) the error/backoff path owns
      // recovery — don't fight it.
      if (es === null || reconnectTimer !== null) return
      if (lastSeenAt !== 0 && Date.now() - lastSeenAt > LIVENESS_STALE_MS) {
        log(`liveness watchdog: no heartbeat for >${LIVENESS_STALE_MS}ms — forcing reconnect`)
        reconnectNow('stale_heartbeat')
      }
    }, LIVENESS_CHECK_MS)
  }

  function stopLivenessWatch() {
    if (livenessTimer !== null) {
      clearInterval(livenessTimer)
      livenessTimer = null
    }
  }

  function onVisibilityChange() {
    if (typeof document === 'undefined') return
    if (document.visibilityState !== 'visible') return
    // Tab/computer resumed. A stream parked through a sleep is often a zombie that never
    // fired `error`; if we have not seen a heartbeat within the tolerance window, or the
    // stream is gone, rebuild it now. (R0001 / group 0025 TR)
    if (es === null || (lastSeenAt !== 0 && Date.now() - lastSeenAt > LIVENESS_STALE_MS)) {
      reconnectNow('visibility_visible')
    }
  }

  function onOnline() {
    // Network regained. The old socket is dead even when `error` never fired, so rebuild
    // with the freshest token. (R0001 / group 0025 TR)
    reconnectNow('network_online')
  }

  function onAccessTokenRefreshed() {
    // The Axios layer rotated the access token. The SSE server validates the token only at
    // CONNECT time (sse_routes._authenticate), so an already-open stream stays authenticated
    // for its whole lifetime and need not be rebuilt. With proactive refresh now rotating the
    // token roughly once per (short) token lifetime, tearing down a healthy stream on every
    // rotation would cause a needless reconnect + full resync storm. So only rebuild when the
    // stream is actually down, in which case the NEXT connect picks up the fresh token.
    // (group 0028 T0004 req 4; preserves NR0003 item 1 recovery for the dead-stream case.)
    if (es === null) {
      reconnectNow('access_token_refreshed')
    }
  }

  function onDocumentContentRefreshCompleted(e: Event) {
    const detail = (e as CustomEvent).detail as {
      refresh_key?: string
      success?: boolean
    } | undefined
    if (!detail?.refresh_key) return
    // The "document updated" notification already fired when the SSE event
    // arrived, independently of whether any viewer is open (group 0035
    // R0001/NR0003). Here we only surface the *distinct* case where the open
    // viewer failed to reload the new revision, so the user knows the copy on
    // screen is stale even though the edit succeeded server-side. A successful
    // reload needs no extra toast — that would duplicate the modification
    // notification for the one user who happens to have the doc open.
    if (detail.success === false) {
      showToast(t('main.notifications.document_content_refresh_failed'), 'error')
    }
  }

  function invalidateAndRefresh(project?: string | null, dashboardImmediate = false) {
    const pid = project ?? projectStore.currentProjectId
    if (pid) explorerStore.invalidateProject(pid)
    if (pid && pid === projectStore.currentProjectId) {
      dashboardStore.invalidate(pid, dashboardImmediate)
    }
    refreshAll()
    // refreshAll() only invalidates the explorer tree. Open document tabs derive
    // their action-bar / workflow-head state from a one-shot fetch on mount, so a
    // sibling doc created or changed out-of-band (e.g. an AI worker via the inbox
    // API) leaves them stale. Signal open tabs to refetch their head state so the
    // action bar stays live (navigate-to-existing instead of stale "proceed/create").
    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('fg:open_docs_refresh', { detail: { project: pid ?? null } }),
      )
      // Signal the 🔔 notification center to refetch its persistent inflow feed + unread badge,
      // so document inflow is visible without entering the dashboard (R0001 group 0045 / NR0003
      // option A + option D). The server stays the single source of truth — the center refetches rather than
      // incrementing locally — so live and persisted counts cannot drift.
      window.dispatchEvent(
        new CustomEvent('fg:notification', { detail: { project: pid ?? null } }),
      )
    }
  }

  function connect() {
    clearReconnectTimer()
    closedByUs = false
    log('connecting')
    const source = new EventSource(getSseUrl(), { withCredentials: true })
    es = source
    // Treat a fresh attempt as "just seen" so the liveness watchdog gives it a full
    // window to open before judging it stale.
    markAlive()

    source.addEventListener('open', () => {
      log('connection open')
      reconnectAttempts = 0
      markAlive()
      if (hadPreviousConnection) {
        // Recovered from a drop. The server does not replay events emitted while we
        // were disconnected, so force a full resync of the explorer + open documents
        // on every (re)open. (NR0003 item 2)
        invalidateAndRefresh(undefined, true)
      }
      hadPreviousConnection = true
    })

    // Server heartbeat (every ~30s). Its only job is to prove the stream is alive so the
    // liveness watchdog can distinguish "quiet but healthy" from "silently dead".
    source.addEventListener('ping', () => {
      markAlive()
    })

    source.addEventListener('error', () => {
      log('connection error', { readyState: source.readyState })
      // Ignore errors from a source we have already torn down/replaced.
      if (es !== source) return
      try { source.close() } catch { /* ignore */ }
      es = null
      // Drive reconnection ourselves so the url is rebuilt with the latest token,
      // instead of letting the browser loop on the stale-token url (or give up on a
      // fatal 401). (NR0003 items 1, 2)
      scheduleReconnect('error')
    })

    source.addEventListener('file_explorer_refresh', (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        invalidateAndRefresh(data.project)
      } catch { /* ignore parse errors */ }
    })

    source.addEventListener('document_explorer_refresh', (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        const payload = data.payload ?? {}
        const operation = payload.operation
        const docId = payload.doc_id ?? data.doc_id ?? null
        // Notify on the SSE event itself so the toast appears whether the changed
        // document is the one on screen, a *different* document is open, or none
        // is. The previous design only toasted when the edited doc's own MdViewer
        // reported a successful reload (fg:document_content_refresh_completed),
        // so an AI edit while a different doc was open produced no toast at all —\n        // the regression reported in R0001. Prefer the human title, fall back to
        // the doc id so the message always identifies what changed (NR0003 #2).
        const label = payload.title ?? docId
        if (label && operation === 'created') {
          showToast(t('main.notifications.document_created', { doc: label }), 'info')
        } else if (label && operation === 'updated') {
          showToast(t('main.notifications.document_updated', { doc: label }), 'info')
        }
        if (operation === 'updated' && docId) {
          // Still drive the open viewer (if any) to reload the new revision; the
          // completion handler only reacts to a *failed* reload now.
          window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
            detail: {
              project: data.project ?? null,
              doc_id: docId,
              revision_no: payload.revision_no ?? null,
              refresh_key: `${docId}:${payload.revision_no ?? ''}`,
            },
          }))
        }
        invalidateAndRefresh(data.project)
      } catch { /* ignore parse errors */ }
    })

    source.addEventListener('group_view_refresh', (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        invalidateAndRefresh(data.project)
      } catch { /* ignore parse errors */ }
    })

    source.addEventListener('test_run_started', (e: Event) => {
      // Start events do not have a paired group_view_refresh from the backend, but open
      // TS tabs need the running embed immediately after a failed re-run starts.
      try {
        const data = JSON.parse((e as MessageEvent).data)
        invalidateAndRefresh(data.project)
      } catch { /* ignore parse errors */ }
    })

    source.addEventListener('test_run_finished', (e: Event) => {
      // Global test-failure toast (R0001 group 0155 / NR0005 §HOW-4 second signal).
      // The in-context TestFailStrip only surfaces a failure while its own TS document
      // is the active tab, so a run that fails while the user is looking at a *different*
      // document — or none at all — went completely unseen: exactly R0001's "a plain
      // 'test failed' notice won't get looked at" worry. This momentary toast fires
      // regardless of which tab is open, giving the persistent strip a transient
      // companion signal (the dual-signal design of NR0005). Only failures toast;
      // a passing run is the happy path and stays silent. The paired group_view_refresh
      // (broadcast alongside by _emit_finished) already drives the explorer/open-doc
      // resync, so this handler raises the toast only — no duplicate refresh here.
      try {
        const data = JSON.parse((e as MessageEvent).data)
        const p = data.payload ?? {}
        if (p.status !== 'failed') return
        const doc = p.doc_id ?? data.doc_id ?? ''
        const failed = p.case_failed
        const total = p.case_total
        const msg =
          failed != null && total != null
            ? t('main.notifications.test_run_failed', { doc, failed, total })
            : t('main.notifications.test_run_failed_nocount', { doc })
        showToast(msg, 'error')
      } catch { /* ignore parse errors */ }
    })

    // AI invoke run lifecycle (flowgate.default.0187 P0005). The dialog owns all
    // presentation — re-broadcast as window events it subscribes to (fg:git_pending_changed
    // pattern). The finished event's paired group_view_refresh (emitted server-side)
    // already drives the explorer resync, so no invalidateAndRefresh here.
    const aiInvokeKinds: Array<[string, string]> = [
      ['ai_invoke_started', 'started'],
      ['ai_invoke_provider_switched', 'switched'],
      ['ai_invoke_finished', 'finished'],
    ]
    for (const [eventName, kind] of aiInvokeKinds) {
      source.addEventListener(eventName, (e: Event) => {
        try {
          const data = JSON.parse((e as MessageEvent).data)
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('fg:ai_invoke', {
              detail: { kind, payload: data.payload ?? {} },
            }))
          }
        } catch { /* ignore parse errors */ }
      })
    }

    source.addEventListener('git_pending_changed', (e: Event) => {
      // Git finalize-pending set changed (flowgate.default.0162 §4-3). The
      // payload carries the server-recomputed absolute pending_count — the
      // action-bar badge and the Git status panel assign it directly and never
      // increment locally (L §2.3). Silent by design: the badge is a work
      // counter, not a toast. Re-broadcast as a window event those components
      // subscribe to, mirroring the fg:q_registered pattern above.
      try {
        const data = JSON.parse((e as MessageEvent).data)
        const p = data.payload ?? {}
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('fg:git_pending_changed', {
            detail: {
              project: p.project ?? data.project ?? null,
              group_id: p.group_id ?? data.group_id ?? null,
              status: p.status ?? null,
              pending_count: p.pending_count ?? null,
            },
          }))
        }
      } catch { /* ignore parse errors */ }
    })

    // 0192 T0005 §2-b: the group slot list appears/disappears on these three git
    // lifecycle events, but the explorer had NO listener for any of them, so a
    // finalized (merged/pushed) group lingered in the dropdown — and selecting the
    // now-deleted branch threw "tree load failed" — while a freshly-provisioned
    // group's branch did not show up until an unrelated remount. Each drives the
    // standard invalidate+refresh, which remounts the explorer and re-fetches its
    // slot list (the git_pending_changed path keeps the dropdown live in place for
    // ordinary status transitions; these cover the create/remove moments).
    const onGitSlotLifecycle = (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        const project = data.payload?.project ?? data.project ?? null
        invalidateAndRefresh(project)
      } catch { /* ignore parse errors */ }
    }
    source.addEventListener('git_finalize_done', onGitSlotLifecycle)
    source.addEventListener('git_worktree_ready', onGitSlotLifecycle)
    source.addEventListener('git_merge_conflict', onGitSlotLifecycle)
    // 0205 P scenarios 4·6·7: a conflict auto-aborted by the sweep/boot recovery
    // (badge clears, group returns to 'waiting') and a persisted provisioning
    // failure ('깃 미추적' warning) both change the slot/pending surface, so they
    // drive the same invalidate+refresh — the panel re-fetches git status and the
    // new conflict_since / provision_failures fields render live (P scenario 8).
    source.addEventListener('git_merge_auto_aborted', onGitSlotLifecycle)
    source.addEventListener('git_worktree_failed', onGitSlotLifecycle)

    source.addEventListener('notification_new_action_candidate', (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        const title = data.payload?.title ?? data.doc_id ?? ''
        if (title) showToast(t('main.notifications.new_action_candidate', { title }), 'info')
      } catch { /* ignore parse errors */ }
    })

    source.addEventListener('edit_marker_added', (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        invalidateAndRefresh(data.project)
      } catch { /* ignore parse errors */ }
    })

    source.addEventListener('qna_q_registered', (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        const qDocId = data.payload?.doc_id ?? data.doc_id ?? ''
        const title = data.payload?.titles?.[0] ?? data.payload?.title ?? qDocId
        if (title) showToast(t('main.notifications.q_registered', { title }), 'info')
        // The Q&A panel embedded in the open document (DocInfoPanel) and the Q-doc
        // viewer (QTDetailViewer) load their items once on mount / doc switch and do
        // NOT consume invalidateAndRefresh's explorer-scoped fg:open_docs_refresh, so
        // a worker-registered Q on the doc on screen stayed invisible until F5
        // (0059 B0001). Dispatch a doc-scoped window event those panels refetch on —\n        // mirrors the fg:doc_review_status_changed pattern used for review badges.
        if (typeof window !== 'undefined' && qDocId) {
          window.dispatchEvent(new CustomEvent('fg:q_registered', {
            detail: { doc_id: qDocId, project: data.project ?? null },
          }))
        }
        invalidateAndRefresh(data.project)
      } catch { /* ignore parse errors */ }
    })

    source.addEventListener('doc_review_status_changed', (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        const payload = data.payload ?? {}
        const prevStatus = String(payload.prev_status ?? '')
        if (
          payload.next_status === 'wf_in_progress'
          && !prevStatus.startsWith('wf_')
        ) {
          // Suppress this info toast only for the decider's own echo: a manual
          // decision already fired a local success toast in DocHeader, so the
          // SSE self-echo would be a duplicate. AI decisions tag actor_user_id
          // as null → no match → requester and spectators still get info.
          const ownUserId = getOwnUserId()
          const isOwnDecision =
            payload.actor_user_id != null
            && ownUserId != null
            && String(payload.actor_user_id) === ownUserId
          if (!isOwnDecision) {
            showToast(
              t('main.notifications.workflow_decided', {
                doc: payload.doc_id ?? data.doc_id ?? '',
              }),
              'info',
            )
          }
        }
        // Dispatch window event so DocHeader instances can update their badges in real time
        window.dispatchEvent(new CustomEvent('fg:doc_review_status_changed', { detail: payload }))
        invalidateAndRefresh(data.project)
      } catch { /* ignore parse errors */ }
    })

    source.addEventListener('ai_review_arrived', (e: Event) => {
      try {
        const data = JSON.parse((e as MessageEvent).data)
        const p = data.payload ?? {}
        const title = p.title ?? p.doc_id ?? ''
        if (title) showToast(t('main.notifications.ai_review_arrived', { title }), 'info')
        // invalidateAndRefresh → fg:open_docs_refresh → open tab refetches → the
        // "AI review arrived" pill surfaces (aiReview is populated from the doc detail).
        invalidateAndRefresh(data.project)
      } catch { /* ignore parse errors */ }
    })
  }

  function disconnect() {
    closedByUs = true
    clearReconnectTimer()
    reconnectAttempts = 0
    if (es) {
      es.close()
      es = null
    }
  }

  onMounted(() => {
    window.addEventListener('fg:document_content_refresh_completed', onDocumentContentRefreshCompleted)
    window.addEventListener('fg:access_token_refreshed', onAccessTokenRefreshed)
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
    }
    window.addEventListener('online', onOnline)
    connect()
    startLivenessWatch()
  })
  onBeforeUnmount(() => {
    window.removeEventListener('fg:document_content_refresh_completed', onDocumentContentRefreshCompleted)
    window.removeEventListener('fg:access_token_refreshed', onAccessTokenRefreshed)
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
    window.removeEventListener('online', onOnline)
    stopLivenessWatch()
    disconnect()
  })

  return { connect, disconnect }
}

