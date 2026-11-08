/** Local correlation only. Never retain response bodies, headers, stack or request config. */
export function diagnosticId(value: unknown): string | null {
  return typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(value)
    && !/bearer|authorization|cookie|secret|password|token/i.test(value) ? value : null
}

/** 0149: bounded, additive refusal facts. No raw params or internal job/decision identities. */
export interface RunBlockage {
  readonly reason: string | null
  readonly nodeKeys: readonly string[]
  readonly blockers: readonly { readonly nodeKey: string; readonly rowStatus: string | null; readonly activeAttempt: boolean }[]
  readonly total: number | null
}

const shortIdentifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(value)

export function runBlockageOf(code: string | null, params: unknown): RunBlockage | null {
  if (!['graph_revision_migration_blocked', 'graph_node_protected', 'graph_node_key_reused'].includes(code ?? '')
      || !params || typeof params !== 'object' || Array.isArray(params)) return null
  const p = params as Record<string, unknown>
  const nodeKeys = [...new Set(Array.isArray(p.node_keys) ? p.node_keys.filter((k): k is string => diagnosticId(k) !== null) : [])].slice(0, 50)
  const blockers: RunBlockage['blockers'][number][] = []
  const seen = new Set<string>()
  if (Array.isArray(p.blockers)) for (const raw of p.blockers) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
    const b = raw as Record<string, unknown>
    if (typeof b.node_key !== 'string' || !nodeKeys.includes(b.node_key) || seen.has(b.node_key)
        || (b.row_status !== null && !shortIdentifier(b.row_status)) || typeof b.active_attempt !== 'boolean') continue
    seen.add(b.node_key)
    blockers.push(Object.freeze({nodeKey: b.node_key, rowStatus: b.row_status as string | null, activeAttempt: b.active_attempt}))
  }
  return Object.freeze({
    reason: shortIdentifier(p.reason) ? p.reason : null,
    nodeKeys: Object.freeze(nodeKeys), blockers: Object.freeze(blockers),
    total: typeof p.node_keys_total === 'number' && Number.isSafeInteger(p.node_keys_total)
      && p.node_keys_total >= nodeKeys.length ? p.node_keys_total : null,
  })
}

export interface RunFailureDiagnostic {
  readonly responseStatus?: number | null
  readonly transport?: 'no_response' | 'http_error' | 'unreadable_2xx'
  readonly commandRequestId?: string | null
  readonly baseline?: { readonly state: 'run' | 'none' | 'unread'; readonly runId: string | null; readonly status: string | null; readonly lastEventSeq: number | null }
  readonly publicationId: string | null
  readonly scope: string | null
  readonly seq: number
  readonly localRequestId: string | null
  readonly serverRequestId: string | null
  readonly action: string | null
  readonly stage: string | null
  readonly mode: string | null
  readonly projectId: string | null
  readonly flowId: string | null
  readonly targetRunId: string | null
  readonly baselineRunId: string | null
  readonly status: number | null
  readonly code: string | null
  readonly outcome: string | null
  readonly blocked?: RunBlockage | null
  readonly observation: { readonly state: string; readonly runId: string | null; readonly status: string | null; readonly lastEventSeq?: number | null } | null
  /** 0156 T1 R2: the exception's name for a client failure (TypeError, QuotaExceededError, ...); never its message. */
  readonly errorName?: string | null
  /** 0156 T1 R3: the server code behind a mapped failure (ai_setup_check_failed), when it is a safe identifier. */
  readonly causeCode?: string | null
}

/** 0156 T1 R2: an exception name kept for diagnosis, only in the short identifier shape. */
export function safeErrorName(value: unknown): string | null {
  return typeof value === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(value) ? value : null
}

// 0156 T1 E2: the buffer is mirrored into this tab's sessionStorage (same bound) so a reload keeps the evidence of the
// click that failed. Local only: never sent anywhere, no telemetry. The records hold only the fixed safe fields above.
export const RUN_FAILURE_DIAGNOSTICS_KEY = 'flowgate.runFailureDiagnostics.v1'
export const RUN_FAILURE_DIAGNOSTICS_LIMIT = 50
const session = (): Storage | null => {
  try { return globalThis.sessionStorage ?? null } catch { return null }
}
function restore(): RunFailureDiagnostic[] {
  try {
    const list = JSON.parse(session()?.getItem(RUN_FAILURE_DIAGNOSTICS_KEY) ?? '[]') as unknown
    return Array.isArray(list)
      ? list.filter((r): r is RunFailureDiagnostic => !!r && typeof r === 'object' && !Array.isArray(r) && !('message' in r)).slice(-RUN_FAILURE_DIAGNOSTICS_LIMIT)
      : []
  } catch { return [] }
}
const recent: RunFailureDiagnostic[] = restore()
export function recordRunFailureDiagnostic(record: RunFailureDiagnostic): void {
  recent.push(record)
  while (recent.length > RUN_FAILURE_DIAGNOSTICS_LIMIT) recent.shift()
  // A failed write (quota, storage disabled) never breaks a command or its notice.
  try { session()?.setItem(RUN_FAILURE_DIAGNOSTICS_KEY, JSON.stringify(recent)) } catch { /* memory buffer only */ }
}
/** A copy for local diagnostic consumers. This buffer is never sent as telemetry. */
export function recentRunFailureDiagnostics(): readonly RunFailureDiagnostic[] {
  return recent.map((r) => JSON.parse(JSON.stringify(r)) as RunFailureDiagnostic)
}
// 0156 T1 E2: read-only field access (DevTools console) to the failure stage/code of the last clicks. Copies only.
try {
  Object.defineProperty(globalThis, '__flowgateRunDiagnostics', { value: () => recentRunFailureDiagnostics(), configurable: true })
} catch { /* a frozen global keeps the module usable */ }

export function safeFlowLabel(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const label = value.trim()
  if (!label || label.length > 60 || /[\x00-\x1f\x7f/\\<>]|https?:|bearer|authorization|cookie|secret|password|token|\bat\s+\S+\(/i.test(label)) return null
  return label
}
