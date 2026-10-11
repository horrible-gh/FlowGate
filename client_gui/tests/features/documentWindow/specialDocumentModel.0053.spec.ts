/**
 * flowgate.v02.0053 T#4 — the special views' pure rules: a document type is shown by its name (never its
 * code), the work plan's cards and Step labels (the v0.1 WorkPlanEditor table: rowNo, stepDocParts, a set
 * card with its result row), the test marks / flags that replace the v0.1 badges and bands, and the Case rows
 * of a test report read from its saved text (testReportRows: only the lines and cell values the server writes,
 * with a url evidence label that may hold ': '; anything else answers null and the window shows the text as written)
 * and the verdict each of those rows shows, which is the server record's, not the text's (0053 Q #419).
 */
import { describe, expect, it } from 'vitest'
import { toTestSpecDocument, toWorkPlanDocument } from '@/features/document'
import {
  documentTypeNameKey,
  testCategoryKey,
  testDocumentFlags,
  testDocumentStructured,
  testEvidenceLink,
  testCaseKey,
  testGateMark,
  testModeMark,
  testReportRows,
  testReportVerdicts,
  testResultByCase,
  testVerdictMark,
  workPlanQuantityNameKey,
  workPlanSheetCards,
} from '@/features/documentWindow/specialDocumentModel'

const step = (key: string, fields: Record<string, unknown> = {}) => {
  const [type, ordinal] = key.split('#')
  return { key, card_id: key, type, ordinal: Number(ordinal), pair_key: null, pair_role: 'single', provider_id: null, review_count: 0, locked: false, ...fields }
}

describe('document type names (flowgate.v02.0053 T#4)', () => {
  it('every work plan Step type has a name; anything else is just a document', () => {
    for (const code of ['DS', 'D', 'P', 'L', 'DB', 'N', 'NR', 'T', 'TR', 'T2', 'TR2', 'TS', 'TSR']) expect(documentTypeNameKey(code)).toBe(`types.${code}`)
    expect(documentTypeNameKey('tr')).toBe('types.TR')
    for (const code of ['X', '', null]) expect(documentTypeNameKey(code)).toBe('types.other')
  })

  it('a set box is named after its set, a sheet box after its type', () => {
    expect(workPlanQuantityNameKey({ type: 'T', unit: 'set' })).toBe('work_plan.sets.T')
    expect(workPlanQuantityNameKey({ type: 'TS', unit: 'set' })).toBe('work_plan.sets.TS')
    expect(workPlanQuantityNameKey({ type: 'D', unit: 'sheet' })).toBe('types.D')
    expect(workPlanQuantityNameKey({ type: 'T', unit: null })).toBe('types.T')
    expect(workPlanQuantityNameKey({ type: 'Z', unit: 'set' })).toBe('types.other')
  })
})

describe('workPlanSheetCards (flowgate.v02.0053 T#4)', () => {
  const doc = toWorkPlanDocument({
    doc_id: 'wp',
    body: {
      counted_types: ['D', 'P', 'T', 'TS'],
      quantities: { D: { count: 2, unit: 'sheet' }, P: { count: 1, unit: 'sheet' }, T: { count: 1, unit: 'set' }, TS: { count: 1, unit: 'set' } },
      steps: [
        step('D#1', { provider_id: 'claude', note: '화면', review_count: 2 }),
        step('D#2'),
        step('P#1', { pre_instruction_text: '지시' }),
        step('T#1', { pair_key: 'TR#1', pair_role: 'instruction', provider_id: 'codex', review_count: -1, pre_instruction_text: '지시' }),
        step('TR#1', { card_id: 'T#1', pair_key: 'T#1', pair_role: 'result', provider_id: 'codex', pre_instruction_text: '무시' }),
        step('TS#1', { pair_key: 'TSR#1', pair_role: 'instruction' }),
        step('TSR#1', { card_id: 'TS#1', pair_key: 'TS#1', pair_role: 'result', locked: true, note: '안 보임', review_count: 3 }),
      ],
    },
    assignment_summary: [
      { provider_id: 'claude', display_name: 'Claude', step_count: 1 },
      { provider_id: 'codex', display_name: 'Codex', step_count: 2 },
    ],
    step_execution_status: [{ step_key: 'TR#1', card_id: 'T#1', started: true }],
  })

  it('one card per single Step or instruction / result pair, rows numbered over the whole plan', () => {
    const cards = workPlanSheetCards(doc)
    expect(cards.map((card) => [card.cardId, card.started, card.rows.map((row) => `${row.no}:${row.key}`)])).toEqual([
      ['D#1', false, ['1:D#1']],
      ['D#2', false, ['2:D#2']],
      ['P#1', false, ['3:P#1']],
      ['T#1', true, ['4:T#1', '5:TR#1']],
      ['TS#1', false, ['6:TS#1', '7:TSR#1']],
    ])
  })

  it('the place label: n/total for a counted sheet type, n for a single sheet, n세트 for a set (result reads its pair)', () => {
    const rows = workPlanSheetCards(doc).flatMap((card) => card.rows)
    expect(rows.map((row) => row.place)).toEqual([
      { key: 'work_plan.sheet_of', n: 1, total: 2 },
      { key: 'work_plan.sheet_of', n: 2, total: 2 },
      { key: 'work_plan.sheet', n: 1 },
      { key: 'work_plan.set', n: 1 },
      { key: 'work_plan.set', n: 1 },
      { key: 'work_plan.set', n: 1 },
      { key: 'work_plan.set', n: 1 },
    ])
  })

  it('provider, review, note and pre-instruction; a result row has no pre-instruction, a server-assembled row nothing', () => {
    const rows = workPlanSheetCards(doc).flatMap((card) => card.rows)
    expect(rows.map((row) => [row.key, row.providerName, row.reviewCount, row.note, row.hasInstruction, row.result, row.locked])).toEqual([
      ['D#1', 'Claude', 2, '화면', false, false, false],
      ['D#2', null, 0, null, false, false, false],
      ['P#1', null, 0, null, true, false, false],
      ['T#1', 'Codex', -1, null, true, false, false],
      ['TR#1', 'Codex', 0, null, false, true, false],
      ['TS#1', null, 0, null, false, false, false],
      ['TSR#1', null, 0, null, false, true, true],
    ])
    expect(workPlanSheetCards(null)).toEqual([])
  })
})

