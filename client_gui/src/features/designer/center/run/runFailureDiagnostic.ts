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
}

const recent: RunFailureDiagnostic[] = []
export function recordRunFailureDiagnostic(record: RunFailureDiagnostic): void {
  recent.push(record)
  if (recent.length > 50) recent.shift()
}
/** A copy for local diagnostic consumers. This buffer is never persisted or sent as telemetry. */
export function recentRunFailureDiagnostics(): readonly RunFailureDiagnostic[] { return [...recent] }

export function safeFlowLabel(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const label = value.trim()
  if (!label || label.length > 60 || /[\x00-\x1f\x7f/\\<>]|https?:|bearer|authorization|cookie|secret|password|token|\bat\s+\S+\(/i.test(label)) return null
  return label
}
