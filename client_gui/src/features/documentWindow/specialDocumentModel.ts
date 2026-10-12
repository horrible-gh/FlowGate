// flowgate.v02.0053 T#4 — what the special document views decide without a screen or a server call: the
// name a document type is shown by (never its code), the work plan's cards and Step labels (the v0.1
// WorkPlanEditor's table, read only), the marks of a test verdict and execution mode (the v0.1
// TestSpecPanel / TestReportPanel badges, as glyph + color with the word in the tooltip) and the Case rows
// of a test report as its saved text says them.
import type { TestSpecDocument, TestSpecEvidence, TestSpecRecordRow, TestSpecResultRow, WorkPlanDocument, WorkPlanQuantity } from '@/features/document'
import { splitDocumentSource } from './documentWindowModel'

// --- Document type names ---------------------------------------------------------------------------

/** The types a work plan Step may carry (server WORK_PLAN_STEP_TYPES); each has a name under `types`. */
const NAMED_TYPES = new Set(['DS', 'D', 'P', 'L', 'DB', 'N', 'NR', 'T', 'TR', 'T2', 'TR2', 'TS', 'TSR'])

/** Message key (under `gui.document_window.`) of a document type's name; an unknown type is just a document. */
export function documentTypeNameKey(type: string | null): string {
  const code = (type ?? '').toUpperCase()
  return NAMED_TYPES.has(code) ? `types.${code}` : 'types.other'
}

/** Instruction types counted in sets: their quantity box is named after the set (조사 · 작업 · 반영 · 테스트). */
const SET_NAMES = new Set(['N', 'T', 'T2', 'TS'])

/** Message key of a quantity box's name (the v0.1 qtyCardName: a set by its set name, a sheet by its type). */
export function workPlanQuantityNameKey(box: Pick<WorkPlanQuantity, 'type' | 'unit'>): string {
  const code = box.type.toUpperCase()
  return box.unit === 'set' && SET_NAMES.has(code) ? `work_plan.sets.${code}` : documentTypeNameKey(code)
}

// --- The work plan table ------------------------------------------------------------------------------

/** The Step's place among its type: `n/total장`, `n장` or `n세트` (the v0.1 stepDocParts). */
export type WorkPlanStepPlace =
  | { readonly key: 'work_plan.sheet_of'; readonly n: number; readonly total: number }
  | { readonly key: 'work_plan.sheet' | 'work_plan.set'; readonly n: number }

export interface WorkPlanSheetRow {
  readonly key: string
  /** 1-based position in the whole plan (the v0.1 rowNo). */
  readonly no: number
  readonly type: string | null
  readonly place: WorkPlanStepPlace | null
  /** The result half of a set (drawn under its instruction). */
  readonly result: boolean
  readonly providerName: string | null
  /** Server-assembled: nobody is assigned, no note. */
  readonly locked: boolean
  /** 0 none, -1 until approved, n times. */
  readonly reviewCount: number
  readonly note: string | null
  readonly hasInstruction: boolean
}

export interface WorkPlanSheetCard {
  readonly cardId: string
  /** The workflow already started the card. */
  readonly started: boolean
  readonly rows: readonly WorkPlanSheetRow[]
}

function stepPlace(doc: WorkPlanDocument, type: string | null, ordinal: number | null, pairKey: string | null): WorkPlanStepPlace | null {
  if (ordinal === null || type === null) return null
  const own = doc.quantities.find((box) => box.type === type)
  const pairType = pairKey ? pairKey.split('#')[0] : null
  const box = own ?? doc.quantities.find((item) => item.type === pairType)
  if (!box) return null
  if (box.unit === 'sheet' && own) return box.count <= 1 ? { key: 'work_plan.sheet', n: ordinal } : { key: 'work_plan.sheet_of', n: ordinal, total: box.count }
  return { key: 'work_plan.set', n: ordinal }
}

/** The plan's cards in plan order: a single Step, or an instruction Step with its result Step. */
export function workPlanSheetCards(doc: WorkPlanDocument | null): WorkPlanSheetCard[] {
  if (!doc) return []
  const cards = new Map<string, { started: boolean; rows: WorkPlanSheetRow[] }>()
  doc.view.steps.forEach((step, index) => {
    const detail = doc.details[step.key]
    const row: WorkPlanSheetRow = {
      key: step.key,
      no: index + 1,
      type: step.type,
      place: stepPlace(doc, step.type, step.ordinal, step.pairKey),
      result: step.pairRole === 'result',
      providerName: step.providerName,
      locked: step.locked,
      reviewCount: step.locked ? 0 : detail?.reviewCount ?? step.reviewCount,
      note: step.locked ? null : detail?.note ?? null,
      hasInstruction: !step.locked && step.pairRole !== 'result' && detail?.hasInstruction === true,
    }
    const card = cards.get(step.cardId)
    if (card) {
      card.rows.push(row)
      card.started = card.started || step.started
    } else cards.set(step.cardId, { started: step.started, rows: [row] })
  })
  return [...cards.entries()].map(([cardId, card]) => ({ cardId, started: card.started, rows: card.rows }))
}

// --- Test marks ---------------------------------------------------------------------------------------

export type TestMarkTone = 'success' | 'danger' | 'warning' | 'neutral' | 'info'

export interface TestMark {
  readonly glyph: string
  readonly tone: TestMarkTone
  /** Message key under `gui.document_window.` (the tooltip). */
  readonly key: string
}

