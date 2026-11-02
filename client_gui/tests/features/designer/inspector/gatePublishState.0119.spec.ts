import { describe, expect, it } from 'vitest'
import { toRunGateState } from '@/features/document'
import { gateResult } from '@/features/designer/inspector/features/gateCard/gateCardModel'

describe('0119 gate publish states', () => {
  it.each([
    ['gate_conflict', 'conflict_review', false, false],
    ['gate_failed', 'recovery_required', true, true],
    ['gate_recovery', 'recovery_required', true, false],
    [null, 'publishing', false, false],
  ])('reads %s without inferring recovery controls', (phase_note, key, retryable, cancelable) => {
    const gate = toRunGateState({ decision_in_progress: true, job: { job_id: 'j', status: 'recovery_required', phase_note },
      recovery: { retryable, cancelable }, decision: { status: 'accepted' } })!
    expect(gate.job?.phase_note).toBe(phase_note)
    expect(gateResult({ status: 'open', gate }).key).toBe(key)
    expect(gate.recovery).toMatchObject({ retryable, cancelable })
    expect(gateResult({ status: 'open', gate }).archived).toBe(false)
  })
  it('waits for confirmation without a decision and preserves old rejection display', () => {
    expect(gateResult({ status: 'open' }).key).toBe('before')
    expect(gateResult({ status: 'rejected' }).key).toBe('rejected')
  })
})