describe('test marks (flowgate.v02.0053 T#4)', () => {
  it('a verdict is a glyph and a tone; anything else reads as not run', () => {
    expect(testVerdictMark('PASS')).toEqual({ glyph: '✓', tone: 'success', key: 'test.verdict.PASS' })
    expect(testVerdictMark('fail')).toMatchObject({ tone: 'danger', key: 'test.verdict.FAIL' })
    expect(testVerdictMark('BLOCKED')).toMatchObject({ tone: 'warning' })
    for (const value of ['NOT_RUN', 'weird', null, undefined]) expect(testVerdictMark(value).key).toBe('test.verdict.NOT_RUN')
  })

  it('mode and category show only known values (no raw value on screen)', () => {
    expect(testModeMark('automated')).toMatchObject({ glyph: '⚙', key: 'test.mode.automated' })
    expect(testModeMark('manual')?.key).toBe('test.mode.manual')
    expect(testModeMark('external')?.key).toBe('test.mode.external')
    expect(testModeMark('robot')).toBeNull()
    expect(testCategoryKey('boundary')).toBe('test.category.boundary')
    expect(testCategoryKey('other')).toBeNull()
    expect(testCategoryKey(null)).toBeNull()
    expect(testGateMark(true)).toMatchObject({ tone: 'success', key: 'test.gate_passed' })
    expect(testGateMark(false)).toMatchObject({ tone: 'danger', key: 'test.gate_blocked' })
  })

  const ts = (fields: Record<string, unknown> = {}) => toTestSpecDocument({ kind: 'TS', doc_id: 'ts', contract_version: 2, ...fields })

  it('flags replace the v0.1 bands: a live run (progress) first, else a stale or unchecked result; errors, unmapped, conflicts', () => {
    expect(testDocumentFlags(ts())).toEqual([])
    const live = testDocumentFlags(ts({ active_run: { run_id: 'r', case_total: 4 }, progress: { automated_total: 3, automated_completed: 1 }, basis_valid: false }))
    expect(live.map((flag) => [flag.mark.key, flag.text, flag.params])).toEqual([['test.running', '1/3', { done: 1, total: 3 }]])
    expect(testDocumentFlags(ts({ basis_valid: false })).map((flag) => flag.mark.key)).toEqual(['test.stale'])
    expect(testDocumentFlags(ts({ basis_verdict: { state: 'unchecked', reasons: [] } })).map((flag) => flag.mark.key)).toEqual(['test.unchecked'])
    const many = testDocumentFlags(
      ts({ errors: [{ code: 'x', message: 'm' }], latest_result: { run_id: 'r', unmapped: [{}, {}], conflicts: [{ case_id: 'C' }] } }),
    )
    expect(many.map((flag) => [flag.mark.key, flag.text])).toEqual([
      ['test.spec_errors', 1],
      ['test.unmapped', 2],
      ['test.conflicts', 1],
    ])
    expect(testDocumentFlags(null)).toEqual([])
  })

  it('only a specification TS / TSR is shown structured', () => {
    expect(testDocumentStructured(ts())).toBe(true)
    expect(testDocumentStructured(toTestSpecDocument({ kind: 'TSR', doc_id: 'r', contract_version: 2 }))).toBe(true)
    expect(testDocumentStructured(ts({ contract_version: 1 }))).toBe(false)
    expect(testDocumentStructured(ts({ contract_version: null }))).toBe(false)
    expect(testDocumentStructured(toTestSpecDocument({ kind: 'V', doc_id: 'v', contract_version: 2 }))).toBe(false)
    expect(testDocumentStructured(null)).toBe(false)
  })

  it('result rows by Case id', () => {
    const rows = ts({ effective_result: { summary: { overall: 'PASS' }, cases: [{ case_id: 'C-1', status: 'PASS' }] } }).rows
    expect(testResultByCase(rows).get('C-1')?.status).toBe('PASS')
    expect(testResultByCase(rows).has('C-2')).toBe(false)
  })
})