const VERDICT_MARKS: Readonly<Record<string, TestMark>> = {
  PASS: { glyph: '✓', tone: 'success', key: 'test.verdict.PASS' },
  FAIL: { glyph: '✕', tone: 'danger', key: 'test.verdict.FAIL' },
  BLOCKED: { glyph: '!', tone: 'warning', key: 'test.verdict.BLOCKED' },
  NOT_RUN: { glyph: '–', tone: 'neutral', key: 'test.verdict.NOT_RUN' },
}

export const TEST_PENDING_MARK: TestMark = { glyph: '⋯', tone: 'info', key: 'test.pending' }

/** A verdict's mark; anything else reads as not run (the v0.1 verdictLabel / verdictBadge). */
export function testVerdictMark(status: string | null | undefined): TestMark {
  return VERDICT_MARKS[(status ?? 'NOT_RUN').toUpperCase()] ?? VERDICT_MARKS.NOT_RUN
}

const MODE_GLYPHS: Readonly<Record<string, string>> = { automated: '⚙', manual: '✍', external: '⇄' }

/** An execution mode's glyph and tooltip key; an unknown mode shows nothing. */
export function testModeMark(mode: string | null | undefined): TestMark | null {
  const glyph = MODE_GLYPHS[mode ?? '']
  return glyph ? { glyph, tone: 'neutral', key: `test.mode.${mode}` } : null
}

const CATEGORIES = new Set(['normal', 'negative', 'boundary', 'regression'])

/** Message key of a Case category, or null for none / an unknown one (no raw value on screen). */
export function testCategoryKey(category: string | null | undefined): string | null {
  return category && CATEGORIES.has(category) ? `test.category.${category}` : null
}

/** Result rows by Case id (the spec view marks each Case with its result). */
export function testResultByCase(rows: readonly TestSpecResultRow[]): ReadonlyMap<string, TestSpecResultRow> {
  return new Map(rows.map((row) => [row.caseId, row]))
}

/** The Case fields the scenario view lists, in the v0.1 order (automation_ref / test_assets are file paths, not shown). */
export const TEST_CASE_FIELDS = ['requirement', 'precondition', 'input', 'procedure', 'expected', 'checkPoints'] as const

/** A test document's flag: a mark, an optional short value after the glyph, and the tooltip's params. */
export interface TestFlag {
  readonly mark: TestMark
  readonly text: string | number | null
  readonly params: Readonly<Record<string, number>>
}

/**
 * The flags a test view puts in its summary line instead of the v0.1 bands and paragraphs: a live run (with
 * its progress), a result the live source moved past (stale) or did not check yet, scenario format errors,
 * results that match no Case and Cases with more than one result.
 */
export function testDocumentFlags(doc: Pick<TestSpecDocument, 'view' | 'unmapped' | 'conflicts'> | null): TestFlag[] {
  if (!doc) return []
  const { view } = doc
  const flags: TestFlag[] = []
  if (view.activeRun) {
    const done = view.progress?.automatedCompleted ?? 0
    const total = view.progress?.automatedTotal ?? view.activeRun.caseTotal ?? 0
    flags.push({ mark: { glyph: '⋯', tone: 'info', key: 'test.running' }, text: total ? `${done}/${total}` : null, params: { done, total } })
  } else if (view.basisValid === false) {
    flags.push({ mark: { glyph: '!', tone: 'warning', key: 'test.stale' }, text: null, params: {} })
  } else if (view.basisState === 'unchecked') {
    flags.push({ mark: { glyph: '?', tone: 'neutral', key: 'test.unchecked' }, text: null, params: {} })
  }
  if (view.specErrors) flags.push({ mark: { glyph: '⚠', tone: 'danger', key: 'test.spec_errors' }, text: view.specErrors, params: { n: view.specErrors } })
  if (doc.unmapped) flags.push({ mark: { glyph: '?', tone: 'warning', key: 'test.unmapped' }, text: doc.unmapped, params: { n: doc.unmapped } })
  if (doc.conflicts) flags.push({ mark: { glyph: '⇆', tone: 'warning', key: 'test.conflicts' }, text: doc.conflicts, params: { n: doc.conflicts } })
  return flags
}

export const TEST_REQUIRED_MARK: TestMark = { glyph: '●', tone: 'neutral', key: 'test.required' }
export const TEST_OPTIONAL_MARK: TestMark = { glyph: '○', tone: 'neutral', key: 'test.optional' }

/** The test gate of a report: 🛡 green when it passed, red when it blocks. */
export function testGateMark(passed: boolean): TestMark {
  return passed ? { glyph: '🛡', tone: 'success', key: 'test.gate_passed' } : { glyph: '🛡', tone: 'danger', key: 'test.gate_blocked' }
}

/** A test view shows its own way only a specification (contract 2) TS or TSR; anything else is Markdown. */
export function testDocumentStructured(doc: Pick<TestSpecDocument, 'view'> | null): boolean {
  return doc !== null && doc.view.contractVersion === 2 && (doc.view.kind === 'TS' || doc.view.kind === 'TSR')
}

