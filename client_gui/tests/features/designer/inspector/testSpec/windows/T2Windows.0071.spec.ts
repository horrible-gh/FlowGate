import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
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
    reviewDocumentRead: mocks.guard, reviewActivityRead: mocks.activity, documentHistoryRead: mocks.history,
    // flowgate.v02.0127 T#5: the windows read and save through the card document v2 calls. The bridge below answers them from
    // the spec's own mocks (stored text / revision / case list), so the assertions keep stating what was saved.
    documentContentRead: mocks.rawRead,
    cardDocumentStructuredRead: async (_ctx: unknown, kind: string) => snapshotOf(rawSource, kind === 'ts_brief' ? { text: (await mocks.briefRead()).text } : { title: server.title, cases: server.cases }),
    cardDocumentRead: async () => { const read = await mocks.rawRead(TS_DOC); return read.missing ? { ...snapshotOf('', undefined), content: null, bodyState: 'file_path_missing' } : snapshotOf(read.content, undefined) },
    cardDocumentRevise: (...args: unknown[]) => reviseBridge(...(args as [unknown, { command?: { kind: string; cases?: unknown[]; title?: string; text?: string }; content?: string; expectedRevision: number }])) }
})
const snapshotOf = (content: string, projection: unknown) => ({ docId: TS_DOC, revisionNo: 4, updatedAt: 't', content, bodyState: 'readable', line: {}, ...(projection !== undefined ? { projection } : {}) })
/** The old v1 style failures of this spec, as the /api/v2 answers the client now reads (`error.code` / `params`). */
function toV2(error: any) {
  const data = error?.response?.data ?? {}
  const code = typeof data.error === 'string' ? data.error : null
  if (!code) return error
  const mapped: Record<string, string> = { invalid_spec: 'invalid_content', document_not_editable: 'not_editable', content_conflict: 'revision_conflict' }
  const params = code === 'invalid_spec' ? { errors: [{ field: 'content', reason: 'invalid_format', validation: { error: 'invalid_spec', errors: data.errors ?? [] } }] } : {}
  return { ...error, response: { ...error.response, data: { ok: false, error: { code: mapped[code] ?? code, params } } } }
}
async function reviseBridge(_ctx: unknown, request: { command?: { kind: string; cases?: unknown[]; title?: string; text?: string }; content?: string; expectedRevision: number }) {
  try {
    if (request.command?.kind === 'ts_spec') await mocks.save(TS_DOC, request.command.cases, request.command.title ?? null)
    else if (request.command?.kind === 'ts_brief') await mocks.briefSave(TS_DOC, request.command.text, rawSource)
    else await mocks.rawSave(TS_DOC, request.content)
  } catch (error) { throw toV2(error) }
  return { ok: true, doc_id: TS_DOC, revision_no: request.expectedRevision + 1, updated_at: 'u', replayed: false, line: {} }
}
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
    draft.execution_mode = 'manual'; draft.required = false
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
  // flowgate.v02.0094: the window only saves — no writer AI name, no length line, no 「저장 후 시나리오 다시 작성」.
  it('saves the instruction through the document API and starts no AI writing', async () => {
    const w = await render('brief')
    expect(w.text()).not.toContain('Writer AI'); expect(w.find('[data-test="brief-count"]').exists()).toBe(false)
    expect(w.find('[data-test="brief-rewrite"]').exists()).toBe(false)
    await w.get('[data-test="brief-text"]').setValue('abc')
    expect(w.get('[data-test="brief-text"]').attributes('maxlength')).toBe('20000')
    expect(w.get('[data-test="brief-save"]').attributes('disabled')).toBeUndefined()
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
    await w.get('button[data-modeless-action="minimize"]').trigger('click')
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
  it('renders the case table without the unused author column and opens the selected case through the session', async () => {
    const w = await render('scenario')
    expect(w.findAll('thead th')).toHaveLength(7); expect(w.findAll('tbody tr')).toHaveLength(2)
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
    server.cases = [fullCases()[0]]; server.case_capabilities = { 'TC-1': 'case_selectable' }
    const w = await render('scenario')
    const count = i18n.global.t('gui.inspector.testSpec.window.scenario.count', { n: 1 })
    expect(w.get('h3 small').text()).toBe(count)
    server.cases = [{ ...fullCases()[0], required: false, automation_ref: '' }]; server.case_capabilities = { 'TC-1': 'unbound' }
    await context.reload(); await flushPromises()
    const token = (key: string) => i18n.global.t(`gui.inspector.testSpec.window.scenario.summary_${key}`, { n: 1 })
    expect(w.get('h3 small').text()).toBe([count, token('ignore'), token('noCode')].join(' · '))
    server.cases = [{ ...fullCases()[1], required: true }]
    await context.reload(); await flushPromises()
    expect(w.get('h3 small').text()).toBe([count, token('human')].join(' · '))
  })
  it('opens the implemented AI rewrite dialog directly', async () => {
    const w = await render('scenario')
    await w.get('[data-test="scenario-rewrite"]').trigger('click')
    expect(w.find('[data-test="scenario-rewrite-confirm"]').exists()).toBe(false)
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
  it('a raw save on a stale base is a revision conflict: the draft and its base stay, the latest is shown apart, and only the explicit adoption of the merged text makes a new intent', async () => {
    const w = await render('scenario'); await w.get('[data-test="scenario-raw"]').trigger('click'); await flushPromises()
    await w.get('[data-test="scenario-raw-text"]').setValue('my raw draft'); rawSource += 'Someone else'
    mocks.rawSave.mockRejectedValueOnce(failure(409, 'content_conflict'))
    await w.get('[data-test="scenario-raw-save"]').trigger('click'); await flushPromises()
    expect(mocks.rawSave).toHaveBeenCalledTimes(1); expect(w.text()).toContain('내용이 변경되었습니다')
    expect(w.get('[data-test="scenario-raw-text"]').element).toHaveProperty('value', 'my raw draft')
    const latest = w.get('[data-test="raw-latest"]').element
    const area = (latest.tagName === 'TEXTAREA' ? latest : latest.querySelector('textarea')) as HTMLTextAreaElement
    expect(area.value).toBe(rawSource)
    await w.get('[data-test="raw-adopt-merge"]').trigger('click'); await flushPromises()
    expect(w.find('[data-test="raw-refusal"]').exists()).toBe(false)
    // the latest frontmatter / identity is adopted with the latest revision; only the merged body is the user's
    expect(w.get('[data-test="scenario-raw-text"]').element).toHaveProperty('value', '---
test_contract_version: 2
---
my raw draft')
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
    expect(w.find('[data-test="brief-conflict"]').exists()).toBe(true)
    expect(mocks.ai).not.toHaveBeenCalled()
  })
  it('a resent uncertain brief request is acknowledged as ITS text: the newer draft stays open, unsaved, and goes out with a new key next', async () => {
    mocks.briefSave.mockRejectedValueOnce({ isAxiosError: true })
    const w = await render('brief')
    await w.get('[data-test="brief-text"]').setValue('A')
    await w.get('[data-test="brief-save"]').trigger('click'); await flushPromises()
    expect(context.windows.brief.open).toBe(true)
    await w.get('[data-test="brief-text"]').setValue('B')
    await w.get('[data-test="brief-save"]').trigger('click'); await flushPromises()
    // the server stored the held request A, never B
    expect(mocks.briefSave).toHaveBeenLastCalledWith(TS_DOC, 'A', rawSource)
    expect(context.windows.brief.open).toBe(true)
    expect(w.get('[data-test="brief-text"]').element).toHaveProperty('value', 'B')
    expect(w.get('[data-test="brief-save"]').attributes('disabled')).toBeUndefined()
    // the next explicit save sends B on the confirmed N+1
    await w.get('[data-test="brief-save"]').trigger('click'); await flushPromises()
    expect(mocks.briefSave).toHaveBeenLastCalledWith(TS_DOC, 'B', rawSource)
  })
  it('adds a complete case, preserving existing hidden fields and reopening the saved case', async () => {
    const w = await render('case')
    context.closeWindow('case'); context.openWindow('case', '__new__'); await flushPromises()
    await w.get('[data-test="case-title"]').setValue('New case')
    await w.get('[data-test="case-requirement"]').setValue('R new')
    await w.get('[data-test="case-procedure"]').setValue('Inspect')
    await w.get('[data-test="case-expected"]').setValue('Visible')
    await w.get('[data-test="case-command"]').setValue('node --test tests/new.test.mjs')
    mocks.save.mockImplementation(async (_id, payload) => { server.cases = payload })
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    const payload = mocks.save.mock.calls[0][1]
    expect(payload.slice(0, 2)).toEqual(fullCases())
    expect(payload[2]).toMatchObject({ case_id: 'TC-3', title: 'New case', requirement: 'R new', procedure: 'Inspect', expected: 'Visible', check_points: 'Visible',
      automation_ref: null, run_command: 'node --test tests/new.test.mjs' })
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
    expect(w.find('[data-test="case-command"]').exists()).toBe(mode === 'automated')
    expect(w.find('[data-test="case-ai-hint"]').exists()).toBe(false)
    if (mode === 'automated') await w.get('[data-test="case-command"]').setValue('node --test tests/manual.test.mjs')
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    const payload = mocks.save.mock.calls[0][1]
    expect(payload[0]).toEqual(fullCases()[0])
    expect(payload[1]).toMatchObject({ case_id: 'TC-2', execution_mode: mode, required: false,
      automation_ref: mode === 'automated' ? null : 'keep/old.py', ...(mode === 'automated' ? { run_command: 'node --test tests/manual.test.mjs' } : {}) })
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
  // flowgate.v02.0094 #7: a new case starts on the leftmost choices; a stored case keeps what it has.
  it('starts a new case on 코드 실행 · 무시하고 진행 and saves those defaults', async () => {
    persist()
    const w = await render('case')
    context.closeWindow('case'); context.openWindow('case', '__new__'); await flushPromises()
    expect(segmentOn(w, 'case-mode')).toBe('코드 실행')
    expect(segmentOn(w, 'case-required')).toBe('무시하고 진행')
    expect(w.find('[data-test="case-command"]').exists()).toBe(true)
    await w.get('[data-test="case-title"]').setValue('Defaults')
    await w.get('[data-test="case-command"]').setValue('node --test tests/defaults.test.mjs')
    await w.get('[data-test="case-requirement"]').setValue('R new')
    await w.get('[data-test="case-procedure"]').setValue('Inspect')
    await w.get('[data-test="case-expected"]').setValue('Visible')
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save.mock.calls[0][1].slice(0, 2)).toEqual(fullCases())
    expect(mocks.save.mock.calls[0][1][2]).toMatchObject({ case_id: 'TC-3', execution_mode: 'automated', required: false, category: 'normal', automation_ref: null })
  })
  it('opens a stored case on its own choices, not the new-case defaults', async () => {
    const w = await render('case')
    expect(segmentOn(w, 'case-mode')).toBe('코드 실행'); expect(segmentOn(w, 'case-required')).toBe('다음 단계 중단')
    context.openWindow('case', 'TC-2'); await flushPromises()
    expect(segmentOn(w, 'case-mode')).toBe('사람 확인'); expect(segmentOn(w, 'case-required')).toBe('무시하고 진행')
  })
  it('saves a new optional case in an all-optional scenario without changing its selection', async () => {
    server.cases = fullCases().map(c => ({ ...c, required: false })); persist()
    const w = await render('case')
    context.closeWindow('case'); context.openWindow('case', '__new__'); await flushPromises()
    expect(segmentOn(w, 'case-required')).toBe('무시하고 진행')
    await w.get('[data-test="case-title"]').setValue('Optional by default')
    await w.get('[data-test="case-command"]').setValue('node --test tests/optional.test.mjs')
    await w.get('[data-test="case-requirement"]').setValue('R new')
    await w.get('[data-test="case-procedure"]').setValue('Inspect')
    await w.get('[data-test="case-expected"]').setValue('Visible')
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeUndefined()
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save.mock.calls[0][1][2]).toMatchObject({ case_id: 'TC-3', execution_mode: 'automated', required: false })
  })
  it('saves all cases as optional and reopens the selected failure policy', async () => {
    persist()
    const w = await render('case')
    await pick(w, 'case-required', '무시하고 진행')
    expect(w.get('[data-test="case-save"]').attributes('disabled')).toBeUndefined()
    await w.get('[data-test="case-save"]').trigger('click'); await flushPromises()
    expect(mocks.save.mock.calls[0][1].every((c: { required: boolean }) => !c.required)).toBe(true)
    context.openWindow('case', 'TC-1'); await flushPromises()
    expect(segmentOn(w, 'case-required')).toBe('무시하고 진행')
  })
  it('deletes the only required case without changing remaining optional cases', async () => {
    persist()
    const w = await render('case')
    await w.get('[data-test="case-delete"]').trigger('click')
    expect(w.get('[data-test="case-delete-submit"]').attributes('disabled')).toBeUndefined()
    await w.get('[data-test="case-delete-submit"]').trigger('click'); await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(TS_DOC, [fullCases()[1]], 'Keep H1')
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

// flowgate.v02.0094 #4-#6 — the windows speak the function names, show ids in the identifier type and edit on the
// document editors' layout.
describe('0094 test card windows follow the existing window rules', () => {
  const heading = (w: VueWrapper) => w.get('.ui-modeless-title')
  it('the scenario window header names 시나리오 without the TS code; ids use the identifier type and ✎ is an icon button', async () => {
    const w = await render('scenario')
    expect(heading(w).text()).toBe('시나리오')
    expect(heading(w).find('.mark').exists()).toBe(false)
    expect(w.get('[data-case="TC-1"] .case-id').text()).toBe('TC-1')
    expect(w.find('[data-case="TC-1"] .mono').exists()).toBe(false)
    const edit = w.findAll('[data-test="scenario-edit"]')[0]
    expect(edit.classes()).toContain('ui-icon-btn'); expect(edit.attributes('title')).toBe('TC-1 편집')
  })
  it('the case window header is the kind, then the case once (id · title, muted), without the TC mark', async () => {
    const w = await render('case')
    expect(heading(w).get('.heading-kind').text()).toBe('케이스')
    expect(heading(w).get('[data-test="case-window-id"]').text()).toBe('TC-1 · Sort')
    expect(heading(w).get('[data-test="case-window-id"]').classes()).toContain('heading-case')
    expect(heading(w).find('.mark').exists()).toBe(false)
    expect(heading(w).text()).not.toContain('—')
    expect(heading(w).text().match(/TC-1/g)).toHaveLength(1)
  })
  it("the case window bar is the card memo window's: [AI 호출] robot, [삭제] · [저장] · [닫기] at the right end", async () => {
    const w = await render('case')
    const bar = w.get('.case-bar')
    expect(bar.findAll('[data-test]').map(b => b.attributes('data-test'))).toEqual(['case-delete', 'case-save', 'case-close'])
    expect(bar.find('[data-test="case-rewrite"]').exists()).toBe(false)
    expect(bar.get('[data-test="case-delete"]').classes()).toEqual(expect.arrayContaining(['ui-btn--secondary', 'case-delete']))
    expect(bar.get('[data-test="case-save"]').classes()).toContain('ui-btn--primary')
    expect(bar.get('[data-test="case-close"]').classes()).toContain('ui-btn--secondary')
    await bar.get('[data-test="case-close"]').trigger('click')
    expect(context.windows.case.open).toBe(false)
  })
  it('a new case has no delete or AI button in its bar', async () => {
    const w = await render('case')
    context.closeWindow('case'); context.openWindow('case', '__new__'); await flushPromises()
    expect(w.get('.case-bar').findAll('[data-test]').map(b => b.attributes('data-test'))).toEqual(['case-save', 'case-close'])
  })
  it("the scenario window: no number before 케이스, and its whole rewrite is the cards' robot button", async () => {
    const w = await render('scenario')
    expect(w.get('.scenario > h3').text()).not.toContain('①')
    expect(w.get('.scenario > h3').text().startsWith('케이스')).toBe(true)
    const rewrite = w.get('[data-test="scenario-rewrite"]')
    expect(rewrite.attributes('data-icon')).toBe('robot'); expect(rewrite.attributes('aria-label')).toBe('전체 다시 작성')
  })
  it('the brief window is the requirement document editor: the title alone, the text filling it, [취소] · [저장] · [닫기] in the bar', async () => {
    const w = await render('brief')
    expect(heading(w).text()).toBe('지시서')
    expect(heading(w).find('.mark').exists()).toBe(false)
    const text = w.get('[data-test="brief-text"]')
    expect(text.element.tagName).toBe('TEXTAREA'); expect(text.classes()).toContain('brief-textarea')
    expect(text.attributes('aria-label')).toBe('추가 지시')
    expect(w.find('[data-test="brief-window"] label').exists()).toBe(false)
    const bar = w.get('.brief-bar')
    expect(bar.findAll('[data-test]').map(b => b.attributes('data-test'))).toEqual(['brief-save', 'brief-close'])
    expect(bar.get('[data-test="brief-save"]').classes()).toContain('ui-btn--primary')
    expect(bar.get('[data-test="brief-close"]').classes()).toContain('ui-btn--secondary')
    expect(bar.text().replace(/\s+/g, '')).toBe('저장닫기')
    expect(w.find('[data-test="brief-reason"]').exists()).toBe(false)
  })
  it('has one close action and retains unsaved session text on reopen', async () => {
    const w = await render('brief')
    await w.get('[data-test="brief-text"]').setValue('Changed')
    expect(w.find('[data-test="brief-cancel"]').exists()).toBe(false)
    await w.get('[data-test="brief-close"]').trigger('click'); await flushPromises()
    expect(context.windows.brief.open).toBe(false)
    context.openWindow('brief'); await flushPromises()
    expect(w.get('[data-test="brief-text"]').element).toHaveProperty('value', 'Changed')
    expect(mocks.briefSave).not.toHaveBeenCalled()
  })
  /* jsdom draws no layout: the rules that make the windows look like the existing ones are kept here. */
  const WINDOWS = resolve(__dirname, '../../../../../../src/features/designer/inspector/features/testSpec/windows')
  const rule = (file: string, selector: string) => {
    const src = readFileSync(resolve(WINDOWS, file), 'utf8'), at = src.indexOf(`\n${selector} {`)
    expect(at, selector).toBeGreaterThan(0)
    return src.slice(at, src.indexOf('}', at))
  }
  it("the brief window fills the shell and its bar is DocumentWindow's editor bar (whole buttons wrap at the right end)", () => {
    expect(rule('BriefWindow.vue', '.brief')).toMatch(/height: calc\(100% \+ 24px\);[^}]*margin: -12px -14px;/)
    const bar = rule('BriefWindow.vue', '.brief-bar')
    expect(bar).toMatch(/justify-content: flex-end;/); expect(bar).toMatch(/flex-wrap: wrap;/)
    expect(bar).toMatch(/padding: 10px 12px;/); expect(bar).toMatch(/border-top: 1px solid var\(--fg2-line\);/)
  })
  it('case ids are written in the type of the line around them: no monospace of their own, code alone stays monospace', () => {
    expect(rule('ScenarioWindow.vue', '.case-id')).not.toMatch(/font-family/)
    const gate = rule('ReportWindow.vue', '.gate-id')
    expect(gate).toMatch(/font-family: inherit;/); expect(gate).toMatch(/font-size: inherit;/); expect(gate).toMatch(/color: var\(--fg2-status-danger-fg\);/)
    expect(rule('ReportWindow.vue', '.mono')).toMatch(/font-family: 'JetBrains Mono'/)
    for (const file of ['ScenarioWindow.vue', 'ReportWindow.vue', 'FollowWindow.vue', 'CaseWindow.vue']) {
      expect(readFileSync(resolve(WINDOWS, file), 'utf8'), file).not.toMatch(/class="mono id"/)
    }
  })
})
