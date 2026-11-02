import type { RunGateState } from '@/features/document'

export interface GateHeaderState {
  readonly key: 'waiting_confirm' | 'publishing' | 'conflict_review' | 'recovery_required' | 'confirmed' | 'rejected'
  readonly tone: 'neutral' | 'info' | 'warning' | 'danger' | 'success'
  readonly labelKey: string
  readonly reason: string | null
}
/** Gates use only the server's decision, job and recovery facts. */
export function gateHeaderState(gate: RunGateState | null | undefined): GateHeaderState {
  const note = gate?.job?.phase_note
  const recovery = gate?.recovery
  const key: GateHeaderState['key'] = note === 'gate_conflict' || recovery?.status === 'conflict' ? 'conflict_review'
    : gate?.recovery_reset_required || note === 'gate_failed' || note === 'gate_recovery' || gate?.job?.status === 'failed' || recovery && recovery.status !== 'succeeded' && recovery.status !== 'resolved' ? 'recovery_required'
    : gate?.decision_in_progress || gate?.job && ['pending', 'running', 'queued'].includes(gate.job.status) ? 'publishing'
    : gate?.decision?.status === 'succeeded' ? gate.decision.result === 'confirmed' ? 'confirmed' : 'rejected'
    : 'waiting_confirm'
  const tones = { waiting_confirm: 'neutral', publishing: 'info', conflict_review: 'warning', recovery_required: 'danger', confirmed: 'success', rejected: 'danger' } as const
  return { key, tone: tones[key], labelKey: `gui.designer.center.card_states.${key}`, reason: recovery?.error_code ?? gate?.job?.last_error_code ?? gate?.decision?.last_error_code ?? null }
}