// What the server writes into a report file (test_spec_service render_report_markdown /
// render_pending_report_markdown, ko), from one result record.
const REPORT = `# 테스트 레포트 — 로그인 시나리오

> 문서 성격: 시험성적서 (TS flowgate.v02.0053.0022-TS revision 1 의 Case ID별 결과)
> 결과 기록: run-1 · 기록 시각 2026-10-11T10:00:00
> 종합 판정(서버 계산, 필수 Case 기준): **FAIL** — 시험 gate 미통과 · 다음 단계 진행 차단

## 요약

| 구분 | 전체 | PASS | FAIL | BLOCKED | NOT_RUN |
|---|---|---|---|---|---|
| 전체 | 2 | 1 | 1 | 0 | 0 |
| 필수 | 1 | 1 | 0 | 0 | 0 |
| 선택 | 1 | 0 | 1 | 0 | 0 |

## Case별 결과

| Case | 제목 | 필수 | 방식 | 기대 결과 | 실제 결과 | 판정 | 결함/참조 |
|---|---|---|---|---|---|---|---|
| C-1 | - | 필수 | automated | 대시보드 | 줄1<br>줄2 \\| 파이프 | **PASS** | - |
| C-2 | 잠긴 계정 | 선택 | manual | 잠김 안내 | 오류 | **FAIL** | B-7 |

## Evidence 및 source identity

- C-1 (PASS) · runner / 2026-10-11T10:00:00
  - [url] 로그: https://ci.example/1
  - [file] out.txt
  - source: git_revision=abc123
  - 이전 기록에서 유지: run-0
- C-2 (FAIL)
  - 없음

## TS에 없는 결과 (unmapped)

- X-9 · FAIL · junit.xml (unknown_case_id)

## 중복 매핑 (mapping conflict)

- C-2: 2 → FAIL, PASS

*이 시험성적서는 결과 기록 run-1 로부터 FlowGate가 조립했다. 종합 판정은 문서 본문이 아니라 서버 기록으로 결정된다.*

## Test Basis

- basis_id: \`b-1\`
- source_fingerprint: \`0123abcd\`
- current_source: 현재 소스와 같음
`

const PENDING = `# 테스트 레포트 — 로그인 시나리오

> 문서 성격: 시험성적서 (TS flowgate.v02.0053.0022-TS revision 1 의 Case ID별 결과)
> 시험 run: run-2 · 접수 시각 2026-10-11T11:00:00
> 상태: **시험 실행 중** — Case 결과 0/1건 반영. 남은 Case가 끝나면 결과가 기록되고 종합 판정이 정해진다.

## Case별 상태

| Case | 제목 | 필수 | 방식 | 기대 결과 | 상태 |
|---|---|---|---|---|---|
| C-1 | 올바른 비밀번호 | 필수 | automated | 대시보드 | 대기 (PENDING) |
| C-2 | 잠긴 계정 | 필수 | manual | 잠김 안내 | FAIL (이전 기록 유지) |

*이 레포트는 시험 run run-2 접수 시 FlowGate가 만들었다. 종합 판정은 결과가 기록된 뒤 서버 기록으로 결정된다.*
`

