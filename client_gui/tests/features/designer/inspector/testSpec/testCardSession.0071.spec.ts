import { computed, defineComponent, h, shallowRef, watchEffect, type PropType } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTestCard } from '@/features/designer/inspector/features/testSpec/useTestCard'
import type { TestCardContext } from '@/features/designer/inspector/features/testSpec/testCardSession'
import TestSpecTab from '@/features/designer/inspector/features/testSpec/TestSpecTab.vue'
import TestCardHeaderState from '@/features/designer/inspector/features/testSpec/TestCardHeaderState.vue'
import TestCardMenu from '@/features/designer/inspector/features/testSpec/TestCardMenu.vue'
import SettingsTab from '@/features/designer/inspector/features/settings/SettingsTab.vue'
import { type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream } from '../support/projectEventStream'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { TS_DOC, TSR_DOC, TS_NODE, testSelection, tsDocument, tsrDocument, liveRun, runRow } from './testSpecFixtures'

const mocks = vi.hoisted(() => ({ read: vi.fn(), node: vi.fn(), choices: vi.fn(), setting: vi.fn(), guard: vi.fn(), activity: vi.fn(), run: vi.fn(), cancel: vi.fn(), save: vi.fn(), retry: vi.fn(), enter: vi.fn(), ai: vi.fn() }))
vi.mock('@/features/document', async (original) => {
  const actual = await original<typeof import('@/features/document')>()
  return { ...actual, testSpecRead: mocks.read, settingsNodeRead: mocks.node, settingsChoicesRead: mocks.choices, settingsNodeWrite: mocks.setting,
    reviewDocumentRead: mocks.guard, reviewActivityRead: mocks.activity, testSpecRunStart: mocks.run, testSpecRunCancel: mocks.cancel,
    testSpecSave: mocks.save, testSpecCaseRun: mocks.retry, testSpecResultEnter: mocks.enter,
    documentHistoryRead: async (id: string) => actual.toDocumentHistory({ doc_id: id }) }
})
vi.mock('@/features/designer/inspector/openDocument', async (original) => {
  const actual = await original<typeof import('@/features/designer/inspector/openDocument')>()
  return { ...actual, openDocumentAi: (...args: unknown[]) => mocks.ai(...args) }
})
vi.mock('@shared/api', async (original) => {
  const actual = await original<typeof import('@shared/api')>()
  return { ...actual, getRequest: vi.fn(async () => { throw new Error('offline') }) }
})
const { toTestSpecView, toReviewDocument, toReviewActivity, toSettingsNode, toSettingsChoices, testSpecCasePayload } = await vi.importActual<typeof import('@/features/document')>('@/features/document')
const i18n = prepareInspectorI18n()
const OTHER = 'flowgate.v02.0048.0088-TS'
const base = targetOf(testSelection('graph', TS_NODE))
const makeTarget = (key = 'gn_a', id = TS_DOC): InspectorTarget => ({ ...base, nodeId: key, nodeType: 'document', cardDocType: 'TS', docId: null, docType: 'TS',
  cardDocuments: [{ slot: 'scenario', docType: 'TS', docId: id, title: 'Scenario', author: 'human', status: 'open', docReviewStatus: 'approved' }] })
