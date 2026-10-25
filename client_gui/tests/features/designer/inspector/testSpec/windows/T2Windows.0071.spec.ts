import { defineComponent, h, shallowRef, watchEffect } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BriefWindow from '@/features/designer/inspector/features/testSpec/windows/BriefWindow.vue'
import ScenarioWindow from '@/features/designer/inspector/features/testSpec/windows/ScenarioWindow.vue'
import CaseWindow from '@/features/designer/inspector/features/testSpec/windows/CaseWindow.vue'
import { caseDraft, mergeCase, scenarioSnapshot } from '@/features/designer/inspector/features/testSpec/windows/scenarioWindowModel'
import { useTestCard, type TestCardContext, type TestCardWindowKind } from '@/features/designer/inspector/features/testSpec/useTestCard'
import { testCardSessionKey } from '@/features/designer/inspector/features/testSpec/testCardSession'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n, targetOf } from '../../support/inspectorSupport'
import { TS_DOC, TS_NODE, testSelection, tsDocument, liveRun } from '../testSpecFixtures'

const mocks = vi.hoisted(() => ({ read: vi.fn(), node: vi.fn(), choices: vi.fn(), setting: vi.fn(), guard: vi.fn(), activity: vi.fn(), save: vi.fn(), rawRead: vi.fn(), rawSave: vi.fn(), ai: vi.fn(), history: vi.fn(), briefRead: vi.fn(), briefSave: vi.fn() }))
vi.mock('@/features/document', async (original) => {
  const actual = await original<typeof import('@/features/document')>()
  return { ...actual, testSpecRead: mocks.read, settingsNodeRead: mocks.node, settingsChoicesRead: mocks.choices, settingsNodeWrite: mocks.setting,
    reviewDocumentRead: mocks.guard, reviewActivityRead: mocks.activity, testSpecSave: mocks.save, testBriefRead: mocks.briefRead, testBriefSave: mocks.briefSave,
    documentContentRead: mocks.rawRead, documentContentSave: mocks.rawSave, documentHistoryRead: mocks.history }
})
vi.mock('@/features/designer/inspector/openDocument', async (original) => ({ ...await original<typeof import('@/features/designer/inspector/openDocument')>(), openDocumentAi: (...args: unknown[]) => mocks.ai(...args) }))
const { toTestSpecView, toReviewDocument, toReviewActivity, toSettingsNode, toSettingsChoices, toDocumentHistory, testSpecCasePayload } = await vi.importActual<typeof import('@/features/document')>('@/features/document')
const i18n = prepareInspectorI18n()
const base = targetOf(testSelection('graph', TS_NODE))
const makeTarget = (key = 'gn_a', id = TS_DOC): InspectorTarget => ({ ...base, nodeId: key, nodeType: 'document', cardDocType: 'TS', docId: null, docType: 'TS',
  cardDocuments: [{ slot: 'scenario', docType: 'TS', docId: id, title: 'Scenario', author: 'human', status: 'open', docReviewStatus: 'approved' }] })
