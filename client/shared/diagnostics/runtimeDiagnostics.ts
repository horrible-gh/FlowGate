// Bounded-memory runtime diagnostics for the intermittent browser-freeze investigation
// (flowgate.default.0616 T0004). Observation only — nothing here changes SSE/AI/UI
// behavior, and every recorder is best-effort (a diagnostics failure must never break the
// flow it observes). Never store document body/prompt/token/attachment content or raw
// paths — timestamp/duration/count/source/state metadata only.

export type AiRefreshSource =
  | 'poll_5s'
  | 'visibility'
  | 'online'
  | 'open_docs_refresh'
  | 'overview_refresh'
  | 'handoff'
  | 'manual_or_other'

export type DiagnosticsEntry =
  | {
      type: 'long_task'
      ts: number
      source: string
      visibilityState: string
      durationMs: number
      activeAiCount: number
      openDocId: string | null
      lastRecoverySource: string | null
      // Age of `lastRecoverySource` at capture time (rev2 finding: a source recorded 85s
      // ago and one recorded 5ms ago both used to render identically as "the" prior
      // trigger — this distinguishes a stale label from a genuinely-immediate one).
      lastRecoverySourceAgeMs: number | null
    }
  // rev4 finding: this entry used to carry a `refreshEpoch` snapshot of the module-global
  // screen-refresh epoch *at record time* — but recordSseEvent() always runs before the
  // handler it wraps even looks at scheduling a refresh, and the real epoch for whichever
  // flush this event ends up coalesced into is only assigned up to 250ms later inside
  // recordScreenRefreshFlushed(). That made the field name a lie: the first event after a
  // clear reported epoch 0 while its own fan-out was actually epoch 1, and the *next*
  // event reported epoch 1 while its fan-out was epoch 2 — a one-step-behind field that
  // reads as "the flush this event belongs to" but actually names the previous one. There
  // is no synchronous value that would be honest here, so the field is dropped rather than
  // given a misleading number; `fan_out`/`screen_refresh_flushed` already carry the real,
  // correctly-attributed epoch (rev2 finding 4), and long_task/markdown_parse correlate to
  // `lastRecoverySource` instead, which is set synchronously and correctly by this same call.
  | { type: 'sse_event'; ts: number; visibilityState: string; eventType: string }
  | { type: 'reconnect_scheduled'; ts: number; reason: string; attempt: number }
  | { type: 'reconnect_opened'; ts: number; reason: string; waitMs: number }
  | {
      type: 'screen_refresh_scheduled'
      ts: number
      immediate: boolean
      reason: string
      windowEventCount: number
    }
  | {
      type: 'screen_refresh_flushed'
      ts: number
      immediate: boolean
      reason: string
      coalescedEventCount: number
      epoch: number
    }
  | {
      type: 'ai_refresh'
      ts: number
      source: AiRefreshSource
      visibilityState: string
      activeRunCount: number
      statusGetCount: number
      singleFlightSkipCount: number
      // Wall-clock duration of the whole refreshAllRunning() call, network-await
      // included. Deliberately NOT promoted to a `long_task` entry (rev2 finding 1):
      // this is time the function spent awaiting an HTTP response, not time it held the
      // main thread, so it must not be counted alongside genuine
      // PerformanceObserver/sync-work long tasks. summarizeDiagnostics() reports it under
      // its own `slowAiRefreshCount`/`maxAiRefreshMs` keys instead.
      durationMs: number
      // Portion of durationMs spent in the handoff bootstrap()/poll step, when this call
      // had a pending handoff (0 otherwise) — kept apart from the per-run status GET cost.
      handoffBootstrapMs: number
    }
  | { type: 'fan_out'; ts: number; kind: string; epoch: number | null }
  | {
      type: 'visibility_recovery'
      ts: number
      generation: number
      module: 'sse' | 'ai_invoke' | 'token'
      detail: Record<string, unknown>
    }
  | {
      type: 'persist'
      ts: number
      cardCount: number
      bytes: number
      stringifyMs: number
      setItemMs: number
      totalMs: number
      result: 'ok' | 'quota_exceeded' | 'error'
    }
  | {
      type: 'markdown_parse'
      ts: number
      contentLength: number
      parseMs: number
      visibilityState: string
      lastRecoverySource: string | null
      lastRecoverySourceAgeMs: number | null
      // null when the caller did not attribute this parse to a specific SSE screen-refresh
      // flush (rev3 finding 2) — the initial/manual document open, in particular, is not an
      // SSE-flush epoch and must not report one.
      refreshEpoch: number | null
    }

// T0004 §3: bounded ring buffer, 300-500 entries. Oldest entries drop first.
const MAX_ENTRIES = 400
const buffer: DiagnosticsEntry[] = []

