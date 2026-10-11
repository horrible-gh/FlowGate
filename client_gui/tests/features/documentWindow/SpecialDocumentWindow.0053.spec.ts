/**
 * flowgate.v02.0053 T#4 — special documents in the document window, as the App Shell mounts it
 * (DocumentWindows) with `@/features/document` mocked at its reads and the save: a work plan shows its plan
 * table (the v0.1 WorkPlanEditor's, read only), a specification test scenario its Cases and a test report its
 * verdicts (the v0.1 TestDocumentBody's), every other document the MD Viewer. The flow is the same as a
 * general document: the special view → ✎ → the text editor of the stored file → 저장 writes it with the v0.1
 * content save and the view reads the document again; 취소 drops it. A legacy or not-yet-written test document
 * keeps the Markdown body, and turns into its test view once the server answers it as a specification. No
 * document type code shows anywhere in these windows.
 * A test report's rows are its saved text (the server answers the run record and never reads the report file):
 * a ✎ edit shows when the view comes back and when the window is opened again, while the verdict, the gate and
 * each Case's verdict stay the record's (0053 Q #419); while a rerun is live its progress leads and the previous
 * verdict and gate are not shown.
 * A report text with any line or cell the server does not write (a quote, a heading or an italic note, a verdict,
 * required word or mode of one's own) is shown as written, saved and opened again alike; an evidence link is a url
 * item's own address, its label kept whole even when it holds ': '.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DocumentWindows, closeAllDocumentWindows, closeDocumentWindow, openDocumentWindow } from '@/features/documentWindow'
import { DOCUMENT_WINDOW_POLL_MS, DOCUMENT_WINDOW_REFRESH_COALESCE_MS } from '@/features/documentWindow/useDocumentWindowEvents'

const recordRead = vi.fn()
const contentRead = vi.fn()
const contentSave = vi.fn()
const planRead = vi.fn()
const testRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentRead: (...args: unknown[]) => recordRead(...args),
    documentContentRead: (...args: unknown[]) => contentRead(...args),
    documentContentSave: (...args: unknown[]) => contentSave(...args),
    workPlanDocumentRead: (...args: unknown[]) => planRead(...args),
    testSpecDocumentRead: (...args: unknown[]) => testRead(...args),
  }
})

const { toDocumentRecord, toTestSpecDocument, toWorkPlanDocument } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

registerAppMessages(i18n)

const PLAN = 'flowgate.v02.0053.0021-WP'
const SPEC = 'flowgate.v02.0053.0022-TS'
const REPORT = 'flowgate.v02.0053.0023-TSR'
const LEGACY = 'flowgate.v02.0053.0024-TS'
const EMPTY_SPEC = 'flowgate.v02.0053.0025-TS'
const ORDER = 'flowgate.v02.0053.0026-T'

const FRONT = '---\ntitle: 테스트 시나리오\n---'

const RECORDS: Record<string, unknown> = {
  [PLAN]: { doc_id: PLAN, type_code: 'WP', title: '작업계획 — 설계 2장 · 작업 1세트', status: 'open', doc_review_status: 'pending_review' },
  [SPEC]: { doc_id: SPEC, type_code: 'TS', title: '로그인 시나리오', status: 'open', doc_review_status: 'approved' },
  // The server names a report after its scenario and writes that title as the report text's first heading.
  [REPORT]: { doc_id: REPORT, type_code: 'TSR', title: '테스트 레포트 — 로그인 시나리오', status: 'open', doc_review_status: 'pending_review' },
  [LEGACY]: { doc_id: LEGACY, type_code: 'TS', title: '옛 시나리오', status: 'closed', doc_review_status: 'approved' },
  [EMPTY_SPEC]: { doc_id: EMPTY_SPEC, type_code: 'TS', title: '시나리오', status: 'draft', doc_review_status: null },
  [ORDER]: { doc_id: ORDER, type_code: 'T', title: '작업지시', status: 'closed', doc_review_status: 'approved' },
}

const planBody = (note = '화면 설계') => ({
  wp_version: 2,
  counted_types: ['D', 'T', 'TS'],
  quantities: { D: { count: 2, unit: 'sheet' }, T: { count: 1, unit: 'set' }, TS: { count: 0, unit: 'set' } },
  steps: [
    { key: 'D#1', card_id: 'D#1', type: 'D', ordinal: 1, pair_role: 'single', provider_id: 'claude', note, review_count: 2, locked: false },
    { key: 'D#2', card_id: 'D#2', type: 'D', ordinal: 2, pair_role: 'single', provider_id: null, review_count: 0, locked: false },
    { key: 'T#1', card_id: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: 'codex', review_count: 0, pre_instruction_text: '# 지시', locked: false },
    { key: 'TR#1', card_id: 'T#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: 'codex', review_count: 0, locked: false },
  ],
})

const planAnswer = (body: Record<string, unknown>) => ({
  ok: true,
  doc_id: PLAN,
  title: '작업계획 — 설계 2장 · 작업 1세트',
  revision_no: 2,
  editable: true,
  body,
  assignment_summary: [
    { provider_id: 'claude', display_name: 'Claude', step_count: 1 },
    { provider_id: 'codex', display_name: 'Codex', step_count: 2 },
  ],
  step_execution_status: [{ step_key: 'T#1', card_id: 'T#1', started: true }],
  totals: { design_sheets: 2, work_sets: 1, steps: 4 },
})

const specAnswer = (fields: Record<string, unknown> = {}) => ({
  ok: true,
  kind: 'TS',
  doc_id: SPEC,
  contract_version: 2,
  title: '로그인 시나리오',
  intro: '로그인 화면을 확인한다.',
  doc_review_status: 'approved',
  cases: [
    { case_id: 'C-1', title: '올바른 비밀번호', category: 'normal', execution_mode: 'automated', required: true, procedure: '1. 로그인', expected: '대시보드', input: '' },
    { case_id: 'C-2', title: '잠긴 계정', category: 'negative', execution_mode: 'manual', required: false, expected: '잠김 안내' },
  ],
  effective_result: {
    summary: { overall: 'FAIL', counts: { total: 2, pass: 1, fail: 1, blocked: 0, not_run: 0 } },
    cases: [
      { case_id: 'C-1', status: 'PASS', required: true },
      { case_id: 'C-2', status: 'FAIL', required: false },
    ],
  },
  basis_valid: false,
  ...fields,
})

const reportAnswer = (fields: Record<string, unknown> = {}) => ({
  ok: true,
  kind: 'TSR',
  doc_id: REPORT,
  contract_version: 2,
  target_ts: SPEC,
  doc_review_status: 'pending_review',
  report: {
    run_id: 'run-1',
    overall: 'FAIL',
    summary: { counts: { total: 2, pass: 1, fail: 1, blocked: 0, not_run: 0 }, required_counts: { total: 2, pass: 1, fail: 1, blocked: 0, not_run: 0 } },
    cases: [
      {
        case_no: 'C-1',
        case_title: '올바른 비밀번호',
        case_status: 'PASS',
        expect: '대시보드',
        actual: '대시보드',
        required: true,
        execution_mode: 'automated',
        checked_by: 'runner',
        evidence: [
          { kind: 'url', value: 'https://ci.example/1', label: '로그' },
          { kind: 'url', value: 'https://ci.example/jobs/17', label: 'CI: job 17' },
        ],
      },
      { case_no: 'C-2', case_title: '잠긴 계정', case_status: 'FAIL', expect: '잠김 안내', actual: '오류', required: true, execution_mode: 'manual', defect_ref: 'B-7' },
    ],
    unmapped: [],
    conflicts: [],
  },
  gate: { applies: true, passed: false, overall: 'FAIL' },
  ...fields,
})

// The report files as the server writes them (test_spec_service render_report_markdown /
// render_pending_report_markdown, ko) from the records above.
const REPORT_HEAD = (run: string, overall: string, gate: string) =>
  `# 테스트 레포트 — 로그인 시나리오

> 문서 성격: 시험성적서 (TS ${SPEC} revision 1 의 Case ID별 결과)
> 결과 기록: ${run} · 기록 시각 2026-10-11T10:00:00
> 종합 판정(서버 계산, 필수 Case 기준): **${overall}** — ${gate}
`
const REPORT_TAIL = (run: string) => `
## TS에 없는 결과 (unmapped)

- 없음

## 중복 매핑 (mapping conflict)

- 없음

*이 시험성적서는 결과 기록 ${run} 로부터 FlowGate가 조립했다. 종합 판정은 문서 본문이 아니라 서버 기록으로 결정된다.*
`
const REPORT_FILE = `${REPORT_HEAD('run-1', 'FAIL', '시험 gate 미통과 · 다음 단계 진행 차단')}
## 요약

| 구분 | 전체 | PASS | FAIL | BLOCKED | NOT_RUN |
|---|---|---|---|---|---|
| 전체 | 2 | 1 | 1 | 0 | 0 |
| 필수 | 2 | 1 | 1 | 0 | 0 |
| 선택 | 0 | 0 | 0 | 0 | 0 |

## Case별 결과

| Case | 제목 | 필수 | 방식 | 기대 결과 | 실제 결과 | 판정 | 결함/참조 |
|---|---|---|---|---|---|---|---|
| C-1 | 올바른 비밀번호 | 필수 | automated | 대시보드 | 대시보드 | **PASS** | - |
| C-2 | 잠긴 계정 | 필수 | manual | 잠김 안내 | 오류 | **FAIL** | B-7 |

## Evidence 및 source identity

- C-1 (PASS) · runner
  - [url] 로그: https://ci.example/1
  - [url] CI: job 17: https://ci.example/jobs/17
  - source: git_revision=abc123
- C-2 (FAIL)
  - 없음
${REPORT_TAIL('run-1')}`
const REPORT_PASS_FILE = `${REPORT_HEAD('run-0', 'PASS', '시험 gate 통과')}
## 요약

| 구분 | 전체 | PASS | FAIL | BLOCKED | NOT_RUN |
|---|---|---|---|---|---|
| 전체 | 2 | 2 | 0 | 0 | 0 |
| 필수 | 2 | 2 | 0 | 0 | 0 |
| 선택 | 0 | 0 | 0 | 0 | 0 |

## Case별 결과

| Case | 제목 | 필수 | 방식 | 기대 결과 | 실제 결과 | 판정 | 결함/참조 |
|---|---|---|---|---|---|---|---|
| C-1 | 올바른 비밀번호 | 필수 | automated | 대시보드 | 대시보드 | **PASS** | - |
| C-2 | 잠긴 계정 | 필수 | manual | 잠김 안내 | 잠김 안내 | **PASS** | - |

## Evidence 및 source identity

- C-1 (PASS) · runner
  - [url] 로그: https://ci.example/1
  - source: git_revision=abc123
- C-2 (PASS)
  - 없음
${REPORT_TAIL('run-0')}`
const REPORT_PENDING_FILE = `# 테스트 레포트 — 로그인 시나리오

> 문서 성격: 시험성적서 (TS ${SPEC} revision 1 의 Case ID별 결과)
> 시험 run: run-2 · 접수 시각 2026-10-11T11:00:00
> 상태: **시험 대기/실행 중** — 결과가 기록되면 이 레포트가 갱신된다. 아직 판정이 없다.

## Case별 상태

| Case | 제목 | 필수 | 방식 | 기대 결과 | 상태 |
|---|---|---|---|---|---|
| C-1 | 올바른 비밀번호 | 필수 | automated | 대시보드 | 대기 (PENDING) |
| C-2 | 잠긴 계정 | 필수 | manual | 잠김 안내 | 대기 (PENDING) |

*이 레포트는 시험 run run-2 접수 시 FlowGate가 만들었다. 종합 판정은 결과가 기록된 뒤 서버 기록으로 결정된다.*
`

// rej_01M4MTMVSD5EVC8Z: a report the server wrote (render_report_markdown, run on a scratch copy of the server
// function) from values holding its cell escapes as text — `\|` (a literal pipe in a regex), `a|b`, labels with
// `\|` — and who checked / when with spaces and ' / ' (normalize_results takes any text); and its record.
const ESCAPED_FILE = `# 테스트 레포트 — 로그인 시나리오

> 문서 성격: 시험성적서 (TS ${SPEC} revision 1 의 Case ID별 결과)
> 결과 기록: run-1 · 기록 시각 2026-10-11T10:00:00
> 종합 판정(서버 계산, 필수 Case 기준): **FAIL** — 시험 gate 미통과 · 다음 단계 진행 차단

## 요약

| 구분 | 전체 | PASS | FAIL | BLOCKED | NOT_RUN |
|---|---|---|---|---|---|
| 전체 | 2 | 1 | 1 | 0 | 0 |
| 필수 | 2 | 1 | 1 | 0 | 0 |
| 선택 | 0 | 0 | 0 | 0 | 0 |

## Case별 결과

| Case | 제목 | 필수 | 방식 | 기대 결과 | 실제 결과 | 판정 | 결함/참조 |
|---|---|---|---|---|---|---|---|
| C-1 | Pattern \\\\| | 필수 | automated | match \\\\| | actual \\\\| and a\\|b | **PASS** | ref \\\\| |
| C-2 | 잠긴 계정 | 필수 | manual | 잠김 안내 | 오류 | **FAIL** | B-7 |

## Evidence 및 source identity

- C-1 (PASS) · QA / night shift / 2026-10-11 10:00:00 KST
  - [url] CI \\| job: 17: https://ci.example/a\\|b
  - [log] grep a\\|b: x \\\\| y
- C-2 (FAIL) · Test Engineer / 2026-10-11 10:05:00
  - 없음

## TS에 없는 결과 (unmapped)

- 없음

## 중복 매핑 (mapping conflict)

- 없음

*이 시험성적서는 결과 기록 run-1 로부터 FlowGate가 조립했다. 종합 판정은 문서 본문이 아니라 서버 기록으로 결정된다.*
`
const escapedRecord = {
  run_id: 'run-1',
  overall: 'FAIL',
  summary: { counts: { total: 2, pass: 1, fail: 1, blocked: 0, not_run: 0 }, required_counts: { total: 2, pass: 1, fail: 1, blocked: 0, not_run: 0 } },
  cases: [
    {
      case_no: 'C-1',
      case_title: 'Pattern \\|',
      case_status: 'PASS',
      expect: 'match \\|',
      actual: 'actual \\| and a|b',
      required: true,
      execution_mode: 'automated',
      defect_ref: 'ref \\|',
      checked_by: 'QA / night shift',
      checked_at: '2026-10-11 10:00:00 KST',
      evidence: [
        { kind: 'url', label: 'CI \\| job: 17', value: 'https://ci.example/a|b' },
        { kind: 'log', label: 'grep a\\|b', value: 'x \\| y' },
      ],
    },
    {
      case_no: 'C-2',
      case_title: '잠긴 계정',
      case_status: 'FAIL',
      expect: '잠김 안내',
      actual: '오류',
      required: true,
      execution_mode: 'manual',
      defect_ref: 'B-7',
      checked_by: 'Test Engineer',
      checked_at: '2026-10-11 10:05:00',
      evidence: [],
    },
  ],
  unmapped: [],
  conflicts: [],
}

/** The record of an earlier run that passed (the report of REPORT_PASS_FILE). */
const passReport = {
  run_id: 'run-0',
  overall: 'PASS',
  summary: { counts: { total: 2, pass: 2, fail: 0, blocked: 0, not_run: 0 }, required_counts: { total: 2, pass: 2, fail: 0, blocked: 0, not_run: 0 } },
  cases: [
    { case_no: 'C-1', case_title: '올바른 비밀번호', case_status: 'PASS', expect: '대시보드', actual: '대시보드', required: true, execution_mode: 'automated', checked_by: 'runner' },
    { case_no: 'C-2', case_title: '잠긴 계정', case_status: 'PASS', expect: '잠김 안내', actual: '잠김 안내', required: true, execution_mode: 'manual' },
  ],
  unmapped: [],
  conflicts: [],
}

