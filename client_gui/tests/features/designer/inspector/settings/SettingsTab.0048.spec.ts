/**
 * flowgate.v02.0048 T#9 — the settings tab with `@/features/document` mocked as a small v0.1 sequence server
 * that also refuses (D#1 §12.1; D#2 §2.4, §4.1 tests ②~⑤): the Node's row and its run AI / review count /
 * reviewer; a change saved as a PATCH holding every row with its item_id and the fields as read, nothing
 * sent when nothing changed, the rows read again right before the save; the server writing the pending rows
 * again under new ids and the tab asking for `refresh-flow`, then finding the row under its new id on the
 * same Node (node_key kept); started rows locked, a protected pending report editable, no review -> no
 * reviewer; the refusals (409 stale / disposed / Graph owner, 423, 422, 400, network) shown and read again;
 * the guard (AI running, disposed); events; a Node change; the running hop of the row (what the run took
 * from the saved row). Last, the real registry: the settings tab on v0.1 step Nodes only.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import SettingsTab from '@/features/designer/inspector/features/settings/SettingsTab.vue'
import { SETTINGS_EVENTS } from '@/features/designer/inspector/features/settings/useSettings'
import { INSPECTOR_HOST, type InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { SELECTIONS, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

// The calls are mocked; normalization, the save payload and error reading stay real.
const sequenceRead = vi.fn()
const runRead = vi.fn()
const choicesRead = vi.fn()
const saveCall = vi.fn()
const documentRead = vi.fn()
const activityRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
const qaReadCall = vi.fn()
const attachmentsCall = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    settingsRead: (...args: unknown[]) => sequenceRead(...args),
    settingsRunRead: (...args: unknown[]) => runRead(...args),
    settingsChoicesRead: (...args: unknown[]) => choicesRead(...args),
    settingsSave: (...args: unknown[]) => saveCall(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    // The other features share the Frame in the registry cases.
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    qaRead: (...args: unknown[]) => qaReadCall(...args),
    attachmentsRead: (...args: unknown[]) => attachmentsCall(...args),
  }
})

const { toSettingsSequence, toSettingsChoices, toSettingsRun, settingsHopOf, settingsSavePayload, toReviewDocument, toReviewActivity, toDocumentDetail, toQaThread, toAttachmentsList } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0060'
const ROOT = `${FLOW}.0001-R`

// --- The server ------------------------------------------------------------------------------------

interface ServerRow {
  item_id: number
  item_seq: number
  type: string
  label: string
  status: string
  protected: boolean
  note: string
  source_doc_id: string | null
  source_revision_no: number | null
  source_wp_card_id: string | null
  provider_id: string | null
  provider_display_name: string | null
  provider_registered: boolean | null
  review_count: number
  reviewer_provider_id: string | null
  reviewer_provider_display_name: string | null
  pre_instruction_text: string | null
  pre_instruction_attachment: unknown
}

const base = (fields: Partial<ServerRow> & Pick<ServerRow, 'item_id' | 'item_seq' | 'type' | 'label' | 'status'>): ServerRow => ({
  protected: false,
  note: '',
  source_doc_id: null,
  source_revision_no: null,
  source_wp_card_id: null,
  provider_id: null,
  provider_display_name: null,
  provider_registered: null,
  review_count: 0,
  reviewer_provider_id: null,
  reviewer_provider_display_name: null,
  pre_instruction_text: null,
  pre_instruction_attachment: null,
  ...fields,
})

let rows: ServerRow[] = []
let nextId = 300
let running = false
let disposed = false
let hop: Record<string, unknown> | null = null
/** The next call of that name is this refusal (once). */
const refusals = new Map<string, unknown>()
/** Every PATCH body the server received. */
const patches: { doc_id: string; items: Record<string, unknown>[] }[] = []

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })
const locked = () => axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'm', run_live: true } })

function refusalOf(call: string): unknown {
  const refusal = refusals.get(call)
  refusals.delete(call)
  return refusal
}

function seed() {
  nextId = 300
  rows = [
    base({ item_id: 124, item_seq: 4, type: 'T', label: '작업지시', status: 'done', protected: true, note: 'T#1 지시', provider_id: 'aip_haiku', provider_display_name: 'Claude Haiku', provider_registered: true }),
    base({ item_id: 125, item_seq: 5, type: 'TR', label: '작업레포트', status: 'pending', protected: true, note: 'T#1 결과', provider_id: 'aip_haiku', provider_display_name: 'Claude Haiku', provider_registered: true }),
    base({
      item_id: 126,
      item_seq: 6,
      type: 'T',
      label: '작업지시',
      status: 'pending',
      note: 'T#2 지시 메모',
      source_doc_id: `${FLOW}.0003-WP`,
      source_revision_no: 2,
      source_wp_card_id: 'c_t2',
      provider_id: 'aip_haiku',
      provider_display_name: 'Claude Haiku',
      provider_registered: true,
      review_count: 2,
      reviewer_provider_id: 'aip_sonnet',
      reviewer_provider_display_name: 'Claude Sonnet',
      pre_instruction_text: '먼저 읽을 것',
      pre_instruction_attachment: { name: 'spec.md', size: 12 },
    }),
    base({ item_id: 127, item_seq: 7, type: 'TR', label: '작업레포트', status: 'pending', note: 'T#2 결과' }),
  ]
}

const sequenceOf = () => toSettingsSequence({ doc_id: ROOT, decided: true, sequence_id: 9, items: rows.map((row) => ({ ...row, id: row.item_id })) })