// Any single measured duration at/above this is treated as a long-task-like candidate
// even on a browser without PerformanceObserver longtask support (T0004 §4 fallback).
const LONG_TASK_LIKE_MS = 50

function push(entry: DiagnosticsEntry): void {
  try {
    buffer.push(entry)
    if (buffer.length > MAX_ENTRIES) buffer.splice(0, buffer.length - MAX_ENTRIES)
  } catch {
    // best-effort: diagnostics must never throw into the caller
  }
}

function vis(): string {
  return typeof document !== 'undefined' ? document.visibilityState : 'unknown'
}

// Context the long-task recorder cannot reach on its own (store state). Registered once
// from the app root (App.vue) where the pinia stores are available.
let contextProvider: (() => { activeAiCount: number; openDocId: string | null }) | null = null
export function registerDiagnosticsContext(
  provider: () => { activeAiCount: number; openDocId: string | null },
): void {
  contextProvider = provider
}

// The most recent SSE/reconnect/visibility signal, attached to every long-task entry so a
// captured freeze can be correlated to what just happened (T0004 §4). `lastRecoverySourceAt`
// is the wall-clock time it was set, so consumers can tell a label recorded moments ago from
// one that has been sitting stale since before an offline/hidden window started (rev2 finding
// 2 — e.g. an SSE `ping` seen right before going offline must not read as "just happened").
let lastRecoverySource: string | null = null
let lastRecoverySourceAt = 0
function noteRecoverySource(source: string): void {
  lastRecoverySource = source
  lastRecoverySourceAt = Date.now()
}
function recoverySourceAgeMs(): number | null {
  return lastRecoverySource === null ? null : Date.now() - lastRecoverySourceAt
}

// A caller-held snapshot of the recovery-source state, taken at the moment IT decides a
// reload is starting — not read again later when that reload's async work finishes (rev3
// finding 2). Without this, a slow fetch lets whatever SSE/recovery event happens to arrive
// before the parse completes silently take credit for triggering a reload it had nothing to
// do with, and a manual/non-SSE reload keeps inheriting the last SSE label as if it were one.
export interface RecoverySourceSnapshot {
  source: string | null
  recordedAt: number
}

export function snapshotRecoverySource(): RecoverySourceSnapshot {
  return { source: lastRecoverySource, recordedAt: lastRecoverySourceAt }
}

// Shared by recordLongTask() (reads the ambient module-global source, correct for
// PerformanceObserver/persist promotions that have no caller-held cause of their own) and
// recordMarkdownParse()'s promotion (rev5 finding: must reuse the exact request-local cause
// already attributed to the paired markdown_parse entry instead of re-reading the ambient
// global, which can have moved on to a different, unrelated event by the time a slow parse
// finishes — see recoverySource/recoverySourceAgeMs params below).
function pushLongTask(
  durationMs: number,
  source: string,
  recoverySource: string | null,
  recoverySourceAgeMs: number | null,
): void {
  try {
    const ctx = contextProvider?.() ?? { activeAiCount: 0, openDocId: null }
    push({
      type: 'long_task',
      ts: Date.now(),
      source,
      visibilityState: vis(),
      durationMs,
      activeAiCount: ctx.activeAiCount,
      openDocId: ctx.openDocId,
      lastRecoverySource: recoverySource,
      lastRecoverySourceAgeMs: recoverySourceAgeMs,
    })
  } catch {
    // best-effort
  }
}

export function recordLongTask(durationMs: number, source = 'browser_longtask'): void {
  pushLongTask(durationMs, source, lastRecoverySource, recoverySourceAgeMs())
}

let longTaskObserver: PerformanceObserver | null = null

// PerformanceObserver longtask entries are the primary signal (Chrome/Edge). Where the
// browser does not support the entry type, callers still get equivalent coverage from the
// explicit performance.now() duration measurements taken around ai-refresh / persist /
// markdown-parse below, which double as the "보조 지표" duration fallback T0004 §4 asks for.
export function startLongTaskObserver(): void {
  if (typeof window === 'undefined' || longTaskObserver) return
  try {
    const supported = (window as any).PerformanceObserver?.supportedEntryTypes as string[] | undefined
    if (!supported || !supported.includes('longtask')) return
    longTaskObserver = new PerformanceObserver((list) => {
      try {
        for (const entry of list.getEntries()) {
          recordLongTask(entry.duration, 'browser_longtask')
        }
      } catch {
        // best-effort
      }
    })
    longTaskObserver.observe({ entryTypes: ['longtask'] })
  } catch {
    longTaskObserver = null
  }
}