const fullCases = () => [
  { case_id: 'TC-1', title: 'Sort', category: 'boundary', requirement: 'R §1', execution_mode: 'automated', required: true,
    precondition: null, input: 'hidden input\nwith spaces  ', procedure: '1. Run\n2. Check', expected: 'Sorted', check_points: 'hidden checks', automation_ref: 'tests/sort.py::test_sort', test_assets: 'fixtures/data.json' },
  { case_id: 'TC-2', title: 'Manual', category: 'regression', requirement: 'R §2', execution_mode: 'manual', required: false,
    precondition: 'Ready', input: 'other input', procedure: 'Look', expected: 'Visible', check_points: 'other checks', automation_ref: 'keep/old.py', test_assets: 'other.png' },
]
let wrapper: VueWrapper | null = null, context: TestCardContext
const contextRef = shallowRef<TestCardContext | null>(null)
let selected = shallowRef(makeTarget())
let server: Record<string, unknown>, rawSource: string, editable: boolean, disposed: boolean, activity: Record<string, unknown>
const failure = (status: number, error: string, extra = {}) => ({ isAxiosError: true, response: { status, data: { error, ...extra } } })
const defer = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { resolve, promise } }
const Probe = defineComponent({ setup() { const card = useTestCard(() => selected.value); watchEffect(() => { if (card.value) { context = card.value; contextRef.value = card.value } }); return () => h('span') } })
async function render(kind: 'brief' | 'scenario' | 'case') {
  const component = { brief: BriefWindow, scenario: ScenarioWindow, case: CaseWindow }[kind]
  wrapper = mount(defineComponent({ setup: () => () => h('div', [h(Probe), contextRef.value?.windows[kind].open ? h(component, { key: testCardSessionKey(selected.value), target: selected.value }) : null]) }),
    { global: { plugins: [i18n, createPinia()], stubs: { teleport: true } }, attachTo: document.body })
  await flushPromises()
  context.openWindow(kind, kind === 'case' ? 'TC-1' : null)
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  Object.values(mocks).forEach(mock => mock.mockReset())
  context = undefined as unknown as TestCardContext
  contextRef.value = null
  i18n.global.locale.value = 'ko'; selected = shallowRef(makeTarget())
  server = tsDocument({ title: 'Keep H1', intro: 'Keep intro', cases: fullCases() }); rawSource = '---\ntest_contract_version: 2\n---\n# Keep H1\n\n## 시험 사양\n...\n\n## Notes\nPreserve me  \n'
  editable = true; disposed = false; activity = { active: false }
  mocks.read.mockImplementation(async () => toTestSpecView(server))
  mocks.node.mockImplementation(async () => toSettingsNode({ flow_id: base.flowId, node_key: selected.value.nodeId, card_kind: 'test', locked: false, fields: { instruction: { value: 'use', choices: ['use', 'omit'] }, run_ai: { value: 'writer' } } }))
  mocks.choices.mockResolvedValue(toSettingsChoices({ providers: [{ id: 'writer', name: 'Writer AI' }] }))
  mocks.guard.mockImplementation(async (id: string) => toReviewDocument({ doc_id: id, is_editable: editable, group_disposed: disposed }))
  mocks.activity.mockImplementation(async () => toReviewActivity(activity))
  mocks.history.mockResolvedValue(toDocumentHistory({ doc_id: TS_DOC, ai_review_history: [{ id: 1, verdict: 'issues', comment: 'Review detail', reviewed_at: '2026-10-01', findings: [{ locus: 'TC-1', note: 'Finding detail' }] }], rejection_history: [{ id: 2, reason: 'Rejected detail', ai_response: 'Response detail' }] }))
  mocks.save.mockResolvedValue(undefined)
  mocks.rawRead.mockImplementation(async (docId: string) => ({ docId, content: rawSource, missing: false }))
  mocks.rawSave.mockResolvedValue(undefined)
  mocks.briefRead.mockResolvedValue({ text: '', content: rawSource })
  mocks.briefSave.mockImplementation(async (_id: string, text: string) => ({ text, content: rawSource + text }))
})
afterEach(() => { wrapper?.unmount(); wrapper = null; document.body.innerHTML = ''; vi.useRealTimers() })

describe('0071 T2 preservation model', () => {
  it('merges only the selected case and retains every hidden field, null and other case', () => {
    const cases = toTestSpecView(server).details!.cases
    const draft = caseDraft(cases[0], cases, 'Keep H1')
    draft.title = 'Edited'; draft.procedure = 'Updated'
    const payload = mergeCase(cases, draft)
    expect(payload[0]).toEqual({ ...fullCases()[0], title: 'Edited', procedure: 'Updated' })
    expect(payload[1]).toEqual(fullCases()[1]); expect(payload[0].precondition).toBeNull()
  })
  it('preserves existing external modes and required unless explicitly changed', () => {
    server.cases = fullCases().map(c => ({ ...c, execution_mode: 'external' }))
    const cases = toTestSpecView(server).details!.cases, draft = caseDraft(cases[0], cases, 'Keep H1')
    draft.title = 'Edited'
    expect(mergeCase(cases, draft)[0]).toMatchObject({ execution_mode: 'external', required: true, automation_ref: fullCases()[0].automation_ref })
    draft.execution_mode = 'manual'; draft.required = false; draft.automation_ref = ''
    expect(mergeCase(cases, draft)[0]).toMatchObject({ execution_mode: 'manual', required: false, automation_ref: fullCases()[0].automation_ref })
  })
  it('a hidden field or title change changes the original snapshot', () => {
    const cases = toTestSpecView(server).details!.cases
    const snapshot = scenarioSnapshot(cases, 'Keep H1')
    expect(scenarioSnapshot(cases, 'Other H1')).not.toBe(snapshot)
    expect(scenarioSnapshot([{ ...cases[0], fields: { ...cases[0].fields, testAssets: 'new' } }, cases[1]], 'Keep H1')).not.toBe(snapshot)
  })
})