// The server's own output (render_report_markdown + _tsr_basis_section, render_pending_report_markdown run on
// a scratch copy of the server functions) in its other locales: an en result report with a url evidence label
// holding ': ', and a ja report of a running run.
const EN_TITLE = 'Test Report — 로그인 시나리오'
const EN_REPORT = `# Test Report — 로그인 시나리오

> Document: test report (per-Case-ID results of TS flowgate.v02.0053.0022-TS revision 1)
> Result record: run-1 · recorded at 2026-10-11T10:00:00+09:00
> Overall (server-computed from required cases): **NOT_RUN** — test gate NOT passed · next step blocked

## Summary

| Scope | Total | PASS | FAIL | BLOCKED | NOT_RUN |
|---|---|---|---|---|---|
| All | 3 | 1 | 1 | 0 | 1 |
| Required | 2 | 1 | 0 | 0 | 1 |
| Optional | 1 | 0 | 1 | 0 | 0 |

## Results by Case

| Case | Title | Required | Mode | Expected | Actual | Verdict | Defect/ref |
|---|---|---|---|---|---|---|---|
| C-1 | Login \\| ok | required | automated | dashboard | line1<br>line2 | **PASS** | - |
| C-2 | Locked | optional | manual | lock notice | error | **FAIL** | B-7 |
| C-3 | - | required | - | - | - | **NOT_RUN** | - |

## Evidence and source identity

- C-1 (PASS) · runner-1 / 2026-10-11T10:00:00+09:00
  - [url] CI: job 17: https://ci.example/jobs/17
  - [url] https://ci.example/raw
  - [log] repro: 3 of 3: failed
  - [text] plain
  - source: git_revision=abc123, runner=pytest
  - carried over from run-0
- C-2 (FAIL)
  - none
- C-3 (NOT_RUN) · runner-1
  - none

## Results not in the TS (unmapped)

- X-9 · FAIL · junit.xml (unknown_case_id)
- - · PASS · suite > test a (missing_case_id)

## Duplicate mappings (mapping conflict)

- C-2: 2 → FAIL, PASS

*FlowGate assembled this test report from result record run-1. The overall verdict is decided by the server record, not by this text.*

## Test Basis

- basis_id: \`b-1\`
- source_fingerprint: \`0123456789ab\`
- git_revision: \`abc123\` (dirty)
- measured_at: 2026-10-11T09:58:00+09:00
- test_assets.manifest_hash: \`mh1\`
- execution_run: \`run-1\`
- current_source: the source changed after execution; this result is not valid for the current source — run again (fingerprint_changed, assets_changed)
`

const JA_TITLE = 'テストレポート — 로그인 시나리오'
const JA_PENDING = `# テストレポート — 로그인 시나리오

> 文書種別: 試験成績書 (TS flowgate.v02.0053.0022-TS revision 1 のCase ID別結果)
> 試験run: run-2 · 受付時刻 2026-10-11T09:59:00+09:00
> 状態: **試験実行中** — Case結果 1/2件反映。残りのCaseが終わると結果が記録され総合判定が決まる。

## Case別状態

| Case | タイトル | 必須 | 方式 | 期待結果 | 状態 |
|---|---|---|---|---|---|
| C-1 | Login | 必須 | automated | dashboard | PASS (今回のrun結果) |
| C-2 | Locked | 必須 | manual | lock notice | 待機 (PENDING) |
| C-3 | Other | 任意 | automated | - | FAIL (以前の記録を維持) |

*このレポートは試験run run-2 の受付時にFlowGateが作成した。総合判定は結果記録後にサーバー記録で決まる。*
`

// What `/test-document` answers for REPORT: its run's rows (who checked each Case and when, its evidence).
const REPORT_RECORD = toTestSpecDocument({
  kind: 'TSR',
  doc_id: 'r',
  contract_version: 2,
  report: {
    run_id: 'run-1',
    cases: [
      {
        case_no: 'C-1',
        case_status: 'PASS',
        expect: '대시보드',
        actual: '줄1\n줄2 | 파이프',
        checked_by: 'runner',
        checked_at: '2026-10-11T10:00:00',
        evidence: [
          { kind: 'url', label: '로그', value: 'https://ci.example/1' },
          { kind: 'file', value: 'out.txt' },
        ],
      },
      { case_no: 'C-2', case_title: '잠긴 계정', case_status: 'FAIL', expect: '잠김 안내', actual: '오류', defect_ref: 'B-7' },
    ],
  },
})

