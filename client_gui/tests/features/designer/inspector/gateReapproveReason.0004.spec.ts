import { describe, expect, it } from 'vitest'
import { gateDecisionReason } from '@/features/designer/inspector/features/gateCard/gateCardModel'
import { checkSettingsView } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'

// flowgate.v02gui.0004 — 0001-B 관찰 1 / 6: a blocked [승인] names the held approval (conflict or still processing)
// instead of "wait for your turn", and an unset 반영 방식 shows 보류 selected.
const gate = (over: Record<string, unknown>) => ({
  decision: null, decision_in_progress: false, can_decide: false, settings_locked: false, archived: false,
  recovery_reset_required: false, recovery: null, job: null, ...over,
})
const run = (g: ReturnType<typeof gate>) => ({ status: 'running', nodes: [{ nodeKey: 'n1', status: 'open', gate: g as never }] })
const target = { nodeId: 'n1', readOnly: null }

describe('gate approve reason', () => {
  it('a held decision parked on a conflict / still running / not decidable yet', () => {
    expect(gateDecisionReason(target, run(gate({ decision_in_progress: true, job: { job_id: 'j', status: 'recovery_required', phase: null, phase_note: 'gate_conflict', last_error_code: 'gate_conflict' } })), false)).toBe('reason.conflict')
    expect(gateDecisionReason(target, run(gate({ decision_in_progress: true })), false)).toBe('reason.decision_in_progress')
    expect(gateDecisionReason(target, run(gate({})), false)).toBe('reason.not_open')
    expect(gateDecisionReason(target, run(gate({ can_decide: true })), false)).toBeNull()
  })
})

describe('check settings scope', () => {
  it('no stored merge_mode reads as 보류 (hold); a stored one as itself', () => {
    const node = (value: unknown) => ({ fields: { merge_mode: { value, stored: value !== null, default: null, choices: ['merge', 'commit', 'hold'] } } })
    expect(checkSettingsView({ node: node(null), pending: {}, card: true } as never).scope).toBe('hold')
    expect(checkSettingsView({ node: node('merge'), pending: {}, card: true } as never).scope).toBe('merge')
    expect(checkSettingsView({ node: null, pending: {}, card: true } as never).scope).toBeNull()
  })
})
