import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toTestSpecView, toTestSpecDocument, testSpecCasePayload, testSpecSave, testSpecCaseRun, testSpecResultEnter } from '@/features/document'
const calls = vi.hoisted(() => ({ put: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), putRequest: calls.put, postRequest: calls.post }))
const caseRow = { case_id: 'TC-1', title: 'Case', category: 'boundary', requirement: 'R1', execution_mode: 'automated', required: false,
  precondition: 'ready', input: 'hidden input', procedure: 'run', expected: 'ok', check_points: 'hidden checks', automation_ref: 'tests/test_x.py::test_x', test_assets: 'fixtures/input.json' }
const response = { doc_id: 'ts', kind: 'TS', contract_version: 2, title: 'Keep title', cases: [caseRow], errors: [{ code: 'invalid_field', case_id: 'TC-1', field: 'input', message: 'Explain' }],
  active_run: { run_id: 'r', selected_case_ids: ['TC-1'] }, effective_result: { cases: [{ case_id: 'TC-1', status: 'FAIL', actual: 'bad', evidence: [{ kind: 'log', value: 'log.txt' }] }] } }
beforeEach(() => { calls.put.mockReset().mockResolvedValue({ data: {} }); calls.post.mockReset().mockResolvedValue({ data: {} }) })
describe('0071 full-case read and existing mutation wrappers', () => {
  it('normalizes every server case field, error detail and selected case ID in both read models', () => {
    const view = toTestSpecView(response), doc = toTestSpecDocument(response)
    expect(testSpecCasePayload(doc.cases[0])).toEqual(caseRow)
    expect(view.details?.cases).toEqual(doc.cases)
    expect(doc.errors).toEqual([{ code: 'invalid_field', caseId: 'TC-1', field: 'input', message: 'Explain' }])
    expect(view.activeRun?.selectedCaseIds).toEqual(['TC-1'])
    expect(view.details?.rows[0]).toMatchObject({ caseId: 'TC-1', status: 'FAIL', actual: 'bad', evidence: [{ kind: 'log', value: 'log.txt' }] })
  })
  it('sends the complete case list and H1, omitting intro and trailing body from the structured save contract', async () => {
    const payload = testSpecCasePayload(toTestSpecDocument(response).cases[0])
    await testSpecSave('id/ts', [payload], 'Keep title')
    expect(calls.put).toHaveBeenCalledWith('/api/v1/documents/id%2Fts/test-spec', { cases: [caseRow], title: 'Keep title' })
  })
  it('single-case rerun calls the case route, separate from Graph/AI execution', async () => {
    calls.post.mockResolvedValueOnce({ data: { run_id: 'case-run', status: 'running' } })
    await expect(testSpecCaseRun('ts', 'TC/1')).resolves.toEqual({ runId: 'case-run', status: 'running' })
    expect(calls.post).toHaveBeenCalledWith('/api/v1/documents/ts/test-spec/cases/TC%2F1/run', {})
  })
  it('manual results and evidence use the existing result route; a partial refusal is not success', async () => {
    const result = { case_id: 'TC-1', status: 'PASS' as const, evidence: [{ kind: 'log', value: 'evidence', label: null }] }
    await testSpecResultEnter('ts', result)
    expect(calls.post).toHaveBeenCalledWith('/api/v1/documents/test-results', { doc_id: 'ts', results: [result] })
    calls.post.mockResolvedValueOnce({ data: { refused_results: [{ case_id: 'TC-1', reason: 'server_owned' }] } })
    await expect(testSpecResultEnter('ts', result)).rejects.toMatchObject({ response: { status: 422 } })
  })
})