export function recordSseEvent(eventType: string): void {
  // A plain SSE event is itself a recovery-chain signal (T0004 §4): a refresh/parse/long
  // task that follows must be able to point back at the event that caused it, not at a
  // stale reconnect/visibility source from an earlier tick.
  noteRecoverySource(`sse_event:${eventType}`)
  push({ type: 'sse_event', ts: Date.now(), visibilityState: vis(), eventType })
}

export function recordReconnectScheduled(reason: string, attempt: number): void {
  noteRecoverySource(`reconnect_scheduled:${reason}`)
  push({ type: 'reconnect_scheduled', ts: Date.now(), reason, attempt })
}

export function recordReconnectOpened(reason: string, waitMs: number): void {
  noteRecoverySource(`reconnect_opened:${reason}`)
  push({ type: 'reconnect_opened', ts: Date.now(), reason, waitMs })
}

// One logical screen-refresh transaction per flush. The epoch is intentionally a loose,
// best-effort correlator (T0004 §7 "가능하면") rather than a strict per-event id: fan-out
// recorders elsewhere read `getCurrentRefreshEpoch()` whenever their own watcher fires.
let refreshEpoch = 0
export function getCurrentRefreshEpoch(): number {
  return refreshEpoch
}

export function recordScreenRefreshScheduled(immediate: boolean, reason: string, windowEventCount: number): void {
  push({ type: 'screen_refresh_scheduled', ts: Date.now(), immediate, reason, windowEventCount })
}

export function recordScreenRefreshFlushed(immediate: boolean, reason: string, coalescedEventCount: number): number {
  refreshEpoch += 1
  push({
    type: 'screen_refresh_flushed',
    ts: Date.now(),
    immediate,
    reason,
    coalescedEventCount,
    epoch: refreshEpoch,
  })
  return refreshEpoch
}

export function recordAiRefresh(
  source: AiRefreshSource,
  activeRunCount: number,
  statusGetCount: number,
  singleFlightSkipCount: number,
  durationMs: number,
  handoffBootstrapMs = 0,
): void {
  // rev2 finding 1: this duration is dominated by awaiting HTTP responses (network I/O),
  // not by main-thread work, so it must never be duplicated into the `long_task` type —
  // doing so previously let a slow background status GET masquerade as evidence of a
  // browser freeze. summarizeDiagnostics() surfaces slow calls under their own key instead.
  push({
    type: 'ai_refresh',
    ts: Date.now(),
    source,
    visibilityState: vis(),
    activeRunCount,
    statusGetCount,
    singleFlightSkipCount,
    durationMs,
    handoffBootstrapMs,
  })
}

// rev2 finding 4: the caller must state which logical refresh (if any) it belongs to —
// this no longer reads the module's "whatever epoch is current right now" state, because a
// fan-out triggered by a manual/non-SSE reload (DashboardView.manualRefresh(),
// GitActionMenu/GitMergeReviewDialog/GitStatusPanel/WorkPlanEditor's own fg:open_docs_refresh
// dispatches) could otherwise be misattributed to a stale SSE epoch that has nothing to do
// with it. Pass the real epoch when the refresh is SSE-flush-derived, `null` otherwise.
export function recordFanOut(kind: string, epoch: number | null): void {
  push({ type: 'fan_out', ts: Date.now(), kind, epoch })
}

// Coalesces same-tick visibility-recovery signals from independent modules (SSE, AI-run
// store, token refresh) under one generation number so their entries can be read as one
// timeline (T0004 §8) without introducing an actual coordinator — none is added by this T.
let visibilityGeneration = 0
let lastVisibilityTickAt = 0
export function beginVisibilityRecoveryTick(): number {
  const now = Date.now()
  if (now - lastVisibilityTickAt > 50) visibilityGeneration += 1
  lastVisibilityTickAt = now
  return visibilityGeneration
}

export function recordVisibilityRecovery(
  moduleName: 'sse' | 'ai_invoke' | 'token',
  generation: number,
  detail: Record<string, unknown>,
): void {
  noteRecoverySource(`visibility_visible:${moduleName}`)
  push({ type: 'visibility_recovery', ts: Date.now(), generation, module: moduleName, detail })
}

export function recordPersist(
  cardCount: number,
  bytes: number,
  stringifyMs: number,
  setItemMs: number,
  result: 'ok' | 'quota_exceeded' | 'error' = 'ok',
): void {
  const totalMs = stringifyMs + setItemMs
  push({ type: 'persist', ts: Date.now(), cardCount, bytes, stringifyMs, setItemMs, totalMs, result })
  if (totalMs >= LONG_TASK_LIKE_MS) recordLongTask(totalMs, 'finished_card_persist')
}