// --- The test report as saved -------------------------------------------------------------------------
// The server assembles a report from its run record into the document file (test_spec_service
// render_report_markdown with test_run_service _tsr_basis_section; render_pending_report_markdown while the run
// has no result yet) and never reads the file back: `/test-document` answers the record. ✎ edits the file, so
// the report's Case rows are read from the saved text; every verdict — overall, gate, counts and each Case's —
// stays the server record's (the report's own footer says the overall verdict is the server's, not the text's;
// 0053 Q #419: a Case's verdict word in the text is not taken either, testReportVerdicts).
// The view shows the Case table and the evidence. Every other line restates the record (the title, the run and
// overall lines, the summary table, source identity, carried-over notes, unmapped / conflict items, the footer,
// the Test Basis facts) and is let through only in the form the server writes it: word for word in one of its
// locales, with a value only where the server puts one and of the kind it puts there (a verdict word, a count,
// an id or a time without spaces). Who checked a Case and when are free text on the server (normalize_results
// takes any string, spaces too), so that line is held to the record instead: with the record the window reads
// (`/test-document`), it must say what the record says. Any other line — a note, a quote, a heading, an italic
// line of one's own — and any table cell the view could not say as written (a verdict, a required / optional
// word or a mode the server does not write) make the whole text read as not a report: the window then shows it
// as written.
// A cell is written by the server's `_cell` (a `|` of the value as `\|`, a line break as `<br>`, empty as `-`):
// the table is split on its borders first, leaving each cell as written, and a cell's escapes are undone once
// after that. Where the record holds a value the server writes as exactly that cell, the cell is that value.

const REPORT_STATUS = 'PASS|FAIL|BLOCKED|NOT_RUN'
const REPORT_MODES = new Set(['automated', 'manual', 'external'])
/** Evidence kinds the server keeps (test_spec_service EVIDENCE_KINDS); `None` is an item stored without one. */
const REPORT_EVIDENCE_KINDS = new Set(['text', 'log', 'structured', 'file', 'attachment', 'screenshot', 'url', 'manual_note', 'None'])
/** Source identity keys the server keeps (test_spec_service _normalize_source_identity). */
const REPORT_SOURCE_KEYS = new Set([
  'bundle_id', 'bundle_hash', 'git_revision', 'tree', 'worktree', 'branch', 'runner', 'runner_version', 'tool', 'tool_version',
  'executed_at', 'ci_url', 'kind', 'basis_id', 'content_fingerprint', 'bundle_sha256', 'exclusion_policy_version',
])
/** A reason code the server puts in a pending report's state line (or `-`). */
const REPORT_REASON = '[\\w.:\\-]+'

interface ReportWords {
  readonly title: string
  readonly kind: string
  readonly run: string
  readonly overall: string
  readonly gate_pass: string
  readonly gate_block: string
  readonly summary_heading: string
  readonly summary_header: string
  readonly all: string
  readonly required: string
  readonly optional: string
  readonly cases_heading: string
  readonly cases_header: string
  readonly evidence_heading: string
  readonly unmapped_heading: string
  readonly conflicts_heading: string
  readonly none: string
  readonly source: string
  readonly carried: string
  readonly footer: string
  readonly yes: string
  readonly no: string
  readonly pending: {
    readonly run: string
    readonly state_queued: string
    readonly state_running: string
    readonly state_prepare_refused: string
    readonly state_cancelled: string
    readonly state_failed: string
    readonly cases_heading: string
    readonly cases_header: string
    readonly pending: string
    readonly not_selected: string
    readonly no_result: string
    readonly kept: string
    readonly reported: string
    readonly footer: string
  }
  /** The current-source texts of the Test Basis section: valid, stale, unverifiable. */
  readonly basis: readonly string[]
}

/**
 * The report's words as the server writes them, per locale: test_spec_service _REPORT_STRINGS /
 * _PENDING_STRINGS, test_run_service _TSR_TITLE_TEMPLATES / _TSR_BASIS_VALIDITY (`{slot}` is a value).
 */
