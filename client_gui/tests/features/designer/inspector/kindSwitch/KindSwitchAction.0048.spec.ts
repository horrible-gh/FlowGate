/**
 * flowgate.v02.0048 T#10 — the kind switch in the Inspector header with `@/features/document` mocked as a
 * small server (D#1 §12.1, D#2 §3.4): the button and its confirmation, the converter call (R -> B, B -> R),
 * `refresh-flow` after a switch (the server sends no event), the switch kept disabled until the Canvas
 * moves the target, the refusals (flat 409 flow_owned_by_graph / flow_ownership_conflict, the converter's
 * 409 details, disposed, 423, 422, 403, 404, network) shown as a reason with the state read again and the
 * Canvas read again when the Flow moved under it, the guards (disposed, AI running) read through the real
 * hub, read-only views and a Node change. Last, the real registry puts the switch in the Frame header of an
 * unassigned R / B root only — never on v01, Graph or conflict Flows or on other cards.
 * flowgate.v02.0051 stage 2: the switch is drawn as the v7 header 종류 배지 ▾ (headerKind) — the badge opens
 * a menu, picking the other kind opens the same confirmation; a disabled switch carries its reason as the
 * badge's hover text instead of a line.
 * flowgate.v02.0070 (0015-T T2): picking the other kind switches at once — no 종류 전환 confirmation, and no read awaited
 * before the call: a read still pending (or settling after the part is gone / the selection cleared) starts nothing.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import KindSwitchAction from '@/features/designer/inspector/features/kindSwitch/KindSwitchAction.vue'
import { INSPECTOR_HOST, type InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import {
  GRAPH_GRAPH,
  SELECTIONS,
  UNASSIGNED_GRAPH,
  UNASSIGNED_REQ_NODE,
  V01_GRAPH,
  prepareInspectorI18n,
  selectionOf,
  targetOf,
} from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

// The calls are mocked; normalization and error reading stay real.
const convertCall = vi.fn()
const documentRead = vi.fn()
const activityRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
const qaRead = vi.fn()
const attachmentsRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    kindSwitchConvert: (...args: unknown[]) => convertCall(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    // flowgate.v02.0064 T#4: the switch reads the document state with its judgement (P0006 §6.1).
    kindSwitchPreviewRead: (...args: unknown[]) => documentRead(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    // The other features sharing the Frame in the registry cases.
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    qaRead: (...args: unknown[]) => qaRead(...args),
    attachmentsRead: (...args: unknown[]) => attachmentsRead(...args),
  }
})

const { toReviewDocument, toReviewActivity, toKindSwitchResult, toDocumentDetail, toQaThread, toAttachmentsList } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const FLOW = UNASSIGNED_GRAPH.flowId
const ROOT_R = UNASSIGNED_REQ_NODE.document!.docId
const ROOT_B = `${FLOW}.0001-B`

// --- The server ------------------------------------------------------------------------------------

interface ServerDoc {
  typeCode: string
  disposed: boolean
}

const docs = new Map<string, ServerDoc>()
let running = false
/** The next switch answer is this refusal (once). */
let refusal: unknown = null

const rowOf = (docId: string) => {
  const doc = docs.get(docId)
  if (!doc) throw { isAxiosError: true, response: { status: 404, data: { detail: `Document not found: ${docId}` } } }
  return { doc_id: docId, project_id: 'flowgate', group_id: FLOW, type_code: doc.typeCode, seq: 1, title: '새 요구사항', status: 'draft', doc_review_status: null, group_disposed: doc.disposed }
}

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })

function serve() {
  documentRead.mockImplementation(async (docId: string) => toReviewDocument(rowOf(docId)))
  activityRead.mockImplementation(async (groupId: string) =>
    toReviewActivity(running ? { ok: true, active: true, run_id: 'run_1', status: 'running', group_id: groupId } : { ok: true, active: false, group_id: groupId }),
  )
  convertCall.mockImplementation(async (docId: string, newType: string) => {
    if (refusal) {
      const error = refusal
      refusal = null
      throw error
    }
    const doc = docs.get(docId)
    if (!doc) throw axiosError(404, { detail: `Document not found: ${docId}` })
    const nextId = docId.replace(/-[RB]$/, `-${newType}`)
    docs.delete(docId)
    docs.set(nextId, { ...doc, typeCode: newType })
    return toKindSwitchResult({ data: rowOf(nextId) }, docId)
  })
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(rowOf(docId)))
  relationsRead.mockImplementation(async () => [])
  qaRead.mockImplementation(async (docId: string) => toQaThread({ qa: { doc_id: docId, status: null, items: [] } }))
  attachmentsRead.mockImplementation(async (docId: string) => toAttachmentsList({ doc_id: docId, items: [] }))
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
  docs.clear()
  docs.set(ROOT_R, { typeCode: 'R', disposed: false })
  running = false
  refusal = null
  intents = []
  for (const mock of [convertCall, documentRead, activityRead, detailRead, relationsRead, qaRead, attachmentsRead]) mock.mockReset()
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  vi.useRealTimers()
  document.body.innerHTML = ''
})

async function mountSwitch(target: InspectorTarget) {
  wrapper = mount(KindSwitchAction, {
    attachTo: document.body,
    props: { target },
    global: { plugins: [i18n, pinia], provide: { [INSPECTOR_HOST as symbol]: { request: (intent: InspectorHostIntent) => intents.push(intent) } } },
  })
  await flushPromises()
  return wrapper
}

const TARGET_R = targetOf(SELECTIONS.unassigned)

/** The unassigned root as a B document (what the Canvas gives after an R -> B switch). */
const B_NODE: FlowGraphNode = {
  ...UNASSIGNED_REQ_NODE,
  id: `doc:${ROOT_B}`,
  docType: 'B',
  document: { ...UNASSIGNED_REQ_NODE.document!, docId: ROOT_B, typeCode: 'B' },
}
const TARGET_B = targetOf(selectionOf({ ...UNASSIGNED_GRAPH, nodes: [B_NODE] }, B_NODE.id))