/** PATCH /workflow/sequence as the server does it (D#2 §2.1): guards, echo by item_id, pending rows written again. */
function patch(body: { doc_id: string; items: Record<string, unknown>[] }) {
  patches.push(body)
  const refusal = refusalOf('save')
  if (refusal) throw refusal
  if (disposed) throw axiosError(409, { error: 'group_disposed', doc_id: body.doc_id })
  if (running) throw locked()
  const byId = new Map(rows.map((row) => [row.item_id, row]))
  for (const [index, item] of body.items.entries()) {
    if (!byId.has(item.item_id as number)) throw axiosError(409, { error: 'sequence_item_stale', doc_id: body.doc_id, item_index: index, item_id: item.item_id })
  }
  const settings = (item: Record<string, unknown>) => ({
    provider_id: item.provider_id as string | null,
    provider_display_name: item.provider_display_name as string | null,
    review_count: item.review_count as number,
    reviewer_provider_id: item.reviewer_provider_id as string | null,
    reviewer_provider_display_name: item.reviewer_provider_display_name as string | null,
    note: item.note as string,
    pre_instruction_text: item.pre_instruction_text as string | null,
    pre_instruction_attachment: item.pre_instruction_attachment,
  })
  let seq = Math.max(...rows.map((row) => row.item_seq))
  rows = body.items.map((item) => {
    const stored = byId.get(item.item_id as number)!
    if (stored.status !== 'pending') return stored // a started row's echo is ignored
    if (stored.protected) return { ...stored, ...settings(item) } // in place
    return { ...stored, ...settings(item), item_id: nextId++, item_seq: ++seq } // written again
  })
}

function serve() {
  sequenceRead.mockImplementation(async (flowId: string) => {
    const refusal = refusalOf('read')
    if (refusal) throw refusal
    if (flowId !== FLOW) throw axiosError(400, { error: 'sequence_not_decided', flow_id: flowId })
    return sequenceOf()
  })
  runRead.mockImplementation(async (groupId: string) =>
    toSettingsRun(running ? { active: true, status: 'running', mode: 'continuous', group_id: groupId, ...(hop ?? {}) } : { ok: true, active: false, group_id: groupId }),
  )
  choicesRead.mockImplementation(async () =>
    toSettingsChoices({
      providers: [
        { id: 'aip_opus', name: 'Claude Opus' },
        { id: 'aip_sonnet', name: 'Claude Sonnet' },
        { id: 'aip_haiku', name: 'Claude Haiku' },
      ],
      default_provider_id: 'aip_sonnet',
      execution_policy: { repeat_count_max: 3 },
    }),
  )
  saveCall.mockImplementation(async (rootDocId: string, items: Parameters<typeof settingsSavePayload>[0], changes: Parameters<typeof settingsSavePayload>[1]) => {
    patch({ doc_id: rootDocId, items: settingsSavePayload(items, changes) })
    return { rootDocId, raw: { doc_id: rootDocId } }
  })
  documentRead.mockImplementation(async (docId: string) => toReviewDocument(docRow(docId)))
  activityRead.mockImplementation(async (groupId: string) => toReviewActivity({ active: running, status: running ? 'running' : null, group_id: groupId }))
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(docRow(docId)))
  relationsRead.mockImplementation(async () => [])
  qaReadCall.mockImplementation(async (docId: string) => toQaThread({ ok: true, qa: { doc_id: docId, status: null, items: [] } }))
  attachmentsCall.mockImplementation(async (docId: string) => toAttachmentsList({ data: { doc_id: docId, attachments: [], count: 0 } }))
}

const docRow = (docId: string) => ({ doc_id: docId, group_id: docId.split('.').slice(0, 3).join('.'), type_code: 'R', title: docId, status: 'open', doc_review_status: 'approved', group_disposed: disposed })

// --- The Flow on the Canvas --------------------------------------------------------------------------

const slotNode = (id: string, itemId: number, itemSeq: number, type: string, kind: string, document: FlowGraphNode['document'] = null): FlowGraphNode => ({
  id,
  kind,
  title: `${type} 단계`,
  state: document ? 'done' : 'draft',
  docType: type,
  x: 0,
  y: 0,
  document,
  slot: { itemId, itemSeq, order: itemSeq, type, label: type, derivedStatus: document ? 'done' : 'pending', protected: false, sourceWpCardId: null },
})

const STARTED = slotNode(`doc:${FLOW}.0004-T`, 124, 4, 'T', 'task', {
  docId: `${FLOW}.0004-T`,
  typeCode: 'T',
  seq: 4,
  title: 'T#1',
  docReviewStatus: 'approved',
  status: 'closed',
  supersedes: [],
})
const REPORT = slotNode('slot_tr1', 125, 5, 'TR', 'task')
const PENDING = slotNode('slot_t2', 126, 6, 'T', 'task')
const ROOT_NODE: FlowGraphNode = {
  id: `doc:${ROOT}`,
  kind: 'requirement',
  title: '요구사항',
  state: 'in_progress',
  docType: 'R',
  x: 0,
  y: 0,
  document: { docId: ROOT, typeCode: 'R', seq: 1, title: '요구사항', docReviewStatus: 'approved', status: 'open', supersedes: [] },
}
const FREE_DOC: FlowGraphNode = {
  id: `doc:${FLOW}.0009-D`,
  kind: 'design',
  title: '자유 설계 문서',
  state: 'in_progress',
  docType: 'D',
  x: 0,
  y: 0,
  document: { docId: `${FLOW}.0009-D`, typeCode: 'D', seq: 9, title: '자유 설계 문서', docReviewStatus: 'pending_review', status: 'open', supersedes: [] },
  slot: null,
}

