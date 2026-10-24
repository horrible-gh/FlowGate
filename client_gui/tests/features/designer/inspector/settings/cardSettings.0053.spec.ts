/**
 * flowgate.v02.0053 T#7 — the settings tab of a Graph card, drawn as v7 draws it and kept by the card itself
 * (T#2 `GET / PATCH /flows/{flow_id}/nodes/{node_key}/settings`, one value per change, no save button).
 * `@/features/document` is mocked as a small card settings server with the T#2 fields, defaults and choices:
 *
 * - every card kind's blocks, items and order as v7 (run: 지시서 · 지시 AI · 실행 설정 + 고급 ▾; test:
 *   지시서 · 시나리오 작성 · 실행; requirement: AI 위임 · 범위 · AI Team · 안전 한도; check: 용도 ·
 *   반영 방식 · 병합 대상 · 원격 반영 · 브랜치 보관) — on a card just placed and on a done card alike;
 * - the items v0.2 had "미연결" (지시서 사용 / 생략, 재시작, 실행 제한 시간, 검수 기준) are saved;
 * - a change is one PATCH of one value at once, shown while it waits, answered by the card's settings;
 * - a running card is 🔒 and off; a refusal is a red ! beside its control (the reason its tooltip); a card not
 *   saved yet is drawn off; no sentence, no type code on screen;
 * - a failed first read is the read failure icon (why: its tooltip) with a ↻ retry in place of the form, and
 *   the same card recovers by it; the check summary names the merge target and push shown (or not set).
 * Last, the real Frame and registry on a card just placed (T#1 card documents) and on done cards whose run
 * bound its result (N → NR, T → TR, TS → TSR — the card keeps its form).
 * flowgate.v02.0056 F2-1: the progress-after-review row is removed with its field (O1 0005-TR §4-3: a PATCH of it
 * is 422 not_a_setting_of_this_card); a RUN card has the 지시 AI row (`instruction_ai`, O2 0007-TR §4-1) right
 * under 지시서, only while the instruction is used — none is the run AI, then the assigned AI — saved as one
 * value (null for none), with its refusals (422 unknown_ai · not_a_text · invalid_text ·
 * not_a_setting_of_this_card, 409 node_settings_locked · card_settings_unavailable) as the ! beside it — the code's
 * sentence, then the server's own message as sent (x-locale) when it says more (ko / en / ja); the 0053 fields keep
 * their sentence alone.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'
import RequirementSettings from '@/features/designer/inspector/features/requirementSettings/RequirementSettings.vue'
import SettingsTab from '@/features/designer/inspector/features/settings/SettingsTab.vue'
import TestRunSettings from '@/features/designer/inspector/features/testRunSettings/TestRunSettings.vue'
import { INSPECTOR_HOST, type InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import { toInspectorTarget, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { prepareInspectorI18n } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

const nodeRead = vi.fn()
const nodeWrite = vi.fn()
const choicesRead = vi.fn()
const teamsRead = vi.fn()
const gitRead = vi.fn()
const specRead = vi.fn()

// Every other read of the Frame's features answers nothing (the settings need none of them).
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline) }
})

vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    settingsNodeRead: (...args: unknown[]) => nodeRead(...args),
    settingsNodeWrite: (...args: unknown[]) => nodeWrite(...args),
    settingsChoicesRead: (...args: unknown[]) => choicesRead(...args),
    settingsTeamsRead: (...args: unknown[]) => teamsRead(...args),
    checkGitRead: (...args: unknown[]) => gitRead(...args),
    testSpecRead: (...args: unknown[]) => specRead(...args),
  }
})

const { toSettingsNode, toSettingsChoices, toSettingsTeams, toCheckGitState, toTestSpecView } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const PROJECT = 'flowgate'
const FLOW = 'flowgate.v02.0060'

// --- The card settings server (T#2 fields.py) ------------------------------------------------------

const REPEAT = [-1, 0, 1, 2, 3]
type FieldDef = readonly [name: string, defaultValue: unknown, choices: readonly unknown[] | null]
const FIELDS: Record<string, readonly FieldDef[]> = {
  run: [
    ['instruction', 'use', ['use', 'omit']],
    ['instruction_ai', null, null],
    ['run_ai', null, null],
    ['restart', 0, REPEAT],
    ['review_ai', null, null],
    ['review_count', 0, REPEAT],
    ['time_limit', 60, [30, 45, 60, 90, 120, 180, 240]],
    ['review_basis', 'type', ['type', 'last']],
  ],
  test: [
    ['instruction', 'use', ['use', 'omit']],
    ['run_ai', null, null],
    ['review_ai', null, null],
    ['review_count', 0, REPEAT],
    ['result_cause', 'ai', ['ai', 'human']],
    ['follow_scope', 'summary', ['summary', 'full']],
  ],
  requirement: [
    ['delegation', 'off', ['off', 'on']],
    ['scope', 'end', ['end', 'confirm']],
    ['team', null, null],
    ['limit_max_nodes', null, null],
    ['limit_total_reviews', null, null],
    ['limit_retries_per_card', null, null],
    ['limit_replans', null, null],
  ],
  check: [
    ['usage', null, ['mid', 'end']],
    ['merge_mode', null, ['merge', 'commit', 'hold']],
    ['merge_target', null, null],
    ['push', null, [true, false]],
    ['keep_branch', null, [true, false]],
  ],
}

interface Card {
  kind: string
  locked: boolean
  stored: Record<string, unknown>
}

let cards: Map<string, Card>
/** The next write is this refusal (once). */
let refusal: unknown = null
/** Every PATCH: flow, node, field, value. */
const writes: [string, string, string, unknown][] = []

const axiosError = (status: number, code: string) => ({ isAxiosError: true, response: { status, data: { ok: false, error: { code, message: code, params: {} } } } })
/** flowgate.v02.0056 F2-1: a 422 refusal naming its reason (O2 0007-TR §4-1 `errors[{field, reason}]`). */
const invalidValue = (reason: string, field = 'value', message = '저장할 수 없는 값') => ({
  isAxiosError: true,
  response: { status: 422, data: { ok: false, error: { code: 'invalid_request', message, params: { errors: [{ field, reason }] } } } },
})
/** flowgate.v02.0056 F2-1: a refusal whose message (x-locale) is not its code. */
const refusedWith = (status: number, code: string, message: string) => ({ isAxiosError: true, response: { status, data: { ok: false, error: { code, message, params: {} } } } })

function answer(nodeKey: string) {
  const card = cards.get(nodeKey)!
  const fields = Object.fromEntries(
    FIELDS[card.kind].map(([name, def, choices]) => [name, { value: name in card.stored ? card.stored[name] : def, stored: name in card.stored, default: def, choices }]),
  )
  return toSettingsNode({ ok: true, flow_id: FLOW, node_key: nodeKey, card_kind: card.kind, locked: card.locked, fields })
}