const views = new Map<string, Record<string, unknown>>()
const stored = new Map<string, Record<string, unknown>>()
const contexts: Record<string, TestCardContext> = {}
let editable: boolean, disposed: boolean, activity: Record<string, unknown>, locked: boolean
let wrapper: VueWrapper | null = null
let target = shallowRef(makeTarget())
const failure = (status: number, error: string) => ({ isAxiosError: true, response: { status, data: { error } } })
const deferred = () => { let resolve!: () => void; const promise = new Promise<void>((done) => { resolve = done }); return { promise, resolve } }
function settings(key: string) {
  const values = stored.get(key) ?? {}
  return toSettingsNode({ flow_id: base.flowId, node_key: key, card_kind: 'test', locked, fields: Object.fromEntries([
    ['instruction', 'use', ['use', 'omit']], ['run_ai', null, null], ['review_ai', null, null], ['review_count', 0, [0, 1, 2]],
    ['result_cause', 'ai', ['ai', 'human']], ['follow_scope', 'summary', ['summary', 'full']],
  ].map(([name, def, choices]) => [String(name), { value: values[String(name)] ?? def, default: def, choices }])) })
}
const Probe = defineComponent({ props: { target: { type: Object as PropType<InspectorTarget>, required: true }, name: { type: String, required: true } }, setup(props) {
  const card = useTestCard(() => props.target)
  watchEffect(() => { if (card.value) contexts[props.name] = card.value })
  return () => h('span', { 'data-probe': props.name }, card.value?.presentation.value.stage)
} })
async function render() {
  wrapper = mount(defineComponent({ setup: () => () => h('div', [
    h(Probe, { target: target.value, name: 'one' }), h(Probe, { target: target.value, name: 'two' }),
    h(TestCardHeaderState, { target: target.value }), h(TestCardMenu, { target: target.value, close: () => {} }),
    h(TestSpecTab, { target: target.value }), h(SettingsTab, { target: target.value }),
  ]) }), { global: { plugins: [i18n, createPinia()] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  i18n.global.locale.value = 'ko'
  Object.values(mocks).forEach((mock) => mock.mockReset())
  views.clear(); stored.clear(); Object.keys(contexts).forEach((key) => delete contexts[key])
  target = shallowRef(makeTarget()); editable = true; disposed = false; locked = false; activity = { active: false }
  views.set(TS_DOC, tsDocument({ doc_review_status: 'approved' })); views.set(OTHER, tsDocument({ doc_id: OTHER, latest_result: null, effective_result: null, run_history: [], last_execution: null }))
  mocks.read.mockImplementation(async (id: string) => toTestSpecView(views.get(id)))
  mocks.node.mockImplementation(async (_flow: string, key: string) => settings(key))
  mocks.choices.mockResolvedValue(toSettingsChoices({ providers: [], execution_policy: { repeat_count_max: 2 } }))
  mocks.setting.mockImplementation(async (_flow: string, key: string, field: string, value: unknown) => { stored.set(key, { ...stored.get(key), [field]: value }); return settings(key) })
  mocks.guard.mockImplementation(async (id: string) => toReviewDocument({ doc_id: id, is_editable: editable, group_disposed: disposed }))
  mocks.activity.mockImplementation(async () => toReviewActivity(activity))
  mocks.run.mockImplementation(async () => { const current = views.get(TS_DOC)!; current.active_run = liveRun('run-live'); current.last_execution = current.active_run; return { runId: 'run-live', status: 'running' } })
  mocks.cancel.mockImplementation(async () => { const current = views.get(TS_DOC)!; current.active_run = liveRun('run-live', { status: 'cancelling' }); return { runId: 'run-live', status: 'cancelling' } })
  mocks.save.mockResolvedValue(undefined); mocks.retry.mockResolvedValue({ runId: 'retry', status: 'running' }); mocks.enter.mockResolvedValue(undefined)
})
afterEach(() => { wrapper?.unmount(); wrapper = null; removeProjectEventStream(); document.body.innerHTML = ''; vi.useRealTimers() })

describe('v8 shared test card session (0071 T1)', () => {
  it('all consumers share one test read and one settings state, including an unbound card', async () => {
    const w = await render()
    expect(contexts.one).toBe(contexts.two)
    expect(mocks.read).toHaveBeenCalledTimes(1)
    expect(mocks.read).toHaveBeenCalledWith(TS_DOC)
    expect(mocks.node).toHaveBeenCalledTimes(1)
    expect(w.findAll('[data-slot]')).toHaveLength(3)
    expect(w.find('[data-test="test-spec-details"]').exists()).toBe(false)
    expect(w.get('[data-test="test-card-header-state"]').text()).toBe('확인 필요 1')
    expect(w.get('[data-test="settings-instruction-switch"]').attributes('role')).toBe('switch')
    expect(w.findAll('[data-test="inspector-menu-item"]')).toHaveLength(2)
  })
  it('starts before any report exists using the scenario TS and cancels the test run id', async () => {
    views.set(TS_DOC, tsDocument({ latest_result: null, effective_result: null, run_history: [], last_execution: null, tsr_doc_id: null }))
    const w = await render()
    expect(w.get('[data-test="test-spec-run"]').attributes('disabled')).toBeUndefined()
    const wait = deferred(); const start = mocks.run.getMockImplementation()!
    mocks.run.mockImplementation(async () => { await wait.promise; return start() })
    await w.get('[data-test="test-spec-run"]').trigger('click'); await flushPromises()
    expect(await contexts.one.run()).toBe(false)
    expect(mocks.run).toHaveBeenCalledTimes(1)
    wait.resolve(); await flushPromises()
    expect(mocks.run).toHaveBeenCalledWith(TS_DOC)
    await w.get('[data-test="test-spec-cancel"]').trigger('click'); await flushPromises()
    expect(mocks.cancel).toHaveBeenCalledWith('run-live')
    expect(w.get('[data-test="test-spec-cancel"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="test-spec-reason"]').text()).toBe('취소를 처리하는 중입니다.')
  })
  it('a setting changed by a window updates the settings tab; OFF closes the brief only after acceptance', async () => {
    const w = await render(); const card = contexts.one
    card.openWindow('brief'); card.drafts.brief = 'unsaved instruction'
    expect(await contexts.two.changeSetting('follow_scope', 'full')).toBe(true)
    await flushPromises()
    expect(w.get('[data-test="settings-follow_scope"]').text()).toContain('요약 + 로그 · 증거')
    const wait = deferred(); const write = mocks.setting.getMockImplementation()!
    mocks.setting.mockImplementation(async (...args: unknown[]) => { await wait.promise; return write(...args) })
    const change = card.changeSetting('instruction', 'omit'); await flushPromises()
    expect(card.windows.brief.open).toBe(true)
    wait.resolve(); expect(await change).toBe(true); await flushPromises()
    expect(card.windows.brief.open).toBe(false)
    expect(card.drafts.brief).toBe('unsaved instruction')
    expect(w.find('[data-slot="instruction"]').exists()).toBe(true)
    expect(w.get('[data-test="test-card-brief"]').attributes('disabled')).toBeDefined()
  })
  it('a refused scope change restores the server value for every consumer and shows its failure', async () => {
    const w = await render()
    mocks.setting.mockRejectedValueOnce(failure(422, 'invalid_request'))
    expect(await contexts.one.changeSetting('follow_scope', 'full')).toBe(false)
    await flushPromises()
    expect(contexts.two.settings.node.value?.fields.follow_scope.value).toBe('summary')
    expect(w.get('[data-test="settings-follow_scope"]').text()).toContain('결함 요약')
    expect(w.get('[data-field="follow_scope"]').attributes('title')).toContain('저장')
  })
  it('late reads and a late permission refusal cannot affect the next card; returning restores its drafts/windows', async () => {
    await render(); const old = contexts.one
    old.openWindow('scenario'); old.windows.scenario.minimized = true; old.windows.scenario.geometry.left = 123; old.drafts['case:TC-1'] = { title: 'unsaved' }
    const wait = deferred(); mocks.run.mockImplementationOnce(async () => { await wait.promise; throw failure(403, 'permission_denied') })
    const started = old.run(); await flushPromises()
    target.value = makeTarget('gn_b', OTHER); await flushPromises()
    const next = contexts.one
    expect(next).not.toBe(old)
    wait.resolve(); expect(await started).toBe(false); await flushPromises()
    expect(next.test.failure.value).toBeNull()
    expect(next.can('run').enabled).toBe(true)
    target.value = makeTarget(); await flushPromises()
    expect(contexts.one).toBe(old)
    expect(old.test.failure.value).toBeNull()
    expect(old.windows.scenario).toMatchObject({ open: true, minimized: true, geometry: { left: 123 } })
    expect(old.drafts['case:TC-1']).toEqual({ title: 'unsaved' })
  })
  it('rewrite-all keeps the previous stage and open drafts through transient empty/failed reads', async () => {
    await render(); const card = contexts.one
    card.openWindow('scenario'); card.openWindow('case', 'TC-1'); card.drafts.case = 'draft'
    activity = { active: true, action_scope: 'edit', status: 'running', doc_ref: TS_DOC, run_id: 'ai-write' }
    await card.reload(); await flushPromises()
    expect(card.writing.value).toBe('rewrite-all')
    const previousStage = card.presentation.value.stage
    views.set(TS_DOC, tsDocument({ cases: [], latest_result: null, effective_result: null }))
    await card.test.reload(); await flushPromises()
    expect(card.presentation.value.stage).toBe(previousStage)
    expect(card.windows.case.open).toBe(true)
    expect(card.windows.scenario.open).toBe(true)
    expect(card.can('caseSave').reason).toBe('guard.writing')
    expect(card.canOpen('scenario')).toBe(true)
    expect(card.canOpen('case')).toBe(false)
    expect(await card.run()).toBe(false)
    expect(await card.saveCases([])).toBe(false)
    expect(await card.changeSetting('result_cause', 'human')).toBe(false)
    activity = { active: false }
    mocks.read.mockRejectedValue(new Error('offline'))
    await card.reload(); await flushPromises()
    expect(card.writing.value).toBeNull()
    expect(card.presentation.value.stage).toBe(previousStage)
    expect(card.drafts.case).toBe('draft')
  })
  it('initial writing blocks scenario/report entry; another document AI run is only a Flow guard', async () => {
    views.set(TS_DOC, tsDocument({ cases: [], latest_result: null, effective_result: null }))
    await render(); const card = contexts.one
    activity = { active: true, action_scope: 'edit', status: 'running', doc_ref: OTHER }
    await card.reload(); await flushPromises()
    expect(card.writing.value).toBeNull()
    expect(card.presentation.value.stage).toBe(1)
    activity = { active: true, action_scope: 'edit', status: 'running', doc_ref: TS_DOC }
    await card.reload(); await flushPromises()
    expect(card.writing.value).toBe('initial')
    expect(card.presentation.value.stage).toBe(2)
    expect(card.canOpen('scenario')).toBe(false)
    expect(card.canOpen('report')).toBe(false)
  })
  it('direct commands enforce readonly, disposal, AI and document edit guards in order; cancel still works', async () => {
    await render(); const card = contexts.one
    target.value = { ...target.value, readOnly: 'revision' }; await flushPromises()
    expect(card.can('caseSave').reason).toBe('reason.read_only_revision')
    expect(await card.saveCases([])).toBe(false); expect(await card.run()).toBe(false)
    expect(card.canOpen('scenario')).toBe(true)
    target.value = { ...target.value, readOnly: null }; disposed = true; await card.reload()
    expect(card.can('run').reason).toBe('reason.disposed')
    expect(await card.enterResult({ case_id: 'TC-3', status: 'PASS' })).toBe(false)
    disposed = false; activity = { active: true, action_scope: 'edit', status: 'running', doc_ref: OTHER }; await card.reload()
    expect(card.can('caseDelete').reason).toBe('reason.ai_running')
    activity = { active: false }; editable = false; await card.reload()
    expect(card.can('caseSave').reason).toBe('guard.approved')
    expect(await card.rewriteAll()).toBe(false)
    expect(mocks.save).not.toHaveBeenCalled(); expect(mocks.run).not.toHaveBeenCalled(); expect(mocks.enter).not.toHaveBeenCalled()
    views.get(TS_DOC)!.active_run = liveRun('run-live'); disposed = true; activity = { active: true, action_scope: 'edit', status: 'running', doc_ref: OTHER }; await card.reload()
    expect(await card.cancel()).toBe(true)
  })
  it('structured save retains title and complete fields, stays busy through reload, and isolates document permission denial', async () => {
    const original = tsDocument()
    original.title = 'Keep H1'; (original.cases as any[])[0] = { ...(original.cases as any[])[0], category: 'normal', requirement: 'R1', procedure: 'steps', expected: 'ok', check_points: 'keep', input: 'hidden', automation_ref: 'tests/test_x.py::test_x', test_assets: 'data/input.json' }
    views.set(TS_DOC, original); await render(); const card = contexts.one
    const payload = card.cases.value.map(testSpecCasePayload)
    expect(await card.saveCases(payload)).toBe(true)
    expect(mocks.save).toHaveBeenCalledWith(TS_DOC, payload, 'Keep H1')
    expect(payload[0]).toMatchObject({ input: 'hidden', check_points: 'keep', automation_ref: 'tests/test_x.py::test_x', test_assets: 'data/input.json' })
    mocks.save.mockRejectedValueOnce(failure(403, 'permission_denied'))
    expect(await card.saveCases(payload)).toBe(false)
    expect(card.can('rawSave').reason).toBe('reason.forbidden')
    expect(card.can('run').enabled).toBe(true)
    expect(await card.saveCases(payload)).toBe(false)
    expect(mocks.save).toHaveBeenCalledTimes(2)
  })
  it('case retry and manual entry use their existing endpoints and reject suite/server-run cases', async () => {
    await render(); const card = contexts.one
    expect(await card.rerunCase('TC-2')).toBe(false)
    expect(await card.rerunCase('TC-1')).toBe(true)
    expect(mocks.retry).toHaveBeenCalledWith(TS_DOC, 'TC-1')
    expect(await card.enterResult({ case_id: 'TC-1', status: 'PASS' })).toBe(false)
    expect(await card.enterResult({ case_id: 'TC-3', status: 'PASS' })).toBe(true)
    expect(mocks.enter).toHaveBeenCalledWith(TS_DOC, { case_id: 'TC-3', status: 'PASS' })
    expect(card.can('caseAdd').reason).toBeNull()
    expect(card.can('decide').reason).toBe('guard.unconnected')
    expect(await card.rewriteAll()).toBe(true)
    expect(mocks.ai).toHaveBeenCalledWith(TS_DOC, expect.objectContaining({ nodeId: 'gn_a' }), expect.any(Function), expect.objectContaining({ submitting: expect.any(Function), rejected: expect.any(Function) }))
  })
  it('events refresh the one session, inactive cards unsubscribe and reactivated cards hear events', async () => {
    vi.useFakeTimers(); const stream = installProjectEventStream()
    await render(); stream.open()
    const count = mocks.read.mock.calls.length
    stream.emit('test_run_phase', { docId: TS_DOC, groupId: base.flowId })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS); await flushPromises()
    expect(mocks.read.mock.calls.length).toBe(count + 1)
    target.value = makeTarget('gn_b', OTHER); await flushPromises()
    const before = mocks.read.mock.calls.length
    stream.emit('test_run_finished', { docId: TS_DOC, groupId: base.flowId })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS); await flushPromises()
    expect(mocks.read.mock.calls.length).toBe(before)
    target.value = makeTarget(); await flushPromises()
    const again = mocks.read.mock.calls.length
    stream.emit('test_run_finished', { docId: TS_DOC, groupId: base.flowId })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS); await flushPromises()
    expect(mocks.read.mock.calls.length).toBe(again + 1)
  })
})