const GRAPH: FlowGraph = { flowId: FLOW, nodes: [ROOT_NODE, STARTED, REPORT, PENDING, FREE_DOC], edges: [], ownership: 'v01', draft: null, viewMode: null }

const targetFor = (node: FlowGraphNode, fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...targetOf(selectionOf(GRAPH, node.id)), ...fields })
/** The same Node after the Canvas was read again: its slot names the row's new id. */
const movedTarget = (node: FlowGraphNode, itemId: number) => {
  const target = targetFor(node)
  return { ...target, slot: { ...target.slot!, itemId } }
}

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null
let intents: InspectorHostIntent[] = []

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  refusals.clear()
  patches.length = 0
  intents = []
  running = false
  disposed = false
  hop = null
  for (const mock of [sequenceRead, runRead, choicesRead, saveCall, documentRead, activityRead, detailRead, relationsRead, qaReadCall, attachmentsCall]) mock.mockReset()
  seed()
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

async function mountTab(target: InspectorTarget) {
  wrapper = mount(SettingsTab, {
    props: { target },
    global: { plugins: [i18n, pinia], provide: { [INSPECTOR_HOST as symbol]: { request: (intent: InspectorHostIntent) => intents.push(intent) } } },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

// flowgate.v02.0051 stage 2: the three fields sit in the common run settings block (InspectorRunSettings).
const RUN_FIELD = { provider: 'run_ai', reviewCount: 'review_count', reviewer: 'review_ai' } as const
const select = (w: VueWrapper<any>, name: 'provider' | 'reviewCount' | 'reviewer') => w.find(`select[data-test="inspector-run-settings-${RUN_FIELD[name]}"]`)
const valueOf = (w: VueWrapper<any>, name: 'provider' | 'reviewCount' | 'reviewer') => (select(w, name).element as HTMLSelectElement).value
const optionLabels = (w: VueWrapper<any>, name: 'provider' | 'reviewCount' | 'reviewer') => select(w, name).findAll('option').map((option) => option.text())
const isDisabled = (el: ReturnType<VueWrapper<any>['find']>) => el.attributes('disabled') !== undefined
const saveButton = (w: VueWrapper<any>) => w.find('[data-test="settings-save"]')
const reasonText = (w: VueWrapper<any>) => w.find('[data-test="settings-reason"]').text()
const failureText = (w: VueWrapper<any>) => w.find('[data-test="settings-failure"]').text()
const refreshes = () => intents.filter((intent) => intent.kind === 'refresh-flow').length

async function choose(w: VueWrapper<any>, name: 'provider' | 'reviewCount' | 'reviewer', value: string) {
  await select(w, name).setValue(value)
  await flushPromises()
}

async function saveNow(w: VueWrapper<any>) {
  await saveButton(w).trigger('click')
  await flushPromises()
}

// --- Reading ---------------------------------------------------------------------------------------

describe('settings tab — reading (flowgate.v02.0048 T#9)', () => {
  it('reads the Flow’s rows once and shows the Node’s row: run AI, review count, reviewer', async () => {
    const w = await mountTab(targetFor(PENDING))
    expect(sequenceRead).toHaveBeenCalledTimes(1)
    expect(sequenceRead).toHaveBeenCalledWith(FLOW)
    expect(choicesRead).toHaveBeenCalledWith('flowgate')
    expect(w.find('[data-test="settings-row"]').text()).toContain('6단계 · T · 작업지시')
    expect(w.find('[data-test="settings-row-state"]').text()).toBe('시작 전')
    expect(valueOf(w, 'provider')).toBe('aip_haiku')
    expect(valueOf(w, 'reviewCount')).toBe('2')
    expect(valueOf(w, 'reviewer')).toBe('aip_sonnet')
    expect(optionLabels(w, 'provider')).toEqual(['기본값 (프로젝트 기본 AI)', 'Claude Opus', 'Claude Sonnet', 'Claude Haiku'])
    expect(optionLabels(w, 'reviewCount')).toEqual(['통과할 때까지', '안 함', '1회', '2회', '3회'])
    for (const name of ['provider', 'reviewCount', 'reviewer'] as const) expect(isDisabled(select(w, name))).toBe(false)
    // Nothing changed yet: saving is off with its reason.
    expect(isDisabled(saveButton(w))).toBe(true)
    expect(reasonText(w)).toBe('바꾼 설정이 없습니다.')
  })

  // flowgate.v02.0051 stage 2 (B14 as turn 7 reset it): the v7 items that are not stored on a Node are drawn
  // inactive next to the three stored settings — never usable, never hidden.
  it('the three stored settings are usable; 재시작 · 지시서 · 검수 뒤 진행 are drawn disabled with their reason', async () => {
    const w = await mountTab(targetFor(PENDING))
    const selects = w.findAll('select')
    expect(selects).toHaveLength(4)
    expect(selects.filter((item) => item.attributes('disabled') !== undefined).map((item) => item.attributes('data-test'))).toEqual(['inspector-run-settings-restart'])
    for (const name of ['provider', 'reviewCount', 'reviewer'] as const) expect(isDisabled(select(w, name))).toBe(false)
    for (const option of w.findAll('[data-test="inspector-segment-option"]')) expect(option.attributes('disabled')).toBeDefined()
    expect(w.text()).not.toContain('연동 전')
  })

  it('a stored AI that is no longer offered stays shown, marked', async () => {
    rows[2] = { ...rows[2], provider_id: 'aip_gone', provider_display_name: 'Old AI', provider_registered: false }
    const w = await mountTab(targetFor(PENDING))
    expect(valueOf(w, 'provider')).toBe('aip_gone')
    expect(optionLabels(w, 'provider').at(-1)).toBe('Old AI (등록되지 않은 AI)')
  })

  it('a started row: shown, every field and saving disabled with the started reason (D#2 E3)', async () => {
    const w = await mountTab(targetFor(STARTED))
    // flowgate.v02.0052 묶음 A: the 🔒 line says it once; the row state line is not repeated.
    expect(w.find('[data-test="inspector-lock-note"]').text()).toBe('🔒 시작됨')
    expect(w.find('[data-test="settings-row-state"]').exists()).toBe(false)
    for (const name of ['provider', 'reviewCount', 'reviewer'] as const) expect(isDisabled(select(w, name))).toBe(true)
    expect(isDisabled(saveButton(w))).toBe(true)
    expect(reasonText(w)).toBe('이미 시작된 단계라 설정을 바꿀 수 없습니다.')
  })

  it('a protected pending report row (after a started instruction) can be set', async () => {
    const w = await mountTab(targetFor(REPORT))
    expect(w.find('[data-test="settings-row-state"]').text()).toBe('시작 전 (앞 단계와 묶인 레포트)')
    expect(isDisabled(select(w, 'provider'))).toBe(false)
  })

  it('review count "none": the reviewer is off with its reason; another count turns it on', async () => {
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'reviewCount', '0')
    expect(isDisabled(select(w, 'reviewer'))).toBe(true)
    expect(w.find('[data-test="settings-reviewer-reason"]').text()).toBe('검수 횟수가 "안 함"이라 검수 AI를 쓰지 않습니다.')
    await choose(w, 'reviewCount', '-1')
    expect(isDisabled(select(w, 'reviewer'))).toBe(false)
    expect(w.find('[data-test="settings-reviewer-reason"]').exists()).toBe(false)
  })

  it('a Node with no v0.1 slot (free document) has nothing to set and reads nothing', async () => {
    const w = await mountTab(targetFor(FREE_DOC))
    expect(w.find('[data-test="inspector-pane-state"][data-state="empty"]').text()).toContain('v0.1 단계(sequence)에 속하지 않아')
    expect(w.findAll('select')).toHaveLength(0)
  })

  it.each([
    ['403', axiosError(403, { error: 'forbidden' }), '이 Flow의 단계를 볼 권한이 없습니다.'],
    ['404', axiosError(404, { error: 'doc_not_found' }), 'Flow나 문서를 찾을 수 없습니다.'],
    ['400 sequence_not_decided', axiosError(400, { error: 'sequence_not_decided' }), '이 Flow에는 결정된 v0.1 워크플로가 없습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
    ['500', axiosError(500, { error: 'internal_error' }), '단계 설정을 불러오지 못했습니다.'],
  ])('a failed read (%s) shows why with a retry that reads again', async (_label, refusal, text) => {
    refusals.set('read', refusal)
    const w = await mountTab(targetFor(PENDING))
    expect(w.find('[data-test="inspector-pane-state"][data-state="error"]').text()).toContain(text)
    await w.find('[data-test="inspector-pane-retry"]').trigger('click')
    await flushPromises()
    expect(sequenceRead).toHaveBeenCalledTimes(2)
    expect(valueOf(w, 'provider')).toBe('aip_haiku')
  })
})

// --- Saving ----------------------------------------------------------------------------------------

describe('settings tab — saving (flowgate.v02.0048 T#9, D#2 §2.4)', () => {
  it('a change is saved: the rows are read again first, the PATCH holds every row with its item_id and fields as read', async () => {
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'provider', 'aip_opus')
    await choose(w, 'reviewCount', '-1')
    expect(isDisabled(saveButton(w))).toBe(false)
    expect(w.find('[data-test="settings-reason"]').exists()).toBe(false)
    const readsBefore = sequenceRead.mock.calls.length
    await saveNow(w)
    // Read again right before the save (D#2 §2.4 rule 2) and again after it (§7.3).
    expect(sequenceRead.mock.calls.length).toBe(readsBefore + 2)
    expect(patches).toHaveLength(1)
    const body = patches[0]
    expect(body.doc_id).toBe(ROOT)
    expect(body.items.map((item) => item.item_id)).toEqual([124, 125, 126, 127])
    expect(body.items[2]).toEqual({
      item_id: 126,
      type: 'T',
      label: '작업지시',
      note: 'T#2 지시 메모',
      source_doc_id: `${FLOW}.0003-WP`,
      source_revision_no: 2,
      source_wp_card_id: 'c_t2',
      provider_id: 'aip_opus',
      provider_display_name: 'Claude Opus',
      review_count: -1,
      reviewer_provider_id: 'aip_sonnet',
      reviewer_provider_display_name: 'Claude Sonnet',
      pre_instruction_text: '먼저 읽을 것',
      pre_instruction_attachment: { name: 'spec.md', size: 12 },
    })
    // The other rows go back exactly as read.
    expect(body.items[0]).toMatchObject({ item_id: 124, provider_id: 'aip_haiku', note: 'T#1 지시', review_count: 0 })
    expect(body.items[3]).toMatchObject({ item_id: 127, provider_id: null, note: 'T#2 결과', review_count: 0 })
    expect(w.find('[data-test="settings-saved"]').text()).toBe('저장했습니다. 다음 연속 작업이 이 단계를 시작할 때 반영됩니다.')
  })

  it('after the save the Canvas is read again (refresh-flow); the same Node finds its row under the new id with the saved values', async () => {
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'provider', 'aip_opus')
    await saveNow(w)
    expect(refreshes()).toBe(1)
    // The server wrote the pending row again under a new id; until the Canvas answers, the old slot finds no row.
    const moved = rows.find((row) => row.item_seq > 7 && row.type === 'T')!
    expect(moved.item_id).not.toBe(126)
    expect(moved.provider_id).toBe('aip_opus')
    expect(w.find('[data-test="inspector-pane-state"][data-state="loading"]').text()).toContain('Canvas를 다시 읽는 중')
    // No second refresh for the same slot.
    expect(refreshes()).toBe(1)
    // The Canvas read again keeps the node_key (same nodeId) with the slot's new id.
    await w.setProps({ target: movedTarget(PENDING, moved.item_id) })
    await flushPromises()
    expect(valueOf(w, 'provider')).toBe('aip_opus')
    expect(w.find('[data-test="settings-saved"]').exists()).toBe(true)
    expect(isDisabled(saveButton(w))).toBe(true)
    expect(refreshes()).toBe(1)
  })

  it('a protected pending report row is saved in place (same id): its row stays found', async () => {
    const w = await mountTab(targetFor(REPORT))
    await choose(w, 'reviewCount', '1')
    await choose(w, 'reviewer', 'aip_opus')
    await saveNow(w)
    expect(patches[0].items[1]).toMatchObject({ item_id: 125, review_count: 1, reviewer_provider_id: 'aip_opus', reviewer_provider_display_name: 'Claude Opus', note: 'T#1 결과' })
    expect(rows.find((row) => row.item_id === 125)).toMatchObject({ review_count: 1, reviewer_provider_id: 'aip_opus' })
    expect(valueOf(w, 'reviewCount')).toBe('1')
    expect(valueOf(w, 'reviewer')).toBe('aip_opus')
  })

  it('nothing changed (or changed back): nothing is sent', async () => {
    const w = await mountTab(targetFor(PENDING))
    await saveNow(w)
    await choose(w, 'provider', 'aip_opus')
    await choose(w, 'provider', 'aip_haiku')
    expect(isDisabled(saveButton(w))).toBe(true)
    await saveNow(w)
    expect(saveCall).not.toHaveBeenCalled()
    expect(patches).toHaveLength(0)
  })

  it('a default pick sends the AI and its name as null', async () => {
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'provider', '')
    await choose(w, 'reviewer', '')
    await saveNow(w)
    expect(patches[0].items[2]).toMatchObject({ provider_id: null, provider_display_name: null, reviewer_provider_id: null, reviewer_provider_display_name: null })
  })

  it('while the save and the read after it run, the fields and saving are off; a second click sends nothing', async () => {
    let release: () => void = () => {}
    saveCall.mockImplementationOnce(
      (rootDocId: string, items: Parameters<typeof settingsSavePayload>[0], changes: Parameters<typeof settingsSavePayload>[1]) =>
        new Promise((resolve) => {
          release = () => {
            patch({ doc_id: rootDocId, items: settingsSavePayload(items, changes) })
            resolve({ rootDocId, raw: {} })
          }
        }),
    )
    const w = await mountTab(targetFor(REPORT))
    await choose(w, 'provider', 'aip_opus')
    void saveButton(w).trigger('click')
    await flushPromises()
    expect(isDisabled(select(w, 'provider'))).toBe(true)
    expect(reasonText(w)).toBe('설정을 저장하는 중입니다.')
    await saveButton(w).trigger('click')
    await flushPromises()
    release()
    await flushPromises()
    expect(saveCall).toHaveBeenCalledTimes(1)
    expect(patches).toHaveLength(1)
    expect(isDisabled(select(w, 'provider'))).toBe(false)
  })

  it('the fields stay off until the read after the save is back (the old rows cannot be sent twice)', async () => {
    const w = await mountTab(targetFor(REPORT))
    await choose(w, 'provider', 'aip_opus')
    let release: () => void = () => {}
    // The read before the save answers at once; the read after it waits.
    sequenceRead.mockImplementationOnce(async () => sequenceOf()).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve(sequenceOf())
        }),
    )
    void saveButton(w).trigger('click')
    await flushPromises()
    expect(patches).toHaveLength(1)
    expect(isDisabled(select(w, 'provider'))).toBe(true)
    expect(reasonText(w)).toBe('설정을 저장하는 중입니다.')
    release()
    await flushPromises()
    expect(isDisabled(select(w, 'provider'))).toBe(false)
  })

  it('the row started between the read and the save: nothing is sent, the stale reason shows, the Canvas is read again', async () => {
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'provider', 'aip_opus')
    rows[2] = { ...rows[2], status: 'in_progress', protected: true }
    await saveNow(w)
    expect(saveCall).not.toHaveBeenCalled()
    expect(failureText(w)).toBe('그사이 단계 목록이 바뀌어 저장하지 않았습니다. 다시 읽은 값을 확인한 뒤 저장하세요.')
    expect(refreshes()).toBe(1)
    expect(w.find('[data-test="inspector-lock-note"]').text()).toBe('🔒 시작됨')
    expect(w.find('[data-test="settings-row-state"]').exists()).toBe(false)
    expect(isDisabled(select(w, 'provider'))).toBe(true)
  })

  it.each([
    ['409 sequence_item_stale', axiosError(409, { error: 'sequence_item_stale', item_id: 126 }), '그사이 단계 목록이 바뀌어 저장하지 않았습니다. 다시 읽은 값을 확인한 뒤 저장하세요.', 1],
    ['409 protected_row_modified', axiosError(409, { error: 'protected_row_modified', item_id: 125 }), '그사이 단계 목록이 바뀌어 저장하지 않았습니다. 다시 읽은 값을 확인한 뒤 저장하세요.', 1],
    ['409 flow_owned_by_graph', axiosError(409, { error: 'flow_owned_by_graph', doc_id: ROOT, group_id: FLOW, graph_revision_no: 1 }), '이 Flow는 v0.1 단계로 저장할 수 없는 상태(Graph 소유·소유 충돌)라 거절되었습니다.', 0],
    ['400 sequence_not_decided', axiosError(400, { error: 'sequence_not_decided', doc_id: ROOT }), '워크플로가 결정되지 않아 저장할 수 없습니다.', 0],
    ['422 note_too_long', axiosError(422, { error: 'note_too_long', max: 1000, length: 1200, item_index: 2 }), '저장 요청이 올바르지 않아 거절되었습니다.', 0],
    ['403', axiosError(403, { error: 'workflow_sequence_edit_token_mismatch' }), '권한이 없어 거절되었습니다.', 0],
    ['500', axiosError(500, { error: 'internal_error' }), '설정을 저장하지 못했습니다.', 0],
    ['network', networkError(), '서버에 연결하지 못했습니다.', 0],
  ])('a refused save (%s) shows why, reads the rows again and keeps the change to try again', async (_label, refusal, text, canvasReads) => {
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'provider', 'aip_opus')
    refusals.set('save', refusal)
    const readsBefore = sequenceRead.mock.calls.length
    await saveNow(w)
    expect(failureText(w)).toBe(text)
    expect(sequenceRead.mock.calls.length).toBe(readsBefore + 2)
    expect(refreshes()).toBe(canvasReads)
    expect(w.find('[data-test="settings-saved"]').exists()).toBe(false)
    // The change is kept; once the server accepts, it goes through.
    expect(valueOf(w, 'provider')).toBe('aip_opus')
    expect(isDisabled(saveButton(w))).toBe(false)
    await saveNow(w)
    expect(w.find('[data-test="settings-saved"]').exists()).toBe(true)
  })

  it('409 group_disposed: the disposed reason, everything off until the Node changes', async () => {
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'provider', 'aip_opus')
    refusals.set('save', axiosError(409, { error: 'group_disposed', doc_id: ROOT }))
    await saveNow(w)
    expect(failureText(w)).toBe('폐기된 그룹이라 저장이 거절되었습니다. 단계 목록을 다시 읽었습니다.')
    expect(reasonText(w)).toBe('폐기된 그룹이라 설정을 바꿀 수 없습니다.')
    expect(isDisabled(select(w, 'provider'))).toBe(true)
    await w.setProps({ target: targetFor(REPORT) })
    await flushPromises()
    expect(w.find('[data-test="settings-failure"]').exists()).toBe(false)
    expect(isDisabled(select(w, 'provider'))).toBe(false)
  })

  it('423 (an AI run took the group after the read): the AI running reason; ai_invoke_finished opens it again', async () => {
    vi.useFakeTimers()
    const w = await mountTab(targetFor(PENDING))
    stream.open()
    await choose(w, 'provider', 'aip_opus')
    refusals.set('save', locked())
    await saveNow(w)
    expect(failureText(w)).toBe('AI 실행 중이라 저장이 거절되었습니다. 단계 목록을 다시 읽었습니다.')
    expect(reasonText(w)).toBe('AI 실행 중이라 설정을 바꿀 수 없습니다.')
    stream.emit('ai_invoke_finished', { groupId: FLOW, docId: `${FLOW}.0004-T` })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(isDisabled(select(w, 'provider'))).toBe(false)
    await saveNow(w)
    expect(patches.at(-1)!.items[2]).toMatchObject({ provider_id: 'aip_opus' })
  })
})