const find = (test: string) => wrapper!.find(`[data-test="${test}"]`)
/** flowgate.v02.0051 stage 2: the switch is the v7 header 종류 배지 ▾ — the badge opens a menu; 0015-T T2: the other kind switches. */
const openButton = () => find('kind-switch-open')
const line = () => find('kind-switch-line')
const confirmButtons = () => [...document.body.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions button')]
const confirmText = () => document.body.querySelector('.ui-confirm-message')?.textContent ?? ''
const menuItem = (kind: 'R' | 'B') => document.body.querySelector<HTMLButtonElement>(`[data-test="kind-switch-item-${kind}"]`)

/** Opens the badge menu and picks the other kind: the switch is asked at once (when it is usable). */
async function pickOther() {
  await openButton().trigger('click')
  await flushPromises()
  const to = openButton().attributes('data-to') as 'R' | 'B'
  menuItem(to)?.click()
  await flushPromises()
}

/** 0015-T T2: a pick of the other kind is the switch — no confirmation opens on the way. */
async function switchKind() {
  await pickOther()
  expect(confirmButtons()).toEqual([])
}

describe('the switch button and its menu', () => {
  it('an unassigned R root shows the 요구사항 ▾ badge whose menu offers 버그, and reads its guards', async () => {
    await mountSwitch(TARGET_R)
    expect(openButton().text()).toBe('요구사항 ▾')
    expect(openButton().attributes('data-to')).toBe('B')
    expect(openButton().attributes('disabled')).toBeUndefined()
    expect(openButton().attributes('title')).toBe('종류 전환: 그룹의 첫 실행 전까지')
    await openButton().trigger('click')
    await flushPromises()
    expect(menuItem('R')?.getAttribute('aria-checked')).toBe('true')
    expect(menuItem('B')?.textContent?.trim()).toBe('🐞버그')
    menuItem('R')!.click()
    await flushPromises()
    expect(confirmButtons()).toEqual([])
    expect(documentRead).toHaveBeenCalledWith(ROOT_R)
    expect(activityRead).toHaveBeenCalledWith(FLOW)
    expect(line().exists()).toBe(false)
  })

  it('a B root offers the way back', async () => {
    docs.set(ROOT_B, { typeCode: 'B', disposed: false })
    await mountSwitch(TARGET_B)
    expect(openButton().text()).toBe('버그 ▾')
    expect(openButton().attributes('data-to')).toBe('R')
  })

  it('picking the other kind switches at once: no 종류 전환 confirmation (0015-T T2)', async () => {
    await mountSwitch(TARGET_R)
    await pickOther()
    expect(confirmButtons()).toEqual([])
    expect(confirmText()).toBe('')
    expect(document.body.textContent).not.toContain('문서 번호(0001-R → 0001-B)가 바뀌고')
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
  })

  it('a pick while the state is still being read does not wait for it: the switch is asked at once (0015-T T2)', async () => {
    let answer!: () => void
    documentRead.mockImplementationOnce((docId: string) => {
      // The state as it was when read (the switch removes the old id from the server meanwhile).
      const row = toReviewDocument(rowOf(docId))
      return new Promise((resolve) => (answer = () => resolve(row)))
    })
    await mountSwitch(TARGET_R)
    await pickOther()
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
    answer()
    await flushPromises()
    expect(confirmButtons()).toEqual([])
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(intents).toHaveLength(1)
  })

  it('a failed state read blocks nothing: the server judges the switch (0015-T T2)', async () => {
    documentRead.mockRejectedValue(networkError())
    await mountSwitch(TARGET_R)
    await pickOther()
    expect(confirmButtons()).toEqual([])
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
  })

  it.each(['answers', 'fails'] as const)('the part unmounted while the state is read: the read that %s afterwards starts nothing', async (end) => {
    let settle!: () => void
    documentRead.mockImplementationOnce((docId: string) => new Promise((resolve, reject) => (settle = () => (end === 'answers' ? resolve(toReviewDocument(rowOf(docId))) : reject(networkError())))))
    await mountSwitch(TARGET_R)
    wrapper!.unmount()
    wrapper = null
    settle()
    await flushPromises()
    expect(convertCall).not.toHaveBeenCalled()
    expect(intents).toEqual([])
  })

  it('a Node change while the state is read: its answer starts nothing on the Node left behind', async () => {
    let answer!: () => void
    docs.set(ROOT_B, { typeCode: 'B', disposed: false })
    documentRead.mockImplementationOnce((docId: string) => new Promise((resolve) => (answer = () => resolve(toReviewDocument(rowOf(docId))))))
    await mountSwitch(TARGET_R)
    await wrapper!.setProps({ target: TARGET_B })
    answer()
    await flushPromises()
    expect(convertCall).not.toHaveBeenCalled()
    expect(confirmButtons()).toEqual([])
  })

  it('en / ja strings', async () => {
    i18n.global.locale.value = 'en'
    await mountSwitch(TARGET_R)
    expect(openButton().text()).toBe('Requirement ▾')
    wrapper!.unmount()
    i18n.global.locale.value = 'ja'
    await mountSwitch(TARGET_R)
    expect(openButton().text()).toBe('要件 ▾')
  })
})

describe('a switch (D#2 §3.4)', () => {
  it('R -> B: one converter call, refresh-flow once, a done line, and no second switch on the old id', async () => {
    await mountSwitch(TARGET_R)
    documentRead.mockClear()
    activityRead.mockClear()
    await switchKind()

    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
    expect(docs.has(ROOT_R)).toBe(false)
    expect(docs.get(ROOT_B)?.typeCode).toBe('B')
    // The old id is gone on the server: it is not read again.
    expect(documentRead).not.toHaveBeenCalled()
    expect(line().attributes('data-kind')).toBe('done')
    expect(line().text()).toBe('버그 문서(0001-B)로 바꿨습니다. Canvas를 다시 읽는 중입니다.')
    // Until the Canvas moves the selection, the target still names the old id.
    expect(openButton().attributes('disabled')).toBeDefined()
    await openButton().trigger('click')
    await flushPromises()
    expect(confirmButtons()).toEqual([])
    expect(convertCall).toHaveBeenCalledTimes(1)
  })

  it('the Canvas gives the new identity: the switch follows it and offers the way back', async () => {
    await mountSwitch(TARGET_R)
    await switchKind()
    await wrapper!.setProps({ target: TARGET_B })
    await flushPromises()
    expect(line().exists()).toBe(false)
    expect(openButton().text()).toBe('버그 ▾')
    expect(openButton().attributes('disabled')).toBeUndefined()
    expect(documentRead).toHaveBeenLastCalledWith(ROOT_B)

    await switchKind()
    expect(convertCall).toHaveBeenLastCalledWith(ROOT_B, 'R')
    expect(docs.get(ROOT_R)?.typeCode).toBe('R')
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }, { kind: 'refresh-flow', flowId: FLOW }])
  })

  it('waiting for the answer: busy, and a second click sends nothing', async () => {
    let answer!: (value: unknown) => void
    convertCall.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)))
    await mountSwitch(TARGET_R)
    await switchKind()
    expect(find('inspector-kind-switch').attributes('data-busy')).toBe('true')
    expect(openButton().attributes('disabled')).toBeDefined()
    await openButton().trigger('click')
    expect(convertCall).toHaveBeenCalledTimes(1)
    answer(toKindSwitchResult({ data: { doc_id: ROOT_B, type_code: 'B', group_id: FLOW } }, ROOT_R))
    await flushPromises()
    expect(find('inspector-kind-switch').attributes('data-busy')).toBe('false')
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
  })
})

