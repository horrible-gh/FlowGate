/**
 * flowgate.v02.0048 T#11 — the test section rules as tables (D#1 §8.5, §10.1, §10.2 T#11 row): the target
 * document, the form (specification / legacy / none), the Case tally, the run state (live phases, a
 * cancel, a run in flight that one server answer lists only in its history, a prepare / execution failure
 * newer than the result, stale / unchecked / done), the history
 * rows, the operation classes, availability with the read-only rows of §8.5 across ownership × view, the
 * reason order (rule 6-1, then the permission refusal and the TS / report facts), cancelling, the keys of
 * a refusal and of a failed read, and every key with its ko / en / ja wording.
 */
import { describe, expect, it } from 'vitest'
import { toTestSpecView } from '@/features/document'
import testSpecMessages from '@/features/designer/inspector/features/testSpec/messages'
import {
  TEST_SPEC_OPERATION_CLASS,
  testSpecAvailability,
  testSpecCancelDisabledReason,
  testSpecCounts,
  testSpecDeniedBy,
  testSpecErrorKey,
  testSpecFailureKey,
  testSpecForm,
  testSpecHistory,
  testSpecLiveRun,
  testSpecRunDisabledReason,
  testSpecRunState,
  testSpecStatusKey,
  testSpecTally,
  testSpecTargetDocId,
  testSpecVerdictKey,
  type TestSpecFailureKind,
  type TestSpecInput,
  type TestSpecRunStateKey,
} from '@/features/designer/inspector/features/testSpec/testSpecModel'
import { inspectorReadOnly, type InspectorReadOnly } from '@/features/designer/inspector/inspectorTarget'
import { targetOf } from '../support/inspectorSupport'
import {
  LEGACY_TS_DOC,
  TSR_DOC,
  TSR_NODE,
  TS_DOC,
  TS_NODE,
  counts,
  legacyDocument,
  liveRun,
  runRow,
  testSelection,
  tsDocument,
  tsrDocument,
} from './testSpecFixtures'

const ts = (fields: Record<string, unknown> = {}) => toTestSpecView(tsDocument(fields))
const tsr = (fields: Record<string, unknown> = {}) => toTestSpecView(tsrDocument(fields))
const legacy = () => toTestSpecView(legacyDocument())

const input = (fields: Partial<TestSpecInput> = {}): TestSpecInput => ({
  readOnly: null,
  groupDisposed: false,
  aiRunning: false,
  view: ts(),
  denied: false,
  busy: false,
  ...fields,
})

// --- Target document and form ----------------------------------------------------------------------

describe('target document and form (flowgate.v02.0048 T#11)', () => {
  it('the section reads the selected Node’s own TS or TSR; nothing without a target', () => {
    expect(testSpecTargetDocId(targetOf(testSelection('v01', TS_NODE)))).toBe(TS_DOC)
    expect(testSpecTargetDocId(targetOf(testSelection('graph', TSR_NODE)))).toBe(TSR_DOC)
    expect(testSpecTargetDocId(null)).toBeNull()
  })

  it.each([
    ['specification TS', () => ts(), 'spec'],
    ['TSR of a specification TS', () => tsr(), 'spec'],
    ['legacy executable TS', legacy, 'legacy'],
    ['TSR of a legacy run', () => toTestSpecView({ kind: 'TSR', doc_id: TSR_DOC, contract_version: 1, target_ts: TS_DOC }), 'legacy'],
    ['TSR without its TS', () => toTestSpecView({ kind: 'TSR', doc_id: TSR_DOC, contract_version: 2 }), 'none'],
    ['another type', () => toTestSpecView({ kind: 'V', doc_id: 'x', contract_version: null }), 'none'],
    ['unknown contract', () => ts({ contract_version: 3 }), 'none'],
    ['not read', () => null, 'none'],
  ] as const)('%s -> %s', (_label, view, form) => {
    expect(testSpecForm(view())).toBe(form)
  })
})

// --- Case tally and counts ---------------------------------------------------------------------------