// --- Guard, events, Node change ---------------------------------------------------------------------

describe('settings tab — guard, events, Node change (flowgate.v02.0048 T#9)', () => {
  it('an AI run on the Flow: shown, everything off with the AI running reason', async () => {
    running = true
    const w = await mountTab(targetFor(PENDING))
    expect(isDisabled(select(w, 'provider'))).toBe(true)
    expect(reasonText(w)).toBe('AI 실행 중이라 설정을 바꿀 수 없습니다.')
  })

  it('a disposed group (root document read): everything off with the disposed reason', async () => {
    disposed = true
    const w = await mountTab(targetFor(PENDING))
    expect(documentRead).toHaveBeenCalledWith(ROOT)
    expect(reasonText(w)).toBe('폐기된 그룹이라 설정을 바꿀 수 없습니다.')
  })

  it('a failed guard read does not block (the server refuses on the same facts)', async () => {
    runRead.mockRejectedValue(networkError())
    documentRead.mockRejectedValue(networkError())
    const w = await mountTab(targetFor(PENDING))
    expect(isDisabled(select(w, 'provider'))).toBe(false)
  })

  it.each(SETTINGS_EVENTS)('%s about the Flow reads the rows again; another Flow’s does not', async (event) => {
    vi.useFakeTimers()
    const w = await mountTab(targetFor(PENDING))
    stream.open()
    expect(sequenceRead).toHaveBeenCalledTimes(1)
    stream.emit(event, { groupId: 'flowgate.v02.0099', docId: null })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(sequenceRead).toHaveBeenCalledTimes(1)
    rows[2] = { ...rows[2], review_count: 3 }
    stream.emit(event, { groupId: FLOW, docId: ROOT })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(sequenceRead).toHaveBeenCalledTimes(2)
    expect(valueOf(w, 'reviewCount')).toBe('3')
  })

  it('a reconnect reads the rows again', async () => {
    vi.useFakeTimers()
    await mountTab(targetFor(PENDING))
    stream.open()
    stream.reconnect()
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(sequenceRead).toHaveBeenCalledTimes(2)
  })

  it('another save wrote the rows again (the slot id is gone from the read): the Canvas is read again once', async () => {
    rows[2] = { ...rows[2], item_id: 999 }
    const w = await mountTab(targetFor(PENDING))
    expect(refreshes()).toBe(1)
    expect(w.find('[data-test="inspector-pane-state"][data-state="loading"]').text()).toContain('Canvas를 다시 읽는 중')
    await w.setProps({ target: movedTarget(PENDING, 999) })
    await flushPromises()
    expect(valueOf(w, 'provider')).toBe('aip_haiku')
    expect(refreshes()).toBe(1)
  })

  it('a Node change drops the unsaved change and shows the new Node’s row (one Flow read)', async () => {
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'provider', 'aip_opus')
    await w.setProps({ target: targetFor(REPORT) })
    await flushPromises()
    expect(valueOf(w, 'provider')).toBe('aip_haiku')
    await w.setProps({ target: targetFor(PENDING) })
    await flushPromises()
    expect(valueOf(w, 'provider')).toBe('aip_haiku')
    expect(isDisabled(saveButton(w))).toBe(true)
    expect(sequenceRead).toHaveBeenCalledTimes(1)
  })

  it('read-only revision (a v0.1 target never has it; the rule is the shared one): everything off with its reason', async () => {
    const w = await mountTab(targetFor(PENDING, { readOnly: 'revision' }))
    expect(isDisabled(select(w, 'provider'))).toBe(true)
    expect(reasonText(w)).toBe('확정된 Graph revision 보기라 읽기 전용입니다.')
  })
})