/** The stored files (doc_id → body); a missing entry is a file not written yet. */
const files = new Map<string, string>()
/** What the special reads answer (doc_id → raw server answer), or an error to throw. */
const planAnswers = new Map<string, unknown>()
const testAnswers = new Map<string, unknown>()

const axiosError = (status: number, data: unknown = { detail: 'x' }) => ({ isAxiosError: true, response: { status, data } })

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentWindows()
  files.clear()
  planAnswers.clear()
  testAnswers.clear()
  files.set(PLAN, JSON.stringify(planBody(), null, 2))
  files.set(SPEC, `${FRONT}\n# 로그인 시나리오\n`)
  files.set(REPORT, REPORT_FILE)
  files.set(LEGACY, `${FRONT}\n# 옛 형식\n\n실행 명령 목록\n`)
  files.set(ORDER, '# 지시\n')
  planAnswers.set(PLAN, planAnswer(planBody()))
  testAnswers.set(SPEC, specAnswer())
  testAnswers.set(REPORT, reportAnswer())
  testAnswers.set(LEGACY, { ok: true, kind: 'TS', doc_id: LEGACY, contract_version: 1 })
  testAnswers.set(EMPTY_SPEC, { ok: true, kind: 'TS', doc_id: EMPTY_SPEC, contract_version: null, can_start_spec: true })
  for (const mock of [recordRead, contentRead, contentSave, planRead, testRead]) mock.mockReset()
  recordRead.mockImplementation(async (docId: string) => {
    if (!RECORDS[docId]) throw axiosError(404)
    return toDocumentRecord(RECORDS[docId])
  })
  contentRead.mockImplementation(async (docId: string) =>
    files.has(docId) ? { docId, content: files.get(docId)!, missing: false } : { docId, content: '', missing: true },
  )
  contentSave.mockImplementation(async (docId: string, content: string) => {
    files.set(docId, content)
  })
  planRead.mockImplementation(async (docId: string) => {
    const answer = planAnswers.get(docId)
    if (!answer || (answer as { isAxiosError?: boolean }).isAxiosError) throw answer ?? axiosError(404)
    return toWorkPlanDocument(answer)
  })
  testRead.mockImplementation(async (docId: string) => {
    const answer = testAnswers.get(docId)
    if (!answer || (answer as { isAxiosError?: boolean }).isAxiosError) throw answer ?? axiosError(404)
    return toTestSpecDocument(answer)
  })
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  vi.useRealTimers()
  document.body.querySelectorAll('.ui-modal-overlay').forEach((node) => node.remove())
})