describe('Case tally and result counts', () => {
  it('counts Cases by execution mode; runnable = automated Cases the server runner can execute', () => {
    expect(testSpecTally(ts())).toEqual({ total: 3, required: 2, automated: 2, manual: 1, external: 0, runnable: 2 })
    const mixed = ts({
      cases: [
        { case_id: 'A', execution_mode: 'automated', required: true },
        { case_id: 'B', execution_mode: 'automated', required: true },
        { case_id: 'C', execution_mode: 'external', required: true },
        { case_id: 'D', execution_mode: 'odd', required: false },
      ],
      case_capabilities: { A: 'case_selectable', B: 'none', C: 'case_selectable' },
    })
    expect(testSpecTally(mixed)).toEqual({ total: 4, required: 3, automated: 2, manual: 0, external: 1, runnable: 1 })
    expect(testSpecTally(null)).toEqual({ total: 0, required: 0, automated: 0, manual: 0, external: 0, runnable: 0 })
  })

  it('shows the valid result’s counts, else the newest record’s', () => {
    expect(testSpecCounts(ts())).toEqual({ total: 3, pass: 2, fail: 0, blocked: 0, notRun: 1 })
    expect(testSpecCounts(ts({ effective_result: null, latest_result: runRow('r', { summary: { counts: counts(1, 1) } }) }))).toEqual({
      total: 2,
      pass: 1,
      fail: 1,
      blocked: 0,
      notRun: 0,
    })
    expect(testSpecCounts(legacy())).toBeNull()
    expect(testSpecCounts(null)).toBeNull()
  })
})

// --- Run state (the v0.1 run pill reading) -----------------------------------------------------------