describe('0071 T2 brief window', () => {
  it('renders the writer and saves the instruction through the document API', async () => {
    const w = await render('brief')
    expect(w.text()).toContain('Writer AI'); expect(w.get('[data-test="brief-count"]').text()).toBe('0/20000자')
    await w.get('[data-test="brief-text"]').setValue('abc')
    expect(w.get('[data-test="brief-count"]').text()).toBe('3/20000자')
    expect(w.get('[data-test="brief-text"]').attributes('maxlength')).toBe('20000')
    expect(w.get('[data-test="brief-save"]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-test="brief-rewrite"]').attributes('disabled')).toBeUndefined()
    await w.get('[data-test="brief-save"]').trigger('click'); await flushPromises()
    expect(mocks.briefSave).toHaveBeenCalledWith(TS_DOC, 'abc', rawSource)
    expect(context.windows.brief.open).toBe(false)
    context.openWindow('brief'); await flushPromises()
    expect(w.get('[data-test="brief-text"]').element).toHaveProperty('value', 'abc')
    expect(mocks.save).not.toHaveBeenCalled(); expect(mocks.ai).not.toHaveBeenCalled()
  })
  it('preserves its draft, shell position and minimized state across close and reopen', async () => {
    const w = await render('brief')
    await w.get('[data-test="brief-text"]').setValue('Retained')
    await w.get('.ui-modeless-titlebar').trigger('mousedown', { clientX: 180, clientY: 150 })
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 230, clientY: 190 }))
    document.dispatchEvent(new MouseEvent('mouseup'))
    await w.get('button[aria-label="Minimize"]').trigger('click')
    expect(context.windows.brief.minimized).toBe(true)
    context.closeWindow('brief'); await flushPromises()
    expect(context.windows.brief.geometry.x).toBe(230)
    context.openWindow('brief'); await flushPromises()
    expect(context.windows.brief.minimized).toBe(false)
    expect(w.get('[data-test="brief-text"]').element).toHaveProperty('value', 'Retained')
    expect(w.get('.ui-modeless-panel').attributes('style')).toContain('left: 230px')
  })
})