const REPORT_WORDS: Readonly<Record<'ko' | 'en' | 'ja', ReportWords>> = {
  ko: {
    title: '테스트 레포트 — {name}',
    kind: '> 문서 성격: 시험성적서 (TS {doc_id} revision {revision} 의 Case ID별 결과)',
    run: '> 결과 기록: {run_id} · 기록 시각 {at}',
    overall: '> 종합 판정(서버 계산, 필수 Case 기준): **{overall}** — {gate}',
    gate_pass: '시험 gate 통과',
    gate_block: '시험 gate 미통과 · 다음 단계 진행 차단',
    summary_heading: '## 요약',
    summary_header: '| 구분 | 전체 | PASS | FAIL | BLOCKED | NOT_RUN |',
    all: '전체',
    required: '필수',
    optional: '선택',
    cases_heading: '## Case별 결과',
    cases_header: '| Case | 제목 | 필수 | 방식 | 기대 결과 | 실제 결과 | 판정 | 결함/참조 |',
    evidence_heading: '## Evidence 및 source identity',
    unmapped_heading: '## TS에 없는 결과 (unmapped)',
    conflicts_heading: '## 중복 매핑 (mapping conflict)',
    none: '없음',
    source: 'source',
    carried: '이전 기록에서 유지: {run_id}',
    footer: '*이 시험성적서는 결과 기록 {run_id} 로부터 FlowGate가 조립했다. 종합 판정은 문서 본문이 아니라 서버 기록으로 결정된다.*',
    yes: '필수',
    no: '선택',
    pending: {
      run: '> 시험 run: {run_id} · 접수 시각 {at}',
      state_queued: '> 상태: **시험 대기/실행 중** — 결과가 기록되면 이 레포트가 갱신된다. 아직 판정이 없다.',
      state_running: '> 상태: **시험 실행 중** — Case 결과 {done}/{total}건 반영. 남은 Case가 끝나면 결과가 기록되고 종합 판정이 정해진다.',
      state_prepare_refused: '> 상태: **준비 실패** ({reason}) — 시험을 실행하지 않았다. 원인을 해결한 뒤 다시 실행한다.',
      state_cancelled: '> 상태: **취소됨** — 시험 run이 결과 없이 취소됐다. 다시 실행한다.',
      state_failed: '> 상태: **실행 중단** ({reason}) — 기록된 결과가 없다. 다시 실행한다.',
      cases_heading: '## Case별 상태',
      cases_header: '| Case | 제목 | 필수 | 방식 | 기대 결과 | 상태 |',
      pending: '대기 (PENDING)',
      not_selected: '이번 run 대상 아님',
      no_result: '결과 없음',
      kept: '{status} (이전 기록 유지)',
      reported: '{status} (이번 run 결과)',
      footer: '*이 레포트는 시험 run {run_id} 접수 시 FlowGate가 만들었다. 종합 판정은 결과가 기록된 뒤 서버 기록으로 결정된다.*',
    },
    basis: ['현재 소스와 같음', '실행 후 소스가 바뀌어 이 결과는 현재 소스에 유효하지 않음 — 다시 실행 필요', '현재 소스를 측정할 수 없어 유효 여부를 확인하지 못함 — 사람 판단 필요'],
  },
  en: {
    title: 'Test Report — {name}',
    kind: '> Document: test report (per-Case-ID results of TS {doc_id} revision {revision})',
    run: '> Result record: {run_id} · recorded at {at}',
    overall: '> Overall (server-computed from required cases): **{overall}** — {gate}',
    gate_pass: 'test gate passed',
    gate_block: 'test gate NOT passed · next step blocked',
    summary_heading: '## Summary',
    summary_header: '| Scope | Total | PASS | FAIL | BLOCKED | NOT_RUN |',
    all: 'All',
    required: 'Required',
    optional: 'Optional',
    cases_heading: '## Results by Case',
    cases_header: '| Case | Title | Required | Mode | Expected | Actual | Verdict | Defect/ref |',
    evidence_heading: '## Evidence and source identity',
    unmapped_heading: '## Results not in the TS (unmapped)',
    conflicts_heading: '## Duplicate mappings (mapping conflict)',
    none: 'none',
    source: 'source',
    carried: 'carried over from {run_id}',
    footer: '*FlowGate assembled this test report from result record {run_id}. The overall verdict is decided by the server record, not by this text.*',
    yes: 'required',
    no: 'optional',
    pending: {
      run: '> Test run: {run_id} · admitted at {at}',
      state_queued: '> State: **test queued/running** — this report is updated when results are recorded. No verdict yet.',
      state_running: '> State: **test running** — {done}/{total} Case results in. The result is recorded and the overall verdict decided when the remaining Cases finish.',
      state_prepare_refused: '> State: **preparation failed** ({reason}) — the test was not run. Fix the cause and run again.',
      state_cancelled: '> State: **cancelled** — the test run was cancelled without results. Run again.',
      state_failed: '> State: **run aborted** ({reason}) — no results were recorded. Run again.',
      cases_heading: '## Case Status',
      cases_header: '| Case | Title | Required | Mode | Expected | Status |',
      pending: 'pending (PENDING)',
      not_selected: 'not in this run',
      no_result: 'no result',
      kept: '{status} (earlier record kept)',
      reported: '{status} (this run)',
      footer: '*FlowGate opened this report when test run {run_id} was admitted. The overall verdict is decided by the server record once results are recorded.*',
    },
    basis: ['matches the current source', 'the source changed after execution; this result is not valid for the current source — run again', 'the current source could not be measured; validity unknown — needs a human'],
  },
  ja: {
    title: 'テストレポート — {name}',
    kind: '> 文書種別: 試験成績書 (TS {doc_id} revision {revision} のCase ID別結果)',
    run: '> 結果記録: {run_id} · 記録時刻 {at}',
    overall: '> 総合判定(サーバー計算・必須Case基準): **{overall}** — {gate}',
    gate_pass: '試験ゲート通過',
    gate_block: '試験ゲート未通過 · 次の段階へ進めない',
    summary_heading: '## 概要',
    summary_header: '| 区分 | 全体 | PASS | FAIL | BLOCKED | NOT_RUN |',
    all: '全体',
    required: '必須',
    optional: '任意',
    cases_heading: '## Case別結果',
    cases_header: '| Case | タイトル | 必須 | 方式 | 期待結果 | 実際の結果 | 判定 | 欠陥/参照 |',
    evidence_heading: '## エビデンスとsource identity',
    unmapped_heading: '## TSにない結果 (unmapped)',
    conflicts_heading: '## 重複マッピング (mapping conflict)',
    none: 'なし',
    source: 'source',
    carried: '以前の記録から維持: {run_id}',
    footer: '*この試験成績書は結果記録 {run_id} からFlowGateが組み立てた。総合判定は本文ではなくサーバー記録で決まる。*',
    yes: '必須',
    no: '任意',
    pending: {
      run: '> 試験run: {run_id} · 受付時刻 {at}',
      state_queued: '> 状態: **試験待機/実行中** — 結果が記録されるとこのレポートが更新される。まだ判定はない。',
      state_running: '> 状態: **試験実行中** — Case結果 {done}/{total}件反映。残りのCaseが終わると結果が記録され総合判定が決まる。',
      state_prepare_refused: '> 状態: **準備失敗** ({reason}) — 試験は実行されていない。原因を解消してから再実行する。',
      state_cancelled: '> 状態: **キャンセル** — 試験runは結果なしでキャンセルされた。再実行する。',
      state_failed: '> 状態: **実行中断** ({reason}) — 記録された結果はない。再実行する。',
      cases_heading: '## Case別状態',
      cases_header: '| Case | タイトル | 必須 | 方式 | 期待結果 | 状態 |',
      pending: '待機 (PENDING)',
      not_selected: '今回のrun対象外',
      no_result: '結果なし',
      kept: '{status} (以前の記録を維持)',
      reported: '{status} (今回のrun結果)',
      footer: '*このレポートは試験run {run_id} の受付時にFlowGateが作成した。総合判定は結果記録後にサーバー記録で決まる。*',
    },
    basis: ['現在のソースと一致', '実行後にソースが変わったため、この結果は現在のソースに対して有効ではありません — 再実行が必要', '現在のソースを測定できず有効性を確認できません — 人の判断が必要'],
  },
}

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** A server template as a whole-line pattern; each `{slot}` is captured, by its pattern in `slots` or as text without spaces. */
function reportLine(template: string, slots: Readonly<Record<string, string>> = {}): RegExp {
  const source = template
    .split(/(\{\w+\})/)
    .map((part) => {
      const slot = /^\{(\w+)\}$/.exec(part)
      return slot ? `(${slots[slot[1]] ?? '\\S+'})` : escapeRegExp(part)
    })
    .join('')
  return new RegExp(`^${source}$`)
}