// rej_01M4MTMVSD5EVC8Z: the server's output (render_report_markdown on a scratch copy of the server function)
// for values that hold its cell escapes as text — a `\|` (a literal pipe in a regex), `a|b`, a `<br>` as text,
// a `-` value, labels with `\|` — and who checked / when with spaces and ' / ' (normalize_results takes any
// text); ESCAPES_RECORD is its record as `/test-document` answers it.
const ESCAPES_TITLE = '테스트 레포트 — Login'
const ESCAPES_REPORT = `# 테스트 레포트 — Login

> 문서 성격: 시험성적서 (TS flowgate.v02.0053.0022-TS revision 1 의 Case ID별 결과)
> 결과 기록: run-1 · 기록 시각 2026-10-11T10:00:00+09:00
> 종합 판정(서버 계산, 필수 Case 기준): **FAIL** — 시험 gate 미통과 · 다음 단계 진행 차단

## 요약

| 구분 | 전체 | PASS | FAIL | BLOCKED | NOT_RUN |
|---|---|---|---|---|---|
| 전체 | 2 | 1 | 1 | 0 | 0 |
| 필수 | 1 | 0 | 1 | 0 | 0 |
| 선택 | 1 | 1 | 0 | 0 | 0 |

## Case별 결과

| Case | 제목 | 필수 | 방식 | 기대 결과 | 실제 결과 | 판정 | 결함/참조 |
|---|---|---|---|---|---|---|---|
| C-1 | Pattern \\\\| | 필수 | automated | - | got a\\|b <br> as text<br>line 2 | **FAIL** | ref \\\\| |
| C-2 | Locked | 선택 | manual | lock notice | error | **PASS** | - |

## Evidence 및 source identity

- C-1 (FAIL) · QA / night shift / 2026-10-11 10:00:00 KST
  - [url] CI \\| job: 17: https://ci.example/a\\|b
  - [log] grep a\\|b: x \\\\| y
  - [text] plain
- C-2 (PASS) · Test Engineer / 2026-10-11 10:05:00
  - 없음

## TS에 없는 결과 (unmapped)

- 없음

## 중복 매핑 (mapping conflict)

- 없음

*이 시험성적서는 결과 기록 run-1 로부터 FlowGate가 조립했다. 종합 판정은 문서 본문이 아니라 서버 기록으로 결정된다.*
`
const ESCAPES_RECORD = toTestSpecDocument({
  kind: 'TSR',
  doc_id: 'r',
  contract_version: 2,
  report: {
    run_id: 'run-1',
    cases: [
      {
        case_no: 'C-1',
        case_title: 'Pattern \\|',
        case_status: 'FAIL',
        expect: '-',
        actual: 'got a|b <br> as text\nline 2',
        required: true,
        execution_mode: 'automated',
        defect_ref: 'ref \\|',
        checked_by: 'QA / night shift',
        checked_at: '2026-10-11 10:00:00 KST',
        evidence: [
          { kind: 'url', label: 'CI \\| job: 17', value: 'https://ci.example/a|b' },
          { kind: 'log', label: 'grep a\\|b', value: 'x \\| y' },
          { kind: 'text', value: 'plain' },
        ],
      },
      {
        case_no: 'C-2',
        case_title: 'Locked',
        case_status: 'PASS',
        expect: 'lock notice',
        actual: 'error',
        required: false,
        execution_mode: 'manual',
        checked_by: 'Test Engineer',
        checked_at: '2026-10-11 10:05:00',
        evidence: [],
      },
    ],
  },
})
/** ESCAPES_REPORT without the one evidence line only the record can tell (a label holding `\|`). */
const ESCAPES_UNLABELED = ESCAPES_REPORT.replace('  - [log] grep a\\|b: x \\\\| y\n', '')

