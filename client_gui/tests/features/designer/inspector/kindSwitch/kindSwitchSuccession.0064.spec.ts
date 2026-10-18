/**
 * flowgate.v02.0064 T#4 — the kind switch with the server's judgement (P0006 §3.1 / §3.3 / §3.5 / §6.1):
 * the document state is read with `include=root_conversion` (kindSwitchPreviewRead, mocked as a small
 * server here); a refused judgement disables the badge with its reason while a judgement not read yet blocks nothing; the new refusals are shown with
 * their reason (a superseded root also reads the Canvas again); a succeeded document links the document it
 * came from — a header part of its own (features/kindSwitchOrigin) read without the judgement, kept on every
 * ownership: the new document's workflow decision turns its Flow v01 and the link stays (rework).
 * flowgate.v02.0070 T#4 (B4) — the link opens that exact id as history (openDocument `exact`, not the document it is
 * succeeded by), read only, with the old id's questions under the body: the Q&A tab's list read as history (no question
 * line, answer form or AI line; its ↗ window says 읽기 전용).
 * flowgate.v02.0070 (0015-T T2) — picking the other kind runs the switch at once (R -> B and B -> R), without a
 * confirmation, whether the server judged a number swap, a succession (also a chained one) or nothing yet; the pick
 * awaits no read before the call, so a pick never runs after the part is gone or the Node changed.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import KindSwitchAction from '@/features/designer/inspector/features/kindSwitch/KindSwitchAction.vue'
import QnaTab from '@/features/designer/inspector/features/qna/QnaTab.vue'
import { closeAllDocumentWindows, documentWindowList } from '@/features/documentWindow'
import {
  kindSwitchAvailability,
  kindSwitchRefreshesFlow,
  type KindSwitchFailureKind,
  type KindSwitchInput,
} from '@/features/designer/inspector/features/kindSwitch/kindSwitchModel'
import { INSPECTOR_HOST, type InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { SELECTIONS, UNASSIGNED_GRAPH, UNASSIGNED_REQ_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream } from '../support/projectEventStream'

const convertCall = vi.fn()
const previewRead = vi.fn()
const originRead = vi.fn()
const activityRead = vi.fn()
const qaRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    kindSwitchConvert: (...args: unknown[]) => convertCall(...args),
    kindSwitchPreviewRead: (...args: unknown[]) => previewRead(...args),
    kindSwitchOriginRead: (...args: unknown[]) => originRead(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    qaRead: (...args: unknown[]) => qaRead(...args),
    // flowgate.v02.0070 T#5: the document read answering waits for (a current document: superseded by none).
    reviewDocumentRead: async () => ({ groupDisposed: false, record: { supersededBy: null } }),
    settingsChoicesRead: async () => ({ providers: [], defaultProviderId: null }),
  }
})
const opened = vi.fn()
vi.mock('@/features/designer/inspector/openDocument', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/designer/inspector/openDocument')>()),
  openDocument: (...args: unknown[]) => opened(...args),
}))

const { toKindSwitchPreview, toReviewActivity, toKindSwitchResult, toQaThread } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')
const { openDocument: realOpenDocument } =
  await vi.importActual<typeof import('@/features/designer/inspector/openDocument')>('@/features/designer/inspector/openDocument')

const i18n = prepareInspectorI18n()

const FLOW = UNASSIGNED_GRAPH.flowId
const ROOT_R = UNASSIGNED_REQ_NODE.document!.docId
const ROOT_B = `${FLOW}.0001-B`

/** What the server judges for the next reads (the `root_conversion` item), and the document's links. */
let judgement: Record<string, unknown> | null = null
let convertedFrom: Record<string, unknown> | null = null
let refusal: unknown = null

