import { beforeEach, describe, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: (...a: unknown[]) => http.get(...a), postRequest: (...a: unknown[]) => http.post(...a) }))
import { runGateDecide, runGateHistoryRead, runGateReceiptsRead, runGateRecover, toRunCurrent } from '@/features/document/api/run'
import { toSettingsNode, settingsNodeErrorOf } from '@/features/document/api/settings'
beforeEach(() => { http.get.mockReset(); http.post.mockReset() })
describe('gate UI wire contract — D §8-10', () => {
  it('retains attempt, server action/lock facts, receipt and phase evidence', () => {
    const facts = { can_decide: false, settings_locked: true, decision_in_progress: true, recovery_reset_required: false, archived: false,
      decision: { decision_id: 'd1', result: 'in_progress', status: 'recovery_required', actor_user_id: 'kim', comment: 'reason', effects: [{ phase: 'push', status: 'unknown' }] },
      recovery: { decision_id: 'd1', decision_version: 7, evidence_version: 5, evidence_refs: ['d1/push@2'], resolvable: true }, job: { job_id: 'j1', status: 'recovery_required' } }
    const run = toRunCurrent({ run_id: 'r1', status: 'idle', node_runs: [{ node_key: 'g1', status: 'open', attempt_id: 'a1', gate: facts }] })!
    expect(run.nodes[0].attemptId).toBe('a1')
    expect(run.nodes[0].gate).toEqual(facts)
    expect(toSettingsNode({ settings_version: 3, locked: true, lock_reason: 'gate_decision_in_progress', fields: {} })).toMatchObject({ settingsVersion: 3, locked: true, lockReason: 'gate_decision_in_progress' })
    expect(settingsNodeErrorOf({ isAxiosError: true, response: { status: 409, data: { error: { code: 'gate_settings_locked' } } } })).toBe('locked')
  })
  it('sends the versioned request and preserves the optional legacy comment', async () => {
    http.post.mockResolvedValue({ data: { run: { run_id: 'r1', status: 'idle', node_runs: [] } } })
    const intent = { request_id: 'request-1', expected_attempt_id: 'a1', expected_settings_version: 2 }
    await runGateDecide('run /1', 'gate /1', 'reject', ' optional reason ', intent)
    expect(http.post).toHaveBeenLastCalledWith('/api/v1/runs/run%20%2F1/nodes/gate%20%2F1/gate-decision', { decision: 'reject', comment: 'optional reason', ...intent })
    await runGateDecide('r1', 'g1', 'confirm')
    expect(http.post).toHaveBeenLastCalledWith('/api/v1/runs/r1/nodes/g1/gate-decision', { decision: 'confirm' })
  })
  it('hydrates the exact recorded receipt because run summaries omit settings', async () => {
    const summary = { decision_id: 'd1', source: 'gate_decision', target_branch: 'release/local', status: 'succeeded' }
    const run = toRunCurrent({ run_id: 'r1', status: 'idle', node_runs: [{ node_key: 'g1', status: 'done', gate: { decision: summary } }] })!
    const full = { ...summary, settings: { shown: { merge_target_branch: 'release/local' }, effective: { merge_mode: 'merge', push: true }, target_basis: { unmerge_supported: false, target_branch: 'release/local' } } }
    http.get.mockResolvedValueOnce({ data: { items: [full], latest: full, next_cursor: null } })
    const hydrated = await runGateReceiptsRead(run, 'g1')
    expect(http.get).toHaveBeenCalledWith('/api/v1/runs/r1/nodes/g1/gate-decisions')
    expect(hydrated?.nodes[0].gate?.decision).toMatchObject({ ...full, settingsUnavailable: false })
    expect(run.nodes[0].gate?.decision).not.toHaveProperty('settings')
  })
  it('never uses a different latest decision or live settings when the matching receipt is missing', async () => {
    const run = toRunCurrent({ run_id: 'r1', status: 'idle', node_runs: [{ node_key: 'g1', gate: { decision: { decision_id: 'd1', source: 'gate_decision', target_branch: 'release/local' } } }] })!
    http.get.mockResolvedValueOnce({ data: { items: [], latest: { decision_id: 'd2', settings: { target_basis: { unmerge_supported: true } } }, next_cursor: null } })
    const missing = await runGateReceiptsRead(run)
    expect(missing?.nodes[0].gate?.decision).toMatchObject({ decision_id: 'd1', target_branch: 'release/local', settingsUnavailable: true })
    http.get.mockRejectedValueOnce({ request: {} })
    expect((await runGateReceiptsRead(run))?.nodes[0].gate?.decision?.settingsUnavailable).toBe(true)
  })
  it('reads history and sends recovery to the scoped path with reviewed evidence unchanged', async () => {
    const page = { items: [], latest: null, next_cursor: null }
    http.get.mockResolvedValue({ data: page }); http.post.mockResolvedValue({ data: { run: null } })
    expect(await runGateHistoryRead('r1', 'g1')).toEqual(page)
    const body = { request_id: 'req', expected_decision_version: 4, resolution: 'abandon' as const, reason: 'remote unavailable', evidence_version: 2, evidence_refs: ['d1/push@1'] }
    await runGateRecover('r1', 'g1', 'd/1', 'resolve', body)
    expect(http.post).toHaveBeenCalledWith('/api/v1/runs/r1/nodes/g1/gate-decisions/d%2F1/resolve', body)
  })
})