describe('the test report as saved (flowgate.v02.0053 T#4)', () => {
  it('reads the Case table and the evidence of a report the server wrote; the facts it does not show are let through', () => {
    expect(testReportRows(REPORT)).toEqual([
      {
        caseId: 'C-1',
        title: null,
        status: 'PASS',
        required: true,
        executionMode: 'automated',
        expected: '대시보드',
        actual: '줄1\n줄2 | 파이프',
        evidence: [
          { kind: 'url', value: 'https://ci.example/1', label: '로그' },
          { kind: 'file', value: 'out.txt', label: null },
        ],
        defectRef: null,
      },
      { caseId: 'C-2', title: '잠긴 계정', status: 'FAIL', required: false, executionMode: 'manual', expected: '잠김 안내', actual: '오류', evidence: [], defectRef: 'B-7' },
    ])
    // CRLF, frontmatter and a report without the Basis section read the same.
    const withoutBasis = REPORT.slice(0, REPORT.indexOf('\n## Test Basis'))
    expect(testReportRows(`---\ntitle: x\n---\n${withoutBasis}`.replace(/\n/g, '\r\n'))).toEqual(testReportRows(REPORT))
    // Read against its record (as the window does), the same rows.
    expect(testReportRows(REPORT, null, REPORT_RECORD)).toEqual(testReportRows(REPORT))
  })

  it('the first heading is the report document title the window knows; without one, any title of the server form', () => {
    expect(testReportRows(REPORT, '테스트 레포트 — 로그인 시나리오')).toEqual(testReportRows(REPORT))
    expect(testReportRows(REPORT, '다른 레포트')).toBeNull()
    expect(testReportRows(REPORT.replace('# 테스트 레포트 — 로그인 시나리오', '# 로그인 시나리오 검수본'))).toBeNull()
  })

  it('reads the report of a run without a result yet: its Cases, a verdict only where the text has one', () => {
    expect(testReportRows(PENDING)!.map((row) => [row.caseId, row.status, row.required, row.expected, row.actual])).toEqual([
      ['C-1', null, true, '대시보드', null],
      ['C-2', 'FAIL', true, '잠김 안내', null],
    ])
  })

  it("reads the server's en and ja reports too", () => {
    const rows = testReportRows(EN_REPORT, EN_TITLE)!
    expect(rows.map((row) => [row.caseId, row.title, row.status, row.required, row.executionMode, row.actual, row.defectRef])).toEqual([
      ['C-1', 'Login | ok', 'PASS', true, 'automated', 'line1\nline2', null],
      ['C-2', 'Locked', 'FAIL', false, 'manual', 'error', 'B-7'],
      ['C-3', null, 'NOT_RUN', true, null, null, null],
    ])
    expect(testReportRows(JA_PENDING, JA_TITLE)!.map((row) => [row.caseId, row.status, row.required])).toEqual([
      ['C-1', 'PASS', true],
      ['C-2', null, true],
      ['C-3', 'FAIL', false],
    ])
  })

  it("a url evidence label may hold ': ' — the address is what follows the last ': ' and the label is kept whole", () => {
    const evidence = testReportRows(EN_REPORT, EN_TITLE)![0].evidence
    expect(evidence).toEqual([
      { kind: 'url', value: 'https://ci.example/jobs/17', label: 'CI: job 17' },
      { kind: 'url', value: 'https://ci.example/raw', label: null },
      // Any other evidence is its text as written (the view shows `label: value` the same way).
      { kind: 'log', value: 'repro: 3 of 3: failed', label: null },
      { kind: 'text', value: 'plain', label: null },
    ])
    expect(evidence.map(testEvidenceLink)).toEqual(['https://ci.example/jobs/17', 'https://ci.example/raw', null, null])
    // A url item with no web address to tell apart is its text, never a link.
    const unclear = testReportRows(EN_REPORT.replace('[url] https://ci.example/raw', '[url] CI job page (see board)'), EN_TITLE)![0].evidence[1]
    expect(unclear).toEqual({ kind: 'url', value: 'CI job page (see board)', label: null })
    expect(testEvidenceLink(unclear)).toBeNull()
    for (const value of ['javascript:alert(1)', 'ci.example/jobs/17', 'https://ci.example/a b']) expect(testEvidenceLink({ kind: 'url', value })).toBeNull()
    expect(testEvidenceLink({ kind: 'text', value: 'https://ci.example/1' })).toBeNull()
  })

  it('an edited cell or evidence is what the rows say', () => {
    const edited = REPORT.replace('| 오류 | **FAIL** | B-7 |', '| 잠김 화면 대신 오류 | **BLOCKED** | B-8 |').replace('- C-2 (FAIL)\n  - 없음', '- C-2 (FAIL)\n  - [log] 재현: 3회')
    const row = testReportRows(edited)![1]
    expect([row.actual, row.status, row.defectRef, row.evidence]).toEqual(['잠김 화면 대신 오류', 'BLOCKED', 'B-8', [{ kind: 'log', value: '재현: 3회', label: null }]])
  })

  it('any text out of the report shape answers null (the window shows it as written)', () => {
    const note = 'Review note: reproduce on staging before release'
    const outOfShape = [
      `${REPORT}\n검수 메모: 수동 확인\n`, // a paragraph
      REPORT.replace('## TS에 없는 결과 (unmapped)', '## 검수 의견\n\n- 확인함\n\n## TS에 없는 결과 (unmapped)'), // a section of one's own
      REPORT.replace('| **FAIL** | B-7 |', '| **FAIL** |'), // a broken row
      REPORT.replace('- C-2 (FAIL)', '- C-3 (FAIL)'), // evidence of a Case not in the table
      REPORT.replace('  - 없음', '  - 메모: 다시 볼 것'), // a line of one's own under a Case
      REPORT.replace('- X-9 · FAIL · junit.xml (unknown_case_id)', '- 다시 확인 필요'), // an item of one's own
      REPORT.replace('- basis_id: `b-1`', '- 메모: b-1'), // a Basis line of one's own
      REPORT.replace('> 결과 기록: run-1', '결과 기록: run-1'), // the heading lines
      PENDING.replace('*이 레포트는', '이 레포트는'),
      '# 레포트\n\n자유 형식\n',
      '',
      // Notes in the server's own markup: a quote, a heading, an italic line by the footer.
      REPORT.replace('\n\n## 요약', `\n> ${note}\n\n## 요약`),
      REPORT.replace('\n\n## 요약', `\n\n# ${note}\n\n## 요약`),
      REPORT.replace('\n\n## Test Basis', `\n\n*${note}*\n\n## Test Basis`),
      `${REPORT}\n*${note}*\n`,
      PENDING.replace('\n\n## Case별 상태', `\n> ${note}\n\n## Case별 상태`),
      `${PENDING}\n*${note}*\n`,
      // A note inside a line the view does not show.
      REPORT.replace('**FAIL** — 시험 gate', `**FAIL** — ${note}`),
      REPORT.replace('source: git_revision=abc123', `source: git_revision=abc123, ${note}`),
      REPORT.replace('이전 기록에서 유지: run-0', `이전 기록에서 유지: run-0 ${note}`),
      REPORT.replace('| 전체 | 2 | 1 |', '| 전체 | 2 (검토) | 1 |'),
      REPORT.replace('(unknown_case_id)', '(검토 필요)'),
      REPORT.replace('2 → FAIL, PASS', '2 → FAIL, PASS — 검토'),
      REPORT.replace('- current_source: 현재 소스와 같음', `- current_source: ${note}`),
      REPORT.replace('**FAIL** — 시험 gate 미통과 · 다음 단계 진행 차단', '**FAIL** — 시험 gate 통과'), // gate words of another verdict
      // A cell the view could not say as written: a verdict, a required word, a mode of one's own.
      REPORT.replace('| **FAIL** | B-7 |', '| **REVIEW_REQUIRED** | B-7 |'),
      REPORT.replace('| **FAIL** | B-7 |', '| FAIL | B-7 |'),
      REPORT.replace('| 잠긴 계정 | 선택 |', '| 잠긴 계정 | required after review |'),
      REPORT.replace('| manual |', '| manual (review) |'),
      PENDING.replace('| 대기 (PENDING) |', '| REVIEW_REQUIRED |'),
      PENDING.replace('| 잠긴 계정 | 필수 |', '| 잠긴 계정 | 필수 (검토 후) |'),
      EN_REPORT.replace('| Locked | optional |', '| Locked | required after review |'),
    ]
    for (const text of outOfShape) {
      expect([REPORT, PENDING, EN_REPORT]).not.toContain(text)
      expect(testReportRows(text)).toBeNull()
      expect(testReportRows(text, null, REPORT_RECORD)).toBeNull()
    }
  })

  it("a cell's escapes are undone once: a value holding `\\|` keeps it (rej_01M4MTMVSD5EVC8Z)", () => {
    // The table is split on its borders only; `\\|` is then `\|` and `\|` is `|` — title, expected, actual, defect alike.
    for (const record of [ESCAPES_RECORD, null]) {
      const rows = testReportRows(ESCAPES_UNLABELED, ESCAPES_TITLE, record)!
      expect(rows.map((row) => [row.caseId, row.title, row.defectRef])).toEqual([
        ['C-1', 'Pattern \\|', 'ref \\|'],
        ['C-2', 'Locked', null],
      ])
    }
    // What only the record can say: a `<br>` and a `-` it holds as text (without it, a line break and empty).
    const exact = testReportRows(ESCAPES_REPORT, ESCAPES_TITLE, ESCAPES_RECORD)![0]
    expect([exact.expected, exact.actual]).toEqual(['-', 'got a|b <br> as text\nline 2'])
    const read = testReportRows(ESCAPES_UNLABELED, ESCAPES_TITLE)![0]
    expect([read.expected, read.actual]).toEqual([null, 'got a|b \n as text\nline 2'])
    // A cell edited with ✎ (not the record's value) is read by the server's escapes, once.
    const edited = ESCAPES_REPORT.replace('| error |', '| edited \\\\| kept a\\|b |')
    expect(edited).not.toBe(ESCAPES_REPORT)
    expect(testReportRows(edited, ESCAPES_TITLE, ESCAPES_RECORD)![1].actual).toBe('edited \\| kept a|b')
  })

  it('evidence: an item the record holds as written is that item; a label holding `\\|` cannot be told without it', () => {
    const evidence = testReportRows(ESCAPES_REPORT, ESCAPES_TITLE, ESCAPES_RECORD)![0].evidence
    expect(evidence).toEqual([
      { kind: 'url', value: 'https://ci.example/a|b', label: 'CI \\| job: 17' },
      { kind: 'log', value: 'x \\| y', label: 'grep a\\|b' },
      { kind: 'text', value: 'plain', label: null },
    ])
    expect(evidence.map(testEvidenceLink)).toEqual(['https://ci.example/a|b', null, null])
    // Without the record: a url item's label is all before its address, as written; the address's escapes undone.
    expect(testReportRows(ESCAPES_UNLABELED, ESCAPES_TITLE)![0].evidence[0]).toEqual({ kind: 'url', value: 'https://ci.example/a|b', label: 'CI \\| job: 17' })
    // Any other item with an escape before its last ': ' may hold it in its label: shown as written (null).
    expect(testReportRows(ESCAPES_REPORT, ESCAPES_TITLE)).toBeNull()
    // An edited item the record does not hold, read by the escapes.
    const edited = ESCAPES_REPORT.replace('  - [text] plain', '  - [text] 재현: a \\\\| b')
    expect(testReportRows(edited, ESCAPES_TITLE, ESCAPES_RECORD)![0].evidence[2]).toEqual({ kind: 'text', value: '재현: a \\| b', label: null })
  })

  it('who checked a Case and when are free text on the server (spaces, " / "): held to the record, not to a form', () => {
    // The server's report with `QA / night shift / 2026-10-11 10:00:00 KST` and `Test Engineer / 2026-10-11 10:05:00`.
    expect(testReportRows(ESCAPES_REPORT, ESCAPES_TITLE, ESCAPES_RECORD)!.map((row) => row.caseId)).toEqual(['C-1', 'C-2'])
    expect(testReportRows(ESCAPES_UNLABELED, ESCAPES_TITLE)!.map((row) => row.caseId)).toEqual(['C-1', 'C-2'])
    const note = 'Review note: recheck on staging'
    for (const text of [
      ESCAPES_REPORT.replace('10:00:00 KST', `10:00:00 KST — ${note}`), // a word of one's own in the line
      ESCAPES_REPORT.replace('· Test Engineer / ', '· Someone Else / '), // a checker that is not the record's
      ESCAPES_REPORT.replace('- C-2 (PASS) · Test Engineer / 2026-10-11 10:05:00', '- C-2 (PASS)'), // one taken out
      REPORT.replace('· runner / 2026-10-11T10:00:00', `· ${note}`),
    ]) {
      expect([ESCAPES_REPORT, REPORT]).not.toContain(text)
      expect(testReportRows(text, null, text.startsWith('# 테스트 레포트 — Login') ? ESCAPES_RECORD : REPORT_RECORD)).toBeNull()
    }
    // A Case the record does not hold cannot be held to it.
    expect(testReportRows(ESCAPES_REPORT, ESCAPES_TITLE, { storedRows: ESCAPES_RECORD.storedRows.slice(0, 1), rows: [] })).toBeNull()
    // The valid result stands in for a Case the report's run does not hold (as for its verdict).
    expect(testReportRows(ESCAPES_REPORT, ESCAPES_TITLE, { storedRows: ESCAPES_RECORD.storedRows.slice(0, 1), rows: ESCAPES_RECORD.storedRows })).not.toBeNull()
  })
})