interface ReportShape {
  readonly words: ReportWords
  readonly title: RegExp
  readonly kind: RegExp
  readonly run: RegExp
  readonly overall: RegExp
  readonly footer: RegExp
  readonly carried: RegExp
  readonly pendingRun: RegExp
  readonly pendingStates: readonly RegExp[]
  readonly pendingFooter: RegExp
  readonly kept: RegExp
  readonly reported: RegExp
  readonly basis: readonly RegExp[]
}

const REPORT_CODE = '`[^`\\s]*`'
const REPORT_SHAPES: readonly ReportShape[] = Object.values(REPORT_WORDS).map((words) => ({
  words,
  title: reportLine(`# ${words.title}`, { name: '.+' }),
  kind: reportLine(words.kind),
  run: reportLine(words.run),
  overall: reportLine(words.overall, { overall: REPORT_STATUS, gate: `${escapeRegExp(words.gate_pass)}|${escapeRegExp(words.gate_block)}` }),
  footer: reportLine(words.footer),
  carried: reportLine(words.carried),
  pendingRun: reportLine(words.pending.run),
  pendingStates: [
    reportLine(words.pending.state_queued),
    reportLine(words.pending.state_running, { done: '\\d+', total: '\\d+' }),
    reportLine(words.pending.state_prepare_refused, { reason: REPORT_REASON }),
    reportLine(words.pending.state_cancelled),
    reportLine(words.pending.state_failed, { reason: REPORT_REASON }),
  ],
  pendingFooter: reportLine(words.pending.footer),
  kept: reportLine(words.pending.kept, { status: REPORT_STATUS }),
  reported: reportLine(words.pending.reported, { status: REPORT_STATUS }),
  // The Test Basis lines in the order the server writes them (each one only when it has the value).
  basis: [
    new RegExp(`^- basis_id: ${REPORT_CODE}$`),
    new RegExp(`^- source_fingerprint: ${REPORT_CODE}$`),
    new RegExp(`^- git_revision: ${REPORT_CODE}(?: \\(dirty\\))?$`),
    /^- measured_at: \S+$/,
    new RegExp(`^- test_assets\\.manifest_hash: ${REPORT_CODE}$`),
    new RegExp(`^- execution_run: ${REPORT_CODE}$`),
    new RegExp(`^- current_source: (?:${words.basis.map(escapeRegExp).join('|')}|\\w+)(?: \\(${REPORT_REASON}(?:, ${REPORT_REASON})*\\))?$`),
  ],
}))

/**
 * A table line's cells as written, or null when the line is not a table row. Only the borders are found here: a
 * `\|` is a pipe inside a cell and stays as written (reportText undoes the cell's escapes, once).
 */
function reportCells(line: string): string[] | null {
  const row = line.trim()
  if (row.length < 2 || !row.startsWith('|') || !row.endsWith('|')) return null
  const cells: string[] = []
  let cell = ''
  for (let i = 1; i < row.length - 1; i += 1) {
    if (row[i] === '\\' && row[i + 1] === '|' && i + 1 < row.length - 1) {
      cell += '\\|'
      i += 1
    } else if (row[i] === '|') {
      cells.push(cell.trim())
      cell = ''
    } else cell += row[i]
  }
  cells.push(cell.trim())
  return cells
}

/** A value as the server's `_cell` writes it in one cell: `|` as `\|`, a line break as `<br>`, empty as `-`. */
function reportCell(value: string | null): string {
  const text = (value ?? '').replace(/\r\n/g, '\n').trim()
  return text.replace(/\|/g, '\\|').replace(/\n/g, '<br>') || '-'
}

