/**
 * flowgate.v02test.0052 0008 task 6 — the check card with an earlier approval still held: [승인] is off and says the
 * earlier approval has to be cancelled first (in the Git tab); a decision that is merely running keeps its old reason.
 */
import { describe, expect, it } from 'vitest'
import { gateActions, gateDecisionReason } from '@/features/designer/inspector/features/gateCard/gateCardModel'
import messages from '@/features/designer/inspector/features/gateCard/messages'

const gate = (extra: Record<string, unknown> = {}, recovery: Record<string, unknown> | null = null) => ({
  decision: null, decision_in_progress: true, can_decide: false, settings_locked: true, archived: false, recovery_reset_required: false,
  job: null, recovery, ...extra,
})
const run = (g: unknown) => ({ status: 'idle', nodes: [{ nodeKey: 'g1', status: 'open', gate: g as never }] })
const reasonOf = (g: unknown) => gateDecisionReason({ nodeId: 'g1', readOnly: null }, run(g), false)
const failed = { decision_id: 'd1', decision_version: 7, status: 'failed', retryable: true, cancelable: true, resolvable: false, evidence_version: 1, evidence_refs: [], phase: 'merge', error_code: 'merge_conflict_aborted' }

describe('gateDecisionReason — an earlier approval that failed and is still held', () => {
  it('says the failed approval must be cancelled first, not "in progress"', () => {
    expect(reasonOf(gate({}, failed))).toBe('reason.old_approval_failed')
    expect(reasonOf(gate({}, { ...failed, status: 'recovery_required', error_code: 'push_timeout' }))).toBe('reason.old_approval_failed')
  })
  it('keeps "in progress" for work that is still going on and for a held decision without a failure', () => {
    expect(reasonOf(gate())).toBe('reason.decision_in_progress')
    expect(reasonOf(gate({ git_active: true }, { ...failed, status: 'accepted' }))).toBe('reason.decision_in_progress')
    expect(reasonOf(gate({}, { ...failed, status: 'accepted', error_code: null }))).toBe('reason.decision_in_progress')
  })
  it('keeps "conflict" for a conflict review that is open', () => {
    expect(reasonOf(gate({ job: { job_id: 'j', status: 'running', phase_note: 'gate_conflict', last_error_code: null } }, failed))).toBe('reason.conflict')
  })
  it('[승인] stays visible but off with that reason', () => {
    const facts = { docId: null, docType: null, docReviewStatus: null, docStatus: null, nodeType: 'gate', cardDocuments: [], nodeId: 'g1', readOnly: null } as never
    const item = gateActions(facts, run(gate({}, failed)) as never).confirm
    expect(item).toEqual({ visible: true, enabled: false, reason: 'reason.old_approval_failed' })
  })
})

describe('the check card texts exist in ko / en / ja', () => {
  it.each(['ko', 'en', 'ja'] as const)('%s', (locale) => {
    const m = messages[locale] as any
    expect(m.reason.old_approval_failed).toBeTruthy()
    expect(m.old_approval.open).toBeTruthy()
  })
})