describe('the verdict a report row shows (flowgate.v02.0053 T#4, Q #419)', () => {
  const row = (caseId: string, status: string | null) => ({
    caseId,
    title: null,
    status,
    required: true,
    executionMode: null,
    expected: null,
    actual: null,
    evidence: [],
    defectRef: null,
    checkedBy: null,
    checkedAt: null,
  })

  it("is the record's: the report's own run first, then the valid result for a Case that run does not hold", () => {
    // A stale report (its run FAIL, the valid result on the live source NOT_RUN) and a Case kept from an earlier record.
    const verdicts = testReportVerdicts({ storedRows: [row('C-1', 'FAIL')], rows: [row('C-1', 'NOT_RUN'), row('C-2', 'PASS')] })
    expect([...verdicts]).toEqual([
      ['C-1', 'FAIL'],
      ['C-2', 'PASS'],
    ])
    // A Case the record has nothing for has no verdict (not run); the text's verdict word plays no part.
    expect(verdicts.get(testCaseKey('C-3'))).toBeUndefined()
    expect(testReportVerdicts({ storedRows: [], rows: [] }).size).toBe(0)
  })

  it('matches Case IDs as the server does (test_spec_service case_id_key)', () => {
    expect(['TC-1', 'tc-001', ' tc_0001 ', 'TC-1'].map(testCaseKey)).toEqual(['TC-1', 'TC-1', 'TC-1', 'TC-1'])
    expect(['TC-0', 'TC-000', 'LOGIN', 'a-b-1', 'C-10'].map(testCaseKey)).toEqual(['TC-0', 'TC-0', 'LOGIN', 'A-B-1', 'C-10'])
    expect(testReportVerdicts({ storedRows: [row('tc_002', 'BLOCKED')], rows: [] }).get(testCaseKey('TC-2'))).toBe('BLOCKED')
  })

  it("reads a report answer's stored run apart from its valid result", () => {
    const read = toTestSpecDocument({
      ok: true,
      kind: 'TSR',
      doc_id: 'flowgate.v02.0053.0023-TSR',
      contract_version: 2,
      report: { run_id: 'run-1', overall: 'FAIL', cases: [{ case_no: 'C-1', case_status: 'FAIL', required: true }] },
      effective_result: { summary: { overall: 'NOT_RUN' }, cases: [{ case_id: 'C-1', status: 'NOT_RUN', required: true }] },
    })
    expect(read.rows.map((item) => [item.caseId, item.status])).toEqual([['C-1', 'NOT_RUN']])
    expect(read.storedRows.map((item) => [item.caseId, item.status])).toEqual([['C-1', 'FAIL']])
    expect(testReportVerdicts(read).get('C-1')).toBe('FAIL')
  })
})
