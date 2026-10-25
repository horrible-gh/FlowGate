import { describe, expect, it } from 'vitest'
import { toTestSpecView } from '@/features/document'
import { testCardPresentation, testSpecTargetDocId } from '@/features/designer/inspector/features/testSpec/testSpecModel'
import { CASES, counts, liveRun, runRow, tsDocument } from './testSpecFixtures'

const rows = CASES.map((c) => ({ caseId: c.case_id, status: 'PASS' }))
const present = (fields: Record<string, unknown> = {}) => {
  const view = toTestSpecView(tsDocument(fields))
  return testCardPresentation(view, null, view.details?.rows.length ? view.details.rows : rows)
}
describe('v8 card stages and validity (0071 T1)', () => {
  it('reads an unbound card through its scenario TS', () => {
    expect(testSpecTargetDocId({ docId: null, docType: 'TS', cardKind: 'test', cardDocuments: [{ slot: 'scenario', docId: 'x' }] as any })).toBe('x')
  })
  it('a document with just the brief is placed; only initial writing becomes stage 2', () => {
    const view = toTestSpecView(tsDocument({ cases: [], latest_result: null, effective_result: null }))
    expect(testCardPresentation(view).stage).toBe(1)
    expect(testCardPresentation(view, 'initial').stage).toBe(2)
    expect(testCardPresentation(view, 'rewrite-all').stage).toBe(1)
  })
  it('a scenario without results is ready even when a TSR document already exists', () => {
    expect(present({ latest_result: null, effective_result: null, run_history: [], last_execution: null })).toMatchObject({ stage: 3, hasReport: false, canFollow: false })
  })
  it.each(['queued', 'preparing', 'executing', 'finalizing'])('a full run in %s is stage 4', (phase) => {
    expect(present({ active_run: liveRun('live', { phase }) })).toMatchObject({ stage: 4, runScope: 'all', canFollow: false })
  })
  it('cancel accepted only in history still has stage 4 and cannot follow', () => {
    expect(present({ active_run: null, last_execution: liveRun('stop', { status: 'cancelling', created_at: '2026-10-10' }) })).toMatchObject({ stage: 4, stateKey: 'state.cancelling', canFollow: false })
  })
  it.each([
    ['cancelled', { status: 'cancelled' }],
    ['prepare_failed', { status: 'failed', prepare_refused: { error: 'refused' } }],
    ['execution_failed', { status: 'failed', error: 'worker_error' }],
  ])('a newer whole attempt %s overrides an old all-pass result', (key, fields) => {
    expect(present({ last_execution: runRow('new', { ...fields, created_at: '2026-10-10' }) })).toMatchObject({ stage: 3, stateKey: `state.${key}`, canFollow: false })
  })
  it('stale results block completion; unchecked results retain confirmation behavior', () => {
    expect(present({ basis_valid: false })).toMatchObject({ stage: 3, canFollow: false })
    expect(present({ basis_valid: null, basis_verdict: { state: 'unchecked' } })).toMatchObject({ stage: 6, canFollow: true, resultState: { key: 'unchecked' } })
  })
  it('failed, blocked and missing human checks remain pending, with no invented decisions', () => {
    const view = toTestSpecView(tsDocument({ effective_result: { summary: { overall: 'FAIL', counts: counts(0, 1, 1, 1) }, cases: [
      { case_id: 'TC-1', status: 'FAIL' }, { case_id: 'TC-2', status: 'BLOCKED' },
    ] } }))
    expect(testCardPresentation(view, null, view.details!.rows)).toMatchObject({ stage: 5, pending: 3, canFollow: false })
  })
  it.each(['running', 'cancelled', 'failed'])('single-case retry %s keeps the previous aggregate at stage 5', (status) => {
    const previous = { summary: { overall: 'FAIL', counts: counts(1, 1) }, cases: [{ case_id: 'TC-1', status: 'FAIL' }, { case_id: 'TC-2', status: 'PASS' }, { case_id: 'TC-3', status: 'PASS' }] }
    const retry = runRow('retry', { status, phase: status === 'running' ? 'executing' : 'finished', selected_case_ids: ['TC-1'], created_at: '2026-10-10' })
    const state = present({ effective_result: previous, last_execution: retry, active_run: status === 'running' ? retry : null })
    expect(state).toMatchObject({ stage: 5, retryCaseId: 'TC-1', canFollow: false })
  })
  it('one runnable case is a full run, and an older failure does not replace the result', () => {
    expect(present({ cases: [CASES[0]], active_run: liveRun('one', { selected_case_ids: ['TC-1'] }) })).toMatchObject({ stage: 4, runScope: 'all' })
    expect(present({ last_execution: runRow('old', { status: 'failed', created_at: '2020-01-01' }) })).toMatchObject({ stage: 6, canFollow: true })
  })
  it('rewrite-all preserves the stage of an existing scenario', () => {
    const view = toTestSpecView(tsDocument())
    expect(testCardPresentation(view, 'rewrite-all', rows).stage).toBe(6)
  })
})