it('active runs block direct run/retry/result commands, including a manually checked TS', async () => {
  views.get(TS_DOC)!.active_run = liveRun('run-live')
  await render(); const card = contexts.one
  expect(card.can('run').reason).toBe('reason.run_active')
  expect(card.can('rerunCase', 'TC-1').reason).toBe('reason.run_active')
  expect(card.can('resultEntry', 'TC-3').reason).toBe('reason.run_active')
  expect(await card.run()).toBe(false)
  expect(await card.rerunCase('TC-1')).toBe(false)
  expect(await card.enterResult({ case_id: 'TC-3', status: 'PASS' })).toBe(false)
  expect(mocks.run).not.toHaveBeenCalled(); expect(mocks.retry).not.toHaveBeenCalled(); expect(mocks.enter).not.toHaveBeenCalled()
})
it('a partial read does not discard a normal scenario or its open windows', async () => {
  await render(); const card = contexts.one
  card.openWindow('scenario')
  views.set(TS_DOC, { doc_id: TS_DOC, kind: 'TS', contract_version: 2 })
  await card.test.reload(); await flushPromises()
  expect(card.presentation.value.hasScenario).toBe(true)
  expect(card.windows.scenario.open).toBe(true)
})
it('a direct raw save shares busy and refusal state with structured saves', async () => {
  await render(); const card = contexts.one
  const wait = deferred()
  const save = card.changeDocument('rawSave', () => wait.promise)
  expect(card.can('caseSave').reason).toBe('reason.busy')
  expect(await card.saveCases([])).toBe(false)
  wait.resolve(); expect(await save).toBe(true)
  expect(card.can('caseSave').enabled).toBe(true)
})