// --- DOM helpers (the windows are teleported to <body>) ----------------------------------------------

function panelOf(docId: string): HTMLElement {
  const panel = [...document.body.querySelectorAll<HTMLElement>('.ui-modeless-panel')].find((node) =>
    node.querySelector(`[data-test="document-window"][data-doc="${docId}"]`),
  )
  if (!panel) throw new Error(`no window for ${docId}`)
  return panel
}
const q = (root: ParentNode, id: string) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const qa = (root: ParentNode, id: string) => [...root.querySelectorAll<HTMLElement>(`[data-test="${id}"]`)]
const text = (node: Element | null) => (node?.textContent ?? '').replace(/\s+/g, ' ').trim()
async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}
async function type(node: HTMLElement | null, value: string) {
  const area = node as HTMLTextAreaElement
  area.value = value
  area.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}
async function open(docId: string, mode: 'view' | 'edit' = 'view') {
  openDocumentWindow(docId, mode)
  await flushPromises()
  return panelOf(docId)
}

/** Document type codes the operator never sees (0053 CH: WP·TS·TSC·TSR·D·L·P·DB, and the rest of the family). */
const TYPE_CODE = /(^|[^A-Za-z0-9])(WP|TSC|TSR|TS|TR2|TR|NR|DS|DB|T2|D|L|P|N|T|R)(?=$|[^A-Za-z0-9])/
/** What the panel shows as text plus every tooltip in it. */
function shown(panel: HTMLElement): string {
  const titles = [...panel.querySelectorAll<HTMLElement>('[title]')].map((node) => node.getAttribute('title'))
  const labels = [...panel.querySelectorAll<HTMLElement>('[aria-label]')].map((node) => node.getAttribute('aria-label'))
  return [panel.textContent ?? '', ...titles, ...labels].join('\n')
}