describe('run state', () => {
  const later = { created_at: '2026-10-09T11:00:00Z' }
  it.each<[string, () => ReturnType<typeof ts> | null, TestSpecRunStateKey, boolean, string]>([
    ['not read', () => null, 'none', false, 'neutral'],
    ['legacy TS', legacy, 'none', false, 'neutral'],
    ['no result yet', () => ts({ latest_result: null, effective_result: null, last_execution: null, run_history: [] }), 'none', false, 'neutral'],
    ['queued', () => ts({ active_run: liveRun('r', { phase: 'queued' }) }), 'queued', true, 'info'],
    ['preparing', () => ts({ active_run: liveRun('r', { phase: 'preparing' }) }), 'preparing', true, 'info'],
    ['executing', () => ts({ active_run: liveRun('r') }), 'executing', true, 'info'],
    ['finalizing', () => ts({ active_run: liveRun('r', { phase: 'finalizing' }) }), 'finalizing', true, 'info'],
    ['an unknown phase reads as executing', () => ts({ active_run: liveRun('r', { phase: 'odd' }) }), 'executing', true, 'info'],
    ['a live run with no phase is queued', () => ts({ active_run: liveRun('r', { phase: null }) }), 'queued', true, 'info'],
    ['a cancel accepted', () => ts({ active_run: liveRun('r', { status: 'cancelling' }) }), 'cancelling', true, 'info'],
    // The server reads its run list and its live run separately: one answer can list a run being cancelled
    // (or still running) in its history while active_run is already empty. It stays in flight here.
    ['a cancel accepted, in the history only', () => ts({ last_execution: liveRun('r', { status: 'cancelling', ...later }) }), 'cancelling', true, 'info'],
    ['a running run, in the history only', () => ts({ last_execution: liveRun('r', later) }), 'executing', true, 'info'],
    ['a running row older than the result is not in flight', () => ts({ last_execution: liveRun('r', { created_at: '2026-10-09T09:00:00Z' }) }), 'done', false, 'success'],
    ['cancelled after the result', () => ts({ last_execution: runRow('r', { status: 'cancelled', ...later }) }), 'cancelled', false, 'warning'],
    ['prepare refused after the result', () => ts({ last_execution: runRow('r', { status: 'failed', prepare_refused: { error: 'basis_capture_failed' }, ...later }) }), 'prepare_failed', false, 'danger'],
    ['execution failed after the result', () => ts({ last_execution: runRow('r', { status: 'failed', error: 'setup_failed', ...later }) }), 'execution_failed', false, 'danger'],
    ['a failure older than the result', () => ts({ last_execution: runRow('r', { status: 'failed', created_at: '2026-10-09T09:00:00Z' }) }), 'done', false, 'success'],
    ['stale for the live source', () => ts({ basis_valid: false, basis_verdict: { state: 'stale' } }), 'stale', false, 'warning'],
    ['source not checked', () => ts({ basis_valid: null, basis_verdict: { state: 'unchecked' } }), 'unchecked', false, 'neutral'],
    ['done PASS', () => ts(), 'done', false, 'success'],
    ['done FAIL', () => ts({ effective_result: { summary: { overall: 'FAIL', counts: counts(1, 1) } } }), 'done', false, 'danger'],
    ['done BLOCKED', () => ts({ effective_result: { summary: { overall: 'BLOCKED', counts: counts(1, 0, 1) } } }), 'done', false, 'warning'],
    ['a valid result with no record of this revision', () => ts({ latest_result: null }), 'none', false, 'neutral'],
    ['TSR: its report', () => tsr(), 'done', false, 'success'],
  ])('%s -> %s', (_label, view, key, active, tone) => {
    const state = testSpecRunState(view())
    expect(state.key).toBe(key)
    expect(state.active).toBe(active)
    expect(state.tone).toBe(tone)
  })

  it('the run in flight: the live run, else the newest server run while it is running or being cancelled', () => {
    expect(testSpecLiveRun(ts({ active_run: liveRun('tr_live') }))?.runId).toBe('tr_live')
    expect(testSpecLiveRun(ts({ last_execution: liveRun('tr_split', { status: 'cancelling', ...later }) }))?.runId).toBe('tr_split')
    expect(testSpecLiveRun(ts({ last_execution: runRow('tr_done', { status: 'cancelled', ...later }) }))).toBeNull()
    expect(testSpecLiveRun(ts())).toBeNull()
    expect(testSpecLiveRun(null)).toBeNull()
  })

  it('a live run carries its id and progress (reported / selected Cases)', () => {
    expect(testSpecRunState(ts({ active_run: liveRun('tr_live') }))).toMatchObject({ runId: 'tr_live', done: 1, total: 2 })
    expect(testSpecRunState(ts({ active_run: liveRun('tr_live', { selected_case_ids: [], case_total: 5 }) }))).toMatchObject({ done: 1, total: 5 })
  })

  it('a failure carries its run and code; a result carries its verdict', () => {
    expect(testSpecRunState(ts({ last_execution: runRow('r9', { status: 'failed', prepare_refused: { error: 'basis_capture_failed' }, created_at: 'z' }) }))).toMatchObject({
      runId: 'r9',
      error: 'basis_capture_failed',
    })
    expect(testSpecRunState(ts({ last_execution: runRow('r8', { status: 'failed', error: 'setup_failed', created_at: 'z' }) }))).toMatchObject({ error: 'setup_failed' })
    expect(testSpecRunState(ts()).overall).toBe('PASS')
    expect(testSpecRunState(ts({ basis_valid: false })).overall).toBe('PASS')
  })
})

// --- History rows and keys ---------------------------------------------------------------------------

describe('history rows and keys', () => {
  it('newest first, at most five, with status / verdict keys, counts and the time', () => {
    const history = Array.from({ length: 7 }, (_, i) => runRow(`r${i}`, { status: i === 0 ? 'running' : 'failed', overall: i === 0 ? null : 'FAIL' }))
    const rows = testSpecHistory(ts({ run_history: history }))
    expect(rows.map((row) => row.runId)).toEqual(['r0', 'r1', 'r2', 'r3', 'r4'])
    expect(rows[0]).toMatchObject({ statusKey: 'status.running', verdictKey: null })
    expect(rows[1]).toMatchObject({ statusKey: 'status.failed', verdictKey: 'verdict.fail', counts: { pass: 2 }, at: '2026-10-09T10:02:00Z' })
    expect(testSpecHistory(ts({ run_history: [runRow('r', { finished_at: null })] }))[0].at).toBe('2026-10-09T10:00:00Z')
    expect(testSpecHistory(legacy())).toEqual([])
    expect(testSpecHistory(null)).toEqual([])
  })

  it.each([
    ['PASS', 'verdict.pass'],
    ['FAIL', 'verdict.fail'],
    ['BLOCKED', 'verdict.blocked'],
    ['NOT_RUN', 'verdict.not_run'],
    ['MAYBE', 'verdict.other'],
    [null, 'verdict.other'],
  ])('verdict %s -> %s', (overall, key) => {
    expect(testSpecVerdictKey(overall)).toBe(key)
  })

  it.each([
    ['running', 'status.running'],
    ['cancelling', 'status.cancelling'],
    ['passed', 'status.passed'],
    ['failed', 'status.failed'],
    ['cancelled', 'status.cancelled'],
    ['queued', 'status.other'],
    [null, 'status.other'],
  ])('status %s -> %s', (status, key) => {
    expect(testSpecStatusKey(status)).toBe(key)
  })
})