it('a completed card bound to TSR still reads and runs its scenario TS', async () => {
  target.value = { ...makeTarget(), docId: TSR_DOC, docType: 'TSR' }
  await render()
  expect(mocks.read).toHaveBeenCalledWith(TS_DOC)
  expect(mocks.read).not.toHaveBeenCalledWith(TSR_DOC)
  expect(mocks.guard).toHaveBeenCalledWith(TS_DOC)
  expect(await contexts.one.run()).toBe(true)
  expect(mocks.run).toHaveBeenCalledWith(TS_DOC)
})
it('a TSR document node uses the TS editability guard when saving scenario cases', async () => {
  target.value = { ...base, nodeType: null, nodeId: `doc:${TSR_DOC}`, docId: TSR_DOC, docType: 'TSR', cardDocType: null, cardDocuments: [] }
  views.set(TSR_DOC, tsrDocument())
  mocks.guard.mockImplementation(async (id: string) => toReviewDocument({ doc_id: id, is_editable: id !== TS_DOC }))
  await render(); await flushPromises()
  expect(mocks.read).toHaveBeenCalledWith(TSR_DOC)
  expect(mocks.guard).toHaveBeenCalledWith(TS_DOC)
  expect(contexts.one.can('caseSave').reason).toBe('guard.approved')
  expect(await contexts.one.saveCases([])).toBe(false)
  expect(mocks.save).not.toHaveBeenCalled()
})


