import { describe, expect, it } from 'vitest'
import { buildContinuousStartBody, groupIsAvailable, reviewerStepSeqs, selectionFromGroup } from '@/features/ai/startPreset'
import type { AiGroup } from '@/features/ai/types'

const group: AiGroup = {
  ai_group_id: 'aig_team', project_id: 'flowgate', name: 'Team', description: null,
  work_executor: { provider_id: 'executor', provider_name: 'Executor', is_available: true },
  reviewer: { provider_id: 'reviewer', provider_name: 'Reviewer', is_available: true },
  reworker: { provider_id: 'reworker', provider_name: 'Reworker', is_available: true },
}

describe('continuous Start group provenance', () => {
  it('prefills all concrete roles and keeps user overrides authoritative', () => {
    expect(groupIsAvailable(group)).toBe(true)
    const preset = selectionFromGroup(group)
    expect(preset).toEqual({ requestedAiGroupId: 'aig_team', providerId: 'executor', reviewerId: 'reviewer', reworkerId: 'reworker' })
    const body = buildContinuousStartBody({
      ...preset, providerId: 'override', reviewerId: 'reviewer-override', reworkerId: 'reworker-override',
      project: 'flowgate', module: 'v02', group: '0015', docRef: 'flowgate.v02.0015.0001-R',
      actionScope: 'new', targetSeq: 3, instructionMode: 'ai_direct', reviewerStepSeqs: [2, 3],
    })
    expect(body).toMatchObject({
      requested_ai_group_id: 'aig_team', provider_id: 'override', provider_pinned: true,
      continuation_reworker_provider_id: 'reworker-override',
      continuation_reviewer_overrides: { 2: 'reviewer-override', 3: 'reviewer-override' },
      continuation_review_mode: false,
    })
    expect(body).not.toHaveProperty('ai_group_id')
  })

  it('marks broken roles unavailable without replacing them', () => {
    const broken = { ...group, reviewer: { ...group.reviewer!, is_available: false } }
    expect(groupIsAvailable(broken)).toBe(false)
    expect(selectionFromGroup(broken).reviewerId).toBe('reviewer')
  })

  it('carries the reviewer as a run-level default before workflow decision', () => {
    const body = buildContinuousStartBody({
      ...selectionFromGroup(group), project: 'flowgate', module: 'v02', group: '0015',
      docRef: 'flowgate.v02.0015.0001-R', actionScope: 'workflow_decide',
      targetSeq: -1, instructionMode: 'auto_approved', reviewerStepSeqs: [],
    })
    expect(body).toMatchObject({
      continuation_reviewer_overrides: { '*': 'reviewer' },
      continuation_review_mode: false, requested_ai_group_id: 'aig_team', provider_id: 'executor',
    })
    expect(body).toHaveProperty('continuation_instruction_mode', 'auto_approved')
  })

  it('keeps continuation_review_mode independent of the reviewer preset', () => {
    const base = {
      ...selectionFromGroup(group), project: 'flowgate', module: 'v02', group: '0015',
      docRef: 'flowgate.v02.0015.0001-R', actionScope: 'edit' as const,
      targetSeq: 2, instructionMode: 'ai_direct' as const, reviewerStepSeqs: [1, 2],
    }
    expect(buildContinuousStartBody(base)).toHaveProperty('continuation_review_mode', false)
    expect(buildContinuousStartBody({ ...base, reviewerId: '' })).toHaveProperty('continuation_review_mode', false)
    expect(buildContinuousStartBody({ ...base, reviewMode: true })).toHaveProperty('continuation_review_mode', true)
  })

  it('selects only runnable steps within the target for the reviewer', () => {
    const steps = [
      { item_seq: 1, type: 'N', status: 'pending' }, { item_seq: 2, type: 'W', status: 'done' },
      { item_seq: 3, type: 'W', status: 'pending' }, { item_seq: 4, type: 'W', status: 'pending' },
    ]
    expect(reviewerStepSeqs(steps, 3, 'ai_direct')).toEqual([1, 3])
    expect(reviewerStepSeqs(steps, 3, 'auto_approved')).toEqual([3])
  })
})