describe('0071 T2 scenario window', () => {
  it('renders the eight-column table and opens the selected case through the session', async () => {
    const w = await render('scenario')
    expect(w.findAll('thead th')).toHaveLength(8); expect(w.findAll('tbody tr')).toHaveLength(2)
    expect(w.get('[data-case="TC-1"]').text()).toContain('R §1'); expect(w.get('[data-case="TC-2"]').text()).toContain('무시하고 진행')
    await w.findAll('[data-test="scenario-edit"]')[1].trigger('click')
    expect(context.windows.case.open).toBe(true); expect(context.windows.case.caseId).toBe('TC-2')
    expect(w.get('[data-test="scenario-add"]').attributes('disabled')).toBeUndefined()
  })
  it('renders full review findings, rejections and their responses', async () => {
    const w = await render('scenario')
    await w.get('[data-test="scenario-history"]').trigger('click')
    expect(w.text()).toContain('Review detail'); expect(w.text()).toContain('Finding detail')
    expect(w.text()).toContain('Rejected detail'); expect(w.text()).toContain('Response detail')
  })
  it.each(['ko', 'en', 'ja'] as const)('uses translated history verdicts and existing time formatting in %s', async locale => {
    i18n.global.locale.value = locale
    const { documentHistoryWhen } = await import('@/features/documentWindow')
    const at = '2026-10-01T12:34:56+09:00'
    mocks.history.mockResolvedValue(toDocumentHistory({ doc_id: TS_DOC,
      ai_review_history: ['pass', 'hold', 'issues'].map((verdict, id) => ({ id, verdict, reviewed_at: at, findings: [] })),
      rejection_history: [{ id: 9, reason: 'Rejected detail', rejected_at: at }] }))
    const w = await render('scenario'); await w.get('[data-test="scenario-history"]').trigger('click')
    expect(w.findAll('[data-test="scenario-history-verdict"]').map(item => item.text())).toEqual(
      ['pass', 'hold', 'issues'].map(key => i18n.global.t(`gui.document_window.history.verdict.${key}`, { n: 0 })))
    expect(w.findAll('[data-test="scenario-history-time"]').map(item => item.text())).toEqual(Array(3).fill(documentHistoryWhen(at)))
    expect(w.text()).toContain(documentHistoryWhen(at)); expect(w.text()).not.toContain(at)
    expect(w.text()).not.toContain('gui.document_window.')
  })
  it.each(['ko', 'en', 'ja'] as const)('omits zero summary tokens and joins positive counts in %s', async locale => {
    i18n.global.locale.value = locale
    server.cases = [fullCases()[0]]
    const w = await render('scenario')
    const count = i18n.global.t('gui.inspector.testSpec.window.scenario.count', { n: 1 })
    expect(w.get('h3 small').text()).toBe(count)
    server.cases = [{ ...fullCases()[0], required: false, automation_ref: '' }]
    await context.reload(); await flushPromises()
    const token = (key: string) => i18n.global.t(`gui.inspector.testSpec.window.scenario.summary_${key}`, { n: 1 })
    expect(w.get('h3 small').text()).toBe([count, token('ignore'), token('noCode')].join(' · '))
    server.cases = [{ ...fullCases()[1], required: true }]
    await context.reload(); await flushPromises()
    expect(w.get('h3 small').text()).toBe([count, token('human')].join(' · '))
  })
  it('requires confirmation before opening the existing AI rewrite dialog', async () => {
    const w = await render('scenario')
    await w.get('[data-test="scenario-rewrite"]').trigger('click')
    expect(mocks.ai).not.toHaveBeenCalled(); expect(w.get('[data-test="scenario-rewrite-confirm"]').text()).toContain('사람 작성 —개 유지')
    await w.get('[data-test="scenario-rewrite-submit"]').trigger('click')
    expect(mocks.ai).toHaveBeenCalledOnce(); expect(mocks.ai.mock.calls[0][0]).toBe(TS_DOC)
  })
  it('disables whole rewrite during case execution and retains the window with placeholders while writing', async () => {
    server.active_run = liveRun('retry', { selected_case_ids: ['TC-1'] })
    const w = await render('scenario')
    expect(w.get('[data-test="scenario-rewrite"]').attributes('disabled')).toBeDefined()
    context.writing.value = 'rewrite-all'; await flushPromises()
    expect(w.find('[data-test="scenario-window"]').exists()).toBe(true); expect(w.findAll('[data-test="scenario-writing"]').length).toBeGreaterThan(0)
  })
  it('hides stale result glyphs and uses the active execution glyph for code being run', async () => {
    server.basis_valid = false; server.basis_verdict = { state: 'stale' }
    server.effective_result = { summary: { overall: 'PASS' }, cases: [{ case_id: 'TC-1', status: 'PASS' }] }
    const w = await render('scenario')
    expect(w.find('[data-case="TC-1"] [data-test="test-mark"]').exists()).toBe(false)
    server.active_run = liveRun('running-all', { selected_case_ids: ['TC-1', 'TC-2'] }); await context.reload(); await flushPromises()
    expect(w.get('[data-case="TC-1"] [data-test="test-mark"]').attributes('data-key')).toBe('test.pending')
    expect(w.find('[data-case="TC-2"] [data-test="test-mark"]').exists()).toBe(false)
  })
  it('reads source once, keeps it unchanged on open, and sends the exact edited full body', async () => {
    const w = await render('scenario')
    await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="scenario-raw-save"]').attributes('disabled')).toBeDefined()
    const edited = rawSource + '\n## Extra\nText  \n'
    await w.get('[data-test="scenario-raw-text"]').setValue(edited)
    await w.get('[data-test="scenario-raw-save"]').trigger('click'); await flushPromises()
    expect(mocks.rawSave).toHaveBeenCalledWith(TS_DOC, edited); expect(w.find('[data-test="scenario-raw-overlay"]').exists()).toBe(true)
    expect(w.get('[data-test="scenario-raw-save"]').attributes('disabled')).toBeDefined()
  })
  it('keeps raw draft and displays normalized refusal on 422, then allows retry', async () => {
    mocks.rawSave.mockRejectedValueOnce(failure(422, 'document_not_editable'))
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    await w.get('[data-test="scenario-raw-text"]').setValue('keep draft')
    await w.get('[data-test="scenario-raw-save"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="scenario-raw-text"]').element).toHaveProperty('value', 'keep draft')
    expect(w.get('[data-test="raw-refusal"]').text()).toBeTruthy(); expect(context.refusal.value?.operation).toBe('rawSave')
  })
  it('retains format errors above the raw editor after a successful raw save', async () => {
    mocks.rawSave.mockImplementation(async () => { server.errors = [{ code: 'missing', case_id: 'TC-1', field: 'title', message: 'Title required' }] })
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    await w.get('[data-test="scenario-raw-text"]').setValue('invalid new raw')
    await w.get('[data-test="scenario-raw-save"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="raw-errors"]').text()).toContain('TC-1 · title · Title required')
    expect(w.find('[data-test="scenario-raw-overlay"]').exists()).toBe(true)
  })
  it('keeps the raw editor repairable when malformed source produces errors and zero cases', async () => {
    mocks.rawSave.mockImplementation(async () => { server.cases = []; server.errors = [{ code: 'invalid', field: 'cases', message: 'Invalid specification' }] })
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    await w.get('[data-test="scenario-raw-text"]').setValue('malformed')
    await w.get('[data-test="scenario-raw-save"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="scenario-raw-overlay"]').exists()).toBe(true)
    expect(w.get('[data-test="raw-errors"]').text()).toContain('Invalid specification')
    expect(context.can('rawSave').enabled).toBe(true)
    expect(context.openReason('scenario')).toBeNull()
    expect(context.windows.case.open).toBe(false)
    context.closeWindow('scenario'); await flushPromises()
    expect(context.openWindow('scenario')).toBe(true)
    expect(context.can('rawSave').reason).toBeNull()
  })
  it('keeps the scenario available to repair a later malformed source', async () => {
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    await w.get('[data-test="scenario-raw-text"]').setValue('valid updated source')
    await w.get('[data-test="scenario-raw-save"]').trigger('click'); await flushPromises()
    server.cases = []; server.errors = [{ code: 'invalid', message: 'Later external error' }]
    await context.reload(); await flushPromises()
    expect(context.windows.scenario.open).toBe(true)
    expect(context.can('rawSave').reason).toBeNull()
  })
  it.each(['missing_spec_section', 'unsupported_contract'])('keeps source authoring available for %s', async code => {
    mocks.rawSave.mockImplementation(async () => { server.cases = []; server.errors = [{ code, message: code }] })
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    await w.get('[data-test="scenario-raw-text"]').setValue('instructions only')
    await w.get('[data-test="scenario-raw-save"]').trigger('click'); await flushPromises()
    expect(context.windows.scenario.open).toBe(true)
    expect(context.can('rawSave').reason).toBeNull()
    expect((context.drafts['t2:raw'] as { text: string }).text).toBe('instructions only')
  })
  it('detects a source conflict without sending PATCH or losing the draft', async () => {
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    await w.get('[data-test="scenario-raw-text"]').setValue('my raw draft'); rawSource += 'Someone else'
    await w.get('[data-test="scenario-raw-save"]').trigger('click'); await flushPromises()
    expect(mocks.rawSave).not.toHaveBeenCalled(); expect(w.text()).toContain('원문이 변경되었습니다')
    expect(w.get('[data-test="scenario-raw-text"]').element).toHaveProperty('value', 'my raw draft')
    await w.get('[data-test="raw-reload"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="scenario-raw-text"]').element).toHaveProperty('value', rawSource)
  })
  it('prevents a pending raw preflight from saving after a card switch', async () => {
    const late = defer<{ docId: string; content: string; missing: boolean }>()
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    await w.get('[data-test="scenario-raw-text"]').setValue('draft before switch')
    mocks.rawRead.mockImplementationOnce(() => late.promise)
    await w.get('[data-test="scenario-raw-save"]').trigger('click')
    selected.value = makeTarget('gn_b', 'flowgate.v02.0048.0088-TS'); await flushPromises()
    late.resolve({ docId: TS_DOC, content: rawSource, missing: false }); await flushPromises()
    expect(mocks.rawSave).not.toHaveBeenCalled()
  })
  it('offers a retry for raw read failures and never enables save for missing source', async () => {
    mocks.rawRead.mockRejectedValueOnce(new Error('offline'))
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="scenario-raw-text"]').exists()).toBe(false)
    mocks.rawRead.mockResolvedValueOnce({ docId: TS_DOC, content: '', missing: true })
    await w.get('[data-test="inspector-pane-retry"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="scenario-raw-save"]').attributes('disabled')).toBeDefined()
    await w.get('[data-test="inspector-pane-retry"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="scenario-raw-text"]').element).toHaveProperty('value', rawSource)
  })
  it('isolates a late raw response from a newly selected card', async () => {
    const late = defer<{ docId: string; content: string; missing: boolean }>()
    mocks.rawRead.mockImplementationOnce(() => late.promise)
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click')
    const old = context
    selected.value = makeTarget('gn_b', 'flowgate.v02.0048.0088-TS'); await flushPromises()
    late.resolve({ docId: TS_DOC, content: 'old response', missing: false }); await flushPromises()
    expect(context).not.toBe(old); expect(context.drafts['t2:raw']).toBeUndefined()
    expect((old.drafts['t2:raw'] as { text: string }).text).toBe('')
  })
})