it('returning to a card after its AI finished releases the writing lock and retains its drafts', async () => {
  await render(); const card = contexts.one
  activity = { active: true, action_scope: 'edit', status: 'running', doc_ref: TS_DOC }; await card.reload(); await flushPromises()
  expect(card.writing.value).toBe('rewrite-all')
  card.drafts.case = 'keep'
  target.value = makeTarget('gn_b', OTHER); await flushPromises()
  activity = { active: false }
  target.value = makeTarget(); await flushPromises()
  expect(contexts.one).toBe(card)
  expect(card.writing.value).toBeNull()
  expect(card.drafts.case).toBe('keep')
})
it('an AI activity response arriving before the scenario read still establishes initial writing', async () => {
  views.set(TS_DOC, tsDocument({ cases: [], latest_result: null, effective_result: null }))
  activity = { active: true, action_scope: 'edit', status: 'running', doc_ref: TS_DOC }
  const wait = deferred(); const read = mocks.read.getMockImplementation()!
  mocks.read.mockImplementation(async (id: string) => { await wait.promise; return read(id) })
  await render()
  wait.resolve(); await flushPromises()
  expect(contexts.one.writing.value).toBe('initial')
  expect(contexts.one.presentation.value.stage).toBe(2)
})


it.each([true, false])('review AI on a TS preserves stage/chips and leaves settings available (scenario=%s)', async (scenario) => {
  if (!scenario) views.set(TS_DOC, tsDocument({ cases: [], latest_result: null, effective_result: null }))
  const w = await render(); const card = contexts.one
  const stage = card.presentation.value.stage
  const header = w.get('[data-test="test-card-header-state"]').text()
  const chip = w.get('[data-test="test-card-scenario-state"]').text()
  activity = { active: true, status: 'running', action_scope: 'review', doc_ref: TS_DOC }
  await card.reload(); await flushPromises()
  expect(card.test.guard.value).toMatchObject({ aiRunning: true, writingDocument: null })
  expect(card.writing.value).toBeNull()
  expect(card.presentation.value.stage).toBe(stage)
  expect(w.get('[data-test="test-card-header-state"]').text()).toBe(header)
  expect(w.get('[data-test="test-card-scenario-state"]').text()).toBe(chip)
  expect(card.can('settingChange', undefined, 'review_count').enabled).toBe(true)
  expect(await card.changeSetting('result_cause', 'human')).toBe(true)
  expect(card.can('run').reason).toBe('reason.ai_running')
  expect(card.can('rawSave').reason).toBe('reason.ai_running')
})

