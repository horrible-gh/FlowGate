import type { RunGateState } from '@/features/document'

export const GATE_HEADER_KEYS = ['pending', 'waiting_confirm', 'publishing', 'conflict_review', 'recovery_required', 'confirmed', 'rejected'] as const
export interface GateHeaderState {
  readonly key: typeof GATE_HEADER_KEYS[number]
  readonly tone: 'neutral' | 'info' | 'warning' | 'danger' | 'success'
  readonly labelKey: string
  readonly reason: string | null
}
/** A receipt describes this attempt only; accepted is ordinary work, never a recovery failure. */
export function gateHeaderState(gate: RunGateState | null | undefined, node?: { status: string | null; attemptId?: string | null } | null): GateHeaderState {
  const note = gate?.job?.phase_note
  const recovery = gate?.recovery
  const active = gate?.git_active === true
  const conflict = !active && (note === 'gate_conflict' || recovery?.status === 'conflict')
  const failed = gate?.recovery_reset_required || !active && (note === 'gate_failed' || note === 'gate_recovery' || gate?.job?.status === 'failed'
    || !!recovery && (['failed', 'recovery_required'].includes(recovery.status) || !!recovery.error_code && recovery.status !== 'accepted'))
  const publishing = gate?.decision_in_progress || active || gate?.job && ['pending', 'running', 'queued', 'retry_wait'].includes(gate.job.status) || recovery?.status === 'accepted'
  const decision = gate?.decision
  const current = !!decision && (decision.attempt_id === null || !!node?.attemptId && decision.attempt_id === node.attemptId)
  const key: GateHeaderState['key'] = conflict ? 'conflict_review' : failed ? 'recovery_required' : publishing ? 'publishing'
    : current && decision.status === 'succeeded' && node?.status === 'done' && decision.result === 'confirmed' ? 'confirmed'
    : current && decision.status === 'succeeded' && node?.status === 'rejected' ? 'rejected'
    : node?.status === 'open' && gate?.can_decide === true ? 'waiting_confirm' : 'pending'
  const tones = { pending: 'neutral', waiting_confirm: 'neutral', publishing: 'warning', conflict_review: 'warning', recovery_required: 'danger', confirmed: 'success', rejected: 'warning' } as const
  return { key, tone: tones[key], labelKey: `gui.designer.center.card_states.${key}`, reason: recovery?.error_code ?? gate?.job?.last_error_code ?? decision?.last_error_code ?? null }
}