const rowOf = (docId: string, typeCode = docId.endsWith('-B') ? 'B' : 'R') => ({
  doc_id: docId, project_id: 'flowgate', group_id: FLOW, type_code: typeCode, seq: 1, title: '새 요구사항', status: 'draft',
  doc_review_status: null, group_disposed: false, converted_from: convertedFrom, converted_to: null, live_root_doc_id: docId,
  ...(judgement ? { root_conversion: { read_at: '2026-10-16T10:04:50.007+09:00', ...judgement } } : {}),
})

const judged = (decision: string, extra: Record<string, unknown> = {}) => ({
  target_type: 'B', decision, mode: decision === 'refused' ? null : decision, refusal: null, new_doc_id: ROOT_B,
  new_doc_exact: true, child_count: 0, chained: false, live_root_doc_id: null, ...extra,
})

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

let pinia: Pinia
let wrapper: VueWrapper<any> | null = null
let intents: InspectorHostIntent[] = []

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  installProjectEventStream()
  judgement = null
  convertedFrom = null
  refusal = null
  intents = []
  for (const mock of [convertCall, previewRead, originRead, activityRead, opened, qaRead]) mock.mockReset()
  previewRead.mockImplementation(async (docId: string) => toKindSwitchPreview(rowOf(docId)))
  originRead.mockImplementation(async (docId: string) => toKindSwitchPreview(rowOf(docId)).convertedFrom)
  activityRead.mockImplementation(async (groupId: string) => toReviewActivity({ ok: true, active: false, group_id: groupId }))
  convertCall.mockImplementation(async (docId: string, newType: string) => {
    if (refusal) throw refusal
    const nextId = docId.replace(/-[RB]$/, `-${newType}`)
    return toKindSwitchResult({ data: rowOf(nextId, newType), conversion: { mode: 'succession', old_doc_id: docId, new_doc_id: nextId, child_count: 3 } }, docId)
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  document.body.innerHTML = ''
})

async function mountSwitch(target: InspectorTarget = targetOf(SELECTIONS.unassigned)) {
  wrapper = mount(KindSwitchAction, {
    attachTo: document.body,
    props: { target },
    global: { plugins: [i18n, pinia], provide: { [INSPECTOR_HOST as symbol]: { request: (intent: InspectorHostIntent) => intents.push(intent) } } },
  })
  await flushPromises()
  return wrapper
}

