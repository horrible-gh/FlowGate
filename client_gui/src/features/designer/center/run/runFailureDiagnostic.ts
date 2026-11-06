/** Local correlation only. Never retain response bodies, headers, stack or request config. */
export function diagnosticId(value: unknown): string | null {
  return typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(value)
    && !/bearer|authorization|cookie|secret|password|token/i.test(value) ? value : null
}

export interface RunFailureDiagnostic {
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
  readonly observation: { readonly state: string; readonly runId: string | null; readonly status: string | null } | null
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