export interface MarkdownParseCause {
  // A snapshot taken by the caller when the reload that led to this parse actually started
  // (e.g. right when the causing SSE event's handler ran) — or `null` when the reload was
  // not SSE/recovery-triggered at all (a manual document open, a prop change, a regenerate).
  recovery: RecoverySourceSnapshot | null
  // The SSE screen-refresh flush epoch this parse belongs to, or `null` when it does not
  // belong to one — e.g. a `document_content_changed` reload fires straight off the raw SSE
  // event, independent of and typically well before the coalesced screen-refresh flush ever
  // assigns an epoch, so it has no epoch of its own to honestly report (rev3 finding 2).
  epoch: number | null
}

export function recordMarkdownParse(
  contentLength: number,
  parseMs: number,
  cause: MarkdownParseCause | null = null,
): void {
  // rev3 finding 2: no default here reads the module's ambient "whatever is current right
  // now" state — that let a stale epoch/recovery label from an unrelated flush attach to a
  // parse that had nothing to do with it, and let a manual/non-SSE open inherit the last SSE
  // label. The caller must state the cause explicitly; omitting it means "not attributable".
  const recovery = cause?.recovery ?? null
  const recoverySource = recovery?.source ?? null
  const recoverySourceAgeMs = recovery?.source != null ? Date.now() - recovery.recordedAt : null
  push({
    type: 'markdown_parse',
    ts: Date.now(),
    contentLength,
    parseMs,
    visibilityState: vis(),
    lastRecoverySource: recoverySource,
    lastRecoverySourceAgeMs: recoverySourceAgeMs,
    refreshEpoch: cause?.epoch ?? null,
  })
  // mdRenderer.parse() runs synchronously on the main thread (unlike ai_refresh above), so
  // a slow parse is a genuine long-task candidate and stays promoted. rev5 finding: this must
  // reuse this call's own already-captured `recoverySource`/`recoverySourceAgeMs` (the exact
  // values just pushed onto the markdown_parse entry above), not recordLongTask()'s ambient
  // module-global read — by the time a >=50ms parse finishes, a second, unrelated SSE event
  // may already have overwritten the module-global `lastRecoverySource`, which previously let
  // the promoted long_task entry name a different cause than the markdown_parse entry it is
  // supposed to represent (breaking the T0004 §4/§13 event-to-long-task correlation).
  if (parseMs >= LONG_TASK_LIKE_MS) {
    pushLongTask(parseMs, 'markdown_parse', recoverySource, recoverySourceAgeMs)
  }
}

export interface DiagnosticsDump {
  capacity: number
  bufferSize: number
  entries: DiagnosticsEntry[]
}

export function dumpDiagnostics(): DiagnosticsDump {
  return { capacity: MAX_ENTRIES, bufferSize: buffer.length, entries: [...buffer] }
}

export function clearDiagnostics(): void {
  // rev2 finding 4: clearing the buffer must also clear the correlation state that outlives
  // it, or a dump taken after "isolate between scenarios" can still read an epoch/recovery
  // label left over from the deleted scenario as if it belonged to the new one.
  buffer.length = 0
  refreshEpoch = 0
  lastRecoverySource = null
  lastRecoverySourceAt = 0
  visibilityGeneration = 0
  lastVisibilityTickAt = 0
}

export function summarizeDiagnostics(): {
  total: number
  byType: Record<string, number>
  maxLongTaskMs: number
  // Kept apart from maxLongTaskMs/byType.long_task (rev2 finding 1): ai_refresh duration is
  // network-await time, not main-thread time, so it is summarized under its own keys instead
  // of being counted as a long task.
  slowAiRefreshCount: number
  maxAiRefreshMs: number
} {
  const byType: Record<string, number> = {}
  let maxLongTaskMs = 0
  let slowAiRefreshCount = 0
  let maxAiRefreshMs = 0
  for (const entry of buffer) {
    byType[entry.type] = (byType[entry.type] ?? 0) + 1
    if (entry.type === 'long_task') maxLongTaskMs = Math.max(maxLongTaskMs, entry.durationMs)
    if (entry.type === 'ai_refresh') {
      maxAiRefreshMs = Math.max(maxAiRefreshMs, entry.durationMs)
      if (entry.durationMs >= LONG_TASK_LIKE_MS) slowAiRefreshCount += 1
    }
  }
  return { total: buffer.length, byType, maxLongTaskMs, slowAiRefreshCount, maxAiRefreshMs }
}

// Dev-console access (T0004 §11): `window.__fgDiagnostics.dump()` / `.summary()` / `.clear()`.
// Non-sensitive metadata only, so this is left available in every build rather than
// dev-gated — the freeze this investigates happens on a real running session, not only in
// a dev server.
if (typeof window !== 'undefined') {
  ;(window as any).__fgDiagnostics = {
    dump: dumpDiagnostics,
    summary: summarizeDiagnostics,
    clear: clearDiagnostics,
  }
}