describe('refusals: a reason, the state read again, the Canvas too when the Flow moved', () => {
  const rows: readonly [string, unknown, string, boolean][] = [
    ['409 flow_owned_by_graph (the Flow became a Graph Flow)', axiosError(409, { error: 'flow_owned_by_graph', doc_id: ROOT_R, group_id: FLOW, graph_revision_no: 1 }), 'Graph 플로우의 시작 문서라 전환이 거절되었습니다. Canvas를 다시 읽었습니다.', true],
    ['409 flow_ownership_conflict', axiosError(409, { error: 'flow_ownership_conflict', doc_id: ROOT_R, group_id: FLOW, graph_revision_no: 1 }), '소유 충돌 플로우라 전환이 거절되었습니다. Canvas를 다시 읽었습니다.', true],
    ['409 decided (a v0.1 sequence now exists)', axiosError(409, { detail: 'Cannot convert root type after the workflow decision was taken.' }), '이미 단계 진행이 정해진 문서라 전환이 거절되었습니다. Canvas를 다시 읽었습니다.', true],
    ['409 child documents', axiosError(409, { detail: 'Cannot convert root type: child documents already reference it.' }), '이 문서를 가리키는 하위 문서가 있어 전환이 거절되었습니다. Canvas를 다시 읽었습니다.', true],
    ['409 target id exists', axiosError(409, { detail: `Target doc_id already exists: ${ROOT_B}.` }), '바꿀 문서 번호가 이미 있어 전환이 거절되었습니다. Canvas를 다시 읽었습니다.', true],
    ['404', axiosError(404, { detail: `Document not found: ${ROOT_R}` }), '문서를 찾을 수 없습니다. Canvas를 다시 읽었습니다.', true],
    ['409 disposed', axiosError(409, { detail: 'Modification not allowed: the group has been disposed.' }), '폐기된 그룹이라 거절되었습니다. 상태를 다시 읽었습니다.', false],
    ['423 AI run', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED' } }), 'AI 실행 중이라 거절되었습니다. 상태를 다시 읽었습니다.', false],
    ['422', axiosError(422, { detail: 'Only workflow root documents (R/B) can be converted' }), '전환할 수 없는 문서라 거절되었습니다.', false],
    ['403', axiosError(403, { detail: 'Forbidden' }), '이 조작을 할 권한이 없습니다.', false],
    ['network', networkError(), '서버에 연결하지 못했습니다.', false],
    ['500', axiosError(500, {}), '전환을 마치지 못했습니다.', false],
  ]

  it.each(rows)('%s', async (_label, error, text, refreshes) => {
    await mountSwitch(TARGET_R)
    documentRead.mockClear()
    activityRead.mockClear()
    refusal = error
    await switchKind()
    expect(convertCall).toHaveBeenCalledTimes(1)
    expect(line().attributes('data-kind')).toBe('failure')
    expect(line().text()).toBe(text)
    expect(documentRead).toHaveBeenCalledWith(ROOT_R)
    expect(activityRead).toHaveBeenCalledWith(FLOW)
    expect(intents).toEqual(refreshes ? [{ kind: 'refresh-flow', flowId: FLOW }] : [])
    // Nothing changed on the server; the switch can be tried again (when the guards allow it).
    expect(docs.get(ROOT_R)?.typeCode).toBe('R')
    expect(openButton().attributes('disabled')).toBeUndefined()
  })

  it('a refusal disappears when the other kind is picked again, and the retry succeeds', async () => {
    await mountSwitch(TARGET_R)
    refusal = networkError()
    await switchKind()
    expect(line().attributes('data-kind')).toBe('failure')
    await switchKind()
    expect(line().attributes('data-kind')).toBe('done')
    expect(convertCall).toHaveBeenCalledTimes(2)
  })

  it('a refusal of the Node left behind is not shown on the new one', async () => {
    let fail!: (error: unknown) => void
    convertCall.mockImplementationOnce(() => new Promise((_resolve, reject) => (fail = reject)))
    docs.set(ROOT_B, { typeCode: 'B', disposed: false })
    await mountSwitch(TARGET_R)
    await switchKind()
    await wrapper!.setProps({ target: TARGET_B })
    fail(axiosError(409, { detail: 'Cannot convert root type: child documents already reference it.' }))
    await flushPromises()
    expect(line().exists()).toBe(false)
  })
})

describe('guards and events', () => {
  it('a disposed group: shown, disabled, with its reason; nothing is sent', async () => {
    docs.get(ROOT_R)!.disposed = true
    await mountSwitch(TARGET_R)
    expect(openButton().attributes('disabled')).toBeDefined()
    expect(openButton().attributes('title')).toBe('폐기된 그룹이라 바꿀 수 없습니다.')
    expect(line().exists()).toBe(false)
    await openButton().trigger('click')
    await flushPromises()
    expect(confirmButtons()).toEqual([])
    expect(convertCall).not.toHaveBeenCalled()
  })

  it('an AI run of the Flow disables it; ai_invoke_finished of the Flow enables it again', async () => {
    vi.useFakeTimers()
    running = true
    await mountSwitch(TARGET_R)
    stream.open()
    expect(openButton().attributes('title')).toBe('AI 실행 중이라 바꿀 수 없습니다.')
    running = false
    stream.emit('ai_invoke_finished', { groupId: 'flowgate.v02.9999' })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(openButton().attributes('title')).toBe('AI 실행 중이라 바꿀 수 없습니다.')
    stream.emit('ai_invoke_finished', { groupId: FLOW })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(openButton().attributes('title')).toBe('종류 전환: 그룹의 첫 실행 전까지')
    expect(openButton().attributes('disabled')).toBeUndefined()
  })

  it('a document event of the root reads its state again (group disposed meanwhile)', async () => {
    vi.useFakeTimers()
    await mountSwitch(TARGET_R)
    stream.open()
    docs.get(ROOT_R)!.disposed = true
    stream.emit('document_explorer_refresh', { docId: ROOT_R, groupId: FLOW })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(openButton().attributes('title')).toBe('폐기된 그룹이라 바꿀 수 없습니다.')
  })

  it('an unanswered guard read does not block (the server refuses in the same states)', async () => {
    documentRead.mockRejectedValue(networkError())
    activityRead.mockRejectedValue(networkError())
    await mountSwitch(TARGET_R)
    expect(openButton().attributes('disabled')).toBeUndefined()
  })

  it('a revision view of an unassigned Flow: shown disabled with the read-only reason', async () => {
    const target = { ...TARGET_R, viewMode: 'revision', readOnly: 'revision' as const }
    await mountSwitch(target)
    expect(openButton().attributes('disabled')).toBeDefined()
    expect(openButton().attributes('title')).toBe('Graph revision 보기에서는 바꿀 수 없습니다. draft 보기로 옮기세요.')
  })

  it.each([
    ['v01', targetOf(selectionOf({ ...V01_GRAPH, nodes: [{ ...UNASSIGNED_REQ_NODE }] }, UNASSIGNED_REQ_NODE.id))],
    ['graph', targetOf(selectionOf({ ...GRAPH_GRAPH, nodes: [{ ...UNASSIGNED_REQ_NODE }] }, UNASSIGNED_REQ_NODE.id))],
    ['conflict', { ...TARGET_R, ownership: 'conflict', readOnly: 'conflict' as const }],
  ] as const)('mounted directly on a %s root it draws nothing', async (_label, target) => {
    await mountSwitch(target)
    expect(find('inspector-kind-switch').exists()).toBe(false)
  })
})

// --- The Frame with the real registry ---------------------------------------------------------------

async function mountFrame(selection: FlowGraphSelection | null) {
  wrapper = mount(DesignerInspector, { attachTo: document.body, props: { selection }, global: { plugins: [i18n, pinia] } })
  await flushPromises()
  return wrapper
}

/** The same R root Node on a Flow of the given ownership / view. */
const rootOn = (graph: Pick<FlowGraph, 'ownership' | 'viewMode'>, node: FlowGraphNode = UNASSIGNED_REQ_NODE) =>
  selectionOf({ ...UNASSIGNED_GRAPH, ...graph, nodes: [node] }, node.id)

describe('in the Frame (real registry)', () => {
  it('an unassigned R root has the switch as its header kind badge (flowgate.v02.0051: headerKind)', async () => {
    await mountFrame(SELECTIONS.unassigned)
    expect(wrapper!.get('[data-test="designer-inspector-header"]').find('[data-test="inspector-kind-switch"]').exists()).toBe(true)
    expect(find('kind-switch-open').text()).toBe('요구사항 ▾')
    // The static badge gives way to the switch.
    expect(wrapper!.findAll('[data-test="designer-inspector-kind"]')).toHaveLength(0)
  })

  it.each(['answers', 'fails'] as const)('the selection cleared on the Canvas while the state is read: the read that %s afterwards sends nothing (0015-T T2)', async (end) => {
    let settle!: () => void
    documentRead.mockImplementation((docId: string) => new Promise((resolve, reject) => (settle = () => (end === 'answers' ? resolve(toReviewDocument(rowOf(docId))) : reject(networkError())))))
    await mountFrame(SELECTIONS.unassigned)
    expect(find('kind-switch-open').exists()).toBe(true)
    await wrapper!.setProps({ selection: null })
    await flushPromises()
    expect(find('inspector-kind-switch').exists()).toBe(false)
    settle()
    await flushPromises()
    expect(convertCall).not.toHaveBeenCalled()
    expect(wrapper!.emitted('host')).toBeUndefined()
  })

  it('a host intent leaves the Frame as its `host` event', async () => {
    await mountFrame(SELECTIONS.unassigned)
    await switchKind()
    expect(wrapper!.emitted('host')).toEqual([[{ kind: 'refresh-flow', flowId: FLOW }]])
  })

  it.each([
    ['v01 root', rootOn({ ownership: 'v01', viewMode: null })],
    ['graph root (draft view)', rootOn({ ownership: 'graph', viewMode: 'draft' })],
    ['graph root (revision view)', rootOn({ ownership: 'graph', viewMode: 'revision' })],
    ['conflict root', rootOn({ ownership: 'conflict', viewMode: 'revision' })],
    ['v01 TR', SELECTIONS.v01],
    ['v01 slot without a document', SELECTIONS.v01NoDocument],
    ['graph TS', SELECTIONS.graph],
    ['graph gate', SELECTIONS.graphGate],
    ['conflict D', SELECTIONS.conflict],
    ['unassigned R without a document', rootOn({ ownership: 'unassigned', viewMode: 'draft' }, { ...UNASSIGNED_REQ_NODE, document: null })],
    ['unassigned N', rootOn({ ownership: 'unassigned', viewMode: 'draft' }, { ...UNASSIGNED_REQ_NODE, docType: 'N', kind: 'research', document: { ...UNASSIGNED_REQ_NODE.document!, typeCode: 'N' } })],
  ] as const)('%s: no switch', async (_label, selection) => {
    await mountFrame(selection)
    expect(find('inspector-kind-switch').exists()).toBe(false)
    expect(find('kind-switch-open').exists()).toBe(false)
    expect(convertCall).not.toHaveBeenCalled()
  })

  it('a B root on an unassigned Flow offers the way back', async () => {
    docs.set(ROOT_B, { typeCode: 'B', disposed: false })
    await mountFrame(rootOn({ ownership: 'unassigned', viewMode: 'draft' }, B_NODE))
    expect(find('kind-switch-open').text()).toBe('버그 ▾')
  })
})