function serve() {
  nodeRead.mockImplementation(async (flowId: string, nodeKey: string) => {
    if (flowId !== FLOW || !cards.has(nodeKey)) throw axiosError(404, 'node_not_found')
    return answer(nodeKey)
  })
  nodeWrite.mockImplementation(async (flowId: string, nodeKey: string, field: string, value: unknown) => {
    writes.push([flowId, nodeKey, field, value])
    if (refusal) {
      const thrown = refusal
      refusal = null
      throw thrown
    }
    const card = cards.get(nodeKey)
    if (!card) throw axiosError(404, 'node_not_found')
    if (card.locked) throw axiosError(409, 'node_settings_locked')
    const def = FIELDS[card.kind].find(([name]) => name === field)
    if (!def) throw invalidValue('not_a_setting_of_this_card', 'field')
    if (field.startsWith('limit_') && value !== null && !(Number.isInteger(value) && (value as number) >= 1 && (value as number) <= 999)) throw axiosError(422, 'invalid_request')
    if (value === null) delete card.stored[field]
    else card.stored[field] = value
    return answer(nodeKey)
  })
  choicesRead.mockImplementation(async () =>
    toSettingsChoices({ providers: [{ id: 'aip_opus', name: 'Claude Opus 5' }, { id: 'aip_sol', name: 'GPT-5.6 Sol' }], execution_policy: { repeat_count_max: 3 } }),
  )
  teamsRead.mockImplementation(async () =>
    toSettingsTeams({
      items: [
        { ai_group_id: 'grp_auth', name: '인증팀', enabled: true, supervisor: { provider_id: 'aip_opus', provider_name: 'Claude Opus 5' } },
        { ai_group_id: 'grp_basic', name: '기본팀', enabled: true, supervisor: null },
        { ai_group_id: 'grp_off', name: '꺼진 팀', enabled: false, supervisor: null },
      ],
    }),
  )
  gitRead.mockImplementation(async (groupId: string) => toCheckGitState({ ok: true, state: { group_id: groupId, status: 'awaiting_choice', branch: 'fg/0060', base_branch: 'main', default_target: 'release' } }))
  specRead.mockImplementation(async (docId: string) =>
    toTestSpecView({
      doc_id: docId,
      kind: 'TS',
      contract_version: 2,
      cases: [
        { case_id: 'c1', execution_mode: 'automated' },
        { case_id: 'c2', execution_mode: 'automated' },
        { case_id: 'c3', execution_mode: 'manual' },
        { case_id: 'c4', execution_mode: 'external' },
      ],
    }),
  )
}

// --- Cards on the Canvas ------------------------------------------------------------------------------

const doc = (key: string, type: string) => `${FLOW}.${key}-${type}`
const line = (slot: string, docType: string, docId: string, author: 'human' | 'run'): FlowGraphCardDocument => ({
  slot,
  docType,
  author,
  docId,
  title: `카드 · ${slot}`,
  status: 'draft',
  docReviewStatus: null,
})

/** A Graph card as the Canvas has it: just placed (empty documents) or done. */
function node(key: string, nodeType: string, docType: string | null, extra: Partial<FlowGraphNode> = {}): FlowGraphNode {
  return {
    id: key,
    kind: 'task',
    title: '카드',
    state: 'draft',
    docType,
    x: 0,
    y: 0,
    definition: { nodeType, docType, paletteItemId: null, label: '카드', assignee: null, rootBound: false },
    document: null,
    cardDocuments: [],
    slot: null,
    nodeRun: null,
    ...extra,
  }
}

const TASK = node('gn_task', 'document', 'T', { cardDocuments: [line('instruction', 'T', doc('0002', 'T'), 'human'), line('report', 'TR', doc('0003', 'TR'), 'run')] })
const DONE_TASK = node('gn_done', 'document', 'T', {
  state: 'done',
  document: { docId: doc('0004', 'TR'), typeCode: 'TR', seq: 4, title: '완료', docReviewStatus: 'approved', status: 'closed', supersedes: [] },
  nodeRun: { status: 'done', openedAt: '2026-10-11T01:00:00Z', finishedAt: '2026-10-11T02:00:00Z', resultDocId: doc('0004', 'TR') },
})
const TEST = node('gn_test', 'document', 'TS', { kind: 'test', cardDocuments: [line('instruction', 'TS', doc('0005', 'TS'), 'human'), line('scenario', 'TS', doc('0005', 'TS'), 'human'), line('report', 'TSR', doc('0006', 'TSR'), 'run')] })
/** Done cards whose run bound its result: the Canvas hands the result as the document (graph_projector). */
const DONE_RESEARCH = node('gn_done_n', 'document', 'N', {
  kind: 'research',
  state: 'done',
  document: { docId: doc('0007', 'NR'), typeCode: 'NR', seq: 7, title: '완료', docReviewStatus: 'approved', status: 'closed', supersedes: [] },
  nodeRun: { status: 'done', openedAt: '2026-10-11T01:00:00Z', finishedAt: '2026-10-11T02:00:00Z', resultDocId: doc('0007', 'NR') },
})
const DONE_TEST = node('gn_done_ts', 'document', 'TS', {
  kind: 'test',
  state: 'done',
  document: { docId: doc('0008', 'TSR'), typeCode: 'TSR', seq: 8, title: '완료', docReviewStatus: 'approved', status: 'closed', supersedes: [] },
  cardDocuments: [line('instruction', 'TS', doc('0005', 'TS'), 'human'), line('report', 'TSR', doc('0008', 'TSR'), 'run')],
  nodeRun: { status: 'done', openedAt: '2026-10-11T01:00:00Z', finishedAt: '2026-10-11T02:00:00Z', resultDocId: doc('0008', 'TSR') },
})
const CUSTOM = node('gn_custom', 'custom', null)
const REQ = node('gn_req', 'document', 'R', { kind: 'requirement', cardDocuments: [line('requirement', 'R', doc('0001', 'R'), 'human')] })
const GATE = node('gn_gate', 'gate', null, { kind: 'approval' })
const PLACING = node('tmp_1', 'document', 'T')

const GRAPH: FlowGraph = {
  flowId: FLOW,
  nodes: [TASK, DONE_TASK, DONE_RESEARCH, DONE_TEST, TEST, CUSTOM, REQ, GATE, PLACING],
  edges: [],
  ownership: 'unassigned',
  viewMode: 'draft',
  draft: null,
}

function targetFor(card: FlowGraphNode, fields: Partial<InspectorTarget> = {}): InspectorTarget {
  return { ...toInspectorTarget({ projectId: PROJECT, graph: GRAPH, node: card })!, ...fields }
}

function seed() {
  cards = new Map([
    ['gn_task', { kind: 'run', locked: false, stored: {} }],
    ['gn_done', { kind: 'run', locked: false, stored: { run_ai: 'aip_sol', review_count: 2, review_ai: 'aip_opus' } }],
    ['gn_done_n', { kind: 'run', locked: false, stored: { instruction: 'omit', restart: 2 } }],
    ['gn_done_ts', { kind: 'test', locked: false, stored: { review_count: 1 } }],
    ['gn_test', { kind: 'test', locked: false, stored: {} }],
    ['gn_custom', { kind: 'run', locked: false, stored: {} }],
    ['gn_req', { kind: 'requirement', locked: false, stored: {} }],
    ['gn_gate', { kind: 'check', locked: false, stored: {} }],
  ])
}

// --- Mounting ---------------------------------------------------------------------------------------

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null
let intents: InspectorHostIntent[] = []

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  intents = []
  writes.length = 0
  refusal = null
  for (const mock of [nodeRead, nodeWrite, choicesRead, teamsRead, gitRead, specRead]) mock.mockReset()
  seed()
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  vi.useRealTimers()
  document.body.innerHTML = ''
})

