/**
 * flowgate.v02.0072 T#3 — the settings tab of a Graph work plan (WP) card. `@/features/document` is mocked as a small
 * card settings server with the plan fields the server keeps (`fields.PLAN`: pre_instruction_auto, run_ai, restart,
 * review_ai, review_count, time_limit, review_basis); every other call of `@shared/api` is offline and recorded:
 *
 * - the top line is [switch] AI 사전 지시서 작성 (the switch left of its name, its tooltip saying it is a card value, no
 *   writer runs from it yet and off keeps the Steps' pre-instructions), then the common 실행 설정 block — 작업 AI ·
 *   재시작 | 검수 AI · 검수 횟수, 고급 ▾ 실행 제한 시간 · 검수 기준 — and no 지시서 / 지시 AI row;
 * - the switch is one card setting PATCH (true / false), shown as answered and read again on the same card; turning it
 *   off sends `false` only — nothing reaches the plan document (no work-plan GET / PUT, no other request);
 * - the run block items are card settings, each its own PATCH, with the same review-off, lock (🔒 / the Flow's run
 *   lock), read-only, refusal (!) and read failure rules as every card;
 * - the real Frame and registry show this tab on a Graph WP card; a v0.1 Flow's plan Node has none.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import SettingsTab from '@/features/designer/inspector/features/settings/SettingsTab.vue'
import { INSPECTOR_HOST } from '@/features/designer/inspector/inspectorHost'
import { inspectorSettingsKind, toInspectorTarget, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n } from '../support/inspectorSupport'

const nodeRead = vi.fn()
const nodeWrite = vi.fn()
const choicesRead = vi.fn()
const transport = vi.hoisted(() => ({ calls: [] as unknown[][] }))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = (...args: unknown[]) => {
    transport.calls.push(args)
    return Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  }
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline), postFormRequest: vi.fn(offline) }
})

vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    settingsNodeRead: (...args: unknown[]) => nodeRead(...args),
    settingsNodeWrite: (...args: unknown[]) => nodeWrite(...args),
    settingsChoicesRead: (...args: unknown[]) => choicesRead(...args),
  }
})

const { toSettingsNode, toSettingsChoices } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const PROJECT = 'flowgate'
const FLOW = 'flowgate.v02.0072'
const REPEAT = [-1, 0, 1, 2, 3]
type FieldDef = readonly [name: string, defaultValue: unknown, choices: readonly unknown[] | null]
/** server fields.PLAN (+ every card's hold). */
const PLAN_FIELDS: readonly FieldDef[] = [
  ['pre_instruction_auto', false, [true, false]],
  ['run_ai', null, null],
  ['restart', 0, REPEAT],
  ['review_ai', null, null],
  ['review_count', 0, REPEAT],
  ['time_limit', 60, [30, 45, 60, 90, 120, 180, 240]],
  ['review_basis', 'type', ['type', 'last']],
  ['hold', false, [true, false]],
]

let stored: Record<string, unknown>
let locked: boolean
let refusal: unknown
const writes: [string, string, string, unknown][] = []

const answer = () =>
  toSettingsNode({
    ok: true,
    flow_id: FLOW,
    node_key: 'gn_wp',
    card_kind: 'plan',
    locked,
    fields: Object.fromEntries(PLAN_FIELDS.map(([name, def, choices]) => [name, { value: name in stored ? stored[name] : def, stored: name in stored, default: def, choices }])),
  })

const invalidValue = (reason: string, field = 'value') => ({
  isAxiosError: true,
  response: { status: 422, data: { ok: false, error: { code: 'invalid_request', message: 'invalid', params: { errors: [{ field, reason }] } } } },
})
const refused = (status: number, code: string) => ({ isAxiosError: true, response: { status, data: { ok: false, error: { code, message: code, params: {} } } } })

