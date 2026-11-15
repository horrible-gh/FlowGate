import { getRequest, postRequest } from '@shared/api'

export interface InvokeRun {
  key: string
  runId: string | null
  groupId: string
  docId: string | null
  phase: string
  mode: string
  provider: string | null
  reached: number
  target: number
  reason: string | null
}
export interface AiNotification {
  run_id: string
  doc_ref?: string | null
  doc_title?: string | null
  succeeded?: boolean | null
  outcome?: string | null
  last_message_excerpt?: string | null
  finished_at?: string | null
}
export interface QuestionNotification { doc_id: string; title?: string | null; type_code?: string | null }
export interface NotificationPage<T> { items: T[]; total: number; has_more: boolean; limit: number }
export interface NotificationFeed {
  ai_invoke_runs: NotificationPage<AiNotification>
  open_questions: NotificationPage<QuestionNotification>
  degraded_sections?: string[]
}
const record = (v: unknown): Record<string, unknown> => v && typeof v === 'object' ? v as Record<string, unknown> : {}
const str = (v: unknown): string | null => typeof v === 'string' && v.trim() ? v : null
const count = (v: unknown): number => typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0

/** active-all includes durable finished review cards too; those belong in the AI feed. */
export function normalizeActiveRuns(payload: unknown): InvokeRun[] {
  const data = record(payload)
  const rows: InvokeRun[] = []
  for (const value of Array.isArray(data.runs) ? data.runs : []) {
    const run = record(value)
    const runId = str(run.run_id), groupId = str(run.group_id)
    if (!runId || !groupId || run.status === 'finished') continue
    const chained = !!str(run.chain_id)
    rows.push({ key: runId, runId, groupId, docId: str(run.doc_ref),
      phase: str(run.status) ?? 'unknown', mode: str(run.mode) ?? 'unknown',
      provider: str(record(run.provider).name),
      reached: count(chained ? run.chain_docs_reached : run.docs_reached_so_far),
      target: count(chained ? run.chain_docs_target : run.docs_target), reason: null })
  }
  for (const value of Array.isArray(data.paused) ? data.paused : []) {
    const run = record(value), groupId = str(run.group_id)
    if (!groupId || rows.some((row) => row.groupId === groupId)) continue
    const chained = !!str(run.chain_id)
    rows.push({ key: `paused:${groupId}`, runId: null, groupId, docId: str(run.doc_ref),
      phase: 'paused', mode: str(run.mode) ?? 'continuous', provider: str(run.resume_provider_name),
      reached: count(chained ? run.chain_docs_reached : run.docs_reached),
      target: count(chained ? run.chain_docs_target : run.docs_target),
      reason: str(run.stop_reason) ?? str(run.stop_code) })
  }
  return rows
}
export async function fetchActiveRuns(): Promise<InvokeRun[]> {
  return normalizeActiveRuns((await getRequest<unknown>('/api/v1/ai-invoke/active-all')).data)
}
export async function fetchNotificationFeed(projectId: string): Promise<NotificationFeed> {
  return (await getRequest<NotificationFeed>(`/api/v1/projects/${encodeURIComponent(projectId)}/notifications`, { limit: 50 })).data
}
export async function cancelInvokeRun(runId: string): Promise<void> {
  await postRequest(`/api/v1/ai-invoke/${encodeURIComponent(runId)}/cancel`, {})
}
