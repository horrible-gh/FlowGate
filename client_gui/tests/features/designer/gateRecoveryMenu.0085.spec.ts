/** D0007 §8-10: recovery returns through the existing card menu, with no new UI entry. */
import { describe, expect, it } from 'vitest'
import { flowCardMenu, type FlowCardMenuInput } from '@/features/designer/center/run/flowCardMenu'
import { toFlowRun } from '@/features/project/flowRunApi'
const base: FlowCardMenuInput = { nodeStatus: 'pending', hold: false, stored: true, lock: null, run: { status: 'idle' }, known: true, ownership: 'graph', busy: false, canHold: true, canTimeMachine: true, gate: true }
const rewind = (change: Partial<FlowCardMenuInput>) => flowCardMenu({ ...base, ...change }).find(item => item.key === 'time_machine')
describe('gate recovery in the existing Time Machine menu', () => {
  it('retains the server recovery flag in run reads', () => {
    expect(toFlowRun({ run_id: 'r1', status: 'idle', node_runs: [{ node_key: 'g1', status: 'pending', gate: { recovery_reset_required: true } }] })?.nodeRuns?.[0].gateRecoveryResetRequired).toBe(true)
  })
  it('opens rejected and recovery-reset pending gates, but never ordinary pending cards', () => {
    expect(rewind({ nodeStatus: 'rejected' })?.disabled).toBe(false)
    expect(rewind({ gateRecoveryResetRequired: true })?.disabled).toBe(false)
    expect(rewind({})).toBeUndefined()
    expect(rewind({ gate: false, gateRecoveryResetRequired: true })).toBeUndefined()
    expect(rewind({ nodeStatus: 'done', gate: false })?.disabled).toBe(false)
  })
  it('keeps the existing idle/known/busy safety checks', () => {
    expect(rewind({ nodeStatus: 'rejected', run: { status: 'running' } })?.reason).toBe('run_not_idle')
    expect(rewind({ nodeStatus: 'rejected', known: false })?.reason).toBe('run_unknown')
    expect(rewind({ gateRecoveryResetRequired: true, busy: true })?.reason).toBe('busy')
  })
})