function serve() {
  nodeRead.mockImplementation(async (flowId: string, nodeKey: string) => {
    if (flowId !== FLOW || nodeKey !== 'gn_wp') throw refused(404, 'node_not_found')
    return answer()
  })
  nodeWrite.mockImplementation(async (flowId: string, nodeKey: string, field: string, value: unknown) => {
    writes.push([flowId, nodeKey, field, value])
    if (refusal) {
      const thrown = refusal
      refusal = null
      throw thrown
    }
    if (locked) throw refused(409, 'node_settings_locked')
    if (!PLAN_FIELDS.some(([name]) => name === field)) throw invalidValue('not_a_setting_of_this_card', 'field')
    if (field === 'pre_instruction_auto' && value !== null && typeof value !== 'boolean') throw invalidValue('not_a_boolean')
    if (value === null) delete stored[field]
    else stored[field] = value
    return answer()
  })
  choicesRead.mockImplementation(async () =>
    toSettingsChoices({ providers: [{ id: 'aip_opus', name: 'Claude Opus 5' }, { id: 'aip_sol', name: 'GPT-5.6 Sol' }], execution_policy: { repeat_count_max: 3 } }),
  )
}

// --- The WP card ------------------------------------------------------------------------------------------

const doc = (key: string, type: string) => `${FLOW}.${key}-${type}`
const line = (slot: string, docType: string, docId: string): FlowGraphCardDocument => ({ slot, docType, author: 'human', docId, title: `카드 · ${slot}`, status: 'open', docReviewStatus: null })

const WP_CARD: FlowGraphNode = {
  id: 'gn_wp',
  kind: 'plan',
  title: '작업계획',
  state: 'draft',
  docType: 'WP',
  x: 0,
  y: 0,
  definition: { nodeType: 'document', docType: 'WP', paletteItemId: null, label: '작업계획', assignee: null, rootBound: false },
  document: { docId: doc('0006', 'WP'), typeCode: 'WP', seq: 6, title: '작업계획', docReviewStatus: null, status: 'open', supersedes: [] },
  cardDocuments: [line('plan', 'WP', doc('0006', 'WP'))],
  slot: null,
  nodeRun: null,
}

const GRAPH: FlowGraph = { flowId: FLOW, nodes: [WP_CARD], edges: [], ownership: 'graph', viewMode: 'draft', draft: null }

const targetFor = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...toInspectorTarget({ projectId: PROJECT, graph: GRAPH, node: WP_CARD })!, ...fields })

// --- Mounting ------------------------------------------------------------------------------------------

let pinia: Pinia
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stored = {}
  locked = false
  refusal = null
  writes.length = 0
  transport.calls.length = 0
  for (const mock of [nodeRead, nodeWrite, choicesRead]) mock.mockReset()
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

