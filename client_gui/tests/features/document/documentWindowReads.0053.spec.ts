/**
 * flowgate.v02.0053 T#4 — the reads of the document window's special views in features/document: the same
 * v0.1 endpoints as the Inspector sections (GET /documents/{doc_id}/work-plan, GET /documents/{doc_id}/test-document,
 * nothing else), normalized with the parts those views show — the work plan's quantity boxes and Step notes /
 * pre-instructions, the scenario's Case fields and the report rows (the valid result first, as v0.1 does).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postRequest = vi.fn()
const putRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: (...args: unknown[]) => postRequest(...args),
  putRequest: (...args: unknown[]) => putRequest(...args),
  patchRequest: vi.fn(),
}))

import {
  testSpecDocumentRead,
  toTestSpecDocument,
  toTestSpecView,
  toWorkPlanDocument,
  toWorkPlanView,
  workPlanDocumentRead,
} from '@/features/document'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
})

const WP = 'flowgate.v02.0053.0011-WP'
const TS = 'flowgate.v02.0053.0012-TS'
const TSR = 'flowgate.v02.0053.0013-TSR'

const plan = (body: Record<string, unknown>) => ({ ok: true, doc_id: WP, title: '작업계획', revision_no: 1, editable: true, body })

describe('workPlanDocumentRead / toWorkPlanDocument (flowgate.v02.0053 T#4)', () => {
  it('GET /api/v1/documents/{doc_id}/work-plan once, doc_id encoded; no write call', async () => {
    getRequest.mockResolvedValue({ data: plan({ steps: [] }) })
    const read = await workPlanDocumentRead('a b/WP')
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/a%20b%2FWP/work-plan')
    expect(postRequest).not.toHaveBeenCalled()
    expect(putRequest).not.toHaveBeenCalled()
    expect(read.view.docId).toBe(WP)
  })

  it('quantity boxes follow counted_types, then any other stored type; unit is sheet / set / null', () => {
    const raw = plan({
      counted_types: ['D', 'T'],
      quantities: { T: { count: 2, unit: 'set' }, D: { count: 1, unit: 'sheet' }, TS: { count: 0, unit: 'set' }, X: { count: -3, unit: 'box' } },
      steps: [],
    })
    expect(toWorkPlanDocument(raw).quantities).toEqual([
      { type: 'D', count: 1, unit: 'sheet' },
      { type: 'T', count: 2, unit: 'set' },
      { type: 'TS', count: 0, unit: 'set' },
      { type: 'X', count: 0, unit: null },
    ])
  })

  it('Step details: the one-line note (blank is none) and whether a pre-instruction text or attachment is set', () => {
    const raw = plan({
      steps: [
        { key: 'D#1', type: 'D', ordinal: 1, note: '  화면 설계  ', pre_instruction_text: '# 지시', review_count: 2 },
        { key: 'T#1', type: 'T', ordinal: 1, note: '   ', pre_instruction_text: '  ', pre_instruction_attachment: { doc_id: 'x', filename: 'a.md' }, review_count: -1 },
        { key: 'TR#1', type: 'TR', ordinal: 1, note: null, pre_instruction_attachment: null, review_count: -2 },
        { type: 'D' },
      ],
    })
    const read = toWorkPlanDocument(raw)
    // review_count keeps -1 (until approved), which the summary row reads as 0; anything below is none.
    expect(read.details).toEqual({
      'D#1': { note: '화면 설계', reviewCount: 2, hasInstruction: true },
      'T#1': { note: null, reviewCount: -1, hasInstruction: true },
      'TR#1': { note: null, reviewCount: 0, hasInstruction: false },
    })
    // The summary view is the Inspector's normalizer, unchanged.
    expect(read.view).toEqual(toWorkPlanView(raw))
  })

  it('a thin answer: no boxes, no details, no throw', () => {
    expect(toWorkPlanDocument({ doc_id: WP })).toMatchObject({ quantities: [], details: {} })
    expect(toWorkPlanDocument(null).quantities).toEqual([])
  })
})

const specCase = (id: string, fields: Record<string, unknown> = {}) => ({
  case_id: id,
  title: `케이스 ${id}`,
  category: 'normal',
  requirement: '요구 1',
  execution_mode: 'automated',
  required: true,
  precondition: '',
  input: '입력값',
  procedure: '1. 연다',
  expected: '열린다',
  check_points: '   ',
  automation_ref: 'tests/x.spec.ts',
  ...fields,
})

describe('testSpecDocumentRead / toTestSpecDocument (flowgate.v02.0053 T#4)', () => {
  it('GET /api/v1/documents/{doc_id}/test-document once, doc_id encoded', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, kind: 'TS', doc_id: TS, contract_version: 2 } })
    const read = await testSpecDocumentRead('a b/TS')
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/a%20b%2FTS/test-document')
    expect(read.view.kind).toBe('TS')
  })

  it('a specification TS: title, intro, Case fields (blank is none), result rows from the valid result first', () => {
    const raw = {
      ok: true,
      kind: 'TS',
      doc_id: TS,
      contract_version: 2,
      title: '로그인 시나리오',
      intro: '로그인을 확인한다.',
      cases: [specCase('C-1'), specCase('C-2', { category: 'boundary', execution_mode: 'manual', required: false })],
      latest_result: { run_id: 'r1', overall: 'FAIL', cases: [{ case_no: 'C-1', case_status: 'FAIL', expect: '옛 기대' }] },
      effective_result: {
        summary: { overall: 'PASS', counts: { total: 2, pass: 2, fail: 0, blocked: 0, not_run: 0 }, required_counts: { total: 1, pass: 1, fail: 0, blocked: 0, not_run: 0 } },
        cases: [{ case_id: 'C-1', title: '케이스 C-1', expected: '열린다', actual: '열림', required: true, status: 'PASS', evidence: [{ kind: 'url', value: 'https://x' }, { kind: 'log', value: ' ' }] }],
      },
      active_run: null,
      pending_case_ids: ['C-2'],
    }
    const read = toTestSpecDocument(raw)
    expect(read.view).toEqual(toTestSpecView(raw))
    expect(read).toMatchObject({ title: '로그인 시나리오', intro: '로그인을 확인한다.', overall: 'PASS', unmapped: 0, conflicts: 0 })
    expect(read.counts).toEqual({ total: 2, pass: 2, fail: 0, blocked: 0, notRun: 0 })
    expect(read.requiredCounts).toEqual({ total: 1, pass: 1, fail: 0, blocked: 0, notRun: 0 })
    expect(read.cases[0]).toEqual({
      caseId: 'C-1',
      title: '케이스 C-1',
      executionMode: 'automated',
      required: true,
      category: 'normal',
      fields: { requirement: '요구 1', precondition: null, input: '입력값', procedure: '1. 연다', expected: '열린다', checkPoints: null, automationRef: 'tests/x.spec.ts', testAssets: null },
    })
    expect(read.cases[1]).toMatchObject({ caseId: 'C-2', category: 'boundary', executionMode: 'manual', required: false })
    expect(read.rows).toEqual([
      {
        caseId: 'C-1',
        title: '케이스 C-1',
        status: 'PASS',
        required: true,
        executionMode: null,
        expected: '열린다',
        actual: '열림',
        evidence: [{ kind: 'url', value: 'https://x', label: null }],
        defectRef: null,
        checkedBy: null,
        checkedAt: null,
      },
    ])
    // Pending Cases count only while a run is live.
    expect(read.pendingCaseIds).toEqual([])
    expect(toTestSpecDocument({ ...raw, active_run: { run_id: 'r2', status: 'running' } }).pendingCaseIds).toEqual(['C-2'])
  })

  it('a TSR without a valid result: rows, overall and counts of its stored report; unmapped and conflicts counted', () => {
    const raw = {
      ok: true,
      kind: 'TSR',
      doc_id: TSR,
      contract_version: 2,
      target_ts: TS,
      report: {
        run_id: 'r1',
        overall: 'FAIL',
        summary: { counts: { total: 2, pass: 1, fail: 1, blocked: 0, not_run: 0 } },
        cases: [
          { case_no: 'C-1', case_title: '로그인', case_status: 'PASS', expect: '열린다', actual: '열림', required: true, execution_mode: 'automated', checked_by: 'Test Engineer', checked_at: '2026-10-11 10:00:00' },
          { case_no: 'C-2', case_title: '잠금', case_status: 'FAIL', required: false, defect_ref: 'B-12' },
          { case_title: 'no id' },
        ],
        unmapped: [{ case_id: 'C-9' }],
        conflicts: [{ case_id: 'C-1', result_count: 2, statuses: ['PASS', 'FAIL'] }, { case_id: 'C-2', result_count: 2, statuses: [] }],
      },
      gate: { applies: true, passed: false, overall: 'FAIL' },
    }
    const read = toTestSpecDocument(raw)
    expect(read).toMatchObject({ overall: 'FAIL', unmapped: 1, conflicts: 2, requiredCounts: null, cases: [] })
    expect(read.counts).toEqual({ total: 2, pass: 1, fail: 1, blocked: 0, notRun: 0 })
    expect(read.rows.map((row) => [row.caseId, row.title, row.status, row.required, row.executionMode, row.expected, row.defectRef])).toEqual([
      ['C-1', '로그인', 'PASS', true, 'automated', '열린다', null],
      ['C-2', '잠금', 'FAIL', false, null, null, 'B-12'],
    ])
    // Who checked a Case and when, as the record holds them (any text: the report text is held to them).
    expect(read.storedRows.map((row) => [row.caseId, row.checkedBy, row.checkedAt])).toEqual([
      ['C-1', 'Test Engineer', '2026-10-11 10:00:00'],
      ['C-2', null, null],
    ])
    expect(read.view.gate).toEqual({ applies: true, passed: false, overall: 'FAIL' })
  })

  it('a legacy or unwritten document reads without rows or Cases', () => {
    const read = toTestSpecDocument({ ok: true, kind: 'TS', doc_id: TS, contract_version: 1 })
    expect(read).toMatchObject({ cases: [], rows: [], overall: null, counts: null, intro: null, title: null })
    expect(read.view.contractVersion).toBe(1)
    expect(() => toTestSpecDocument({ ok: true, kind: 'TS' })).toThrow(/doc_id/)
  })
})