/** Undoes the server's `_cell` escapes, once: `<br>` is a line break, `\|` a pipe (so `\\|` is `\|`). */
const reportUnescape = (written: string) => written.replace(/<br>/g, '\n').replace(/\\\|/g, '|')

/**
 * A written cell's value (`-` is empty). `recorded` is the record's value for it, when the record has the Case:
 * if the server writes that value as exactly this cell, it is the value (so one that holds `<br>` or is `-` as
 * text stays so); otherwise — a cell edited with ✎ — the cell is read by the server's escapes.
 */
function reportText(written: string, recorded?: string | null): string | null {
  if (recorded !== undefined && reportCell(recorded) === written) return recorded?.trim() ? recorded : null
  const text = reportUnescape(written).trim()
  return text && text !== '-' ? text : null
}

/** What `/test-document` answers for a report: its run's rows (who checked a Case and when, too) and the valid result. */
export type TestReportRecord = Pick<TestSpecDocument, 'storedRows' | 'rows'>

/** The record's row of a Case: the report's own run first, then the valid result (as testReportVerdicts). */
function reportRecorded(record: TestReportRecord | null, caseId: string): TestSpecRecordRow | undefined {
  if (!record) return undefined
  const key = testCaseKey(caseId)
  return record.storedRows.find((row) => testCaseKey(row.caseId) === key) ?? record.rows.find((row) => testCaseKey(row.caseId) === key)
}

const tableRule = (width: number) => `|${'---|'.repeat(width)}`

type ReportRow = Omit<TestSpecResultRow, 'evidence'> & { evidence: TestSpecEvidence[] }

/** The cells every Case table has (Case, title, required, mode, expected), or null when one is not the server's. */
function reportCase(
  cells: readonly string[],
  words: ReportWords,
  recorded: TestSpecRecordRow | undefined,
): Omit<ReportRow, 'status' | 'actual' | 'defectRef'> | null {
  const caseId = reportText(cells[0])
  if (caseId === null || (cells[2] !== words.yes && cells[2] !== words.no)) return null
  if (cells[3] !== '-' && !REPORT_MODES.has(cells[3])) return null
  return {
    caseId,
    title: reportText(cells[1], recorded ? recorded.title : undefined),
    required: cells[2] === words.yes,
    executionMode: reportText(cells[3]),
    expected: reportText(cells[4], recorded ? recorded.expected : undefined),
    evidence: [],
  }
}

/** The record's row of the Case a table row is of (by its written Case cell). */
function reportRowRecorded(cells: readonly string[], record: TestReportRecord | null): TestSpecRecordRow | undefined {
  const caseId = reportText(cells[0])
  return caseId === null ? undefined : reportRecorded(record, caseId)
}

/** The heading quotes: the title, the document line, the run line and the overall (or the pending state) line. */
function reportHead(lines: readonly string[], shape: ReportShape, title: string | null, pending: boolean): boolean {
  if (lines.length !== 4) return false
  const [name, kind, run, state] = lines
  if (!(title !== null ? name === `# ${title}` : shape.title.test(name)) || !shape.kind.test(kind)) return false
  if (pending) return shape.pendingRun.test(run) && shape.pendingStates.some((line) => line.test(state))
  const overall = shape.overall.exec(state)
  // The gate words go with the verdict, as the server writes them.
  return shape.run.test(run) && overall !== null && overall[2] === (overall[1] === 'PASS' ? shape.words.gate_pass : shape.words.gate_block)
}

/** The summary table: its header and the all / required / optional rows of counts. */
function reportSummary(lines: readonly string[], words: ReportWords): boolean {
  if (lines.length !== 5 || lines[0] !== words.summary_header || lines[1] !== tableRule(6)) return false
  return [words.all, words.required, words.optional].every((label, index) => {
    const cells = reportCells(lines[index + 2])
    return cells !== null && cells.length === 6 && cells[0] === label && cells.slice(1).every((cell) => /^\d+$/.test(cell))
  })
}

const REPORT_TABLE_VERDICT = new RegExp(`^\\*\\*(${REPORT_STATUS})\\*\\*$`)

/** The result table (8 cells: … expected, actual, **verdict**, defect). */
function reportResultTable(lines: readonly string[], words: ReportWords, record: TestReportRecord | null): ReportRow[] | null {
  if (lines[0] !== words.cases_header || lines[1] !== tableRule(8)) return null
  const rows: ReportRow[] = []
  for (const line of lines.slice(2)) {
    const cells = reportCells(line)
    if (!cells || cells.length !== 8) return null
    const recorded = reportRowRecorded(cells, record)
    const base = reportCase(cells, words, recorded)
    const verdict = REPORT_TABLE_VERDICT.exec(cells[6])
    if (!base || !verdict) return null
    rows.push({
      ...base,
      status: verdict[1],
      actual: reportText(cells[5], recorded ? recorded.actual : undefined),
      defectRef: reportText(cells[7], recorded ? recorded.defectRef : undefined),
    })
  }
  return rows
}