describe('0071 T2 case window', () => {
  it('renders the mapped fields and saves a whole list with H1, hidden fields and other cases intact', async () => {
    const w = await render('case')
    expect(w.get('[data-test="case-code"]').element).toHaveProperty('value', fullCases()[0].automation_ref)
    await w.get('[data-test="case-title"]').setValue('Edited title')
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(TS_DOC, [{ ...fullCases()[0], title: 'Edited title' }, fullCases()[1]], 'Keep H1')
    expect(mocks.save.mock.calls[0]).toHaveLength(3); expect(context.windows.case.open).toBe(false)
    expect(document.body.textContent).toContain('케이스 저장됨')
  })
  it('requires the fields the server requires and supports custom requirements', async () => {
    const w = await render('case')
    await w.get('[data-test="case-title"]').setValue('')
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeDefined()
    await w.get('[data-test="case-title"]').setValue('Title')
    await w.get('[data-test="case-procedure"]').setValue('  ')
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('제목 · 근거 · 절차 · 기대 결과를 입력해 주세요')
    await w.get('[data-test="case-procedure"]').setValue('1. Run')
    await w.get('[data-test="case-requirement-select"]').setValue('__custom')
    await w.get('[data-test="case-requirement"]').setValue('R §99')
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeUndefined()
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save.mock.calls[0][1][0].requirement).toBe('R §99')
  })
  it.each(['external-delete', 'raw-save', 'rewrite-all', 'last-case'] as const)('closes a disappeared case, retains its draft and notifies after %s', async source => {
    const w = await render('case'); await w.get('[data-test="case-title"]').setValue('Keep missing case draft')
    const draft = context.drafts['t2:case:TC-1']
    if (source === 'rewrite-all') context.writing.value = 'rewrite-all'
    const disappear = async () => { server.cases = source === 'last-case' ? [] : [fullCases()[1]] }
    if (source === 'raw-save') await context.changeDocument('rawSave', disappear)
    else { await disappear(); await context.reload() }
    if (source === 'rewrite-all') {
      expect(context.windows.case.open).toBe(true)
      context.writing.value = null
      await context.reload()
    }
    await flushPromises()
    expect(context.windows.case.open).toBe(false)
    expect(w.find('[data-test="case-window"]').exists()).toBe(false)
    expect(w.find('[data-test="inspector-pane-state"]').exists()).toBe(false)
    expect(context.drafts['t2:case:TC-1']).toBe(draft)
    expect(draft).toMatchObject({ title: 'Keep missing case draft' })
    expect(document.body.textContent).toContain('TC-1 케이스가 사라져 창을 닫았습니다. 미저장 초안은 보관했습니다.')
    expect(mocks.save).not.toHaveBeenCalled()
  })
  it('keeps case windows and drafts through loading, failed and incomplete reads', async () => {
    const w = await render('case'); await w.get('[data-test="case-title"]').setValue('Keep draft')
    const late = defer<ReturnType<typeof toTestSpecView>>()
    mocks.read.mockImplementationOnce(() => late.promise)
    const pending = context.reload(); await flushPromises()
    expect(context.windows.case.open).toBe(true)
    late.resolve(toTestSpecView({ doc_id: TS_DOC, contract_version: 2 })); await pending; await flushPromises()
    expect(context.windows.case.open).toBe(true)
    mocks.read.mockRejectedValueOnce(new Error('offline')); await context.reload(); await flushPromises()
    expect(context.windows.case.open).toBe(true)
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'Keep draft')
  })
  it.each(['rewrite-all', 'initial'] as const)('keeps unsaved inputs visible and disabled during %s', async writing => {
    const w = await render('case'); await w.get('[data-test="case-title"]').setValue('Visible draft')
    context.writing.value = writing; await flushPromises()
    expect(w.find('[data-test="case-writing"]').exists()).toBe(false)
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'Visible draft')
    expect(w.get('[data-test="case-title"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="case-procedure"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeDefined()
  })
  it('uses a placeholder only for the case being rewritten and scopes connection reasons to controls', async () => {
    const w = await render('case')
    expect(w.get('[data-test="case-rewrite"]').attributes('title')).toBe('연결 예정')
    expect(w.text()).not.toContain('연결 예정')
    context.writing.value = 'rewrite-case'; context.writingCaseId.value = 'TC-2'; await flushPromises()
    expect(w.find('[data-test="case-writing"]').exists()).toBe(false)
    expect(w.get('[data-test="case-title"]').attributes('disabled')).toBeDefined()
    context.writingCaseId.value = 'TC-1'; await flushPromises()
    expect(w.find('[data-test="case-writing"]').exists()).toBe(true)
    expect(w.find('[data-test="case-title"]').exists()).toBe(false)
  })
  it('retains automation_ref when verification changes to manual', async () => {
    const w = await render('case')
    await w.get('[data-test="case-mode"]').findAll('button').find(b => b.text() === '사람 확인')!.trigger('click')
    expect(w.find('[data-test="case-code"]').exists()).toBe(false)
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save.mock.calls[0][1][0]).toMatchObject({ execution_mode: 'manual', automation_ref: fullCases()[0].automation_ref, required: true })
  })
  it('requires two actions to delete and preserves the remaining case in full', async () => {
    const w = await render('case')
    context.openWindow('case', 'TC-2'); await flushPromises()
    await w.get('[data-test="case-delete"]').trigger('click')
    expect(mocks.save).not.toHaveBeenCalled(); expect(w.get('[data-test="case-delete-confirm"]').text()).toContain('TC-2')
    await w.get('[data-test="case-delete-submit"]').trigger('click'); await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(TS_DOC, [fullCases()[0]], 'Keep H1')
    expect(context.windows.case.open).toBe(false); expect(document.body.textContent).toContain('TC-2 삭제됨')
  })
  it('resets delete confirmation when another case is selected', async () => {
    const w = await render('case'); await w.get('[data-test="case-delete"]').trigger('click')
    context.openWindow('case', 'TC-2'); await flushPromises()
    expect(w.find('[data-test="case-delete-confirm"]').exists()).toBe(false)
    expect(mocks.save).not.toHaveBeenCalled()
  })
  it('keeps a rejected draft, field errors and the case window visible', async () => {
    mocks.save.mockRejectedValueOnce(failure(422, 'invalid_spec', { errors: [{ code: 'required', case_id: 'TC-1', field: 'title', message: 'Invalid title' }] }))
    const w = await render('case'); await w.get('[data-test="case-title"]').setValue('Keep me')
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(context.windows.case.open).toBe(true); expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'Keep me')
    expect(w.get('[data-test="case-refusal"]').text()).toBeTruthy(); expect(w.text()).toContain('Invalid title')
  })
  it('refreshes before saving and detects a hidden-field change without sending PUT', async () => {
    const w = await render('case'); await w.get('[data-test="case-title"]').setValue('My draft')
    server.cases = fullCases().map((item, i) => i === 1 ? { ...item, test_assets: 'Changed elsewhere' } : item)
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save).not.toHaveBeenCalled(); expect(w.find('[data-test="case-conflict"]').exists()).toBe(true)
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'My draft')
    await w.get('[data-test="case-reload"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'Sort')
  })
  it('retains separate drafts when switching case IDs and closing/reopening', async () => {
    const w = await render('case'); await w.get('[data-test="case-title"]').setValue('First draft')
    context.openWindow('case', 'TC-2'); await flushPromises()
    await w.get('[data-test="case-title"]').setValue('Second draft')
    context.closeWindow('case'); await flushPromises(); context.openWindow('case', 'TC-1'); await flushPromises()
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'First draft')
    context.openWindow('case', 'TC-2'); await flushPromises()
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'Second draft')
  })
  it('does not submit a case when its preflight read fails', async () => {
    const w = await render('case'); await w.get('[data-test="case-title"]').setValue('Retained')
    mocks.read.mockRejectedValueOnce(new Error('offline'))
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save).not.toHaveBeenCalled(); expect(w.find('[data-test="inspector-pane-retry"]').exists()).toBe(true)
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'Retained')
  })
  it.each(['readOnly', 'disposed', 'ai', 'approved', 'writing', 'busy'] as const)('blocks case mutations but keeps reading and window controls for %s', async guard => {
    const w = await render('case')
    if (guard === 'readOnly') selected.value = { ...selected.value, readOnly: 'revision' }
    if (guard === 'disposed') disposed = true
    if (guard === 'ai') activity = { active: true, doc_ref: 'other', action_scope: 'review' }
    if (guard === 'approved') editable = false
    if (guard === 'writing') context.writing.value = 'rewrite-all'
    if (guard === 'busy') context.busy.value = 'rawSave'
    if (['disposed', 'ai', 'approved'].includes(guard)) await context.reload()
    await flushPromises()
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="case-delete"]').attributes('disabled')).toBeDefined()
    expect(w.get('button[aria-label="Close"]').attributes('disabled')).toBeUndefined()
    expect(mocks.save).not.toHaveBeenCalled()
  })
  it('shows document permission refusal and retains the draft after 403', async () => {
    mocks.save.mockRejectedValueOnce(failure(403, 'permission_denied'))
    const w = await render('case'); await w.get('[data-test="case-title"]').setValue('Retain')
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'Retain')
    expect(context.refusal.value?.kind).toBe('forbidden')
  })
})

