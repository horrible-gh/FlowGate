/** flowgate.v02.0132 T#4 (NR §4.4.3): a gate with Git work going on locks the Flow like a running run; an idle run with a parked gate is editable. */
import { describe, expect, it } from 'vitest'
import { editLockOf, flowRunMarks, isGateGitActive, isRunLocked } from '@/features/designer/center/run/flowRunLock'
import type { FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { toFlowRun } from '@/features/project/flowRunApi'
import { toRunGateState } from '@/features/document/api/run'

const gate = (extra: Record<string, unknown> = {}) => ({
  decision: null, decision_in_progress: true, can_decide: false, settings_locked: true, archived: false,
  recovery_reset_required: false, recovery: null, job: null, ...extra,
})
const gateRun = (status: string, extra: Record<string, unknown> = {}): FlowRunInfo => ({
  runId: 'r1', status, openNodeKeys: [],
  nodeRuns: [{ nodeKey: 'g1', status: 'open', gate: gate(extra) as never }],
} as unknown as FlowRunInfo)

describe('flowRunLock — gate git_active / parked (0132 T#4)', () => {
  it('git_active on an idle run locks the structure like a running run', () => {
    expect(editLockOf(gateRun('idle', { git_active: true }))).toBe('run')
    expect(flowRunMarks(null, gateRun('idle', { git_active: true })).lock).toBe('run')
    expect(isGateGitActive(gateRun('idle', { git_active: true }))).toBe(true)
  })
  it('an idle run with a parked gate (no Git work) is editable', () => {
    const parked = gateRun('idle', { git_active: false, parked: true })
    expect(editLockOf(parked)).toBeNull()
    expect(flowRunMarks(null, parked).lock).toBeNull()
    expect(isGateGitActive(parked)).toBe(false)
    expect(isRunLocked(parked)).toBe(false)
  })
  it('git_active lock holds while the run status is not confirmed, and a gate without the field changes nothing', () => {
    expect(flowRunMarks(null, gateRun('idle', { git_active: true }), false).lock).toBe('run')
    expect(editLockOf(gateRun('idle'))).toBeNull()
    expect(editLockOf({ status: 'idle' })).toBeNull()
    expect(editLockOf(null, false)).toBe('unknown')
  })
  it('the status rules stay: running / paused / stopping lock whatever the gate says', () => {
    for (const status of ['running', 'paused', 'stopping']) expect(editLockOf(gateRun(status, { parked: true }))).toBe('run')
  })
  it('reads the server wire: the gate block of the run read -> the lock', () => {
    const wire = (g: Record<string, unknown>) => toFlowRun({ run_id: 'r1', status: 'idle', node_runs: [{ node_key: 'g1', status: 'open', gate: g }] })
    expect(editLockOf(wire({ git_active: true, parked: false }) as never)).toBe('run')
    expect(editLockOf(wire({ git_active: false, parked: true }) as never)).toBeNull()
    expect(toRunGateState({ git_active: true, parked: false, settings_lock_reason: 'decision_reserved' })).toMatchObject({ git_active: true, parked: false, settings_lock_reason: 'decision_reserved' })
    expect(toRunGateState({ settings_locked: true })).not.toHaveProperty('git_active')
  })
})