/** The table of a run without a result yet (6 cells: … expected, state), then the footer. */
function reportPendingTable(lines: readonly string[], shape: ReportShape, record: TestReportRecord | null): ReportRow[] | null {
  const words = shape.words.pending
  if (lines.length < 3 || lines[0] !== words.cases_header || lines[1] !== tableRule(6) || !shape.pendingFooter.test(lines[lines.length - 1])) return null
  const rows: ReportRow[] = []
  for (const line of lines.slice(2, -1)) {
    const cells = reportCells(line)
    if (!cells || cells.length !== 6) return null
    const base = reportCase(cells, shape.words, reportRowRecorded(cells, record))
    if (!base) return null
    // A verdict only where the state has one (kept from an earlier record, or reported by this run).
    const verdict = shape.kept.exec(cells[5]) ?? shape.reported.exec(cells[5])
    if (!verdict && ![words.pending, words.not_selected, words.no_result].includes(cells[5])) return null
    rows.push({ ...base, status: verdict?.[1] ?? null, actual: null, defectRef: null })
  }
  return rows
}

/** A web address the view may open (http / https, no spaces). */
const TEST_EVIDENCE_LINK = /^https?:\/\/\S+$/i

/** The address an evidence item opens: a `url` item whose value is a web address; null for anything else (shown as text). */
export function testEvidenceLink(item: Pick<TestSpecEvidence, 'kind' | 'value'>): string | null {
  return item.kind === 'url' && TEST_EVIDENCE_LINK.test(item.value) ? item.value : null
}

/** How the server writes an evidence item after `  - ` (render_report_markdown): `[kind] label: value`, the value as a cell. */
const reportEvidenceLine = (item: TestSpecEvidence) => `[${item.kind ?? 'None'}] ${item.label ? `${item.label}: ` : ''}${reportCell(item.value)}`

/**
 * An evidence item as written: `[kind] label: value` where the label is optional, kept as given (`: ` allowed,
 * no escapes) and the value is written as a cell. A url item's value is its address, which has no spaces, so it
 * is what follows the last `: ` and the label is all before it. Any other item reads `label: value` as one text
 * (which is how the view shows it anyway), its escapes undone — unless one stands before the last `: `, where it
 * may be the label's own text: that item cannot be told (null; the window then shows the text as written). A url
 * item with no address to tell apart is read the same way, so it is never a link.
 */
function reportEvidenceItem(kind: string, written: string): TestSpecEvidence | null {
  const split = written.lastIndexOf(': ')
  if (kind === 'url') {
    const whole = reportUnescape(written)
    if (TEST_EVIDENCE_LINK.test(whole)) return { kind, value: whole, label: null }
    const value = split > 0 ? reportUnescape(written.slice(split + 2)) : ''
    if (TEST_EVIDENCE_LINK.test(value)) return { kind, value, label: written.slice(0, split) }
  }
  if (split > 0 && /<br>|\\\|/.test(written.slice(0, split))) return null
  return { kind: kind === 'None' ? null : kind, value: reportUnescape(written).trim(), label: null }
}

/** `source: key=value, …` with the server's identity keys. */
function reportSourceLine(text: string, words: ReportWords): boolean {
  const prefix = `${words.source}: `
  if (!text.startsWith(prefix)) return false
  return text
    .slice(prefix.length)
    .split(', ')
    .every((part) => part.indexOf('=') > 0 && REPORT_SOURCE_KEYS.has(part.slice(0, part.indexOf('='))))
}

const REPORT_EVIDENCE_ITEM = /^\[([A-Za-z_]+)\] (.+)$/
/** `verdict)` and ` · checked by / at` when the Case has them — free text on the server (spaces too). */
const REPORT_EVIDENCE_HEAD = new RegExp(`^(${REPORT_STATUS})\\)(?: ·(?: .*)?)?$`)

/**
 * The evidence section: per Case of the table, in its order, `- id (verdict)[ · checked by / at]` and its items
 * — evidence, then the source identity, then a carried-over note — or the one "none" item. Adds the evidence to
 * the rows; false when a line is not the server's. Who checked a Case and when are any text on the server, so
 * with a record the line must be the one the server writes from it (a word of one's own there is not hidden);
 * without one (nothing to hold it to) any text there is let through. An evidence item the record holds as
 * written is that item, exactly.
 */
function reportEvidence(lines: readonly string[], rows: readonly ReportRow[], shape: ReportShape, record: TestReportRecord | null): boolean {
  let at = 0
  for (const row of rows) {
    const head = lines[at] ?? ''
    const prefix = `- ${row.caseId} (`
    const verdict = head.startsWith(prefix) ? REPORT_EVIDENCE_HEAD.exec(head.slice(prefix.length)) : null
    if (!verdict) return false
    const recorded = reportRecorded(record, row.caseId)
    if (record) {
      if (!recorded) return false
      const checked = [recorded.checkedBy, recorded.checkedAt].filter(Boolean).join(' / ')
      if (head !== `${prefix}${verdict[1]})${checked ? ` · ${checked}` : ''}`.trimEnd()) return false
    }
    const items: string[] = []
    for (at += 1; at < lines.length && lines[at].startsWith('  - '); at += 1) items.push(lines[at].slice(4))
    if (items.length === 1 && items[0] === shape.words.none) continue
    if (!items.length) return false
    // 0 evidence, 1 source identity, 2 carried over: each in the server's order.
    let phase = 0
    for (const item of items) {
      const evidence = REPORT_EVIDENCE_ITEM.exec(item)
      if (phase === 0 && evidence && REPORT_EVIDENCE_KINDS.has(evidence[1])) {
        const known = recorded?.evidence.find((kept) => reportEvidenceLine(kept) === item)
        const read = known ? { kind: known.kind, value: known.value, label: known.label } : reportEvidenceItem(evidence[1], evidence[2])
        if (!read) return false
        row.evidence.push(read)
      } else if (phase < 1 && reportSourceLine(item, shape.words)) phase = 1
      else if (phase < 2 && shape.carried.test(item)) phase = 2
      else return false
    }
  }
  return at === lines.length
}