const find = (test: string) => wrapper!.find(`[data-test="${test}"]`)
const openButton = () => find('kind-switch-open')
const confirmButtons = () => [...document.body.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions button, [role="dialog"] button')]

async function pickOther() {
  await openButton().trigger('click')
  await flushPromises()
  const to = openButton().attributes('data-to') as 'R' | 'B'
  document.body.querySelector<HTMLButtonElement>(`[data-test="kind-switch-item-${to}"]`)?.click()
  await flushPromises()
}

/** The unassigned root as its B document (the way back: B -> R). */
const B_ROOT_NODE: FlowGraphNode = { ...UNASSIGNED_REQ_NODE, id: `doc:${ROOT_B}`, docType: 'B', document: { ...UNASSIGNED_REQ_NODE.document!, docId: ROOT_B, typeCode: 'B' } }
const TARGET_B = targetOf(selectionOf({ ...UNASSIGNED_GRAPH, nodes: [B_ROOT_NODE] }, B_ROOT_NODE.id))

/** A read that answers when the test says so (or fails). */
function heldRead() {
  let answer!: () => void
  let fail!: () => void
  previewRead.mockImplementationOnce((docId: string) => new Promise((resolve, reject) => {
    answer = () => resolve(toKindSwitchPreview(rowOf(docId)))
    fail = () => reject(axiosError(503, {}))
  }))
  return { answer: () => answer(), fail: () => fail() }
}

describe('a pick switches at once, whatever the judgement (0015-T T2, P0006 §6.1)', () => {
  it.each([
    ['number_swap', 'R -> B', {}],
    ['succession', 'R -> B', { child_count: 3 }],
    ['number_swap', 'B -> R', { target_type: 'R', new_doc_id: ROOT_R }],
    ['succession', 'B -> R', { target_type: 'R', new_doc_id: ROOT_R, child_count: 2 }],
  ] as const)('a judged %s (%s) calls the existing switch once, with no confirmation, and reads the Canvas again', async (decision, way, extra) => {
    judgement = judged(decision, extra)
    const back = way === 'B -> R'
    await mountSwitch(back ? TARGET_B : targetOf(SELECTIONS.unassigned))
    expect(previewRead).toHaveBeenCalledWith(back ? ROOT_B : ROOT_R)
    await pickOther()
    expect(confirmButtons()).toEqual([])
    expect(document.body.textContent).not.toContain('새 문서로 이어 가기')
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(convertCall).toHaveBeenCalledWith(back ? ROOT_B : ROOT_R, back ? 'R' : 'B')
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
    expect(find('kind-switch-line').attributes('data-kind')).toBe('done')
  })

  it('a chained succession: the done line names the number the server gave', async () => {
    judgement = judged('succession', { new_doc_id: `${FLOW}.0005-B`, new_doc_exact: false, chained: true })
    convertCall.mockImplementationOnce(async (docId: string) =>
      toKindSwitchResult({ data: rowOf(`${FLOW}.0005-B`, 'B'), conversion: { mode: 'succession', old_doc_id: docId, new_doc_id: `${FLOW}.0005-B`, child_count: 3 } }, docId),
    )
    await mountSwitch()
    await pickOther()
    expect(confirmButtons()).toEqual([])
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
    expect(find('kind-switch-line').text()).toBe('버그 문서(0005-B)로 바꿨습니다. Canvas를 다시 읽는 중입니다.')
  })

  it('a pick while the judgement is still being read does not wait for it: the switch is asked at once', async () => {
    judgement = judged('succession', { child_count: 3 })
    const held = heldRead()
    await mountSwitch()
    await pickOther()
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
    held.answer()
    await flushPromises()
    expect(confirmButtons()).toEqual([])
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(intents).toHaveLength(1)
  })

  it.each(['answers', 'fails'] as const)('the part gone while the judgement is read: the read that %s afterwards starts nothing', async (end) => {
    judgement = judged('succession', { child_count: 3 })
    const held = heldRead()
    await mountSwitch()
    wrapper!.unmount()
    wrapper = null
    if (end === 'answers') held.answer()
    else held.fail()
    await flushPromises()
    expect(convertCall).not.toHaveBeenCalled()
    expect(intents).toEqual([])
    expect(confirmButtons()).toEqual([])
  })

  it.each(['answers', 'fails'] as const)('picked, then the part gone before the judgement %s: only the switch asked at the pick, nothing after', async (end) => {
    judgement = judged('succession', { child_count: 3 })
    const held = heldRead()
    await mountSwitch()
    await pickOther()
    // The switch was asked at the pick itself, while the part was still shown.
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
    wrapper!.unmount()
    wrapper = null
    if (end === 'answers') held.answer()
    else held.fail()
    await flushPromises()
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
  })

  it('another Node while the judgement is read: its answer starts nothing on the Node left behind', async () => {
    judgement = judged('succession', { child_count: 3 })
    const held = heldRead()
    await mountSwitch()
    await wrapper!.setProps({ target: TARGET_B })
    held.answer()
    await flushPromises()
    expect(convertCall).not.toHaveBeenCalled()
    expect(intents).toEqual([])
  })
})

describe('the judgement and the badge', () => {
  it.each([
    ['root_superseded', { live_root_doc_id: ROOT_B }, '이미 다른 문서로 전환되어 이어진 문서입니다. 이어 가는 문서에서 전환하세요. Canvas를 다시 읽었습니다.'],
    ['root_body_unreadable', { reason: 'file_not_found', mode: 'succession' }, '문서 본문을 읽을 수 없어 전환할 수 없습니다. 본문을 먼저 복구하거나 파일 권한·인코딩을 확인하세요.'],
    ['front_matter_identity_unsafe', { reason: 'duplicate_key', field: 'type' }, '문서 머리말의 종류·번호를 안전하게 바꿀 수 없어 전환할 수 없습니다. 머리말을 고친 뒤 다시 전환하세요.'],
    ['attachment_target_rows_exist', { target_doc_id: ROOT_B, mode: 'succession' }, '첨부 파일 때문에 전환할 수 없습니다. 아무것도 바뀌지 않았습니다. 첨부를 확인한 뒤 다시 전환하세요.'],
    ['decision_taken', {}, '이미 단계 진행이 정해진 문서라 전환이 거절되었습니다. Canvas를 다시 읽었습니다.'],
  ])('a refused judgement (%s) disables the badge with its reason', async (code, extra, text) => {
    judgement = judged('refused', { new_doc_id: null, new_doc_exact: false, refusal: { code, ...extra } })
    await mountSwitch()
    expect(openButton().attributes('disabled')).toBeDefined()
    expect(openButton().attributes('title')).toBe(text)
  })

  it('no judgement yet (or none from an older server) blocks nothing', async () => {
    previewRead.mockImplementation(() => new Promise(() => undefined))
    await mountSwitch()
    expect(openButton().attributes('disabled')).toBeUndefined()
    wrapper!.unmount()
    previewRead.mockImplementation(async (docId: string) => toKindSwitchPreview(rowOf(docId)))   // no root_conversion
    await mountSwitch()
    expect(openButton().attributes('disabled')).toBeUndefined()
    await pickOther()
    // 0015-T T2: no confirmation — the server judges the switch itself.
    expect(confirmButtons()).toEqual([])
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
  })

  it('a judgement for the other kind is not used', async () => {
    judgement = judged('refused', { target_type: 'R', refusal: { code: 'root_superseded' } })
    await mountSwitch()
    expect(openButton().attributes('disabled')).toBeUndefined()
  })
})

describe('refusals when the switch runs (P0006 §3.5)', () => {
  it.each([
    ['root_superseded', 'superseded', true],
    ['root_body_unreadable', 'body_unreadable', false],
    ['front_matter_identity_unsafe', 'identity_unsafe', false],
    ['attachments_changed', 'attachment', false],
    ['attachment_path_unsafe', 'attachment', false],
  ])('409 %s shows failure.%s (Canvas read again: %s)', async (code, kind, refresh) => {
    refusal = axiosError(409, { error: code, detail: 'x', doc_id: ROOT_R })
    await mountSwitch()
    await pickOther()
    const line = find('kind-switch-line')
    expect(line.attributes('data-kind')).toBe('failure')
    expect(line.text()).toBe(i18n.global.t(`gui.inspector.kindSwitch.failure.${kind}`))
    expect(intents).toEqual(refresh ? [{ kind: 'refresh-flow', flowId: FLOW }] : [])
  })
})

describe('the document a succeeded root came from (P0006 §3.3, in the Frame with the real registry)', () => {
  const B_NODE: FlowGraphNode = { ...UNASSIGNED_REQ_NODE, id: `doc:${ROOT_B}`, docType: 'B', document: { ...UNASSIGNED_REQ_NODE.document!, docId: ROOT_B, typeCode: 'B' } }
  /** The succeeded B root on a Flow of the given ownership (v01: after its own workflow decision). */
  const bOn = (graph: Pick<FlowGraph, 'ownership' | 'viewMode'>) => selectionOf({ ...UNASSIGNED_GRAPH, ...graph, nodes: [B_NODE] }, B_NODE.id)

  async function mountFrame(selection: ReturnType<typeof selectionOf>) {
    wrapper = mount(DesignerInspector, { attachTo: document.body, props: { selection }, global: { plugins: [i18n, pinia] } })
    await flushPromises()
    return wrapper
  }

  const links = () => wrapper!.get('[data-test="designer-inspector-header"]').findAll('[data-test="kind-switch-converted-from"]')

  beforeEach(() => {
    convertedFrom = { doc_id: ROOT_R, mode: 'succession', at: '2026-10-16T10:20:44+09:00', by: 'u_hong', children: 3 }
  })

  it.each([
    ['unassigned', { ownership: 'unassigned', viewMode: 'draft' }, true],
    ['v01', { ownership: 'v01', viewMode: null }, false],
  ] as const)('on a %s Flow the header links it once and opens it (switch shown: %s)', async (_label, graph, switchShown) => {
    await mountFrame(bOn(graph))
    expect(links()).toHaveLength(1)
    expect(links()[0].text()).toBe('전환 전 문서: 0001-R')
    expect(find('inspector-kind-switch').exists()).toBe(switchShown)
    expect(originRead).toHaveBeenCalledWith(ROOT_B)
    await links()[0].trigger('click')
    // flowgate.v02.0070 T#4: that exact id, as history, with the target it was pressed on (for its Q&A).
    expect(opened).toHaveBeenCalledWith(ROOT_R, 'view', { exact: true, target: expect.objectContaining({ docId: ROOT_B, flowId: FLOW }) })
  })

  it('stays when the new document takes its workflow decision (unassigned -> v01)', async () => {
    await mountFrame(bOn({ ownership: 'unassigned', viewMode: 'draft' }))
    expect(links()).toHaveLength(1)
    await wrapper!.setProps({ selection: bOn({ ownership: 'v01', viewMode: null }) })
    await flushPromises()
    expect(find('inspector-kind-switch').exists()).toBe(false)
    expect(links()).toHaveLength(1)
    await links()[0].trigger('click')
    expect(opened).toHaveBeenCalledWith(ROOT_R, 'view', { exact: true, target: expect.objectContaining({ docId: ROOT_B }) })
  })

  it('is absent without a succession, on any ownership', async () => {
    convertedFrom = null
    await mountFrame(bOn({ ownership: 'unassigned', viewMode: 'draft' }))
    expect(links()).toHaveLength(0)
    wrapper!.unmount()
    await mountFrame(bOn({ ownership: 'v01', viewMode: null }))
    expect(links()).toHaveLength(0)
  })

  it('the switch alone does not draw it', async () => {
    await mountSwitch(targetOf(bOn({ ownership: 'unassigned', viewMode: 'draft' })))
    expect(find('inspector-kind-switch').exists()).toBe(true)
    expect(find('kind-switch-converted-from').exists()).toBe(false)
  })

  // --- flowgate.v02.0070 T#4 (B4): the old id as history, with its Q&A ---------------------------------------

  const thread = (docId: string) =>
    toQaThread({
      qa: {
        doc_id: docId,
        status: 'pending',
        items: [
          { id: 11, seq: 1, title: '기능 범위', body: '범위가 어디까지입니까?', asker_kind: 'ai', options: [{ id: 'a', label: '전체' }], answers: [] },
          { id: 12, seq: 2, title: '우선순위', body: '먼저 할 것은?', asker_kind: 'human', answer_count: 1, answers: [{ id: 1, body: '로그인', author_kind: 'human', author_name: '홍길동' }] },
        ],
      },
    })

  it('opens the exact window of the old id (not the B it is succeeded by), carrying the Q&A tab as its history', () => {
    try {
      realOpenDocument(ROOT_R, 'view', { exact: true, target: targetOf(bOn({ ownership: 'v01', viewMode: null })) })
      const [win] = documentWindowList()
      expect(win).toMatchObject({ id: `exact:${ROOT_R}`, docId: ROOT_R, shown: ROOT_R, exact: true, mode: 'view' })
      expect(win.aside?.component).toBe(QnaTab)
      expect(win.aside?.props).toMatchObject({ history: true, target: { docId: ROOT_R, flowId: FLOW } })
      // Without options a document opens as before: the usual window, following its chain, no part.
      realOpenDocument(ROOT_R)
      expect(documentWindowList()[1]).toMatchObject({ id: ROOT_R, exact: false, aside: null })
    } finally {
      closeAllDocumentWindows()
    }
  })

  it('the history reads the old id: its questions and answers, no question line, answer form or AI line; ↗ says 읽기 전용', async () => {
    qaRead.mockImplementation(async (docId: string) => thread(docId))
    const target = { ...targetOf(bOn({ ownership: 'unassigned', viewMode: 'draft' })), docId: ROOT_R }
    wrapper = mount(QnaTab, { attachTo: document.body, props: { target, history: true }, global: { plugins: [i18n, pinia] } })
    await flushPromises()
    expect(qaRead).toHaveBeenCalledWith(ROOT_R)
    expect(wrapper.findAll('[data-test="qna-item"]').map((item) => item.attributes('data-item'))).toEqual(['11', '12'])
    for (const test of ['qna-ask', 'qna-answer-form', 'qna-ai', 'qna-option']) expect(find(test).exists(), test).toBe(false)
    await wrapper.get('[data-test="qna-item"][data-item="11"] [data-test="qna-open"]').trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-test="qna-window"]')?.getAttribute('data-item')).toBe('11')
    expect(document.body.querySelector('[data-test="qna-window-body"]')?.textContent).toBe('범위가 어디까지입니까?')
    expect(document.body.querySelector('[data-test="qna-window-form"]')).toBeNull()
    expect(document.body.querySelector('[data-test="qna-window-mode"]')?.textContent).toBe('읽기 전용')
    // flowgate.v02.0070 T#5: the stored options stay readable; no body line in history (the badge says it).
    expect(document.body.querySelector('[data-test="qna-window-options"]')?.textContent).toBe('선택지: 전체')
    expect(document.body.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
    // flowgate.v02.0070 T#5: answers are read in the ↗ window (the list line keeps who asked only).
    await wrapper.get('[data-test="qna-item"][data-item="12"] [data-test="qna-open"]').trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-test="qna-window"][data-item="12"] [data-test="qna-window-answer"] .inspector-qna-text')?.textContent).toBe('로그인')
    // The same tab of the live document keeps its forms — in the ↗ window, with its document's body there (0070 T#5).
    wrapper.unmount()
    const live = { ...target, cardDocuments: [{ slot: 'requirement', docType: 'R', author: 'human', docId: ROOT_R, title: ROOT_R, status: 'open', docReviewStatus: null, hasBody: true, bodyState: null }] }
    wrapper = mount(QnaTab, { attachTo: document.body, props: { target: live }, global: { plugins: [i18n, pinia] } })
    await flushPromises()
    expect(find('qna-ask').exists()).toBe(true)
    await wrapper.get('[data-test="qna-item"][data-item="11"] [data-test="qna-open"]').trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-test="qna-window"][data-item="11"] [data-test="qna-window-form"]')).not.toBeNull()
  })
})

describe('kindSwitchModel', () => {
  const input = (refusalKind: KindSwitchFailureKind | null): KindSwitchInput => ({
    cardKind: 'requirement', docType: 'R', hasDocument: true, ownership: 'unassigned', readOnly: null, aiRunning: false,
    groupDisposed: false, busy: false, refusal: refusalKind,
  })

  it('a judged refusal is the last reason, after the existing ones', () => {
    expect(kindSwitchAvailability(input('superseded')).switch).toEqual({ visible: true, enabled: false, reason: 'failure.superseded' })
    expect(kindSwitchAvailability({ ...input('superseded'), groupDisposed: true }).switch.reason).toBe('reason.disposed')
    expect(kindSwitchAvailability(input(null)).switch).toEqual({ visible: true, enabled: true, reason: null })
  })

  it('only a superseded root (of the new kinds) reads the Canvas again', () => {
    expect(kindSwitchRefreshesFlow('superseded')).toBe(true)
    for (const kind of ['body_unreadable', 'identity_unsafe', 'attachment'] as const) expect(kindSwitchRefreshesFlow(kind)).toBe(false)
  })
})