// --- Availability (D#1 §8.3, §8.5) ---------------------------------------------------------------------

describe('availability', () => {
  it('reading and opening are queries; starting and cancelling change the server state', () => {
    expect(TEST_SPEC_OPERATION_CLASS).toEqual({ read: 'query', open: 'query', run: 'change', cancel: 'change' })
  })

  const ownerships = ['v01', 'graph', 'unassigned', 'conflict'] as const
  const viewModes = ['revision', 'draft', null] as const
  const rows = ownerships.flatMap((ownership) => viewModes.map((viewMode) => [ownership, viewMode] as const))

  it.each(rows)('ownership %s × view %s: run follows the read-only rule; reading and opening stay enabled', (ownership, viewMode) => {
    const readOnly = inspectorReadOnly(ownership, viewMode)
    const availability = testSpecAvailability(input({ readOnly }))
    const reason = readOnly === 'conflict' ? 'reason.read_only_conflict' : readOnly === 'revision' ? 'reason.read_only_revision' : null
    // A document feature: visible in every ownership (D#1 §8.3 rules 2-4); only the view decides enabled.
    expect(availability.run).toEqual({ visible: true, enabled: reason === null, reason })
    expect(availability.cancel).toEqual({ visible: false, enabled: false, reason: null })
    expect(availability.read).toEqual({ visible: true, enabled: true, reason: null })
    expect(availability.open).toEqual({ visible: true, enabled: true, reason: null })
    const live = testSpecAvailability(input({ readOnly, view: ts({ active_run: liveRun('r') }) }))
    expect(live.cancel).toEqual({ visible: true, enabled: reason === null, reason })
    expect(live.run.visible).toBe(false)
  })

  // The §8.5 read-only required rows, named.
  it.each<[string, InspectorReadOnly, Partial<TestSpecInput>, string | null]>([
    ['1. graph × revision', inspectorReadOnly('graph', 'revision'), {}, 'reason.read_only_revision'],
    ['2. graph × draft', inspectorReadOnly('graph', 'draft'), {}, null],
    ['3. unassigned × revision', inspectorReadOnly('unassigned', 'revision'), {}, 'reason.read_only_revision'],
    ['3. unassigned × draft', inspectorReadOnly('unassigned', 'draft'), {}, null],
    ['4. conflict × revision', inspectorReadOnly('conflict', 'revision'), {}, 'reason.read_only_conflict'],
    ['5. revision × AI running', 'revision', { aiRunning: true }, 'reason.read_only_revision'],
    ['5. revision × disposed', 'revision', { groupDisposed: true }, 'reason.read_only_revision'],
  ])('§8.5 %s -> %s', (_label, readOnly, fields, reason) => {
    const availability = testSpecAvailability(input({ readOnly, ...fields }))
    expect(availability.run).toEqual({ visible: true, enabled: reason === null, reason })
    expect(availability.open.enabled).toBe(true)
  })

  // Rule 6-1: one reason, the first that holds.
  it.each<[string, Partial<TestSpecInput>, string | null]>([
    ['nothing holds', {}, null],
    ['conflict before everything', { readOnly: 'conflict', groupDisposed: true, aiRunning: true, denied: true, busy: true }, 'reason.read_only_conflict'],
    ['revision before disposed', { readOnly: 'revision', groupDisposed: true }, 'reason.read_only_revision'],
    ['disposed before AI running', { groupDisposed: true, aiRunning: true, denied: true }, 'reason.disposed'],
    ['AI running before the permission refusal', { aiRunning: true, denied: true }, 'reason.ai_running'],
    ['the permission refusal before the TS facts', { denied: true, view: tsr({ doc_review_status: 'approved' }) }, 'reason.forbidden'],
    ['report approved', { view: tsr({ doc_review_status: 'approved' }) }, 'reason.report_approved'],
    ['report approved (TS view)', { view: ts({ tsr_review_status: 'approved' }) }, 'reason.report_approved'],
    ['TS draft', { view: ts({ doc_review_status: 'draft' }) }, 'reason.not_approved'],
    ['TS rejected', { view: ts({ doc_review_status: 'rejected' }) }, 'reason.not_approved'],
    ['TS pending review that already ran: run again', { view: ts({ doc_review_status: 'pending_review' }) }, null],
    ['TS revised that already ran: run again', { view: ts({ doc_review_status: 'revised' }) }, null],
    ['TS pending review that never ran', { view: ts({ doc_review_status: 'pending_review', run_history: [] }) }, 'reason.not_approved'],
    ['TSR: its TS not approved', { view: tsr({ ts_review_status: 'draft', run_history: [] }) }, 'reason.not_approved'],
    ['spec errors', { view: ts({ errors: [{ code: 'x', message: 'm' }] }) }, 'reason.no_automated'],
    ['no runnable automated Case', { view: ts({ case_capabilities: { 'TC-1': 'none', 'TC-2': 'none' } }) }, 'reason.no_automated'],
    ['manual Cases only', { view: ts({ cases: [{ case_id: 'M', execution_mode: 'manual' }], case_capabilities: { M: 'case_selectable' } }) }, 'reason.no_automated'],
    ['TSR: no Cases in its view, the server judges', { view: tsr() }, null],
    ['TSR: spec errors are not in its view either', { view: tsr({ errors: [{ code: 'x' }] }) }, null],
    ['a call in flight', { busy: true }, 'reason.busy'],
  ])('run: %s -> %s', (_label, fields, reason) => {
    expect(testSpecRunDisabledReason(input(fields))).toBe(reason)
    expect(testSpecAvailability(input(fields)).run).toEqual({ visible: true, enabled: reason === null, reason })
  })

  it('a live run: [▶ 실행] is not shown (run_active is its reason if asked), [■ 취소] is', () => {
    const fields = { view: ts({ active_run: liveRun('r') }) }
    expect(testSpecRunDisabledReason(input(fields))).toBe('reason.run_active')
    expect(testSpecAvailability(input(fields))).toMatchObject({ run: { visible: false }, cancel: { visible: true, enabled: true, reason: null } })
  })

  it('a cancel the answer lists only in its history: [▶ 실행] is not shown, [■ 취소] is disabled as accepted', () => {
    const fields = { view: ts({ last_execution: liveRun('r', { status: 'cancelling', created_at: '2026-10-09T11:00:00Z' }) }) }
    expect(testSpecRunDisabledReason(input(fields))).toBe('reason.run_active')
    expect(testSpecAvailability(input(fields))).toMatchObject({
      run: { visible: false },
      cancel: { visible: true, enabled: false, reason: 'reason.cancelling' },
    })
  })

  it.each<[string, Partial<TestSpecInput>, string | null]>([
    ['nothing holds', {}, null],
    ['conflict', { readOnly: 'conflict' }, 'reason.read_only_conflict'],
    ['revision', { readOnly: 'revision' }, 'reason.read_only_revision'],
    // The server does not refuse a cancel for these: a runaway run can always be reclaimed.
    ['disposed group', { groupDisposed: true }, null],
    ['AI running', { aiRunning: true }, null],
    ['the permission refusal', { denied: true }, 'reason.forbidden'],
    ['a cancel already accepted', { view: ts({ active_run: liveRun('r', { status: 'cancelling' }) }) }, 'reason.cancelling'],
    ['a call in flight', { busy: true }, 'reason.busy'],
  ])('cancel: %s -> %s', (_label, fields, reason) => {
    const view = fields.view ?? ts({ active_run: liveRun('r') })
    expect(testSpecCancelDisabledReason(input({ ...fields, view }))).toBe(reason)
    expect(testSpecAvailability(input({ ...fields, view })).cancel).toEqual({ visible: true, enabled: reason === null, reason })
  })

  it.each([
    ['legacy TS', legacy()],
    ['other answer', toTestSpecView({ kind: 'V', doc_id: 'x' })],
    ['not read', null],
  ])('%s: no run operation; reading and opening only', (_label, view) => {
    const availability = testSpecAvailability(input({ view }))
    expect(availability.run.visible).toBe(false)
    expect(availability.cancel.visible).toBe(false)
    expect(availability.open).toEqual({ visible: true, enabled: true, reason: null })
  })

  it('the legacy TS document id stays readable for ↗ 열기', () => {
    expect(legacy().docId).toBe(LEGACY_TS_DOC)
  })
})