it('rewrite submission guards immediately, survives stale activity and releases on failure or completion', async () => {
  await render(); const card = contexts.one
  expect(await card.rewriteAll()).toBe(true)
  expect(card.writing.value).toBeNull()
  const [, , finished, lifecycle] = mocks.ai.mock.calls[0]
  target.value = { ...target.value, readOnly: 'revision' }; await flushPromises()
  expect(lifecycle.submitting()).toBe(false)
  target.value = { ...target.value, readOnly: null }; await flushPromises()
  expect(lifecycle.submitting()).toBe(true)
  expect(card.writing.value).toBe('rewrite-all')
  expect(card.can('rawSave').reason).toBe('guard.writing')
  expect(card.can('settingChange').reason).toBe('guard.writing')
  expect(await card.run()).toBe(false)
  await card.reload(); await flushPromises() // activity has not received the start event yet
  expect(card.writing.value).toBe('rewrite-all')
  lifecycle.rejected(); await flushPromises()
  expect(card.writing.value).toBeNull()
  expect(lifecycle.submitting()).toBe(true)
  const wait = deferred(); const read = mocks.read.getMockImplementation()!
  mocks.read.mockImplementation(async (...args: unknown[]) => { await wait.promise; return read(...args) })
  finished(); await flushPromises()
  expect(card.writing.value).toBe('rewrite-all')
  wait.resolve(); await flushPromises()
  expect(card.writing.value).toBeNull()
})

