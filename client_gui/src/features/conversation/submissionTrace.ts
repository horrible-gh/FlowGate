// Local diagnostics only: fixed fields, no bodies, keys, headers or error messages.
export type SubmissionPhase = { phase: 'creating' | 'loading' | 'sending' | 'failed'; stage: 'create' | 'load' | 'send'; attempt: number }
export type TraceStage = 'submit' | 'handler' | 'create_start' | 'create_end' | 'list_start' | 'list_end' | 'history_start' | 'history_end' | 'catchup_start' | 'catchup_end' | 'older_start' | 'older_end' | 'turn_start' | 'turn_end' | 'pending_frame' | 'accepted_frame' | 'ai_start'
export interface TraceRecord { traceId: string; stage: TraceStage; ms: number; deltaMs: number; attempt?: number; status?: number; docId?: string; result?: 'ok' | 'error' }
export function traceRecord(traceId: string, stage: TraceStage, start: number, previous: number, now: number, fields: Partial<TraceRecord> = {}): TraceRecord {
  return { traceId: /^ct-[a-zA-Z0-9-]{1,64}$/.test(traceId) ? traceId : 'ct-invalid', stage,
    ms: Math.max(0, now - start), deltaMs: Math.max(0, now - previous),
    ...(Number.isFinite(fields.attempt) ? { attempt: fields.attempt } : {}),
    ...(Number.isFinite(fields.status) ? { status: fields.status } : {}),
    ...(fields.docId && /^[a-zA-Z0-9_.-]{1,200}$/.test(fields.docId) ? { docId: fields.docId } : {}),
    ...(fields.result === 'ok' || fields.result === 'error' ? { result: fields.result } : {}) }
}
export function createSubmissionTrace(sink: (record: TraceRecord) => void = (record) => {
  if (import.meta.env.DEV) console.debug('[conversation timing]', record)
}, clock: () => number = () => performance.now()) {
  let id = 'ct-unavailable'
  try { id = `ct-${globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)}` } catch { /* Best effort. */ }
  const nowSafe = () => { try { return clock() } catch { return 0 } }
  const start = nowSafe()
  let previous = start
  const mark = (stage: TraceStage, fields: Partial<TraceRecord> = {}) => {
    try { const now = nowSafe(); sink(traceRecord(id, stage, start, previous, now, fields)); previous = now } catch { /* Diagnostics cannot fail a send. */ }
  }
  const frame = (stage: 'pending_frame' | 'accepted_frame', docId: string) => {
    try { globalThis.requestAnimationFrame?.(() => mark(stage, { docId })) } catch { /* No renderer in a function harness. */ }
  }
  return { id, mark, frame }
}
export type SubmissionTrace = ReturnType<typeof createSubmissionTrace>