describe('work plan in the document window (flowgate.v02.0053 T#4)', () => {
  it('shows the plan table — totals, quantity boxes, one row per Step by card — not the MD Viewer', async () => {
    const panel = await open(PLAN)
    expect(q(panel, 'document-window-body')!.getAttribute('data-view')).toBe('work_plan')
    expect(q(panel, 'md-viewer')).toBeNull()
    expect(contentRead).not.toHaveBeenCalled()
    expect(planRead).toHaveBeenCalledWith(PLAN)

    const parts = (node: HTMLElement | null, ...classes: string[]) => classes.map((name) => text(node!.querySelector(`.${name}`)))
    expect(['design', 'work', 'steps'].map((key) => parts(q(panel, `work-plan-total-${key}`), 'wp-tile-label', 'wp-tile-value'))).toEqual([
      ['설계', '2장'],
      ['작업', '1세트'],
      ['단계', '4개'],
    ])
    expect(qa(panel, 'work-plan-quantity').map((node) => parts(node, 'wp-quantity-name', 'wp-quantity-value', 'wp-quantity-unit'))).toEqual([
      ['기본 설계', '2', '장'],
      ['작업', '1', '세트'],
      ['테스트', '0', '세트'],
    ])
    expect(qa(panel, 'work-plan-quantity').map((node) => node.classList.contains('wp-quantity--zero'))).toEqual([false, false, true])

    const cards = qa(panel, 'work-plan-card')
    expect(cards.map((card) => [card.getAttribute('data-card'), card.getAttribute('data-started')])).toEqual([
      ['D#1', 'false'],
      ['D#2', 'false'],
      ['T#1', 'true'],
    ])
    expect(qa(panel, 'work-plan-step-type').map(text)).toEqual(['기본 설계 1/2장', '기본 설계 2/2장', '작업 지시서 1세트', '작업 레포트 1세트'])
    expect(qa(panel, 'work-plan-step-provider').map(text)).toEqual(['Claude', '—', 'Codex', 'Codex'])
    expect(qa(panel, 'work-plan-step-provider')[1].getAttribute('title')).toBe('담당 미지정')
    expect(q(panel, 'work-plan-step-review')!.getAttribute('title')).toBe('검수 2회')
    expect(text(q(panel, 'work-plan-step-note'))).toBe('화면 설계')
    expect(qa(panel, 'work-plan-step-instruction').map((node) => node.getAttribute('title'))).toEqual(['사전 지시 있음'])
    expect(q(cards[2], 'work-plan-card-started')!.getAttribute('title')).toBe('시작됨')
    // None of the editor's controls came along: the view has no input, select or button of its own.
    expect(q(panel, 'work-plan-sheet')!.querySelectorAll('input, select, textarea, button')).toHaveLength(0)
  })

  it('shows no type code, key or explanation paragraph — text and tooltips alike', async () => {
    // The check itself catches a code next to Korean text, alone or in a key.
    for (const sample of ['작업계획 (WP)', 'TSR', '레포트·TR', 'T#1', 'D 설계']) expect(sample).toMatch(TYPE_CODE)
    for (const sample of ['작업계획', 'Claude', 'C-1', 'Markdown']) expect(sample).not.toMatch(TYPE_CODE)
    const panel = await open(PLAN)
    expect(shown(panel)).not.toMatch(TYPE_CODE)
    expect(q(panel, 'work-plan-sheet')!.querySelectorAll('p')).toHaveLength(0)
  })

  it('✎ edits the stored plan text in the same window; 저장 writes it and the table reads the plan again', async () => {
    const panel = await open(PLAN)
    await click(q(panel, 'document-window-edit'))
    const area = q(panel, 'document-window-textarea') as HTMLTextAreaElement
    expect(area.value).toBe(files.get(PLAN))
    expect(q(panel, 'work-plan-sheet')).toBeNull()

    const edited = JSON.stringify(planBody('결제 화면'), null, 2)
    planAnswers.set(PLAN, planAnswer(planBody('결제 화면')))
    await type(area, edited)
    await click(q(panel, 'document-window-save'))

    expect(contentSave).toHaveBeenCalledTimes(1)
    expect(contentSave).toHaveBeenCalledWith(PLAN, edited)
    expect(q(panel, 'document-window-editor')).toBeNull()
    expect(planRead).toHaveBeenCalledTimes(2)
    expect(text(q(panel, 'work-plan-step-note'))).toBe('결제 화면')
  })

  it('취소 drops the edit and the table comes back unchanged', async () => {
    const panel = await open(PLAN)
    await click(q(panel, 'document-window-edit'))
    await click(q(panel, 'document-window-cancel'))
    expect(contentSave).not.toHaveBeenCalled()
    expect(text(q(panel, 'work-plan-step-note'))).toBe('화면 설계')
  })

  it('a plan the server cannot read as a table: one short line and its raw text, still editable with ✎', async () => {
    files.set(PLAN, '{ "broken": ')
    planAnswers.set(PLAN, axiosError(409, { code: 'wp_unreadable', reason: 'not_json', revision_no: 3 }))
    const panel = await open(PLAN)
    expect(text(q(panel, 'work-plan-unreadable'))).toContain('표로 읽을 수 없습니다')
    expect(q(panel, 'work-plan-raw')!.textContent).toBe('{ "broken": ')
    expect((q(panel, 'document-window-edit') as HTMLButtonElement).disabled).toBe(false)
  })

  it('any other failed read hands the document to the MD Viewer', async () => {
    planAnswers.set(PLAN, axiosError(422))
    files.set(PLAN, '# 계획 원문\n')
    const panel = await open(PLAN)
    expect(q(panel, 'work-plan-body')!.getAttribute('data-state')).toBe('fallback')
    expect(q(panel, 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('계획 원문')
  })
})

describe('test scenario / test report in the document window (flowgate.v02.0053 T#4)', () => {
  it('a specification scenario shows its Cases with result, category, mode and required marks', async () => {
    const panel = await open(SPEC)
    expect(q(panel, 'document-window-body')!.getAttribute('data-view')).toBe('test')
    expect(q(panel, 'test-spec-panel')).not.toBeNull()
    expect(q(panel, 'md-viewer')).toBeNull()

    expect(text(q(panel, 'test-spec-count'))).toBe('케이스 2개')
    expect(text(q(panel, 'test-spec-summary'))).toContain('필수 1개')
    const overall = q(panel, 'test-spec-overall')!
    expect(overall.getAttribute('data-tone')).toBe('danger')
    expect(overall.getAttribute('title')).toBe('종합 결과: 실패')
    // A result the live source moved past is a mark, not a band of text.
    const stale = [...panel.querySelectorAll('[data-key="test.stale"]')]
    expect(stale).toHaveLength(1)
    expect(stale[0].getAttribute('title')).toBe('지금 소스와 다른 결과입니다')
    expect(stale[0].textContent).toBe('!')
    expect(text(q(panel, 'test-spec-intro'))).toBe('로그인 화면을 확인한다.')

    const cases = qa(panel, 'test-spec-case')
    expect(cases.map((node) => node.getAttribute('data-case-id'))).toEqual(['C-1', 'C-2'])
    expect(qa(panel, 'test-spec-case-verdict').map((node) => [node.textContent, node.getAttribute('title')])).toEqual([
      ['✓', '통과'],
      ['✕', '실패'],
    ])
    const marks = (node: HTMLElement) => qa(node, 'test-mark').map((mark) => mark.getAttribute('title'))
    expect(marks(cases[0])).toEqual(['자동 실행', '필수'])
    expect(marks(cases[1])).toEqual(['사람이 확인', '선택'])
    expect(text(cases[0].querySelector('.ts-chip'))).toBe('정상')
    // The written fields, blank ones left out, in the v0.1 order.
    expect([...cases[0].querySelectorAll('dt')].map((node) => node.textContent)).toEqual(['절차', '기대 결과'])
    expect([...cases[0].querySelectorAll('dd')].map((node) => node.textContent)).toEqual(['1. 로그인', '대시보드'])
    expect(shown(panel)).not.toMatch(TYPE_CODE)
  })

  it('a live run marks its Cases pending and shows its progress', async () => {
    testAnswers.set(SPEC, specAnswer({ active_run: { run_id: 'run-2', status: 'running', case_total: 2 }, pending_case_ids: ['C-2'], progress: { automated_total: 2, automated_completed: 1 } }))
    const panel = await open(SPEC)
    expect(qa(panel, 'test-spec-case-pending').map((node) => node.getAttribute('title'))).toEqual(['결과 기다리는 중'])
    const running = panel.querySelector('[data-key="test.running"]')!
    expect(running.textContent).toBe('⋯1/2')
    expect(running.getAttribute('title')).toBe('실행 중 1/2')
    // The previous verdict is not shown as this run's.
    expect(q(panel, 'test-spec-overall')).toBeNull()
  })

  it('a test report shows the overall verdict, the gate, the tiles and one row per Case', async () => {
    const panel = await open(REPORT)
    expect(q(panel, 'test-report-panel')).not.toBeNull()
    const overall = q(panel, 'test-report-overall')!
    expect(overall.getAttribute('data-tone')).toBe('danger')
    expect(text(overall)).toBe('✕실패')
    expect(q(panel, 'test-report-gate')!.getAttribute('title')).toBe('관문 막힘')
    expect(['total', 'pass', 'fail', 'blocked', 'not_run'].map((key) => text(q(panel, `test-report-tile-${key}`)))).toEqual(['2Σ', '1✓', '1✕', '0!', '0–'])
    expect(q(panel, 'test-report-tile-fail')!.getAttribute('title')).toBe('실패')
    expect(text(q(panel, 'test-report-scope'))).toBe('필수 1/2 통과')

    const rows = qa(panel, 'test-report-row')
    expect(rows.map((row) => [row.getAttribute('data-case-id'), row.classList.contains('tsr-row--bad')])).toEqual([
      ['C-1', false],
      ['C-2', true],
    ])
    expect(qa(panel, 'test-report-row-verdict').map((node) => node.getAttribute('title'))).toEqual(['통과', '실패'])
    // Evidence links: the label is kept whole even when it holds ': ', the address is the item's own.
    const links = [...rows[0].querySelectorAll('a')]
    expect(links.map((link) => [link.textContent, link.getAttribute('href'), link.getAttribute('rel')])).toEqual([
      ['로그', 'https://ci.example/1', 'noopener noreferrer'],
      ['CI: job 17', 'https://ci.example/jobs/17', 'noopener noreferrer'],
    ])
    expect(text(rows[1])).toContain('⚑ B-7')
    // No run id, Basis hash or rerun control in the view.
    expect(panel.textContent).not.toContain('run-1')
    expect(q(panel, 'test-report-panel')!.querySelectorAll('button')).toHaveLength(0)
    expect(shown(panel)).not.toMatch(TYPE_CODE)
  })

  it('✎ edits the stored text of a test document; 저장 writes it with the frontmatter and the view reads it again', async () => {
    const panel = await open(SPEC)
    await click(q(panel, 'document-window-edit'))
    const area = q(panel, 'document-window-textarea') as HTMLTextAreaElement
    expect(area.value).toBe('# 로그인 시나리오\n')
    testAnswers.set(SPEC, specAnswer({ intro: '바뀐 소개' }))
    await type(area, '# 로그인 시나리오\n\n바뀐 소개\n')
    await click(q(panel, 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(SPEC, `${FRONT}\n# 로그인 시나리오\n\n바뀐 소개\n`)
    expect(testRead).toHaveBeenCalledTimes(2)
    expect(text(q(panel, 'test-spec-intro'))).toBe('바뀐 소개')
  })

  it('✎ on a test report: 저장 writes the report text, and the view shows it back and after the window is opened again', async () => {
    const panel = await open(REPORT)
    await click(q(panel, 'document-window-edit'))
    const area = q(panel, 'document-window-textarea') as HTMLTextAreaElement
    expect(area.value).toBe(REPORT_FILE)
    const edited = REPORT_FILE.replace('| 잠김 안내 | 오류 | **FAIL** | B-7 |', '| 잠김 안내 | 잠김 안내 없이 오류 화면 | **FAIL** | B-7 |').replace(
      '- C-2 (FAIL)\n  - 없음',
      '- C-2 (FAIL)\n  - [log] 재현: 3회 중 3회',
    )
    expect(edited).not.toBe(REPORT_FILE)
    await type(area, edited)
    await click(q(panel, 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(REPORT, edited)
    expect(files.get(REPORT)).toBe(edited)

    // The server answers its record as before (it does not read the file); the view shows the saved text.
    const check = (root: HTMLElement) => {
      const rows = qa(root, 'test-report-row')
      expect(rows.map((row) => row.getAttribute('data-case-id'))).toEqual(['C-1', 'C-2'])
      const cells = [...rows[1].querySelectorAll('td')].map(text)
      expect(cells.slice(1)).toEqual(['잠김 안내', '잠김 안내 없이 오류 화면', '재현: 3회 중 3회'])
      expect(text(rows[1])).toContain('⚑ B-7')
      // The verdict and the gate are the record's.
      expect(text(q(root, 'test-report-overall'))).toBe('✕실패')
      expect(q(root, 'test-report-gate')!.getAttribute('title')).toBe('관문 막힘')
      expect(q(root, 'test-report-source')).toBeNull()
    }
    expect(q(panel, 'document-window-editor')).toBeNull()
    check(panel)

    closeDocumentWindow(REPORT)
    await flushPromises()
    expect(document.body.querySelector(`[data-test="document-window"][data-doc="${REPORT}"]`)).toBeNull()
    check(await open(REPORT))
    expect(testRead).toHaveBeenCalledTimes(3)
    expect(testRead.mock.calls.every(([docId]) => docId === REPORT)).toBe(true)
  })

  it("a verdict word changed in the report text is not taken: each row's verdict, the overall verdict, the gate and the counts stay the record's (Q #419)", async () => {
    const edited = REPORT_FILE.replace('| 오류 | **FAIL** | B-7 |', '| 오류 | **PASS** | B-7 |')
    expect(edited).not.toBe(REPORT_FILE)
    files.set(REPORT, edited)
    const panel = await open(REPORT)
    expect(qa(panel, 'test-report-row-verdict').map((node) => node.getAttribute('title'))).toEqual(['통과', '실패'])
    // The required Case the record failed stays marked.
    expect(qa(panel, 'test-report-row').map((row) => row.classList.contains('tsr-row--bad'))).toEqual([false, true])
    expect(text(q(panel, 'test-report-overall'))).toBe('✕실패')
    expect(q(panel, 'test-report-gate')!.getAttribute('title')).toBe('관문 막힘')
    expect(text(q(panel, 'test-report-tile-fail'))).toBe('1✕')
  })

  it("a stale report: each row's verdict is its own run's record, the summary the valid result on the live source", async () => {
    testAnswers.set(
      REPORT,
      reportAnswer({
        basis_valid: false,
        effective_result: {
          summary: { overall: 'NOT_RUN', counts: { total: 2, pass: 0, fail: 0, blocked: 0, not_run: 2 } },
          cases: [
            { case_id: 'C-1', status: 'NOT_RUN', required: true },
            { case_id: 'C-2', status: 'NOT_RUN', required: true },
          ],
        },
      }),
    )
    const panel = await open(REPORT)
    expect(qa(panel, 'test-report-row-verdict').map((node) => node.getAttribute('title'))).toEqual(['통과', '실패'])
    expect(text(q(panel, 'test-report-tile-not_run'))).toBe('2–')
  })

  it("a live run: a Case it reported shows the run's verdict, a pending Case waits, whatever the text says", async () => {
    files.set(REPORT, REPORT_PENDING_FILE.replace('| 대시보드 | 대기 (PENDING) |', '| 대시보드 | PASS (이번 run 결과) |'))
    testAnswers.set(
      REPORT,
      reportAnswer({
        report: { run_id: 'run-2', status: 'running', overall: null, cases: [{ case_no: 'C-1', case_status: 'FAIL', required: true }] },
        active_run: { run_id: 'run-2', status: 'running', phase: 'executing', case_total: 2, selected_case_ids: ['C-1', 'C-2'] },
        pending_case_ids: ['C-2'],
        effective_result: { summary: { overall: 'PASS', counts: passReport.summary.counts }, cases: passReport.cases.map((item) => ({ case_id: item.case_no, status: 'PASS', required: true })) },
        gate: { applies: true, passed: false, overall: 'NOT_RUN', execution_active: true },
      }),
    )
    const panel = await open(REPORT)
    expect(q(panel, 'test-report-overall')).toBeNull()
    expect(qa(panel, 'test-report-row-verdict').map((node) => [node.closest('tr')!.getAttribute('data-case-id'), node.getAttribute('title')])).toEqual([['C-1', '실패']])
    expect(qa(panel, 'test-report-row-pending').map((node) => node.closest('tr')!.getAttribute('data-case-id'))).toEqual(['C-2'])
  })

  it("a report text out of the report's shape is shown as written, under the record's verdict and gate", async () => {
    const panel = await open(REPORT)
    await click(q(panel, 'document-window-edit'))
    const edited = `${REPORT_FILE}\n검수 메모: 잠긴 계정은 수동으로 다시 확인했다.\n`
    await type(q(panel, 'document-window-textarea'), edited)
    await click(q(panel, 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(REPORT, edited)

    expect(q(panel, 'test-report-panel')).not.toBeNull()
    expect(text(q(panel, 'test-report-overall'))).toBe('✕실패')
    expect(q(panel, 'test-report-gate')!.getAttribute('title')).toBe('관문 막힘')
    expect(q(panel, 'test-report-table')).toBeNull()
    expect(text(q(q(panel, 'test-report-source')!, 'md-viewer-content'))).toContain('검수 메모: 잠긴 계정은 수동으로 다시 확인했다.')
  })

  // The review's cases (rej_01M4MP5ENS3VDDM1): notes in the server's own markup and cells the view could not
  // say as written. Each is saved with ✎, shown as written when the view comes back and after the window is
  // opened again; no Case row stands for it, and the verdict and the gate stay the record's.
  const NOTE = 'Review note: reproduce on staging before release'
  it.each([
    ['a quote line before the summary', REPORT_FILE.replace('\n\n## 요약', `\n> ${NOTE}\n\n## 요약`), NOTE],
    ['a heading before the summary', REPORT_FILE.replace('\n\n## 요약', `\n\n# ${NOTE}\n\n## 요약`), NOTE],
    ['an italic line by the footer', REPORT_FILE.replace(/\n$/, `\n*${NOTE}*\n`), NOTE],
    ['a verdict of its own', REPORT_FILE.replace('| 오류 | **FAIL** |', '| 오류 | **REVIEW_REQUIRED** |'), 'REVIEW_REQUIRED'],
    ['a required word of its own', REPORT_FILE.replace('| 잠긴 계정 | 필수 |', '| 잠긴 계정 | required after review |'), 'required after review'],
    ['a mode of its own', REPORT_FILE.replace('| 필수 | manual |', '| 필수 | manual (review) |'), 'manual (review)'],
  ])('a report text with %s: ✎ 저장 → the view and the window opened again show it as written', async (_case, edited, written) => {
    expect(edited).not.toBe(REPORT_FILE)
    const panel = await open(REPORT)
    await click(q(panel, 'document-window-edit'))
    await type(q(panel, 'document-window-textarea'), edited)
    await click(q(panel, 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(REPORT, edited)
    expect(files.get(REPORT)).toBe(edited)

    const check = (root: HTMLElement) => {
      expect(q(root, 'document-window-editor')).toBeNull()
      expect(text(q(root, 'test-report-overall'))).toBe('✕실패')
      expect(q(root, 'test-report-gate')!.getAttribute('title')).toBe('관문 막힘')
      // No row reads the text into another meaning (e.g. "not run" or "optional").
      expect(q(root, 'test-report-table')).toBeNull()
      expect(qa(root, 'test-report-row')).toHaveLength(0)
      const source = text(q(q(root, 'test-report-source')!, 'md-viewer-content'))
      expect(source).toContain(written)
      expect(source).toContain('잠김 안내')
    }
    check(panel)
    closeDocumentWindow(REPORT)
    await flushPromises()
    check(await open(REPORT))
  })

  it('a url evidence the view cannot tell an address in is its text as written, never a link', async () => {
    const panel = await open(REPORT)
    await click(q(panel, 'document-window-edit'))
    const edited = REPORT_FILE.replace('  - [url] 로그: https://ci.example/1', '  - [url] 로그: 빌드 화면 참고')
    await type(q(panel, 'document-window-textarea'), edited)
    await click(q(panel, 'document-window-save'))
    const row = qa(panel, 'test-report-row')[0]
    expect([...row.querySelectorAll('a')].map((link) => link.getAttribute('href'))).toEqual(['https://ci.example/jobs/17'])
    expect(text(row)).toContain('로그: 빌드 화면 참고')
  })

  it('a report holding `\\|` and checkers with spaces: its Case table as the server wrote it; ✎ 저장 → the view and the window opened again keep it', async () => {
    files.set(REPORT, ESCAPED_FILE)
    testAnswers.set(REPORT, reportAnswer({ report: escapedRecord }))
    const check = (root: HTMLElement, otherActual: string) => {
      // The server's own report is its Case table (not the text as written), checker and time lines included.
      expect(q(root, 'test-report-source')).toBeNull()
      const rows = qa(root, 'test-report-row')
      expect(rows.map((row) => row.getAttribute('data-case-id'))).toEqual(['C-1', 'C-2'])
      // Each `\|` the values hold stays: the cell's escapes are undone once.
      expect(text(rows[0].querySelector('.tsr-case-title'))).toBe('Pattern \\|')
      const cells = [...rows[0].querySelectorAll('td')].map(text)
      expect(cells.slice(1, 3)).toEqual(['match \\|', 'actual \\| and a|b'])
      expect(text(rows[0])).toContain('⚑ ref \\|')
      const links = [...rows[0].querySelectorAll('a')]
      expect(links.map((link) => [link.textContent, link.getAttribute('href')])).toEqual([['CI \\| job: 17', 'https://ci.example/a|b']])
      expect(cells[3]).toContain('grep a\\|b: x \\| y')
      expect(text(rows[1].querySelectorAll('td')[2])).toBe(otherActual)
      // No checker, time or run id on screen; the verdict and the gate are the record's.
      expect(root.textContent).not.toContain('Test Engineer')
      expect(root.textContent).not.toContain('night shift')
      expect(text(q(root, 'test-report-overall'))).toBe('✕실패')
      expect(q(root, 'test-report-gate')!.getAttribute('title')).toBe('관문 막힘')
      expect(shown(root)).not.toMatch(TYPE_CODE)
    }
    const panel = await open(REPORT)
    check(panel, '오류')

    await click(q(panel, 'document-window-edit'))
    const area = q(panel, 'document-window-textarea') as HTMLTextAreaElement
    expect(area.value).toBe(ESCAPED_FILE)
    // An edit written in the server's escapes: `\\|` is `\|`.
    const edited = ESCAPED_FILE.replace('| 잠김 안내 | 오류 |', '| 잠김 안내 | 오류 \\\\| 재현 |')
    expect(edited).not.toBe(ESCAPED_FILE)
    await type(area, edited)
    await click(q(panel, 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(REPORT, edited)
    expect(files.get(REPORT)).toBe(edited)
    expect(q(panel, 'document-window-editor')).toBeNull()
    check(panel, '오류 \\| 재현')

    closeDocumentWindow(REPORT)
    await flushPromises()
    check(await open(REPORT), '오류 \\| 재현')
  })

  it('a note put in the line of who checked a Case is held to the record: ✎ 저장 → the text is shown as written, then and when opened again', async () => {
    files.set(REPORT, ESCAPED_FILE)
    testAnswers.set(REPORT, reportAnswer({ report: escapedRecord }))
    const panel = await open(REPORT)
    await click(q(panel, 'document-window-edit'))
    const edited = ESCAPED_FILE.replace('· Test Engineer / 2026-10-11 10:05:00', '· Test Engineer / 2026-10-11 10:05:00 — 재확인 필요')
    expect(edited).not.toBe(ESCAPED_FILE)
    await type(q(panel, 'document-window-textarea'), edited)
    await click(q(panel, 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(REPORT, edited)

    const check = (root: HTMLElement) => {
      expect(text(q(root, 'test-report-overall'))).toBe('✕실패')
      expect(q(root, 'test-report-table')).toBeNull()
      expect(text(q(q(root, 'test-report-source')!, 'md-viewer-content'))).toContain('재확인 필요')
    }
    check(panel)
    closeDocumentWindow(REPORT)
    await flushPromises()
    check(await open(REPORT))
  })

  it('a rerun of a passed report: progress leads while it is live, the verdict comes back when it is done', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
    const period = async () => {
      await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS + DOCUMENT_WINDOW_REFRESH_COALESCE_MS + 200)
      await flushPromises()
    }
    const passed = { summary: { overall: 'PASS', counts: passReport.summary.counts, required_counts: passReport.summary.required_counts }, cases: [] }
    files.set(REPORT, REPORT_PASS_FILE)
    testAnswers.set(REPORT, reportAnswer({ report: passReport, effective_result: passed, gate: { applies: true, passed: true, overall: 'PASS' } }))
    const panel = await open(REPORT)
    expect(text(q(panel, 'test-report-overall'))).toBe('✓통과')
    expect(q(panel, 'test-report-gate')!.getAttribute('title')).toBe('관문 통과')
    expect(qa(panel, 'test-report-row-verdict').map((node) => node.getAttribute('title'))).toEqual(['통과', '통과'])

    // The rerun is admitted: the server opens the pending report and links the run; the previous valid result stays.
    files.set(REPORT, REPORT_PENDING_FILE)
    testAnswers.set(
      REPORT,
      reportAnswer({
        report: { run_id: 'run-2', status: 'running', overall: null, cases: [] },
        active_run: { run_id: 'run-2', status: 'running', phase: 'executing', case_total: 2, selected_case_ids: ['C-1', 'C-2'] },
        pending_case_ids: ['C-1', 'C-2'],
        progress: { automated_total: 2, automated_completed: 0 },
        effective_result: passed,
        gate: { applies: true, passed: false, overall: 'NOT_RUN', execution_active: true },
      }),
    )
    await period()
    expect(q(panel, 'test-report-overall')).toBeNull()
    expect(q(panel, 'test-report-gate')).toBeNull()
    const summary = qa(q(panel, 'test-report-summary')!, 'test-mark')
    expect(summary[0].getAttribute('data-key')).toBe('test.running')
    expect(summary[0].textContent).toBe('⋯0/2')
    expect(shown(q(panel, 'test-report-summary')!)).not.toContain('통과')
    expect(qa(panel, 'test-report-row-pending')).toHaveLength(2)
    expect(qa(panel, 'test-report-row-verdict')).toHaveLength(0)

    // Done: the new record, its report and its gate.
    files.set(REPORT, REPORT_FILE)
    testAnswers.set(REPORT, reportAnswer())
    await period()
    expect(text(q(panel, 'test-report-overall'))).toBe('✕실패')
    expect(q(panel, 'test-report-gate')!.getAttribute('title')).toBe('관문 막힘')
    expect(panel.querySelector('[data-key="test.running"]')).toBeNull()
    expect(qa(panel, 'test-report-row-pending')).toHaveLength(0)
    expect(qa(panel, 'test-report-row-verdict').map((node) => node.getAttribute('title'))).toEqual(['통과', '실패'])
  })

  it('a legacy test document keeps the Markdown body', async () => {
    const panel = await open(LEGACY)
    expect(q(panel, 'test-document-body')!.getAttribute('data-state')).toBe('fallback')
    expect(q(panel, 'test-spec-panel')).toBeNull()
    expect(q(panel, 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('옛 형식')
  })

  it('a failed read keeps the Markdown body too (fail open, as v0.1)', async () => {
    testAnswers.set(SPEC, axiosError(500))
    const panel = await open(SPEC)
    expect(q(panel, 'test-document-body')!.getAttribute('data-state')).toBe('fallback')
    expect(q(panel, 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('로그인 시나리오')
  })

  it('a scenario before it is written opens empty, and becomes its scenario view once the server has one', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
    const panel = await open(EMPTY_SPEC)
    expect(q(panel, 'test-document-body')!.getAttribute('data-state')).toBe('fallback')
    expect(text(q(panel, 'md-viewer'))).toBe('')

    files.set(EMPTY_SPEC, `${FRONT}\n# 시나리오\n`)
    testAnswers.set(EMPTY_SPEC, { ...specAnswer(), doc_id: EMPTY_SPEC })
    await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS + DOCUMENT_WINDOW_REFRESH_COALESCE_MS + 200)
    await flushPromises()

    expect(q(panel, 'test-document-body')!.getAttribute('data-state')).toBe('structured')
    expect(qa(panel, 'test-spec-case')).toHaveLength(2)
  })

  it('a document of another type stays on the MD Viewer', async () => {
    const panel = await open(ORDER)
    expect(q(panel, 'document-window-body')!.getAttribute('data-view')).toBe('markdown')
    expect(planRead).not.toHaveBeenCalled()
    expect(testRead).not.toHaveBeenCalled()
  })
})