// --- Refusals and failed reads -----------------------------------------------------------------------

describe('refusal and error keys', () => {
  const kinds: TestSpecFailureKind[] = [
    'forbidden',
    'running',
    'disposed',
    'not_approved',
    'report_approved',
    'origin_pending',
    'no_automated',
    'conflict',
    'invalid',
    'not_found',
    'network',
    'other',
  ]

  it.each(kinds)('run / cancel refused as %s', (kind) => {
    const own = kind === 'conflict' || kind === 'invalid' || kind === 'other'
    expect(testSpecFailureKey('run', kind)).toBe(own ? `failure.run_${kind}` : `failure.${kind}`)
    expect(testSpecFailureKey('cancel', kind)).toBe(own ? `failure.cancel_${kind}` : `failure.${kind}`)
    expect(testSpecDeniedBy(kind)).toBe(kind === 'forbidden')
  })

  it.each(kinds)('a failed read as %s', (kind) => {
    expect(testSpecErrorKey(kind)).toBe(kind === 'forbidden' || kind === 'not_found' || kind === 'network' ? `error.${kind}` : 'error.other')
  })
})

// --- Wording -----------------------------------------------------------------------------------------

describe('wording', () => {
  const lookup = (messages: Record<string, unknown>, key: string) =>
    key.split('.').reduce<unknown>((node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined), messages)

  const runStates: TestSpecRunStateKey[] = [
    'queued',
    'preparing',
    'executing',
    'finalizing',
    'cancelling',
    'prepare_failed',
    'execution_failed',
    'cancelled',
    'stale',
    'unchecked',
    'done',
    'none',
  ]
  const kinds: TestSpecFailureKind[] = ['forbidden', 'running', 'disposed', 'not_approved', 'report_approved', 'origin_pending', 'no_automated', 'conflict', 'invalid', 'not_found', 'network', 'other']
  const keys = [
    'title',
    'run',
    'run_hint',
    'cancel',
    'open',
    'open_hint',
    'open_spec',
    'open_report',
    'legacy',
    'none',
    'tally',
    'runnable',
    'spec_errors',
    'counts',
    'progress',
    'gate.passed',
    'gate.blocked',
    'history.title',
    'history.empty',
    ...runStates.map((key) => `state.${key}`),
    ...['running', 'cancelling', 'passed', 'failed', 'cancelled', null].map(testSpecStatusKey),
    ...['PASS', 'FAIL', 'BLOCKED', 'NOT_RUN', null].map(testSpecVerdictKey),
    'reason.read_only_conflict',
    'reason.read_only_revision',
    'reason.disposed',
    'reason.ai_running',
    'reason.forbidden',
    'reason.report_approved',
    'reason.not_approved',
    'reason.no_automated',
    'reason.run_active',
    'reason.cancelling',
    'reason.busy',
    ...kinds.flatMap((kind) => [testSpecFailureKey('run', kind), testSpecFailureKey('cancel', kind), testSpecErrorKey(kind)]),
  ]

  it.each(['ko', 'en', 'ja'] as const)('every key the rules and the section use has %s wording', (locale) => {
    const missing = keys.filter((key) => typeof lookup(testSpecMessages[locale] as Record<string, unknown>, key) !== 'string')
    expect(missing).toEqual([])
  })

  it('the shared read-only reason key names (D#1 §8.3 rule 6-2)', () => {
    expect(testSpecMessages.ko.reason.read_only_conflict).toBeTruthy()
    expect(testSpecMessages.ko.reason.read_only_revision).toBeTruthy()
  })
})
