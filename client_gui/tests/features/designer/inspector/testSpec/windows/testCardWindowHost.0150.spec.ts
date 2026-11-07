/**
 * flowgate.v02.0150 — the test card windows are the Frame's windows host (TestCardWindowHost), not the header's: a window
 * opened on a card stays open — with its card's live session — when the card is unselected or another card is selected,
 * Esc does not close it, and only its own close removes it (and the card from the host).
 */
import { defineComponent, h, shallowRef, watchEffect } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TestCardWindowHost from '@/features/designer/inspector/features/testSpec/TestCardWindowHost.vue'
import { testCardSessionKey, testCardWindowCards } from '@/features/designer/inspector/features/testSpec/testCardSession'
import { useTestCard, type TestCardContext } from '@/features/designer/inspector/features/testSpec/useTestCard'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n, targetOf } from '../../support/inspectorSupport'
import { TS_DOC, TS_NODE, counts, runRow, testSelection, tsDocument } from '../testSpecFixtures'

const mocks = vi.hoisted(() => ({ read: vi.fn(), node: vi.fn(), choices: vi.fn(), guard: vi.fn(), activity: vi.fn(), history: vi.fn() }))
vi.mock('@/features/document', async (original) => {
  const actual = await original<typeof import('@/features/document')>()
  return { ...actual, testSpecRead: mocks.read, settingsNodeRead: mocks.node, settingsChoicesRead: mocks.choices,
    reviewDocumentRead: mocks.guard, reviewActivityRead: mocks.activity, documentHistoryRead: mocks.history }
})
vi.mock('@shared/api', async (original) => ({ ...await original<typeof import('@shared/api')>(), getRequest: vi.fn(async () => { throw new Error('offline') }) }))
const { toTestSpecView, toReviewDocument, toReviewActivity, toSettingsNode, toSettingsChoices, toDocumentHistory } = await vi.importActual<typeof import('@/features/document')>('@/features/document')
const i18n = prepareInspectorI18n()
const base = targetOf(testSelection('graph', TS_NODE))
const makeTarget = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...base, nodeId: 'gn_a', nodeType: 'document', cardDocType: 'TS', docId: null, docType: 'TS',
  cardDocuments: [{ slot: 'scenario', docType: 'TS', docId: TS_DOC, title: 'Scenario', author: 'human', status: 'open', docReviewStatus: 'approved' }], ...fields })
const CASES = [
  { case_id: 'TC-1', title: 'Expiry notice', category: 'normal', requirement: 'R §1', execution_mode: 'automated', required: true, procedure: 'Run', expected: 'Notice shown', check_points: 'c', automation_ref: 'tests/test_auth.py::test_1' },
]
const server = () => tsDocument({ cases: CASES, effective_result: { summary: { overall: 'FAIL', counts: counts(0, 1) }, cases: [{ case_id: 'TC-1', status: 'FAIL', actual: 'No notice', evidence: [] }] },
  latest_result: runRow('tr_2', { overall: 'FAIL', summary: { counts: counts(0, 1) }, created_at: '2026-10-10T10:00:00Z', finished_at: '2026-10-10T10:02:00Z' }),
  last_execution: runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' }), run_history: [runRow('tr_2', { overall: 'FAIL', created_at: '2026-10-10T10:00:00Z' })] })

let wrapper: VueWrapper | null = null
let context: TestCardContext | null = null
const selected = shallowRef<InspectorTarget | null>(null)
/** The selected card's parts (the header, the tabs): they live only while the card is selected. */
const Selected = defineComponent({
  props: { target: { type: Object, required: true } },
  setup(props) {
    const card = useTestCard(() => props.target as InspectorTarget)
    watchEffect(() => { if (card.value) context = card.value })
    return () => h('span', { 'data-test': 'selected-card' })
  },
})
async function render() {
  wrapper = mount(defineComponent({ setup: () => () => h('div', [
    selected.value ? h(Selected, { target: selected.value }) : null,
    h(TestCardWindowHost, { target: selected.value }),
  ]) }), { global: { plugins: [i18n, createPinia()] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}
/** The windows are async components (TEST_CARD_WINDOW_LOADERS): their modules load before they draw. */
async function settle() {
  await flushPromises()
  await vi.dynamicImportSettled()
  await flushPromises()
}
const reportWindow = () => document.body.querySelector('[data-test="report-tag"]')?.closest('.ui-modeless-panel') ?? null

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset())
  i18n.global.locale.value = 'ko'
  context = null
  selected.value = makeTarget()
  mocks.read.mockImplementation(async () => toTestSpecView(server()))
  mocks.node.mockImplementation(async () => toSettingsNode({ flow_id: base.flowId, node_key: 'gn_a', card_kind: 'test', locked: false, fields: {
    instruction: { value: 'use', choices: ['use', 'omit'] }, result_cause: { value: 'ai', default: 'ai', choices: ['ai', 'human'] } } }))
  mocks.choices.mockResolvedValue(toSettingsChoices({ providers: [] }))
  mocks.guard.mockImplementation(async (id: string) => toReviewDocument({ doc_id: id, is_editable: true, group_disposed: false }))
  mocks.activity.mockImplementation(async () => toReviewActivity({ active: false }))
  mocks.history.mockResolvedValue(toDocumentHistory({ doc_id: TS_DOC }))
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  testCardWindowCards.clear()
  document.body.innerHTML = ''
})

describe('test card windows stay open whatever is selected (flowgate.v02.0150)', () => {
  it('a window opened on a card stays when the card is unselected or another card is selected; Esc keeps it; its close removes it', async () => {
    await render()
    expect(context!.openWindow('report')).toBe(true)
    await settle()
    expect(reportWindow()).not.toBeNull()
    expect([...testCardWindowCards.keys()]).toEqual([testCardSessionKey(makeTarget())])
    const card = context!

    selected.value = null
    await settle()
    expect(document.body.querySelector('[data-test="selected-card"]')).toBeNull()
    expect(reportWindow()).not.toBeNull()
    // The card's session stays live for its window: its commands still see the card it was opened for.
    expect(card.target.value?.nodeId).toBe('gn_a')
    expect(card.can('run').reason).not.toBe('reason.busy')

    selected.value = makeTarget({ nodeId: 'gn_b', cardDocuments: [] })
    await settle()
    expect(reportWindow()).not.toBeNull()

    reportWindow()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settle()
    expect(reportWindow()).not.toBeNull()

    reportWindow()!.querySelector<HTMLButtonElement>('[data-modeless-action="close"]')!.click()
    await settle()
    expect(reportWindow()).toBeNull()
    expect(card.windows.report.open).toBe(false)
    expect(testCardWindowCards.size).toBe(0)
  })

  it('the card selected again gives its open windows its latest target', async () => {
    await render()
    context!.openWindow('report')
    await settle()
    const again = makeTarget({ title: '다시 고른 카드' })
    selected.value = again
    await settle()
    expect(testCardWindowCards.get(testCardSessionKey(again))).toBe(again)
    expect(reportWindow()!.textContent).toContain('다시 고른 카드')
  })
})