it.each(['missing_spec_section', 'unsupported_contract', 'invalid'])('zero-case %s errors retain an authoring and raw repair surface', async (code) => {
  views.set(TS_DOC, tsDocument({ cases: [], errors: [{ code, field: 'cases', message: code }], latest_result: null, effective_result: null }))
  const w = await render(); const card = contexts.one
  expect(card.presentation.value.stage).toBe(1)
  expect(card.openReason('scenario', true)).toBeNull()
  expect(w.get('[data-test="test-card-scenario-open"]').attributes('disabled')).toBeUndefined()
  const menu = w.findAllComponents(TestCardMenu)[0].findAll('[data-test="inspector-menu-item"]')[0]
  expect(menu.attributes('disabled')).toBeUndefined()
  await menu.trigger('click'); await w.get('[data-test="test-card-scenario-open"]').trigger('click')
  expect(card.windows.scenario.open).toBe(true)
  expect(card.openWindow('scenario')).toBe(true)
  expect(card.can('rawSave').reason).toBeNull()
  const save = vi.fn()
  expect(await card.changeDocument('rawSave', save)).toBe(true)
  expect(save).toHaveBeenCalledOnce()
})

it('connected windows share stage-specific open reasons with menu and section cards', async () => {
  const { TEST_CARD_WINDOW_LOADERS } = await import('@/features/designer/inspector/features/testSpec/testCardSession')
  const connected = ['./windows/ScenarioWindow.vue', './windows/ReportWindow.vue']
  // These files exist before component setup in a T2/T3 build.
  for (const key of connected) TEST_CARD_WINDOW_LOADERS[key] = async () => ({ default: defineComponent({ render: () => null }) })
  try {
    views.set(TS_DOC, tsDocument({ cases: [], latest_result: null, effective_result: null }))
    const w = await render(); const card = contexts.one
    expect(card.openReason('scenario', false)).toBe('guard.unconnected')
    expect(card.openReason('scenario', true)).toBeNull()
    expect(card.openReason('report', true)).toBe('guard.no_result')
    expect(w.get('[data-test="test-card-scenario-open"]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-test="test-spec-open"]').attributes('title')).toBe('아직 표시할 실행 결과가 없습니다')
    card.writing.value = 'initial'; await flushPromises()
    expect(card.openReason('scenario', true)).toBe('guard.writing')
    expect(card.openReason('report', true)).toBe('guard.writing')
    expect(w.get('[data-test="test-card-scenario-open"]').attributes('title')).toBe('시나리오를 작성하는 중입니다')
    expect(w.get('[data-test="test-spec-open"]').attributes('title')).toBe('시나리오를 작성하는 중입니다')
    const items = w.findAllComponents(TestCardMenu)[0].findAll('[data-test="inspector-menu-item"]')
    expect(items.map((item) => item.attributes('title'))).toEqual(['시나리오를 작성하는 중입니다', '시나리오를 작성하는 중입니다'])
    card.writing.value = null
    expect(await card.changeSetting('instruction', 'omit')).toBe(true)
    expect(card.openReason('brief', true)).toBe('guard.instruction_omitted')
  } finally { for (const key of connected) delete TEST_CARD_WINDOW_LOADERS[key] }
})