async function mountPart(component: unknown, target: InspectorTarget) {
  wrapper = mount(component as never, {
    props: { target },
    global: { plugins: [i18n, pinia], provide: { [INSPECTOR_HOST as symbol]: { request: (intent: InspectorHostIntent) => intents.push(intent) } } },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

// flowgate.v02.0056 F2-1: the 지시 AI is its own row's select (under 지시서), the rest the run settings block's.
const runSelect = (w: VueWrapper<any>, field: string) =>
  w.find(field === 'instruction_ai' ? 'select[data-test="settings-instruction-ai-select"]' : `select[data-test="inspector-run-settings-${field}"]`)
const valueOf = (w: VueWrapper<any>, field: string) => (runSelect(w, field).element as HTMLSelectElement).value
const labelsOf = (w: VueWrapper<any>, field: string) => runSelect(w, field).findAll('option').map((option) => option.text())
const shownOf = (w: VueWrapper<any>, field: string) => runSelect(w, field).findAll('option').find((option) => (option.element as HTMLOptionElement).selected)?.text()
const segmentOn = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`).find('[aria-checked="true"]').attributes('data-value') ?? null
const segmentPick = async (w: VueWrapper<any>, test: string, value: string) => {
  await w.get(`[data-test="${test}"] [data-value="${value}"]`).trigger('click')
  await flushPromises()
}
const isDisabled = (el: ReturnType<VueWrapper<any>['find']>) => el.attributes('disabled') !== undefined
const hintOf = (el: ReturnType<VueWrapper<any>['find']>) =>
  el.element.closest('[data-test="inspector-control-row"]')!.querySelector('.inspector-control-label')!.getAttribute('title')
const failureOf = (w: VueWrapper<any>, field: string) => {
  const icon = w.find(`[data-test="inspector-failure-icon"][data-field="${field}"]`)
  return icon.exists() ? (icon.attributes('title') ?? null) : null
}
const pick = async (w: VueWrapper<any>, field: string, value: string) => {
  await runSelect(w, field).setValue(value)
  await flushPromises()
}
const openAdvanced = async (w: VueWrapper<any>) => {
  await w.get('[data-test="inspector-run-settings-advanced"]').trigger('click')
  await flushPromises()
}
/** The read failure icon's tooltip (in the form's place), or null; it has a name and no text on screen. */
const readFailureOf = (w: VueWrapper<any>) => {
  const failure = w.find('[data-test="inspector-read-failure"]')
  if (!failure.exists()) return null
  expect(failure.text()).toBe('')
  expect(failure.get('[data-test="inspector-failure-icon"]').attributes('aria-label')).toBe('불러오지 못했습니다.')
  expect(failure.get('[data-test="inspector-read-retry"]').attributes('aria-label')).toBe('다시 시도')
  expect(failure.get('[data-test="inspector-read-retry"]').attributes('title')).toBe('다시 시도')
  return failure.get('[data-test="inspector-failure-icon"]').attributes('title') ?? null
}
const retryRead = async (w: VueWrapper<any>) => {
  await w.get('[data-test="inspector-read-retry"]').trigger('click')
  await flushPromises()
}
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })
const summaryOf = (w: VueWrapper<any>) => w.get('[data-test="check-settings-summary"]').text()

/** The visible labels of a part, in DOM order (v7 items). */
const rowLabels = (w: VueWrapper<any>) => w.findAll('[data-test="inspector-control-row"]').map((row) => row.attributes('data-label'))

// --- Run cards -----------------------------------------------------------------------------------------

describe('a run card’s settings (조사 · 작업 · 설계 · Custom) — v7 execSettings', () => {
  it('a card just placed: the v7 blocks, items and order with the card’s values (the server defaults), no sentence', async () => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    expect(nodeRead).toHaveBeenCalledWith(FLOW, 'gn_task')
    expect(choicesRead).toHaveBeenCalledWith(PROJECT)
    expect(w.get('[data-test="inspector-settings"]').attributes('data-source')).toBe('card')
    expect(w.find('[data-test="inspector-lock-note"]').exists()).toBe(false)
    expect(
      w.findAll('[data-test="settings-instruction"], [data-test="settings-policy"], [data-test="settings-instruction-ai"], [data-test="inspector-run-settings"]').map((el) => el.attributes('data-test')),
    ).toEqual(['settings-instruction', 'settings-instruction-ai', 'inspector-run-settings'])
    expect(w.get('[data-test="inspector-run-settings"] [data-test="inspector-block-title"]').text()).toBe('실행 설정')
    expect(rowLabels(w)).toEqual(['지시서', '지시 AI', '실행 AI', '재시작', '검수 AI', '검수 횟수'])
    expect(segmentOn(w, 'settings-instruction-switch')).toBe('use')
    expect(w.find('[data-test="settings-policy-note"]').exists()).toBe(false)
    // flowgate.v02.0056 F2-1: the 지시 AI — none follows the run AI (then the assigned AI).
    expect(shownOf(w, 'instruction_ai')).toBe('실행 AI 따름')
    expect(labelsOf(w, 'instruction_ai')).toEqual(['실행 AI 따름', 'Claude Opus 5', 'GPT-5.6 Sol'])
    expect(hintOf(runSelect(w, 'instruction_ai'))).toBe('지시서를 쓰는 AI · 비우면 실행 AI, 그다음 담당 AI가 씁니다')
    expect(shownOf(w, 'run_ai')).toBe('담당 AI')
    expect(labelsOf(w, 'run_ai')).toEqual(['담당 AI', 'Claude Opus 5', 'GPT-5.6 Sol'])
    expect(labelsOf(w, 'restart')).toEqual(['끝날 때까지', '안 함', '1회', '2회', '3회'])
    expect(shownOf(w, 'restart')).toBe('안 함')
    expect(shownOf(w, 'review_ai')).toBe('기본 검수 AI')
    expect(labelsOf(w, 'review_count')).toEqual(['통과할 때까지', '안 함', '1회', '2회', '3회'])
    expect(shownOf(w, 'review_count')).toBe('안 함')
    // 고급 ▾ folds 실행 제한 시간 · 검수 기준.
    expect(runSelect(w, 'time_limit').exists()).toBe(false)
    await openAdvanced(w)
    expect(rowLabels(w).slice(-2)).toEqual(['실행 제한 시간', '검수 기준'])
    expect(labelsOf(w, 'time_limit')).toEqual(['30분', '45분', '60분', '90분', '120분', '180분', '240분'])
    expect(shownOf(w, 'time_limit')).toBe('60분')
    expect(labelsOf(w, 'review_basis')).toEqual(['문서 타입 기본', '직전 반려 사유만'])
    // Every item can change (the "미연결" ones included); nothing of 0048/0052 is left.
    for (const field of ['instruction_ai', 'run_ai', 'restart', 'review_count', 'time_limit', 'review_basis']) expect(isDisabled(runSelect(w, field)), field).toBe(false)
    for (const option of w.findAll('[data-test="inspector-segment-option"]')) expect(option.attributes('disabled')).toBeUndefined()
    // With no review the review AI is off, its reason a tooltip.
    expect(isDisabled(runSelect(w, 'review_ai'))).toBe(true)
    expect(hintOf(runSelect(w, 'review_ai'))).toBe('검수 안 함 · 검수 AI 쓰지 않음')
    for (const gone of ['미연결', '저장', '단계', 'v0.1', '시작 전', '실행 중인 이 단계']) expect(w.text(), gone).not.toContain(gone)
    expect(w.find('[data-test="inspector-pane-state"]').exists()).toBe(false)
  })

  it.each([
    ['지시서 생략', 'instruction', 'omit', 'segment'],
    ['지시 AI', 'instruction_ai', 'aip_sol', 'select'],
    ['실행 AI', 'run_ai', 'aip_opus', 'select'],
    ['재시작 2회', 'restart', 2, 'select'],
    ['재시작 끝날 때까지', 'restart', -1, 'select'],
    ['검수 횟수 통과할 때까지', 'review_count', -1, 'select'],
    ['실행 제한 시간 120분', 'time_limit', 120, 'select'],
    ['검수 기준 직전 반려 사유만', 'review_basis', 'last', 'select'],
  ] as const)('%s is saved at once: one PATCH of that one value, the answer shown', async (_name, field, value, kind) => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    if (field === 'time_limit' || field === 'review_basis') await openAdvanced(w)
    if (kind === 'segment') await segmentPick(w, 'settings-instruction-switch', String(value))
    else await pick(w, field, String(value))
    expect(writes).toEqual([[FLOW, 'gn_task', field, value]])
    expect(cards.get('gn_task')!.stored).toEqual({ [field]: value })
    if (kind === 'segment') expect(segmentOn(w, 'settings-instruction-switch')).toBe(value)
    else expect(valueOf(w, field)).toBe(String(value))
    expect(failureOf(w, field)).toBeNull()
  })

  it('a review count turns the review AI on; the default AI is sent as null (the 지시 AI too)', async () => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    await pick(w, 'instruction_ai', 'aip_opus')
    await pick(w, 'instruction_ai', '')
    expect(shownOf(w, 'instruction_ai')).toBe('실행 AI 따름')
    await pick(w, 'review_count', '2')
    expect(isDisabled(runSelect(w, 'review_ai'))).toBe(false)
    await pick(w, 'review_ai', 'aip_sol')
    await pick(w, 'run_ai', 'aip_opus')
    await pick(w, 'run_ai', '')
    expect(writes.map(([, , field, value]) => [field, value])).toEqual([
      ['instruction_ai', 'aip_opus'],
      ['instruction_ai', null],
      ['review_count', 2],
      ['review_ai', 'aip_sol'],
      ['run_ai', 'aip_opus'],
      ['run_ai', null],
    ])
    expect(cards.get('gn_task')!.stored).toEqual({ review_count: 2, review_ai: 'aip_sol' })
    expect(shownOf(w, 'run_ai')).toBe('담당 AI')
  })

  it('the value sent shows at once while the server answers; changes go one after another, in order', async () => {
    const releases: (() => void)[] = []
    nodeWrite.mockImplementation(
      (flowId: string, nodeKey: string, field: string, value: unknown) =>
        new Promise((resolve) => {
          releases.push(() => {
            writes.push([flowId, nodeKey, field, value])
            cards.get(nodeKey)!.stored[field] = value
            resolve(answer(nodeKey))
          })
        }),
    )
    const w = await mountPart(SettingsTab, targetFor(TASK))
    await pick(w, 'restart', '1')
    await pick(w, 'review_count', '3')
    expect(valueOf(w, 'restart')).toBe('1')
    expect(valueOf(w, 'review_count')).toBe('3')
    // The second change waits for the first.
    expect(releases).toHaveLength(1)
    releases[0]()
    await flushPromises()
    expect(releases).toHaveLength(2)
    expect(valueOf(w, 'review_count')).toBe('3')
    releases[1]()
    await flushPromises()
    expect(writes.map(([, , field, value]) => [field, value])).toEqual([
      ['restart', 1],
      ['review_count', 3],
    ])
    expect(valueOf(w, 'restart')).toBe('1')
    expect(valueOf(w, 'review_count')).toBe('3')
  })

  it('a done card shows the same form with its values — and they can still change (T#2: only a running card is locked)', async () => {
    const w = await mountPart(SettingsTab, targetFor(DONE_TASK))
    expect(w.find('[data-test="settings-done-values"]').exists()).toBe(false)
    expect(w.find('[data-test="inspector-lock-note"]').exists()).toBe(false)
    expect(shownOf(w, 'run_ai')).toBe('GPT-5.6 Sol')
    expect(shownOf(w, 'review_count')).toBe('2회')
    expect(shownOf(w, 'review_ai')).toBe('Claude Opus 5')
    expect(rowLabels(w)).toEqual(['지시서', '지시 AI', '실행 AI', '재시작', '검수 AI', '검수 횟수'])
    await pick(w, 'restart', '1')
    expect(writes).toEqual([[FLOW, 'gn_done', 'restart', 1]])
  })

  it('a running card: one 🔒 실행 중 line, the values drawn locked, the reason a tooltip; nothing is sent', async () => {
    cards.get('gn_task')!.locked = true
    const w = await mountPart(SettingsTab, targetFor(TASK))
    expect(w.get('[data-test="inspector-lock-note"]').text()).toBe('🔒 실행 중')
    expect(w.get('[data-test="inspector-run-settings"]').classes()).toContain('is-locked')
    expect(isDisabled(runSelect(w, 'run_ai'))).toBe(true)
    expect(hintOf(runSelect(w, 'run_ai'))).toBe('실행 중')
    for (const option of w.get('[data-test="settings-instruction-switch"]').findAll('[data-test="inspector-segment-option"]')) {
      expect(option.attributes('disabled')).toBeDefined()
      expect(option.attributes('title')).toBe('실행 중')
    }
    await segmentPick(w, 'settings-instruction-switch', 'omit')
    expect(writes).toHaveLength(0)
  })

  it('the card started after it was read (409 node_settings_locked): a red ! beside the field, read again → 🔒', async () => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    cards.get('gn_task')!.locked = true
    await pick(w, 'restart', '2')
    expect(failureOf(w, 'restart')).toBe('실행 중이라 바꿀 수 없습니다')
    expect(w.get('[data-test="inspector-failure-icon"]').attributes('aria-label')).toBe('저장 실패')
    expect(w.get('[data-test="inspector-failure-icon"]').attributes('role')).toBe('img')
    expect(w.get('[data-test="inspector-failure-icon"]').text()).toBe('')
    expect(nodeRead).toHaveBeenCalledTimes(2)
    expect(w.get('[data-test="inspector-lock-note"]').text()).toBe('🔒 실행 중')
    expect(valueOf(w, 'restart')).toBe('0')
    expect(w.text()).not.toContain('바꿀 수 없습니다')
  })

  it.each([
    ['422', axiosError(422, 'invalid_request'), '저장할 수 없는 값입니다'],
    ['403', axiosError(403, 'forbidden'), '권한이 없습니다'],
    ['404', axiosError(404, 'node_not_found'), '카드를 찾을 수 없습니다'],
    ['409 card_settings_unavailable', axiosError(409, 'card_settings_unavailable'), '서버에 카드 설정이 없습니다'],
    ['network', { isAxiosError: true, message: 'Network Error' }, '서버에 연결하지 못했습니다'],
  ])('a refused change (%s): the ! on that field only, the field back to the server’s value; the next change clears it', async (_name, refused, text) => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    refusal = refused
    await pick(w, 'review_count', '1')
    expect(failureOf(w, 'review_count')).toBe(text)
    expect(failureOf(w, 'restart')).toBeNull()
    expect(valueOf(w, 'review_count')).toBe('0')
    await pick(w, 'review_count', '1')
    expect(failureOf(w, 'review_count')).toBeNull()
    expect(cards.get('gn_task')!.stored).toEqual({ review_count: 1 })
  })

  it('409 flow_disposed: the ! and everything off (disposed) until another card is selected', async () => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    refusal = axiosError(409, 'flow_disposed')
    await segmentPick(w, 'settings-instruction-switch', 'omit')
    expect(failureOf(w, 'instruction')).toBe('폐기된 그룹입니다')
    expect(isDisabled(runSelect(w, 'run_ai'))).toBe(true)
    expect(hintOf(runSelect(w, 'run_ai'))).toBe('폐기된 그룹')
    await w.setProps({ target: targetFor(DONE_TASK) })
    await flushPromises()
    expect(isDisabled(runSelect(w, 'run_ai'))).toBe(false)
    expect(failureOf(w, 'instruction')).toBeNull()
  })

  it('a card just placed (its key not the server’s yet): the form drawn off, nothing read; the stored key reads it', async () => {
    const w = await mountPart(SettingsTab, targetFor(PLACING))
    expect(nodeRead).not.toHaveBeenCalled()
    expect(rowLabels(w)).toEqual(['지시서', '지시 AI', '실행 AI', '재시작', '검수 AI', '검수 횟수'])
    expect(isDisabled(runSelect(w, 'run_ai'))).toBe(true)
    expect(hintOf(runSelect(w, 'run_ai'))).toBe('카드를 저장하는 중')
    expect(w.find('[data-test="inspector-pane-state"]').exists()).toBe(false)
    await w.setProps({ target: targetFor(TASK) })
    await flushPromises()
    expect(nodeRead).toHaveBeenCalledWith(FLOW, 'gn_task')
    expect(isDisabled(runSelect(w, 'run_ai'))).toBe(false)
  })

  it('a revision view is read only: the values shown, every control off with the reason', async () => {
    const w = await mountPart(SettingsTab, targetFor(TASK, { readOnly: 'revision' }))
    expect(isDisabled(runSelect(w, 'restart'))).toBe(true)
    expect(hintOf(runSelect(w, 'restart'))).toBe('확정된 리비전 보기 · 읽기 전용')
  })

  it('a failed read: the read failure icon (why: its tooltip) and a ↻ retry that reads the same card again', async () => {
    nodeRead.mockRejectedValueOnce(axiosError(403, 'forbidden'))
    const w = await mountPart(SettingsTab, targetFor(TASK))
    expect(readFailureOf(w)).toBe('볼 권한이 없습니다.')
    expect(rowLabels(w)).toEqual([])
    await retryRead(w)
    expect(nodeRead).toHaveBeenCalledTimes(2)
    expect(readFailureOf(w)).toBeNull()
    expect(rowLabels(w)).toContain('실행 AI')
  })

  it.each(['graph_run_status_changed', 'graph_node_state_changed', 'ai_invoke_started', 'ai_invoke_finished'])('%s of the card’s Flow reads it again (its 🔒); another Flow’s does not', async (event) => {
    vi.useFakeTimers()
    const w = await mountPart(SettingsTab, targetFor(TASK))
    stream.open()
    stream.emit(event, { groupId: 'flowgate.v02.0099', docId: null })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(nodeRead).toHaveBeenCalledTimes(1)
    cards.get('gn_task')!.locked = true
    stream.emit(event, { groupId: FLOW, docId: null })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(nodeRead).toHaveBeenCalledTimes(2)
    expect(w.get('[data-test="inspector-lock-note"]').text()).toBe('🔒 실행 중')
  })

  it('the Canvas run state of the card moving reads it again', async () => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    cards.get('gn_task')!.locked = true
    await w.setProps({ target: targetFor(TASK, { nodeRun: { status: 'open', openedAt: '2026-10-11T03:00:00Z', finishedAt: null, resultDocId: null } }) })
    await flushPromises()
    expect(nodeRead).toHaveBeenCalledTimes(2)
    expect(w.find('[data-test="inspector-lock-note"]').exists()).toBe(true)
  })

  it('a Custom card has the same form (the server keeps it as a run card)', async () => {
    const w = await mountPart(SettingsTab, targetFor(CUSTOM))
    expect(rowLabels(w)).toEqual(['지시서', '지시 AI', '실행 AI', '재시작', '검수 AI', '검수 횟수'])
    await segmentPick(w, 'settings-instruction-switch', 'omit')
    expect(writes).toEqual([[FLOW, 'gn_custom', 'instruction', 'omit']])
  })

  it('follows the locale (en / ja)', async () => {
    i18n.global.locale.value = 'en'
    let w = await mountPart(SettingsTab, targetFor(TASK))
    expect(rowLabels(w)).toEqual(['Instruction', 'Instruction AI', 'Run AI', 'Restart', 'Review AI', 'Review rounds'])
    expect(shownOf(w, 'run_ai')).toBe('Assigned AI')
    wrapper!.unmount()
    i18n.global.locale.value = 'ja'
    w = await mountPart(SettingsTab, targetFor(TASK))
    expect(rowLabels(w)).toEqual(['指示書', '指示AI', '実行AI', '再開', 'レビューAI', 'レビュー回数'])
  })
})

// --- The 지시 AI (flowgate.v02.0056 F2-1, O2 0007-TR §4-1) ---------------------------------------------------

describe('a run card’s 지시 AI (instruction_ai) — shown while the instruction is used, saved as one value', () => {
  it('omitting the instruction takes the 지시 AI row away (the server does not read it); using it again brings it back', async () => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    expect(w.find('[data-test="settings-instruction-ai"]').exists()).toBe(true)
    await segmentPick(w, 'settings-instruction-switch', 'omit')
    expect(w.find('[data-test="settings-instruction-ai"]').exists()).toBe(false)
    expect(rowLabels(w)).toEqual(['지시서', '실행 AI', '재시작', '검수 AI', '검수 횟수'])
    await segmentPick(w, 'settings-instruction-switch', 'use')
    expect(w.find('[data-test="settings-instruction-ai"]').exists()).toBe(true)
    expect(writes.map(([, , field, value]) => [field, value])).toEqual([
      ['instruction', 'omit'],
      ['instruction', 'use'],
    ])
  })

  it('a stored 지시 AI is shown; none is sent as null (back to the run AI)', async () => {
    cards.get('gn_task')!.stored = { instruction_ai: 'aip_sol' }
    const w = await mountPart(SettingsTab, targetFor(TASK))
    expect(valueOf(w, 'instruction_ai')).toBe('aip_sol')
    expect(shownOf(w, 'instruction_ai')).toBe('GPT-5.6 Sol')
    await pick(w, 'instruction_ai', '')
    expect(writes).toEqual([[FLOW, 'gn_task', 'instruction_ai', null]])
    expect(cards.get('gn_task')!.stored).toEqual({})
  })

  it('a stored AI no longer offered stays shown, marked', async () => {
    cards.get('gn_task')!.stored = { instruction_ai: 'aip_gone' }
    const w = await mountPart(SettingsTab, targetFor(TASK))
    expect(valueOf(w, 'instruction_ai')).toBe('aip_gone')
    expect(labelsOf(w, 'instruction_ai').at(-1)).toBe('aip_gone (없는 AI)')
  })

  it.each([
    // The server's message follows the code's sentence; a message that only repeats the code is not shown.
    ['422 unknown_ai', invalidValue('unknown_ai'), '없는 AI 연결입니다 — 저장할 수 없는 값'],
    ['422 not_a_text', invalidValue('not_a_text'), '값이 비어 있거나 글자가 아닙니다 — 저장할 수 없는 값'],
    ['422 invalid_text', invalidValue('invalid_text'), '값이 너무 길거나 쓸 수 없는 글자가 있습니다 — 저장할 수 없는 값'],
    ['422 not_a_setting_of_this_card', invalidValue('not_a_setting_of_this_card', 'field'), '이 카드에는 없는 설정입니다 — 저장할 수 없는 값'],
    ['422 without a reason', axiosError(422, 'invalid_request'), '저장할 수 없는 값입니다'],
    ['409 node_settings_locked', axiosError(409, 'node_settings_locked'), '실행 중이라 바꿀 수 없습니다'],
    ['409 card_settings_unavailable', axiosError(409, 'card_settings_unavailable'), '서버에 카드 설정이 없습니다'],
  ])('a refused 지시 AI (%s): the ! beside it says why, the value back to the server’s', async (_name, refused, text) => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    refusal = refused
    await pick(w, 'instruction_ai', 'aip_opus')
    expect(failureOf(w, 'instruction_ai')).toBe(text)
    expect(failureOf(w, 'run_ai')).toBeNull()
    expect(valueOf(w, 'instruction_ai')).toBe('')
  })

  it.each([
    ['ko', invalidValue('unknown_ai', 'value', '프로젝트에 등록된 AI가 아닙니다: aip_opus'), '없는 AI 연결입니다 — 프로젝트에 등록된 AI가 아닙니다: aip_opus'],
    ['ko', refusedWith(409, 'node_settings_locked', '카드가 실행 중이라 설정이 잠겨 있습니다'), '실행 중이라 바꿀 수 없습니다 — 카드가 실행 중이라 설정이 잠겨 있습니다'],
    ['en', invalidValue('unknown_ai', 'value', 'aip_opus is not an AI of this project'), 'No such AI connection — aip_opus is not an AI of this project'],
    ['en', refusedWith(409, 'node_settings_locked', 'The card is running, so its settings are locked'), 'Running: it cannot change now — The card is running, so its settings are locked'],
    ['ja', invalidValue('unknown_ai', 'value', 'aip_opus はこのプロジェクトの AI ではありません'), '存在しない AI 接続です — aip_opus はこのプロジェクトの AI ではありません'],
    ['ja', refusedWith(409, 'node_settings_locked', 'カードが実行中のため設定はロックされています'), '実行中のため変更できません — カードが実行中のため設定はロックされています'],
  ])('a refused 지시 AI shows the server’s own message (%s, x-locale) after the code’s sentence', async (locale, refused, text) => {
    i18n.global.locale.value = locale
    const w = await mountPart(SettingsTab, targetFor(TASK))
    refusal = refused
    await pick(w, 'instruction_ai', 'aip_opus')
    expect(failureOf(w, 'instruction_ai')).toBe(text)
  })

  it('the 0053 fields keep their sentence alone (the server’s message is the 지시 AI’s only)', async () => {
    const w = await mountPart(SettingsTab, targetFor(TASK))
    refusal = invalidValue('unknown_ai', 'value', '프로젝트에 등록된 AI가 아닙니다: aip_opus')
    await pick(w, 'run_ai', 'aip_opus')
    expect(failureOf(w, 'run_ai')).toBe('없는 AI 연결입니다')
  })

  it('a running card: the 지시 AI drawn locked, nothing sent', async () => {
    cards.get('gn_task')!.locked = true
    const w = await mountPart(SettingsTab, targetFor(TASK))
    expect(isDisabled(runSelect(w, 'instruction_ai'))).toBe(true)
    expect(hintOf(runSelect(w, 'instruction_ai'))).toBe('실행 중')
    expect(w.get('[data-test="settings-instruction-ai"]').classes()).toContain('is-locked')
  })

  it('the server takes no progress-after-review value from a run card (422 not_a_setting_of_this_card)', async () => {
    const field = ['after', 'review'].join('_')
    await expect(nodeWrite(FLOW, 'gn_task', field, 'follow')).rejects.toMatchObject({ response: { status: 422 } })
    expect(cards.get('gn_task')!.stored).toEqual({})
  })
})

// --- Test cards ---------------------------------------------------------------------------------------

describe('a test card’s settings — v7 testSettings', () => {
  it('지시서 · 시나리오 작성 (작성 AI | 검수 AI · 검수 횟수), then 실행 (케이스별), no code on screen; no 지시 AI on a test card', async () => {
    const w = await mountPart(SettingsTab, targetFor(TEST))
    expect(w.get('[data-test="inspector-run-settings"]').attributes('data-layout')).toBe('scenario')
    expect(w.get('[data-test="inspector-run-settings"] [data-test="inspector-block-title"]').text()).toBe('시나리오 작성')
    expect(w.find('[data-test="inspector-run-settings"] [data-test="inspector-block-sub"]').exists()).toBe(false)
    expect(w.find('[data-test="inspector-run-settings-advanced"]').exists()).toBe(false)
    expect(rowLabels(w)).toEqual(['지시서', '작성 AI', '검수 AI', '검수 횟수', '원인 판정', '후속 전달'])
    expect(runSelect(w, 'restart').exists()).toBe(false)
    expect(w.find('[data-test="settings-instruction-ai"]').exists()).toBe(false)
    await pick(w, 'run_ai', 'aip_sol')
    await pick(w, 'review_count', '1')
    await w.get('[data-test="settings-instruction-switch"]').trigger('click')
    await flushPromises()
    expect(writes.map(([, key, field, value]) => [key, field, value])).toEqual([
      ['gn_test', 'run_ai', 'aip_sol'],
      ['gn_test', 'review_count', 1],
      ['gn_test', 'instruction', 'omit'],
    ])
    const run = await mountPart(TestRunSettings, targetFor(TEST))
    expect(specRead).toHaveBeenCalledWith(doc('0005', 'TS'))
    expect(run.find('[data-test="inspector-test-run-settings"]').exists()).toBe(false)
    expect(run.findAll('[data-test^="test-run-mode-"]')).toHaveLength(0)
    for (const text of [w.text(), run.text()]) for (const code of ['TSC', 'TSR', 'TS)', 'automated', 'manual', 'external']) expect(text).not.toContain(code)
  })
})

// --- Requirement cards ----------------------------------------------------------------------------------

describe('a requirement card’s settings — v7 reqSettings (values only, no delegation engine)', () => {
  it('OFF: the AI 위임 block and its one short line; turning it on saves at once, shows 범위 · AI Team · 안전 한도, reads the Canvas again', async () => {
    const w = await mountPart(RequirementSettings, targetFor(REQ))
    expect(nodeRead).toHaveBeenCalledWith(FLOW, 'gn_req')
    expect(w.get('[data-test="inspector-requirement-settings"]').attributes('data-delegation')).toBe('off')
    expect(w.get('[data-test="inspector-requirement-settings"]').attributes('data-connected')).toBe('true')
    expect(w.find('[data-test="requirement-settings-unconnected"]').exists()).toBe(false)
    expect(w.get('[data-test="requirement-delegation-off"]').text()).toBe('꺼짐 — 직접 구성하거나 작업계획으로 전개')
    expect(w.find('[data-test="requirement-limits"]').exists()).toBe(false)
    await segmentPick(w, 'requirement-delegation-switch', 'on')
    expect(writes).toEqual([[FLOW, 'gn_req', 'delegation', 'on']])
    // The card's AI 위임 document line is the server's: the Canvas is read again.
    expect(intents.filter((intent) => intent.kind === 'refresh-flow')).toHaveLength(1)
    expect(w.get('[data-test="inspector-requirement-settings"]').attributes('data-delegation')).toBe('on')
    expect(rowLabels(w)).toEqual(['범위', 'AI Team', '최대 생성 Node', '총 검수 · 재작업', '카드별 재시도', '재계획'])
    expect(segmentOn(w, 'requirement-delegation-scope')).toBe('end')
    expect(w.get('[data-test="requirement-delegation-team"]').findAll('option').map((option) => option.text())).toEqual(['—', '인증팀 · Supervisor Claude Opus 5', '기본팀 · Supervisor 없음'])
    expect(w.findAll('[data-test$="-default"]')).toHaveLength(4)
  })

  it('범위, AI Team and each 안전 한도 are saved at once; an empty limit takes the default back; a bad one is refused with the !', async () => {
    cards.get('gn_req')!.stored = { delegation: 'on' }
    const w = await mountPart(RequirementSettings, targetFor(REQ))
    await segmentPick(w, 'requirement-delegation-scope', 'confirm')
    await w.get('[data-test="requirement-delegation-team"]').setValue('grp_auth')
    await flushPromises()
    const limit = w.get('[data-test="requirement-limit-total_reviews"]')
    ;(limit.element as HTMLInputElement).value = '30'
    await limit.trigger('change')
    await flushPromises()
    expect(w.find('[data-test="requirement-limit-total_reviews-default"]').exists()).toBe(false)
    ;(limit.element as HTMLInputElement).value = ''
    await limit.trigger('change')
    await flushPromises()
    const replans = w.get('[data-test="requirement-limit-replans"]')
    ;(replans.element as HTMLInputElement).value = '1000'
    await replans.trigger('change')
    await flushPromises()
    await w.get('[data-test="requirement-delegation-team"]').setValue('')
    await flushPromises()
    expect(writes.map(([, , field, value]) => [field, value])).toEqual([
      ['scope', 'confirm'],
      ['team', 'grp_auth'],
      ['limit_total_reviews', 30],
      ['limit_total_reviews', null],
      ['limit_replans', 1000],
      ['team', null],
    ])
    expect(cards.get('gn_req')!.stored).toEqual({ delegation: 'on', scope: 'confirm' })
    expect(failureOf(w, 'limit_replans')).toBe('저장할 수 없는 값입니다 (1~999)')
    expect(w.find('[data-test="requirement-limit-total_reviews-default"]').exists()).toBe(true)
    expect(intents).toHaveLength(0)
  })

  it('a running card: 🔒 위임 진행 중 and every control off', async () => {
    cards.get('gn_req')!.stored = { delegation: 'on' }
    cards.get('gn_req')!.locked = true
    const w = await mountPart(RequirementSettings, targetFor(REQ))
    expect(w.get('[data-test="inspector-lock-note"]').text()).toBe('🔒 위임 진행 중')
    for (const option of w.findAll('[data-test="inspector-segment-option"]')) expect(option.attributes('disabled')).toBeDefined()
    expect(isDisabled(w.find('[data-test="requirement-delegation-team"]'))).toBe(true)
  })

  it('a failed first read: no OFF form the server may not hold — the read failure icon, and its retry recovers the same card', async () => {
    cards.get('gn_req')!.stored = { delegation: 'on', scope: 'confirm' }
    nodeRead.mockRejectedValueOnce(networkError())
    const w = await mountPart(RequirementSettings, targetFor(REQ))
    expect(nodeRead).toHaveBeenCalledTimes(1)
    expect(readFailureOf(w)).toBe('서버에 연결하지 못했습니다')
    expect(w.find('[data-test="requirement-delegation-switch"]').exists()).toBe(false)
    expect(w.find('[data-test="requirement-delegation-off"]').exists()).toBe(false)
    await retryRead(w)
    expect(nodeRead).toHaveBeenCalledTimes(2)
    expect(nodeRead).toHaveBeenLastCalledWith(FLOW, 'gn_req')
    expect(teamsRead).toHaveBeenCalledTimes(2)
    expect(readFailureOf(w)).toBeNull()
    expect(w.get('[data-test="inspector-requirement-settings"]').attributes('data-delegation')).toBe('on')
    expect(segmentOn(w, 'requirement-delegation-scope')).toBe('confirm')
    expect(w.get('[data-test="requirement-delegation-switch"] [data-value="off"]').attributes('disabled')).toBeUndefined()
    await segmentPick(w, 'requirement-delegation-scope', 'end')
    expect(writes).toEqual([[FLOW, 'gn_req', 'scope', 'end']])
  })

  it.each([
    ['403', axiosError(403, 'forbidden'), '볼 권한이 없습니다'],
    ['404', axiosError(404, 'node_not_found'), '카드를 찾을 수 없습니다'],
    ['unavailable', axiosError(409, 'card_settings_unavailable'), '서버에 카드 설정이 없습니다'],
    ['500', axiosError(500, 'internal_error'), '설정을 불러오지 못했습니다'],
  ])('a failed first read (%s) names why in the icon’s tooltip', async (_label, thrown, why) => {
    nodeRead.mockRejectedValueOnce(thrown)
    const w = await mountPart(RequirementSettings, targetFor(REQ))
    expect(readFailureOf(w)).toBe(why)
  })
})

// --- Check cards ------------------------------------------------------------------------------------------

describe('a check card’s settings — v7 confirmSettings', () => {
  it('용도 · 반영 방식 · 병합 대상 · 원격 반영 · 브랜치 보관, each saved at once; the summary and the ⛔ follow v7', async () => {
    const w = await mountPart(CheckSettings, targetFor(GATE))
    expect(nodeRead).toHaveBeenCalledWith(FLOW, 'gn_gate')
    expect(gitRead).toHaveBeenCalledWith(FLOW)
    expect(rowLabels(w)).toEqual(['반영 방식', '병합 대상 · 원격'])
    expect(w.get('[data-test="check-settings-usage"]').text()).toContain('용도')
    expect(w.get('[data-test="check-settings-summary"]').text()).toBe('반영 방식은 최종 확인 때 고름')
    expect(w.get('[data-test="check-settings-target"]').findAll('option').map((option) => option.text())).toEqual(['—', 'release', 'main'])
    await segmentPick(w, 'check-settings-usage-value', 'end')
    await segmentPick(w, 'check-settings-scope', 'merge')
    // No target or push chosen yet: the summary says so (not v7's sample "main · 원격 반영").
    expect(summaryOf(w)).toBe('작업 브랜치를 병합 · 병합 대상 · 원격 반영 미지정 → 그룹 종료')
    expect(w.get('[data-test="check-settings-scope"] [data-value="hold"]').text()).toBe('보류 ⛔')
    await w.get('[data-test="check-settings-target"]').setValue('main')
    await flushPromises()
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합 · 원격 반영 미지정 → 그룹 종료')
    await w.get('[data-test="check-settings-push"]').trigger('click')
    await flushPromises()
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합하고 원격 반영 → 그룹 종료')
    await w.get('[data-test="check-settings-keep-branch"]').trigger('click')
    await flushPromises()
    await segmentPick(w, 'check-settings-scope', 'hold')
    expect(w.get('[data-test="check-settings-hold-block"]').text()).toContain('⛔ 보류 + 그룹 종료는 함께 고를 수 없습니다')
    expect(writes.map(([, , field, value]) => [field, value])).toEqual([
      ['usage', 'end'],
      ['merge_mode', 'merge'],
      ['merge_target', 'main'],
      ['push', true],
      ['keep_branch', true],
      ['merge_mode', 'hold'],
    ])
    expect(cards.get('gn_gate')!.stored).toEqual({ usage: 'end', merge_mode: 'hold', merge_target: 'main', push: true, keep_branch: true })
    expect(w.get('[data-test="check-settings-push"]').attributes('aria-checked')).toBe('true')
  })

  it('the summary is the values shown: release without push is not main with push; a value being sent shows at once', async () => {
    cards.get('gn_gate')!.stored = { usage: 'mid', merge_mode: 'merge', merge_target: 'main', push: true }
    const w = await mountPart(CheckSettings, targetFor(GATE))
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합하고 원격 반영')
    await w.get('[data-test="check-settings-target"]').setValue('release')
    await flushPromises()
    await w.get('[data-test="check-settings-push"]').trigger('click')
    await flushPromises()
    expect(cards.get('gn_gate')!.stored).toMatchObject({ merge_target: 'release', push: false })
    expect(summaryOf(w)).toBe('작업 브랜치를 release에 병합 · 원격 반영 안 함')
    await w.get('[data-test="check-settings-target"]').setValue('')
    await flushPromises()
    expect(summaryOf(w)).toBe('작업 브랜치를 병합 · 원격 반영 안 함 · 병합 대상 미지정')
    // While a change waits for its answer, the summary already follows the value sent.
    let answerNow: () => void = () => {}
    nodeWrite.mockImplementationOnce(
      (flowId: string, nodeKey: string, field: string, value: unknown) =>
        new Promise((resolve) => {
          answerNow = () => {
            writes.push([flowId, nodeKey, field, value])
            cards.get(nodeKey)!.stored[field] = value
            resolve(answer(nodeKey))
          }
        }),
    )
    await w.get('[data-test="check-settings-target"]').setValue('main')
    await flushPromises()
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합 · 원격 반영 안 함')
    answerNow()
    await flushPromises()
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합 · 원격 반영 안 함')
    await segmentPick(w, 'check-settings-scope', 'commit')
    expect(summaryOf(w)).toBe('작업 브랜치에 커밋만 남김 (정상 완료)')
    await segmentPick(w, 'check-settings-usage-value', 'end')
    expect(summaryOf(w)).toBe('작업 브랜치에 커밋만 남김 (정상 완료) → 그룹 종료')
  })

  it('the summary follows the locale with the values (en / ja)', async () => {
    cards.get('gn_gate')!.stored = { usage: 'end', merge_mode: 'merge', merge_target: 'release', push: false }
    i18n.global.locale.value = 'en'
    const en = await mountPart(CheckSettings, targetFor(GATE))
    expect(summaryOf(en)).toBe('Merge the work branch into release · no push → group end')
    en.unmount()
    i18n.global.locale.value = 'ja'
    const ja = await mountPart(CheckSettings, targetFor(GATE))
    expect(summaryOf(ja)).toBe('作業ブランチを release にマージ · リモート反映なし → グループ終了')
  })

  it('a failed first read: the read failure icon in place of the form, and its retry recovers the same card', async () => {
    cards.get('gn_gate')!.stored = { usage: 'end', merge_mode: 'merge', merge_target: 'release', push: false }
    nodeRead.mockRejectedValueOnce(networkError())
    const w = await mountPart(CheckSettings, targetFor(GATE))
    expect(nodeRead).toHaveBeenCalledTimes(1)
    expect(readFailureOf(w)).toBe('서버에 연결하지 못했습니다')
    expect(w.find('[data-test="check-settings-summary"]').exists()).toBe(false)
    expect(w.find('[data-test="check-settings-scope"]').exists()).toBe(false)
    await retryRead(w)
    expect(nodeRead).toHaveBeenCalledTimes(2)
    expect(gitRead).toHaveBeenCalledTimes(2)
    expect(readFailureOf(w)).toBeNull()
    expect(summaryOf(w)).toBe('작업 브랜치를 release에 병합 · 원격 반영 안 함 → 그룹 종료')
    await segmentPick(w, 'check-settings-scope', 'commit')
    expect(writes).toEqual([[FLOW, 'gn_gate', 'merge_mode', 'commit']])
  })

  /** Every PATCH waits until released (in the order sent); a release with a refusal throws it. */
  function holdWrites() {
    const releases: ((fail?: unknown) => void)[] = []
    nodeWrite.mockImplementation(
      (flowId: string, nodeKey: string, field: string, value: unknown) =>
        new Promise((resolve, reject) => {
          releases.push((fail?: unknown) => {
            writes.push([flowId, nodeKey, field, value])
            if (fail) return reject(fail)
            cards.get(nodeKey)!.stored[field] = value
            resolve(answer(nodeKey))
          })
        }),
    )
    return releases
  }

  it('원격 반영 changed again before its answers (on → off → on): the last value and its summary stay until the last answer', async () => {
    cards.get('gn_gate')!.stored = { usage: 'mid', merge_mode: 'merge', merge_target: 'main', push: false }
    const releases = holdWrites()
    const w = await mountPart(CheckSettings, targetFor(GATE))
    const push = () => w.get('[data-test="check-settings-push"]')
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합 · 원격 반영 안 함')
    for (let i = 0; i < 3; i++) {
      await push().trigger('click')
      await flushPromises()
    }
    expect(push().attributes('aria-checked')).toBe('true')
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합하고 원격 반영')
    // the first answer (on) and the second (off) are not the latest change: on stays shown
    releases[0]()
    await flushPromises()
    expect(push().attributes('aria-checked')).toBe('true')
    releases[1]()
    await flushPromises()
    expect(cards.get('gn_gate')!.stored.push).toBe(false)
    expect(push().attributes('aria-checked')).toBe('true')
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합하고 원격 반영')
    releases[2]()
    await flushPromises()
    expect(writes.map(([, , field, value]) => [field, value])).toEqual([
      ['push', true],
      ['push', false],
      ['push', true],
    ])
    expect(cards.get('gn_gate')!.stored.push).toBe(true)
    expect(push().attributes('aria-checked')).toBe('true')
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합하고 원격 반영')
    expect(failureOf(w, 'push')).toBeNull()
  })

  it('an earlier change refused while the next one waits: no red ! once the next one is saved', async () => {
    cards.get('gn_gate')!.stored = { usage: 'mid', merge_mode: 'merge', merge_target: 'main', push: false }
    const releases = holdWrites()
    const w = await mountPart(CheckSettings, targetFor(GATE))
    const push = () => w.get('[data-test="check-settings-push"]')
    await push().trigger('click')
    await flushPromises()
    await push().trigger('click')
    await flushPromises()
    expect(push().attributes('aria-checked')).toBe('false')
    releases[0](axiosError(422, 'invalid_request'))
    await flushPromises()
    expect(failureOf(w, 'push')).toBeNull()
    expect(push().attributes('aria-checked')).toBe('false')
    releases[1]()
    await flushPromises()
    expect(writes.map(([, , field, value]) => [field, value])).toEqual([
      ['push', true],
      ['push', false],
    ])
    expect(failureOf(w, 'push')).toBeNull()
    expect(push().attributes('aria-checked')).toBe('false')
    expect(summaryOf(w)).toBe('작업 브랜치를 main에 병합 · 원격 반영 안 함')
  })

  it('a refused change: the ! beside its control', async () => {
    const w = await mountPart(CheckSettings, targetFor(GATE))
    refusal = axiosError(409, 'node_settings_locked')
    await w.get('[data-test="check-settings-push"]').trigger('click')
    await flushPromises()
    expect(failureOf(w, 'push')).toBe('실행 중이라 바꿀 수 없습니다')
  })
})

// --- The Frame ----------------------------------------------------------------------------------------------

describe('the settings tab of a card just placed and of a done card, in the real Frame and registry (완료 기준)', () => {
  async function mountFrame(card: FlowGraphNode) {
    wrapper = mount(DesignerInspector, { props: { selection: { projectId: PROJECT, graph: GRAPH, node: card } }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    await flushPromises()
    await wrapper.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((tab) => tab.text() === '설정')!.trigger('click')
    await flushPromises()
    return wrapper
  }
  const sections = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-section"]').map((section) => section.attributes('data-key'))

  it.each([
    [TASK, ['settings'], ['지시서', '지시 AI', '실행 AI', '재시작', '검수 AI', '검수 횟수']],
    [CUSTOM, ['settings'], ['지시서', '지시 AI', '실행 AI', '재시작', '검수 AI', '검수 횟수']],
    [TEST, ['settings', 'testRunSettings'], ['지시서', '작성 AI', '검수 AI', '검수 횟수', '원인 판정', '후속 전달']],
    [REQ, ['requirementSettings'], []],
    [GATE, ['checkSettings'], ['반영 방식', '병합 대상 · 원격']],
  ] as const)('a %s card: its settings form, no "v0.1 단계" line', async (card, parts, labels) => {
    const w = await mountFrame(card)
    expect(sections(w)).toEqual(parts)
    expect(rowLabels(w)).toEqual(labels)
    expect(w.find('[data-test="designer-inspector-tab-state"]').exists()).toBe(false)
    expect(w.text()).not.toContain('v0.1 단계')
    expect(w.text()).not.toContain('Inspector에서 아직 지원하지 않습니다')
  })

  // The Canvas hands a done card its run's result as the document (N → NR, T → TR, TS → TSR); the settings
  // stay the card's (T#2 keeps them by the card definition).
  it.each([
    [DONE_RESEARCH, 'NR', ['settings'], ['지시서', '실행 AI', '재시작', '검수 AI', '검수 횟수']],
    [DONE_TASK, 'TR', ['settings'], ['지시서', '지시 AI', '실행 AI', '재시작', '검수 AI', '검수 횟수']],
    [DONE_TEST, 'TSR', ['settings', 'testRunSettings'], ['지시서', '작성 AI', '검수 AI', '검수 횟수', '원인 판정', '후속 전달']],
  ] as const)('a done card bound to its result (%#) keeps the same form', async (card, result, parts, labels) => {
    expect(targetFor(card)).toMatchObject({ docType: result, cardDocType: card.definition!.docType })
    const w = await mountFrame(card)
    expect(sections(w)).toEqual(parts)
    expect(rowLabels(w)).toEqual(labels)
    expect(nodeRead).toHaveBeenCalledWith(FLOW, card.id)
    expect(w.find('[data-test="settings-done-values"]').exists()).toBe(false)
  })

  it('a done card bound to its result shows its stored values and a change is saved', async () => {
    const w = await mountFrame(DONE_RESEARCH)
    expect(w.get('[data-test="settings-instruction-switch"] [aria-checked="true"]').attributes('data-value')).toBe('omit')
    expect(valueOf(w, 'restart')).toBe('2')
    await pick(w, 'restart', '1')
    expect(writes).toEqual([[FLOW, 'gn_done_n', 'restart', 1]])
    w.unmount()
    const test = await mountFrame(DONE_TEST)
    expect(valueOf(test, 'review_count')).toBe('1')
  })

  it('a change on a card just placed goes to the card’s settings at once', async () => {
    const w = await mountFrame(TASK)
    await w.get('[data-test="settings-instruction-switch"] [data-value="omit"]').trigger('click')
    await flushPromises()
    expect(writes).toEqual([[FLOW, 'gn_task', 'instruction', 'omit']])
    expect(w.get('[data-test="settings-instruction-switch"] [aria-checked="true"]').attributes('data-value')).toBe('omit')
  })
})