const REPORT_UNMAPPED_ITEM = new RegExp(`^- \\S+ · (?:${REPORT_STATUS}) · .+ \\((?:missing_case_id|unknown_case_id)\\)$`)
const REPORT_CONFLICT_ITEM = new RegExp(`^- \\S+: \\d+ →(?: (?:${REPORT_STATUS})(?:, (?:${REPORT_STATUS}))*)?$`)

/** The unmapped / conflict items (only their counts are shown, from the record): the server's items, or its one "none". */
function reportListed(lines: readonly string[], words: ReportWords, item: RegExp): boolean {
  if (lines.length === 1 && lines[0] === `- ${words.none}`) return true
  return lines.length > 0 && lines.every((line) => item.test(line))
}

/** Every line matches one of `patterns`, in their order, each at most once. */
function reportInOrder(lines: readonly string[], patterns: readonly RegExp[]): boolean {
  let next = 0
  return lines.every((line) => {
    const at = patterns.findIndex((pattern, index) => index >= next && pattern.test(line))
    next = at + 1
    return at >= 0
  })
}

interface ReportSection {
  readonly heading: string
  /** The section's lines that have text (trailing spaces cut). */
  readonly lines: string[]
}

/** The lines before the first `## ` heading, then one section per heading. */
function reportSections(body: string): ReportSection[] {
  const sections: ReportSection[] = [{ heading: '', lines: [] }]
  for (const raw of body.split('\n')) {
    const line = raw.trimEnd()
    if (line.startsWith('## ')) sections.push({ heading: line, lines: [] })
    else if (line.trim()) sections[sections.length - 1].lines.push(line)
  }
  return sections
}

function reportRowsIn(sections: readonly ReportSection[], shape: ReportShape, title: string | null, record: TestReportRecord | null): ReportRow[] | null {
  const { words } = shape
  const [head, ...parts] = sections
  const headings = parts.map((part) => part.heading)
  if (headings.length === 1 && headings[0] === words.pending.cases_heading) {
    return reportHead(head.lines, shape, title, true) ? reportPendingTable(parts[0].lines, shape, record) : null
  }
  const named = [words.summary_heading, words.cases_heading, words.evidence_heading, words.unmapped_heading, words.conflicts_heading]
  const basis = headings.length === 6 && headings[5] === '## Test Basis'
  if (!(headings.length === 5 || basis) || named.some((heading, index) => headings[index] !== heading)) return null
  const [summary, cases, evidence, unmapped, conflicts] = parts
  const rows = reportResultTable(cases.lines, words, record)
  const footer = conflicts.lines[conflicts.lines.length - 1] ?? ''
  if (
    !rows ||
    !reportHead(head.lines, shape, title, false) ||
    !reportSummary(summary.lines, words) ||
    !reportEvidence(evidence.lines, rows, shape, record) ||
    !reportListed(unmapped.lines, words, REPORT_UNMAPPED_ITEM) ||
    !shape.footer.test(footer) ||
    !reportListed(conflicts.lines.slice(0, -1), words, REPORT_CONFLICT_ITEM) ||
    (basis && !reportInOrder(parts[5].lines, shape.basis))
  )
    return null
  return rows
}

/**
 * The Case rows of a test report as its saved text says them (the Case table and the evidence), or null when
 * the text is not in the shape the server writes a report in (see above). `title` is the report document's
 * title, which the server writes as the text's first heading; without it any title of the server's form is let
 * through. `record` is what `/test-document` answers for the report (the window always has it): who checked
 * each Case and when must be what it says, and a cell or an evidence item it holds as written is its value.
 * Without it that line is let through in the server's form and cells are read by the server's escapes.
 */
export function testReportRows(content: string, title: string | null = null, record: TestReportRecord | null = null): TestSpecResultRow[] | null {
  const sections = reportSections(splitDocumentSource(content.replace(/\r\n?/g, '\n')).body)
  const known = title?.trim() || null
  for (const shape of REPORT_SHAPES) {
    const rows = reportRowsIn(sections, shape, known, record ?? null)
    if (rows) return rows
  }
  return null
}

/** A Case ID's matching key, as the server matches results to Cases (test_spec_service case_id_key: `tc_001` is `TC-1`). */
export function testCaseKey(caseId: string): string {
  const norm = caseId.trim().toUpperCase().replace(/_/g, '-')
  const numbered = /^([A-Z][A-Z0-9]*)-0*(\d+)$/.exec(norm)
  return numbered ? `${numbered[1]}-${numbered[2]}` : norm
}

/**
 * The verdict each Case of a test report shows, by Case key: the server record's, never the report text's
 * (0053 Q #419). First the record the report was assembled from (its run — while a run is live, the Cases it
 * has reported so far), then the valid result for a Case that run does not hold (a Case kept from an earlier
 * record). A Case in neither has no verdict (not run).
 */
export function testReportVerdicts(doc: Pick<TestSpecDocument, 'rows' | 'storedRows'>): ReadonlyMap<string, string | null> {
  const verdicts = new Map<string, string | null>()
  for (const row of [...doc.storedRows, ...doc.rows]) {
    const key = testCaseKey(row.caseId)
    if (!verdicts.has(key)) verdicts.set(key, row.status)
  }
  return verdicts
}