describe('0071 T2 theme and locale contract', () => {
  it.each(['ko', 'en', 'ja'] as const)('renders all windows in %s with no untranslated keys and token-backed shell', async locale => {
    i18n.global.locale.value = locale
    const w = await render('scenario')
    expect(w.text()).not.toContain('gui.inspector.testSpec.')
    const panel = w.get('.ui-modeless-panel')
    expect(panel.classes()).not.toContain('light'); expect(panel.attributes('style')).not.toContain('#fff')
    context.closeWindow('scenario'); await flushPromises(); wrapper?.unmount(); wrapper = null; context = undefined as unknown as TestCardContext
    const c = await render('case'); expect(c.text()).not.toContain('gui.inspector.testSpec.')
  })
})

describe('T0015 authoring regression', () => {
  it('keeps a refused instruction draft open and never reports it saved', async () => {
    mocks.briefSave.mockRejectedValue(failure(409, 'content_conflict'))
    const w = await render('brief')
    await w.get('[data-test="brief-text"]').setValue('keep my draft')
    await w.get('[data-test="brief-save"]').trigger('click'); await flushPromises()
    expect(context.windows.brief.open).toBe(true)
    expect(w.get('[data-test="brief-text"]').element).toHaveProperty('value', 'keep my draft')
    expect(mocks.ai).not.toHaveBeenCalled()
  })
  it('adds a complete case, preserving existing hidden fields and reopening the saved case', async () => {
    const w = await render('case')
    context.closeWindow('case'); context.openWindow('case', '__new__'); await flushPromises()
    await w.get('[data-test="case-title"]').setValue('New case')
    await w.get('[data-test="case-requirement"]').setValue('R new')
    await w.get('[data-test="case-procedure"]').setValue('Inspect')
    await w.get('[data-test="case-expected"]').setValue('Visible')
    mocks.save.mockImplementation(async (_id, payload) => { server.cases = payload })
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    const payload = mocks.save.mock.calls[0][1]
    expect(payload.slice(0, 2)).toEqual(fullCases())
    expect(payload[2]).toMatchObject({ case_id: 'TC-3', title: 'New case', requirement: 'R new', procedure: 'Inspect', expected: 'Visible', check_points: 'Visible' })
    expect(context.windows.case.open).toBe(false)
    context.openWindow('case', 'TC-3'); await flushPromises()
    expect(w.get('[data-test="case-title"]').element).toHaveProperty('value', 'New case')
  })
})