// --- What the run takes from the saved row (D#2 §2.1, §4.1 ⑤) ----------------------------------------

describe('settings tab — the saved row reaches the continuous hop (flowgate.v02.0048 T#9)', () => {
  it('the start answer of the hop that runs the saved row: selected_provider_source stored_sequence and the row’s review settings', async () => {
    // Save first, as the tab does.
    const w = await mountTab(targetFor(PENDING))
    await choose(w, 'provider', 'aip_opus')
    await choose(w, 'reviewCount', '1')
    await saveNow(w)
    const saved = rows.find((row) => row.type === 'T' && row.item_seq > 7)!
    await w.setProps({ target: movedTarget(PENDING, saved.item_id) })
    await flushPromises()
    // POST /ai-invoke/start (mode continuous) answered for that hop (shape of admission.py L1940-1953).
    const start = settingsHopOf({
      run_id: 'run_9',
      status: 'running',
      mode: 'continuous',
      hop_item_seq: saved.item_seq,
      hop_kind: 'worker',
      provider: { id: 'aip_opus', name: 'Claude Opus' },
      selected_provider_source: 'stored_sequence',
      hop_review_count: 1,
      hop_reviewer_provider_id: 'aip_sonnet',
      hop_reviewer_provider_name: 'Claude Sonnet',
    })
    expect(start).toMatchObject({ itemSeq: saved.item_seq, providerId: saved.provider_id, providerSource: 'stored_sequence', reviewCount: saved.review_count, reviewerProviderId: saved.reviewer_provider_id })
    // The same hop seen live (GET /ai-invoke/active) on the tab: as saved.
    running = true
    hop = { hop_item_seq: saved.item_seq, provider: { id: 'aip_opus', name: 'Claude Opus' }, hop_review_count: 1, hop_reviewer_provider_id: 'aip_sonnet', hop_reviewer_provider_name: 'Claude Sonnet' }
    await w.vm.$nextTick()
    stream.open()
    stream.emit('ai_invoke_started', { groupId: FLOW, docId: null })
    await new Promise((resolve) => setTimeout(resolve, INSPECTOR_REFRESH_COALESCE_MS + 20))
    await flushPromises()
    expect(w.find('[data-test="settings-hop-provider"]').text()).toBe('실행 AI: Claude Opus · 저장된 설정대로')
    expect(w.find('[data-test="settings-hop-review-count"]').text()).toBe('검수 횟수: 1회 · 저장된 설정대로')
    expect(w.find('[data-test="settings-hop-reviewer"]').text()).toBe('검수 AI: Claude Sonnet · 저장된 설정대로')
    // The fields stay off while the hop runs.
    expect(isDisabled(select(w, 'provider'))).toBe(true)
  })

  it('a hop running with something else than the saved row says so; another row’s hop shows nothing', async () => {
    running = true
    hop = { hop_item_seq: 6, provider: { id: 'aip_opus', name: 'Claude Opus' }, hop_review_count: 0, hop_reviewer_provider_id: null }
    const w = await mountTab(targetFor(PENDING))
    expect(w.find('[data-test="settings-hop-provider"]').text()).toBe('실행 AI: Claude Opus · 저장된 설정과 다름(실행 요청에서 지정)')
    expect(w.find('[data-test="settings-hop-review-count"]').text()).toBe('검수 횟수: 안 함 · 저장된 설정과 다름(실행 요청에서 지정)')
    await w.setProps({ target: targetFor(REPORT) })
    await flushPromises()
    expect(w.find('[data-test="settings-hop"]').exists()).toBe(false)
  })
})