async function mountTab(target: InspectorTarget = targetFor()) {
  wrapper = mount(SettingsTab, {
    props: { target },
    global: { plugins: [i18n, pinia], provide: { [INSPECTOR_HOST as symbol]: { request: () => undefined } } },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

type W = VueWrapper<any>
const toggle = (w: W) => w.get('[data-test="settings-pre-instruction-switch"]')
const isOn = (w: W) => toggle(w).attributes('aria-checked') === 'true'
const select = (w: W, field: string) => w.get(`select[data-test="inspector-run-settings-${field}"]`)
const shownOf = (w: W, field: string) => select(w, field).findAll('option').find((option) => (option.element as HTMLOptionElement).selected)?.text()
const rowLabels = (w: W) => w.findAll('[data-test="inspector-control-row"]').map((row) => row.attributes('data-label'))
const failureOf = (w: W, field: string) => {
  const icon = w.find(`[data-test="inspector-failure-icon"][data-field="${field}"]`)
  return icon.exists() ? (icon.attributes('title') ?? null) : null
}
async function flip(w: W) {
  await toggle(w).trigger('click')
  await flushPromises()
}
async function pick(w: W, field: string, value: string) {
  await select(w, field).setValue(value)
  await flushPromises()
}
async function openAdvanced(w: W) {
  await w.get('[data-test="inspector-run-settings-advanced"]').trigger('click')
  await flushPromises()
}

// --- The form ---------------------------------------------------------------------------------------------

describe('the WP card’s settings form (flowgate.v02.0072 T#3)', () => {
  it('a Graph WP card is the plan kind; a v0.1 plan Node has no settings', () => {
    expect(inspectorSettingsKind(targetFor())).toBe('plan')
    expect(inspectorSettingsKind({ ...targetFor(), nodeType: null, cardDocType: null, slot: null })).toBeNull()
  })

  it('[switch] AI 사전 지시서 작성 first, then 작업 AI · 재시작 | 검수 AI · 검수 횟수, 고급: 실행 제한 시간 · 검수 기준', async () => {
    const w = await mountTab()
    expect(w.get('[data-test="inspector-settings"]').attributes('data-kind')).toBe('plan')
    const line = w.get('[data-test="settings-pre-instruction"]')
    const children = [...line.get('.inspector-settings-switch').element.children]
    expect(children[0].getAttribute('role')).toBe('switch')
    expect(children[1].textContent).toBe('AI 사전 지시서 작성')
    expect(w.get('[data-test="settings-pre-instruction-label"]').attributes('title')).toBe(
      '카드에만 저장 · 자동 작성은 아직 연결되지 않음 · 꺼도 단계의 사전 지시서는 지워지지 않음',
    )
    expect(isOn(w)).toBe(false)
    // No 지시서 / 지시 AI rows on a plan card.
    expect(w.find('[data-test="settings-instruction"]').exists()).toBe(false)
    expect(w.find('[data-test="settings-instruction-ai"]').exists()).toBe(false)
    expect(rowLabels(w)).toEqual(['작업 AI', '재시작', '검수 AI', '검수 횟수'])
    expect(w.get('[data-test="inspector-run-settings"]').attributes('data-layout')).toBe('run')
    await openAdvanced(w)
    expect(rowLabels(w)).toEqual(['작업 AI', '재시작', '검수 AI', '검수 횟수', '실행 제한 시간', '검수 기준'])
    expect(shownOf(w, 'run_ai')).toBe('담당 AI')
    expect(shownOf(w, 'review_ai')).toBe('기본 검수 AI')
    expect(shownOf(w, 'time_limit')).toBe('60분')
    expect(shownOf(w, 'review_basis')).toBe('문서 타입 기본')
    // The run settings block and the switch are v0.2 parts (the common Select / Toggle), not a copied drawing.
    expect(toggle(w).classes()).toContain('ui-toggle')
    expect(select(w, 'run_ai').classes()).toContain('ui-select')
  })

  it('English and Japanese wording', async () => {
    i18n.global.locale.value = 'en'
    let w = await mountTab()
    expect(w.get('[data-test="settings-pre-instruction-label"]').text()).toBe('Write AI pre-instructions')
    expect(rowLabels(w)[0]).toBe('Work AI')
    w.unmount()
    i18n.global.locale.value = 'ja'
    w = await mountTab()
    expect(w.get('[data-test="settings-pre-instruction-label"]').text()).toBe('AI事前指示書の作成')
    expect(rowLabels(w)[0]).toBe('作業AI')
  })
})

// --- The switch --------------------------------------------------------------------------------------------

describe('the AI 사전 지시서 작성 switch is a card setting (flowgate.v02.0072 T#3)', () => {
  it('on: one PATCH of true, shown as answered and read again; off: one PATCH of false — no plan document call', async () => {
    let w = await mountTab()
    await flip(w)
    expect(writes).toEqual([[FLOW, 'gn_wp', 'pre_instruction_auto', true]])
    expect(isOn(w)).toBe(true)
    expect(stored).toEqual({ pre_instruction_auto: true })
    w.unmount()
    w = await mountTab()
    expect(nodeRead).toHaveBeenCalledTimes(2)
    expect(isOn(w)).toBe(true)
    await flip(w)
    expect(writes).toEqual([
      [FLOW, 'gn_wp', 'pre_instruction_auto', true],
      [FLOW, 'gn_wp', 'pre_instruction_auto', false],
    ])
    expect(isOn(w)).toBe(false)
    expect(stored).toEqual({ pre_instruction_auto: false })
    // Off changes the card's setting only: nothing went to the plan document (no work-plan GET / PUT) or anywhere else.
    expect(transport.calls).toEqual([])
  })

  it('a refused change (422 not_a_boolean, 409 locked) is the red ! beside the switch; the shown value stays the server’s', async () => {
    const w = await mountTab()
    refusal = invalidValue('not_a_boolean')
    await flip(w)
    expect(failureOf(w, 'pre_instruction_auto')).toBe('켜기 / 끄기 값이 아닙니다')
    expect(isOn(w)).toBe(false)
    refusal = refused(409, 'node_settings_locked')
    await flip(w)
    expect(failureOf(w, 'pre_instruction_auto')).toBe('실행 중이라 바꿀 수 없습니다')
    expect(isOn(w)).toBe(false)
  })

  it('a running card, the Flow’s run lock, an unconfirmed run status, a read-only view, a card not saved yet: off, nothing sent', async () => {
    locked = true
    let w = await mountTab()
    expect(w.get('[data-test="inspector-lock-note"]').text()).toBe('🔒 실행 중')
    expect(toggle(w).attributes('disabled')).toBeDefined()
    await flip(w)
    w.unmount()
    locked = false
    for (const fields of [{ editLock: 'run' as const }, { editLock: 'unknown' as const }, { readOnly: 'revision' as const }, { readOnly: 'conflict' as const }, { nodeId: 'tmp_wp' }]) {
      w = await mountTab(targetFor(fields))
      expect(toggle(w).attributes('disabled'), JSON.stringify(fields)).toBeDefined()
      expect(select(w, 'run_ai').attributes('disabled'), JSON.stringify(fields)).toBeDefined()
      await flip(w)
      w.unmount()
    }
    wrapper = null
    expect(writes).toEqual([])
  })
})

// --- The run block --------------------------------------------------------------------------------------

describe('the run block of a WP card (flowgate.v02.0072 T#3)', () => {
  it('each item is its own card setting PATCH; review 0 keeps the review AI off', async () => {
    const w = await mountTab()
    expect(select(w, 'review_ai').attributes('disabled')).toBeDefined()
    await pick(w, 'run_ai', 'aip_opus')
    await pick(w, 'restart', '-1')
    await pick(w, 'review_count', '2')
    expect(select(w, 'review_ai').attributes('disabled')).toBeUndefined()
    await pick(w, 'review_ai', 'aip_sol')
    await openAdvanced(w)
    await pick(w, 'time_limit', '120')
    await pick(w, 'review_basis', 'last')
    expect(writes).toEqual([
      [FLOW, 'gn_wp', 'run_ai', 'aip_opus'],
      [FLOW, 'gn_wp', 'restart', -1],
      [FLOW, 'gn_wp', 'review_count', 2],
      [FLOW, 'gn_wp', 'review_ai', 'aip_sol'],
      [FLOW, 'gn_wp', 'time_limit', 120],
      [FLOW, 'gn_wp', 'review_basis', 'last'],
    ])
    expect(shownOf(w, 'run_ai')).toBe('Claude Opus 5')
    expect(shownOf(w, 'restart')).toBe('끝날 때까지')
    expect(isOn(w)).toBe(false)
    expect(transport.calls).toEqual([])
  })

  it('a stored AI no longer offered stays shown, marked; none is sent as null (the default)', async () => {
    stored = { run_ai: 'aip_gone' }
    const w = await mountTab()
    expect(shownOf(w, 'run_ai')).toBe('aip_gone (없는 AI)')
    await pick(w, 'run_ai', '')
    expect(writes).toEqual([[FLOW, 'gn_wp', 'run_ai', null]])
    expect(stored).toEqual({})
  })

  it('a failed first read is the read failure with a retry; the retry recovers the form', async () => {
    nodeRead.mockRejectedValueOnce(refused(500, 'boom'))
    const w = await mountTab()
    expect(w.find('[data-test="settings-pre-instruction"]').exists()).toBe(false)
    expect(w.find('[data-test="inspector-read-failure"]').exists()).toBe(true)
    await w.get('[data-test="inspector-read-retry"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="settings-pre-instruction"]').exists()).toBe(true)
  })
})

// --- The Frame -------------------------------------------------------------------------------------------

describe('the WP card’s settings tab in the real Frame and registry (flowgate.v02.0072 T#3)', () => {
  it('a Graph WP card has the 설정 tab with this form', async () => {
    wrapper = mount(DesignerInspector, { props: { selection: { projectId: PROJECT, graph: GRAPH, node: WP_CARD } }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    await flushPromises()
    const tab = wrapper.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((item) => item.text() === '설정')
    expect(tab).toBeDefined()
    await tab!.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-test="designer-inspector-section"]').map((section) => section.attributes('data-key'))).toEqual(['settings'])
    expect(wrapper.find('[data-test="settings-pre-instruction-switch"]').exists()).toBe(true)
    expect(rowLabels(wrapper)).toEqual(['작업 AI', '재시작', '검수 AI', '검수 횟수'])
  })
})