it.each(['caseSave', 'caseDelete', 'rawSave'] as const)('%s retains operation-specific failures, normalized fields and drafts', async (operation) => {
  await render(); const card = contexts.one
  card.drafts.source = 'keep source'; card.openWindow('scenario')
  const errors = [{ code: 'required', case_id: 'TC-1', field: 'title', message: 'Title required' }]
  const invalid = { isAxiosError: true, response: { status: 422, data: { detail: { error: 'invalid_spec', message: 'Check fields', errors } } } }
  expect(await card.changeDocument(operation, async () => { throw invalid })).toBe(false)
  expect(card.refusal.value).toMatchObject({ operation, kind: 'invalid', key: `failure.${operation}_invalid`, status: 422, code: 'invalid_spec', message: 'Check fields', fieldErrors: [{ code: 'required', caseId: 'TC-1', field: 'title', message: 'Title required' }] })
  expect(card.drafts.source).toBe('keep source')
  expect(card.windows.scenario.open).toBe(true)
  for (const [status, code, key] of [[409, 'run_in_progress', 'failure.running'], [409, 'group_disposed', 'failure.disposed'], [422, 'doc_not_editable', 'guard.approved'], [409, 'state_changed', `failure.${operation}_conflict`], [500, 'unknown', `failure.${operation}_other`]] as const) {
    expect(await card.changeDocument(operation, async () => { throw failure(status, code) })).toBe(false)
    expect(card.refusal.value?.key).toBe(key)
  }
  expect(await card.changeDocument(operation, async () => { throw failure(403, 'permission_denied') })).toBe(false)
  expect(card.refusal.value?.key).toBe(`failure.${operation}_forbidden`)
  expect(card.can('caseSave').reason).toBe('reason.forbidden')
  expect(card.can('run').enabled).toBe(true)
})

it('raw saves recognize the content endpoint disposal and editability details', async () => {
  await render(); const card = contexts.one
  for (const [status, detail, key] of [[409, 'Modification not allowed: the group has been disposed.', 'failure.disposed'], [422, 'Modification not allowed after final approval.', 'guard.approved']] as const) {
    expect(await card.changeDocument('rawSave', async () => { throw { response: { status, data: { detail } } } })).toBe(false)
    expect(card.refusal.value?.key).toBe(key)
  }
})

it('stage 6 summarizes passed cases and highlights only briefly after stage 5', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const w = await render(); const card = contexts.one
  expect(card.presentation.value.stage).toBe(5)
  expect(w.get('[data-slot="report"]').classes()).toContain('is-focus')
  const result = { summary: { overall: 'PASS', counts: { total: 3, pass: 3, fail: 0, blocked: 0, not_run: 0 } }, cases: ['TC-1', 'TC-2', 'TC-3'].map((id) => ({ case_id: id, status: 'PASS' })) }
  views.set(TS_DOC, tsDocument({ effective_result: result, latest_result: runRow('tr_1', { summary: result.summary }) }))
  await card.reload(); await flushPromises()
  expect(card.presentation.value.stage).toBe(6)
  expect(w.get('[data-test="test-card-report-summary"]').text()).toBe('통과 3/3')
  expect(w.get('[data-slot="report"]').classes()).toContain('is-focus')
  await vi.advanceTimersByTimeAsync(1800); await flushPromises()
  expect(w.get('[data-slot="report"]').classes()).not.toContain('is-focus')
  await card.reload(); await flushPromises()
  expect(w.get('[data-slot="report"]').classes()).not.toContain('is-focus')
})

it('review activity after a writer ends releases writing and the settings lock', async () => {
  await render(); const card = contexts.one
  activity = { active: true, action_scope: 'edit', status: 'running', doc_ref: TS_DOC }
  await card.reload(); await flushPromises()
  expect(card.writing.value).toBe('rewrite-all')
  activity = { active: true, action_scope: 'review', status: 'running', doc_ref: TS_DOC }
  await card.reload(); await flushPromises()
  expect(card.writing.value).toBeNull()
  expect(card.can('settingChange').enabled).toBe(true)
  expect(card.can('rawSave').reason).toBe('reason.ai_running')
})
it('structured deletion preserves the deletion operation in its refusal', async () => {
  await render(); const card = contexts.one
  mocks.save.mockRejectedValueOnce(failure(422, 'invalid_spec'))
  expect(await card.saveCases([], 'caseDelete')).toBe(false)
  expect(card.refusal.value).toMatchObject({ operation: 'caseDelete', key: 'failure.caseDelete_invalid' })
})