// --- The real registry -------------------------------------------------------------------------------

describe('settings tab in the Frame (real registry, flowgate.v02.0048 T#9)', () => {
  async function mountFrame(selection: FlowGraphSelection) {
    wrapper = mount(DesignerInspector, { props: { selection }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  const tabLabels = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((tab) => tab.text())
  const openTab = async (w: VueWrapper<any>, label: string) => {
    await w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((tab) => tab.text().startsWith(label))!.trigger('click')
    await flushPromises()
  }

  it('a v0.1 step Node with a document: 설정 is the last tab; it reads nothing until opened', async () => {
    const w = await mountFrame(selectionOf(GRAPH, STARTED.id))
    expect(tabLabels(w).at(-1)).toBe('설정')
    expect(sequenceRead).not.toHaveBeenCalled()
    await openTab(w, '설정')
    expect(w.find('[data-test="inspector-settings"]').exists()).toBe(true)
    expect(sequenceRead).toHaveBeenCalledWith(FLOW)
  })

  it('a v0.1 step not started (no document): 설정 is its only tab and the save goes through the Frame’s refresh-flow', async () => {
    const w = await mountFrame(selectionOf(GRAPH, PENDING.id))
    // flowgate.v02.0051 stage 2: the card's own documents part gives the 문서 tab as well.
    expect(tabLabels(w)).toContain('설정')
    await openTab(w, '설정')
    expect(w.find('[data-test="inspector-settings"]').exists()).toBe(true)
    await choose(w, 'provider', 'aip_opus')
    await saveNow(w)
    expect(patches).toHaveLength(1)
    expect((w.emitted('host') ?? []).some(([intent]) => (intent as InspectorHostIntent).kind === 'refresh-flow')).toBe(true)
  })

  it('the support fixture: v01 task Node and v01 slot Node carry 설정', async () => {
    let w = await mountFrame(SELECTIONS.v01)
    expect(tabLabels(w)).toContain('설정')
    wrapper!.unmount()
    w = await mountFrame(SELECTIONS.v01NoDocument)
    expect(tabLabels(w)).toContain('설정')
  })

  // flowgate.v02.0051 stage 2: a card's own settings part (AI 위임 · test · check) may give a 설정 tab anywhere;
  // the v0.1 run settings themselves stay on v0.1 Flows only.
  it.each(['graph', 'graphGate', 'unassigned', 'conflict'] as const)('the %s Flow has no v0.1 run settings (v0.1 Node settings only, D#1 §8.3)', async (name) => {
    const w = await mountFrame(SELECTIONS[name])
    if (tabLabels(w).includes('설정')) await openTab(w, '설정')
    expect(w.find('[data-test="inspector-settings"]').exists()).toBe(false)
    expect(sequenceRead).not.toHaveBeenCalled()
  })

  it('R/B (AI delegation), AC and WP cards have no v0.1 run settings on a v0.1 Flow (D#1 §10.1)', async () => {
    const card = (id: string, type: string, kind: string): FlowGraphNode => ({
      id,
      kind,
      title: type,
      state: 'done',
      docType: type,
      x: 0,
      y: 0,
      document: { docId: `${FLOW}.00${id.length}-${type}`, typeCode: type, seq: 1, title: type, docReviewStatus: 'approved', status: 'open', supersedes: [] },
      slot: { itemId: 50 + id.length, itemSeq: 1, order: 1, type, label: type, derivedStatus: 'done', protected: true, sourceWpCardId: null },
    })
    const graph: FlowGraph = { ...GRAPH, nodes: [ROOT_NODE, card('wp', 'WP', 'plan'), card('acx', 'AC', 'approval'), card('bbbb', 'B', 'requirement')] }
    for (const node of graph.nodes) {
      const w = await mountFrame(selectionOf(graph, node.id))
      if (tabLabels(w).includes('설정')) await openTab(w, '설정')
      expect(w.find('[data-test="inspector-settings"]').exists()).toBe(false)
      expect(sequenceRead).not.toHaveBeenCalled()
      wrapper!.unmount()
      wrapper = null
    }
  })

  it.each([
    ['N', 'research'],
    ['T', 'task'],
    ['D', 'design'],
    ['TS', 'test'],
  ])('a free %s document on a v0.1 Flow (no sequence slot) has no v0.1 run settings and reads nothing (D#2 §2.4 rule 3)', async (type, kind) => {
    const free: FlowGraphNode = {
      ...FREE_DOC,
      id: `doc:${FLOW}.0019-${type}`,
      kind,
      docType: type,
      document: { ...FREE_DOC.document!, docId: `${FLOW}.0019-${type}`, typeCode: type },
      slot: null,
    }
    const w = await mountFrame(selectionOf({ ...GRAPH, nodes: [...GRAPH.nodes, free] }, free.id))
    expect(tabLabels(w).length).toBeGreaterThan(0)
    if (tabLabels(w).includes('설정')) await openTab(w, '설정')
    expect(w.find('[data-test="inspector-settings"]').exists()).toBe(false)
    expect(sequenceRead).not.toHaveBeenCalled()
  })
})