describe('TR0016 rev1 verification modes and the case save contract', () => {
  const segmentOn = (w: VueWrapper, name: string) => w.get(`[data-test="${name}"]`).findAll('button').find(b => b.attributes('aria-checked') === 'true')!.text()
  const pick = (w: VueWrapper, name: string, label: string) => w.get(`[data-test="${name}"]`).findAll('button').find(b => b.text() === label)!.trigger('click')
  const persist = () => mocks.save.mockImplementation(async (_id, payload) => { server.cases = payload })

  it.each([['코드 실행', 'automated'], ['AI 확인', 'ai'], ['사람 확인', 'manual']] as const)('selects, saves and reopens %s', async (label, mode) => {
    persist()
    const w = await render('case')
    context.openWindow('case', 'TC-2'); await flushPromises()
    expect(segmentOn(w, 'case-mode')).toBe('사람 확인')
    if (mode === 'manual') await pick(w, 'case-mode', 'AI 확인')
    await pick(w, 'case-mode', label)
    expect(segmentOn(w, 'case-mode')).toBe(label)
    expect(w.find('[data-test="case-code"]').exists()).toBe(mode === 'automated')
    expect(w.find('[data-test="case-ai-hint"]').exists()).toBe(mode === 'ai')
    if (mode === 'automated') await w.get('[data-test="case-code"]').setValue('tests/test_manual.py::test_visible')
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    const payload = mocks.save.mock.calls[0][1]
    expect(payload[0]).toEqual(fullCases()[0])
    expect(payload[1]).toMatchObject({ case_id: 'TC-2', execution_mode: mode, required: false,
      automation_ref: mode === 'automated' ? 'tests/test_manual.py::test_visible' : 'keep/old.py' })
    expect(context.windows.case.open).toBe(false)
    context.openWindow('case', 'TC-2'); await flushPromises()
    expect(segmentOn(w, 'case-mode')).toBe(label)
  })
  it('shows the AI check in the scenario table and summary', async () => {
    server.cases = [fullCases()[0], { ...fullCases()[1], execution_mode: 'ai' }]
    const w = await render('scenario')
    expect(w.get('[data-case="TC-2"]').text()).toContain('AI 확인')
    expect(w.get('h3 small').text()).toContain('AI 확인 1')
  })
  it('starts a new case as required and saves it even when every existing case is optional', async () => {
    server.cases = fullCases().map(c => ({ ...c, required: false })); persist()
    const w = await render('case')
    context.closeWindow('case'); context.openWindow('case', '__new__'); await flushPromises()
    expect(segmentOn(w, 'case-required')).toBe('다음 단계 중단')
    await w.get('[data-test="case-title"]').setValue('Required by default')
    await w.get('[data-test="case-requirement"]').setValue('R new')
    await w.get('[data-test="case-procedure"]').setValue('Inspect')
    await w.get('[data-test="case-expected"]').setValue('Visible')
    await pick(w, 'case-mode', 'AI 확인')
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save.mock.calls[0][1][2]).toMatchObject({ case_id: 'TC-3', execution_mode: 'ai', required: true, category: 'normal' })
  })
  it('explains and blocks a save that would leave no required case, before any request', async () => {
    const w = await render('case')
    await pick(w, 'case-required', '무시하고 진행')
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('실패 시 「다음 단계 중단」(필수) 케이스가 최소 1개 있어야 저장할 수 있습니다')
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save).not.toHaveBeenCalled()
    await pick(w, 'case-required', '다음 단계 중단')
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeUndefined()
  })
  it('blocks deleting the only required case with the way out', async () => {
    const w = await render('case')
    await w.get('[data-test="case-delete"]').trigger('click')
    expect(w.get('[data-test="case-delete-submit"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="case-delete-required"]').text()).toContain('다른 케이스를 먼저 「다음 단계 중단」으로 바꿔 주세요')
    await w.get('[data-test="case-delete-submit"]').trigger('click'); await flushPromises()
    expect(mocks.save).not.toHaveBeenCalled()
  })
  it('deletes the last case and returns to an authorable empty scenario', async () => {
    server.cases = [fullCases()[0]]
    mocks.save.mockImplementation(async (_id, payload) => { server.cases = payload; server.errors = [{ code: 'no_cases', message: 'No case' }] })
    const w = await render('case')
    await w.get('[data-test="case-delete"]').trigger('click')
    await w.get('[data-test="case-delete-submit"]').trigger('click'); await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(TS_DOC, [], 'Keep H1')
    expect(context.cases.value).toHaveLength(0); expect(context.formatErrors.value).toHaveLength(0)
    expect(context.can('caseAdd').enabled).toBe(true)
    expect(context.openWindow('case', '__new__')).toBe(true)
  })
  it('fills the hidden fields the server requires only when the edited case has none', () => {
    server.cases = [{ ...fullCases()[0], category: null, check_points: null }, fullCases()[1]]
    const cases = toTestSpecView(server).details!.cases
    const draft = caseDraft(cases[0], cases, 'Keep H1')
    draft.title = 'Edited'
    const payload = mergeCase(cases, draft)
    expect(payload[0]).toMatchObject({ title: 'Edited', category: 'normal', check_points: 'Sorted' })
    expect(payload[1]).toEqual(fullCases()[1])
  })
})
