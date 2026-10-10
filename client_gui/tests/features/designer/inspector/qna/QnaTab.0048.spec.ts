/**
 * flowgate.v02.0048 T#7 — the Q&A tab with `@/features/document` mocked as a small server (D#1
 * §12.1): the questions of the target document in display order with who asked and who answered,
 * answering with prose / an option / both and the questions read again after it, the refusals
 * (423 AI running, 409 disposed, 403, 404, network) shown as a reason with the questions read again,
 * the read-only Flows and the guard (AI running, disposed), the Q&A events read again through the
 * real hub (only for the target document), a failed read with retry, and a Node change. Last, the
 * real registry: the Q&A tab with its number in the Frame (the number follows the events and the
 * answers), and no tab on a Node without a document.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import QnaTab from '@/features/designer/inspector/features/qna/QnaTab.vue'
import { QNA_EVENTS, QNA_GUARD_EVENTS } from '@/features/designer/inspector/features/qna/useQna'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { SELECTIONS, V01_GRAPH, V01_TASK_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

// The calls are mocked; normalization and error reading stay real.
const qaReadCall = vi.fn()
const qaAnswerCall = vi.fn()
const activityRead = vi.fn()
const documentRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    qaRead: (...args: unknown[]) => qaReadCall(...args),
    qaAnswer: (...args: unknown[]) => qaAnswerCall(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    // The documents tab and the header actions share the Frame in the registry cases.
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
  }
})

const { toQaThread, toQaAnswerResult, toReviewActivity, toReviewDocument, toDocumentDetail } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const V01_DOC = V01_TASK_NODE.document!.docId
const V01_FLOW = V01_GRAPH.flowId
const OTHER_DOC = 'flowgate.v02.0048.0013-TR'

// --- The server ------------------------------------------------------------------------------------

interface ServerItem {
  id: number
  seq: number
  title: string | null
  body: string
  asker_kind: string
  options: { id: string; label: string }[]
  asker_provider: Record<string, unknown> | null
  answers: Record<string, unknown>[]
}

const threads = new Map<string, ServerItem[]>()
let running = false
let disposed = false
let answerSeq = 100
/** The next answer / read is this refusal (once). */
const refusals = new Map<string, unknown>()

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })
const qRefusal = (status: number, message: string) => axiosError(status, { ok: false, http_status: status, error_message: message })

function refusalOf(call: string): unknown {
  const refusal = refusals.get(call)
  refusals.delete(call)
  return refusal
}

function treeOf(docId: string) {
  const items = threads.get(docId) ?? []
  return {
    ok: true,
    qa: {
      doc_id: docId,
      status: items.length ? (items.every((entry) => entry.answers.length) ? 'done' : 'pending') : null,
      items: items.map((entry) => ({ ...entry, answer_count: entry.answers.length })),
    },
  }
}

function seed() {
  threads.set(V01_DOC, [
    {
      id: 10,
      seq: 1,
      title: '조사 범위 확인',
      body: '어느 범위를 조사할까요?',
      asker_kind: 'ai',
      options: [
        { id: 'o1', label: '코드 품질' },
        { id: 'o2', label: '성능' },
      ],
      asker_provider: { ai_run_id: 'aiv_1', ai_provider_id: 'p1', ai_provider_name: 'Claude Opus 5.5' },
      answers: [],
    },
    {
      id: 11,
      seq: 2,
      title: '가정 안내',
      body: '범위를 가정했습니다.',
      asker_kind: 'ai',
      options: [],
      asker_provider: null,
      answers: [{ id: 9, body: 'ok', author_kind: 'human', author_id: 'u1', selected_options: [], author_provider: null }],
    },
    { id: 12, seq: 3, title: '사람 질문', body: '이 항목은 필요한가요?', asker_kind: 'human', options: [], asker_provider: null, answers: [] },
    { id: 13, seq: 4, title: '두 번째 대기', body: '완료 기준은?', asker_kind: 'ai', options: [], asker_provider: null, answers: [] },
  ])
  threads.set(OTHER_DOC, [{ id: 20, seq: 1, title: '다른 문서 질문', body: '?', asker_kind: 'ai', options: [], asker_provider: null, answers: [] }])
}

function serve() {
  qaReadCall.mockImplementation(async (docId: string) => {
    const refusal = refusalOf('read')
    if (refusal) throw refusal
    return toQaThread(treeOf(docId))
  })
  qaAnswerCall.mockImplementation(async (request: { docId: string; itemId: number; body: string; selectedOptionIds: string[] }) => {
    const refusal = refusalOf('answer')
    if (refusal) throw refusal
    const entry = threads.get(request.docId)!.find((candidate) => candidate.id === request.itemId)!
    const label = entry.options.find((option) => option.id === request.selectedOptionIds[0])?.label
    entry.answers.push({ id: ++answerSeq, body: request.body || label, author_kind: 'human', author_id: 'u1', selected_options: request.selectedOptionIds, author_provider: null })
    return toQaAnswerResult({ ok: true, doc_id: request.docId, item_id: request.itemId, answer_id: answerSeq, author_kind: 'human', status: 'pending' })
  })
  activityRead.mockImplementation(async (groupId: string) =>
    toReviewActivity(running ? { active: true, run_id: 'run_1', status: 'running', group_id: groupId } : { active: false, group_id: groupId }),
  )
  documentRead.mockImplementation(async (docId: string) => toReviewDocument(rowOf(docId)))
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(rowOf(docId)))
  relationsRead.mockImplementation(async () => [])
}

const rowOf = (docId: string) => ({ doc_id: docId, group_id: docId.split('.').slice(0, 3).join('.'), type_code: 'TR', title: docId, status: 'open', doc_review_status: 'approved', group_disposed: disposed })

// --- Mounting --------------------------------------------------------------------------------------

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  threads.clear()
  refusals.clear()
  running = false
  disposed = false
  answerSeq = 100
  for (const mock of [qaReadCall, qaAnswerCall, activityRead, documentRead, detailRead, relationsRead]) mock.mockReset()
  seed()
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  vi.useRealTimers()
})

async function mountTab(target: InspectorTarget) {
  wrapper = mount(QnaTab, { props: { target }, global: { plugins: [i18n, pinia] } })
  await flushPromises()
  return wrapper
}

const v01Target = (fields: Partial<InspectorTarget> = {}): InspectorTarget => ({ ...targetOf(SELECTIONS.v01), ...fields })

const itemEl = (w: VueWrapper<any>, id: number) => w.find(`[data-test="qna-item"][data-item="${id}"]`)
const order = (w: VueWrapper<any>) => w.findAll('[data-test="qna-item"]').map((el) => [Number(el.attributes('data-item')), el.attributes('data-state')])
const submitOf = (w: VueWrapper<any>, id: number) => itemEl(w, id).find('[data-test="qna-answer-submit"]')
const isDisabled = (el: ReturnType<VueWrapper<any>['find']>) => el.attributes('disabled') !== undefined

async function write(w: VueWrapper<any>, id: number, text: string) {
  await itemEl(w, id).find('textarea[data-test="qna-answer-body-input"]').setValue(text)
}

async function send(w: VueWrapper<any>, id: number) {
  await submitOf(w, id).trigger('click')
  await flushPromises()
}

// --- Reading -------------------------------------------------------------------------------------

describe('Q&A tab — the questions of the target document (flowgate.v02.0048 T#7)', () => {
  it('reads the target document once, shows waiting first, then open, then answered', async () => {
    const w = await mountTab(v01Target())
    expect(qaReadCall).toHaveBeenCalledTimes(1)
    expect(qaReadCall).toHaveBeenCalledWith(V01_DOC)
    expect(order(w)).toEqual([
      [10, 'waiting'],
      [13, 'waiting'],
      [12, 'open'],
      [11, 'answered'],
    ])
    expect(itemEl(w, 10).find('[data-test="qna-item-state"]').text()).toBe('답변 대기')
    expect(itemEl(w, 12).find('[data-test="qna-item-state"]').text()).toBe('미답변')
  })

  it('shows who asked and who answered — never a guessed provider', async () => {
    const w = await mountTab(v01Target())
    expect(itemEl(w, 10).find('[data-test="qna-item-asker"]').text()).toBe('질문: AI · Claude Opus 5.5')
    expect(itemEl(w, 13).find('[data-test="qna-item-asker"]').text()).toBe('질문: AI(실행 정보 없음)')
    expect(itemEl(w, 12).find('[data-test="qna-item-asker"]').text()).toBe('질문: 사용자')
    expect(itemEl(w, 11).find('[data-test="qna-answer-side"]').text()).toBe('답변: 사용자')
    expect(itemEl(w, 11).find('[data-test="qna-answer-body"]').text()).toBe('ok')
  })

  it('an answered question has no answer form; an unanswered one (AI or person asked) has one', async () => {
    const w = await mountTab(v01Target())
    expect(itemEl(w, 11).find('[data-test="qna-answer-form"]').exists()).toBe(false)
    for (const id of [10, 12, 13]) expect(itemEl(w, id).find('[data-test="qna-answer-form"]').exists(), String(id)).toBe(true)
    expect(itemEl(w, 10).findAll('[data-test="qna-option"]').map((el) => el.text())).toEqual(['코드 품질', '성능'])
    expect(itemEl(w, 13).findAll('[data-test="qna-option"]')).toHaveLength(0)
  })

  it('a document without questions shows the empty state', async () => {
    const w = await mountTab(v01Target({ docId: 'flowgate.v02.0048.0099-T' }))
    expect(w.find('[data-test="inspector-pane-state"]').attributes('data-state')).toBe('empty')
    expect(w.text()).toContain('이 문서에 등록된 질문이 없습니다.')
  })

  it('a failed read shows the error with retry; retry reads again', async () => {
    refusals.set('read', qRefusal(403, 'Insufficient permissions'))
    const w = await mountTab(v01Target())
    expect(w.find('[data-test="inspector-pane-state"]').attributes('data-state')).toBe('error')
    expect(w.text()).toContain('이 문서의 질문을 볼 권한이 없습니다.')
    await w.find('[data-test="inspector-pane-retry"]').trigger('click')
    await flushPromises()
    expect(qaReadCall).toHaveBeenCalledTimes(2)
    expect(order(w)).toHaveLength(4)
  })

  it('a network failure of the read is worded as such', async () => {
    refusals.set('read', networkError())
    const w = await mountTab(v01Target())
    expect(w.text()).toContain('서버에 연결하지 못했습니다.')
  })
})

// --- Answering -----------------------------------------------------------------------------------

describe('Q&A tab — answering (flowgate.v02.0048 T#7)', () => {
  it('blank prose cannot be sent; trimmed prose is sent, then the questions and the guard are read again', async () => {
    const w = await mountTab(v01Target())
    expect(isDisabled(submitOf(w, 13))).toBe(true)
    await write(w, 13, '   ')
    expect(isDisabled(submitOf(w, 13))).toBe(true)
    await write(w, 13, '  테스트 통과가 완료 기준입니다. ')
    expect(isDisabled(submitOf(w, 13))).toBe(false)
    await send(w, 13)
    expect(qaAnswerCall).toHaveBeenCalledWith({ docId: V01_DOC, itemId: 13, body: '테스트 통과가 완료 기준입니다.', selectedOptionIds: [] })
    expect(qaReadCall).toHaveBeenCalledTimes(2)
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(documentRead).toHaveBeenCalledTimes(2)
    expect(itemEl(w, 13).attributes('data-state')).toBe('answered')
    expect(itemEl(w, 13).find('[data-test="qna-answer-form"]').exists()).toBe(false)
    expect(itemEl(w, 13).find('[data-test="qna-answer-body"]').text()).toBe('테스트 통과가 완료 기준입니다.')
  })

  it('a picked option alone can be sent; picking again takes it back; the answer shows the pick', async () => {
    const w = await mountTab(v01Target())
    const options = () => itemEl(w, 10).findAll('[data-test="qna-option"]')
    await options()[1].trigger('click')
    expect(options()[1].attributes('aria-pressed')).toBe('true')
    await options()[1].trigger('click')
    expect(options()[1].attributes('aria-pressed')).toBe('false')
    expect(isDisabled(submitOf(w, 10))).toBe(true)
    await options()[0].trigger('click')
    expect(isDisabled(submitOf(w, 10))).toBe(false)
    await send(w, 10)
    expect(qaAnswerCall).toHaveBeenCalledWith({ docId: V01_DOC, itemId: 10, body: '', selectedOptionIds: ['o1'] })
    expect(itemEl(w, 10).find('[data-test="qna-answer-picked"]').text()).toBe('선택: 코드 품질')
    expect(itemEl(w, 10).find('[data-test="qna-answer-body"]').text()).toBe('코드 품질')
  })

  it('an option and prose are sent together', async () => {
    const w = await mountTab(v01Target())
    await itemEl(w, 10).findAll('[data-test="qna-option"]')[1].trigger('click')
    await write(w, 10, '응답 시간 위주')
    await send(w, 10)
    expect(qaAnswerCall).toHaveBeenCalledWith({ docId: V01_DOC, itemId: 10, body: '응답 시간 위주', selectedOptionIds: ['o2'] })
  })

  it.each([
    ['423 AI running', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED' } }), 'AI 실행 중이라 거절되었습니다. 질문을 다시 읽었습니다.'],
    ['409 disposed', qRefusal(409, 'Modification not allowed: the group has been disposed.'), '폐기된 그룹이라 거절되었습니다. 질문을 다시 읽었습니다.'],
    ['403', qRefusal(403, 'Insufficient permissions'), '답변할 권한이 없어 거절되었습니다.'],
    ['404', qRefusal(404, 'question_item 13 does not exist'), '질문을 찾을 수 없어 거절되었습니다. 질문을 다시 읽었습니다.'],
    ['400', qRefusal(400, 'unknown option id'), '답변 내용이 올바르지 않아 거절되었습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
    ['500', axiosError(500, 'Internal Server Error'), '답변을 보내지 못했습니다.'],
  ])('%s: the reason is shown on that question, the draft stays, the questions are read again', async (_label, refusal, text) => {
    const w = await mountTab(v01Target())
    await write(w, 13, '완료 기준')
    refusals.set('answer', refusal)
    await send(w, 13)
    expect(itemEl(w, 13).find('[data-test="qna-answer-failure"]').text()).toBe(text)
    expect(itemEl(w, 10).find('[data-test="qna-answer-failure"]').exists()).toBe(false)
    expect(qaReadCall).toHaveBeenCalledTimes(2)
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(itemEl(w, 13).attributes('data-state')).toBe('waiting')
    expect((itemEl(w, 13).find('textarea').element as HTMLTextAreaElement).value).toBe('완료 기준')
  })

  it('after a 423 the guard read again finds the run: answering is disabled with its reason', async () => {
    const w = await mountTab(v01Target())
    await write(w, 13, '완료 기준')
    refusals.set('answer', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED' } }))
    running = true
    await send(w, 13)
    expect(isDisabled(submitOf(w, 13))).toBe(true)
    expect(itemEl(w, 13).find('[data-test="qna-answer-reason"]').text()).toBe('AI 실행 중이라 답변할 수 없습니다.')
  })

  it('while an answer waits for the server every answer is disabled and a second click sends nothing', async () => {
    let release: () => void = () => {}
    qaAnswerCall.mockImplementationOnce(() => new Promise((resolve) => (release = () => resolve(toQaAnswerResult({ ok: true })))))
    const w = await mountTab(v01Target())
    await write(w, 13, '완료 기준')
    await submitOf(w, 13).trigger('click')
    await flushPromises()
    expect(isDisabled(submitOf(w, 13))).toBe(true)
    expect(itemEl(w, 10).find('[data-test="qna-answer-reason"]').text()).toBe('답변을 보내는 중입니다.')
    await submitOf(w, 13).trigger('click')
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    release()
    await flushPromises()
    expect(itemEl(w, 10).find('[data-test="qna-answer-reason"]').exists()).toBe(false)
  })

  it('after a saved answer the question cannot be sent again until the read after it is back', async () => {
    const w = await mountTab(v01Target())
    let releaseRead: () => void = () => {}
    qaReadCall.mockImplementationOnce((docId: string) => new Promise((resolve) => (releaseRead = () => resolve(toQaThread(treeOf(docId))))))
    await write(w, 13, '완료 기준')
    await send(w, 13)
    // Saved on the server; the questions shown are still the read from before the answer.
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    expect(itemEl(w, 13).attributes('data-state')).toBe('waiting')
    expect(isDisabled(submitOf(w, 13))).toBe(true)
    expect(itemEl(w, 10).find('[data-test="qna-answer-reason"]').text()).toBe('답변을 보내는 중입니다.')
    await submitOf(w, 13).trigger('click')
    await flushPromises()
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    releaseRead()
    await flushPromises()
    expect(itemEl(w, 13).attributes('data-state')).toBe('answered')
    expect(itemEl(w, 13).find('[data-test="qna-answer-form"]').exists()).toBe(false)
    expect(itemEl(w, 10).find('[data-test="qna-answer-reason"]').exists()).toBe(false)
    expect(threads.get(V01_DOC)!.find((entry) => entry.id === 13)!.answers).toHaveLength(1)
  })

  it('a saved answer whose read after it fails is not sent again from the old form; other questions stay open', async () => {
    const w = await mountTab(v01Target())
    await write(w, 13, '완료 기준')
    refusals.set('read', networkError())
    await send(w, 13)
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    expect(itemEl(w, 13).attributes('data-state')).toBe('waiting')
    await write(w, 13, '완료 기준 다시')
    expect(isDisabled(submitOf(w, 13))).toBe(true)
    await submitOf(w, 13).trigger('click')
    await flushPromises()
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    await write(w, 12, '필요합니다')
    expect(isDisabled(submitOf(w, 12))).toBe(false)
    // The read again (here the retry) shows the answer.
    await w.find('[data-test="inspector-pane-retry"]').trigger('click')
    await flushPromises()
    expect(itemEl(w, 13).attributes('data-state')).toBe('answered')
    expect(threads.get(V01_DOC)!.find((entry) => entry.id === 13)!.answers).toHaveLength(1)
  })

  it('a late refusal of the previous Node is not shown on the new one', async () => {
    let reject: (error: unknown) => void = () => {}
    qaAnswerCall.mockImplementationOnce(() => new Promise((_resolve, fail) => (reject = fail)))
    const w = await mountTab(v01Target())
    await write(w, 13, '완료 기준')
    await submitOf(w, 13).trigger('click')
    await w.setProps({ target: v01Target({ docId: OTHER_DOC, nodeId: `doc:${OTHER_DOC}` }) })
    reject(qRefusal(409, 'disposed'))
    await flushPromises()
    expect(order(w)).toEqual([[20, 'waiting']])
    expect(w.find('[data-test="qna-answer-failure"]').exists()).toBe(false)
  })
})

// --- Availability ----------------------------------------------------------------------------------

describe('Q&A tab — read-only Flows and the guard (flowgate.v02.0048 T#7)', () => {
  it.each([
    ['ownership conflict', { readOnly: 'conflict' as const }, 'Flow 소유가 충돌해 읽기 전용입니다.'],
    ['Graph revision view', { readOnly: 'revision' as const }, '확정된 Graph revision 보기라 읽기 전용입니다. draft 보기에서 답변합니다.'],
  ])('%s: the questions are shown, answering is visible but disabled with the reason', async (_label, fields, reason) => {
    const w = await mountTab(v01Target(fields))
    expect(order(w)).toHaveLength(4)
    expect(isDisabled(submitOf(w, 13))).toBe(true)
    expect(isDisabled(itemEl(w, 13).find('textarea'))).toBe(true)
    expect(isDisabled(itemEl(w, 10).find('[data-test="qna-option"]'))).toBe(true)
    expect(itemEl(w, 13).find('[data-test="qna-answer-reason"]').text()).toBe(reason)
    await submitOf(w, 13).trigger('click')
    expect(qaAnswerCall).not.toHaveBeenCalled()
  })

  it('the real conflict and Graph selections read the same way', async () => {
    threads.set(targetOf(SELECTIONS.conflict).docId!, [{ id: 31, seq: 1, title: 'c', body: '?', asker_kind: 'ai', options: [], asker_provider: null, answers: [] }])
    const conflict = await mountTab(targetOf(SELECTIONS.conflict))
    expect(isDisabled(submitOf(conflict, 31))).toBe(true)
    expect(itemEl(conflict, 31).find('[data-test="qna-answer-reason"]').text()).toBe('Flow 소유가 충돌해 읽기 전용입니다.')
    conflict.unmount()
    wrapper = null
    threads.set(targetOf(SELECTIONS.graph).docId!, [{ id: 30, seq: 1, title: 'g', body: '?', asker_kind: 'ai', options: [], asker_provider: null, answers: [] }])
    const graph = await mountTab(targetOf(SELECTIONS.graph))
    // Graph draft view: a document feature, answering is open (blank prose keeps the button off).
    await write(graph, 30, 'g')
    expect(isDisabled(submitOf(graph, 30))).toBe(false)
    await graph.setProps({ target: { ...targetOf(SELECTIONS.graph), readOnly: 'revision' } })
    expect(itemEl(graph, 30).find('[data-test="qna-answer-reason"]').text()).toContain('읽기 전용')
  })

  it('an AI run in the Flow disables answering from the start', async () => {
    running = true
    const w = await mountTab(v01Target())
    expect(activityRead).toHaveBeenCalledWith(V01_FLOW)
    expect(isDisabled(submitOf(w, 13))).toBe(true)
    expect(itemEl(w, 13).find('[data-test="qna-answer-reason"]').text()).toBe('AI 실행 중이라 답변할 수 없습니다.')
  })

  it('a disposed group disables answering', async () => {
    disposed = true
    const w = await mountTab(v01Target())
    expect(itemEl(w, 13).find('[data-test="qna-answer-reason"]').text()).toBe('폐기된 그룹이라 답변할 수 없습니다.')
  })

  it('a failed guard read does not block (the server decides)', async () => {
    activityRead.mockRejectedValue(networkError())
    const w = await mountTab(v01Target())
    await write(w, 13, 'x')
    expect(isDisabled(submitOf(w, 13))).toBe(false)
    expect(itemEl(w, 13).find('[data-test="qna-answer-reason"]').exists()).toBe(false)
  })
})

// --- Events ----------------------------------------------------------------------------------------

describe('Q&A tab — server events (flowgate.v02.0048 T#7)', () => {
  it('subscribes to the Q&A events and the guard events only', async () => {
    await mountTab(v01Target())
    const [connection] = stream.connections()
    for (const event of [...QNA_EVENTS, ...QNA_GUARD_EVENTS]) expect(connection.listened(event), event).toBeGreaterThan(0)
    expect(connection.listened('test_run_started')).toBe(0)
    expect(connection.listened('doc_review_status_changed')).toBe(0)
  })

  it('qna_q_registered of the document reads again and the new question appears; another document does not', async () => {
    vi.useFakeTimers()
    const w = await mountTab(v01Target())
    stream.open()
    threads.get(OTHER_DOC)!.push({ id: 21, seq: 2, title: 'x', body: 'x', asker_kind: 'ai', options: [], asker_provider: null, answers: [] })
    stream.emit('qna_q_registered', { docId: OTHER_DOC, payload: { doc_id: OTHER_DOC, titles: ['x'] } })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(qaReadCall).toHaveBeenCalledTimes(1)
    threads.get(V01_DOC)!.push({ id: 14, seq: 5, title: '새 질문', body: '새로 묻습니다.', asker_kind: 'ai', options: [], asker_provider: null, answers: [] })
    stream.emit('qna_q_registered', { docId: V01_DOC, payload: { doc_id: V01_DOC, project_id: 'flowgate', titles: ['새 질문'] } })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(qaReadCall).toHaveBeenCalledTimes(2)
    expect(itemEl(w, 14).attributes('data-state')).toBe('waiting')
  })

  it('qna_answer_registered (another tab answered) reads again; the question becomes answered', async () => {
    vi.useFakeTimers()
    const w = await mountTab(v01Target())
    stream.open()
    threads.get(V01_DOC)!.find((entry) => entry.id === 13)!.answers.push({ id: 50, body: '다른 탭 답변', author_kind: 'ai', selected_options: [], author_provider: { ai_run_id: 'r9', ai_provider_name: 'Codex' } })
    stream.emit('qna_answer_registered', { docId: V01_DOC, payload: { doc_id: V01_DOC, item_id: 13, unanswered_count: 1 } })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(qaReadCall).toHaveBeenCalledTimes(2)
    expect(itemEl(w, 13).attributes('data-state')).toBe('answered')
    expect(itemEl(w, 13).find('[data-test="qna-answer-side"]').text()).toBe('답변: AI · Codex')
  })

  it('a Q&A frame without doc_id follows the default rule: the target Flow reads again, another Flow does not', async () => {
    vi.useFakeTimers()
    await mountTab(v01Target())
    stream.open()
    stream.emit('qna_q_registered', { groupId: 'flowgate.v02.0049' })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(qaReadCall).toHaveBeenCalledTimes(1)
    stream.emit('qna_q_registered', { groupId: V01_FLOW })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(qaReadCall).toHaveBeenCalledTimes(2)
  })

  it('ai_invoke_started / finished of the Flow read the guard again: disabled, then enabled', async () => {
    vi.useFakeTimers()
    const w = await mountTab(v01Target())
    stream.open()
    running = true
    stream.emit('ai_invoke_started', { groupId: V01_FLOW, docId: OTHER_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(itemEl(w, 13).find('[data-test="qna-answer-reason"]').text()).toBe('AI 실행 중이라 답변할 수 없습니다.')
    expect(qaReadCall).toHaveBeenCalledTimes(1)
    running = false
    stream.emit('ai_invoke_finished', { groupId: V01_FLOW, docId: OTHER_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(itemEl(w, 13).find('[data-test="qna-answer-reason"]').exists()).toBe(false)
    const before = activityRead.mock.calls.length
    stream.emit('ai_invoke_started', { groupId: 'flowgate.v02.0049', docId: 'flowgate.v02.0049.0007-TS' })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(activityRead).toHaveBeenCalledTimes(before)
  })

  it('a reconnect reads the questions again', async () => {
    vi.useFakeTimers()
    await mountTab(v01Target())
    stream.open()
    stream.reconnect()
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    expect(qaReadCall).toHaveBeenCalledTimes(2)
  })
})

// --- Node change -----------------------------------------------------------------------------------

describe('Q&A tab — Node change (flowgate.v02.0048 T#7)', () => {
  it('another Node reads its own document; the previous answer of a slow read never replaces it', async () => {
    let releaseFirst: () => void = () => {}
    qaReadCall.mockImplementationOnce(
      (docId: string) => new Promise((resolve) => (releaseFirst = () => resolve(toQaThread(treeOf(docId))))),
    )
    const w = await mountTab(v01Target())
    await w.setProps({ target: v01Target({ docId: OTHER_DOC, nodeId: `doc:${OTHER_DOC}` }) })
    await flushPromises()
    expect(order(w)).toEqual([[20, 'waiting']])
    releaseFirst()
    await flushPromises()
    expect(order(w)).toEqual([[20, 'waiting']])
  })

  it('unsent drafts belong to their document', async () => {
    const w = await mountTab(v01Target())
    await write(w, 13, '초안')
    await w.setProps({ target: v01Target({ docId: OTHER_DOC, nodeId: `doc:${OTHER_DOC}` }) })
    await flushPromises()
    await w.setProps({ target: v01Target() })
    await flushPromises()
    expect((itemEl(w, 13).find('textarea').element as HTMLTextAreaElement).value).toBe('')
  })
})

// --- In the Frame ----------------------------------------------------------------------------------

describe('Q&A tab in the Frame (real registry)', () => {
  async function mountFrame(selection: FlowGraphSelection) {
    wrapper = mount(DesignerInspector, { props: { selection }, global: { plugins: [i18n, pinia] } })
    await flushPromises()
    return wrapper
  }

  const tabLabels = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((tab) => tab.text())
  const openTab = async (w: VueWrapper<any>, label: string) => {
    await w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((tab) => tab.text().startsWith(label))!.trigger('click')
    await flushPromises()
  }

  it('the Q&A tab carries the number of questions waiting for the user (AI asked, unanswered)', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    expect(tabLabels(w)).toContain('Q&A (2)')
    await openTab(w, 'Q&A')
    expect(w.find('[data-test="inspector-qna"]').exists()).toBe(true)
    expect(order(w).map(([id]) => id)).toEqual([10, 13, 12, 11])
  })

  it('no waiting question: the tab shows no number', async () => {
    threads.set(V01_DOC, [{ id: 12, seq: 1, title: 'h', body: '?', asker_kind: 'human', options: [], asker_provider: null, answers: [] }])
    const w = await mountFrame(SELECTIONS.v01)
    expect(tabLabels(w)).toContain('Q&A')
    expect(tabLabels(w).some((label) => /^Q&A \(/.test(label))).toBe(false)
  })

  it('answering in the tab lowers the number; a qna event raises it again', async () => {
    vi.useFakeTimers()
    const w = await mountFrame(SELECTIONS.v01)
    stream.open()
    await openTab(w, 'Q&A')
    await write(w, 13, '완료 기준')
    await send(w, 13)
    // The answer posts qna_answer_registered to the user's tabs; the number reads again on it.
    stream.emit('qna_answer_registered', { docId: V01_DOC, payload: { doc_id: V01_DOC, item_id: 13, unanswered_count: 2 } })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(tabLabels(w)).toContain('Q&A (1)')
    threads.get(V01_DOC)!.push({ id: 15, seq: 6, title: 'n', body: 'n', asker_kind: 'ai', options: [], asker_provider: null, answers: [] })
    stream.emit('qna_q_registered', { docId: V01_DOC, payload: { doc_id: V01_DOC } })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(tabLabels(w)).toContain('Q&A (2)')
  })

  it('the number follows the selected Node’s document (an instruction and its report are separate Nodes)', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    expect(tabLabels(w)).toContain('Q&A (2)')
    const other = selectionOf(V01_GRAPH, V01_TASK_NODE.id)
    await w.setProps({ selection: { ...other, node: { ...other.node, id: `doc:${OTHER_DOC}`, document: { ...other.node.document!, docId: OTHER_DOC } } } })
    await flushPromises()
    expect(qaReadCall).toHaveBeenCalledWith(OTHER_DOC)
    expect(tabLabels(w)).toContain('Q&A (1)')
  })

  it('a failed count read shows the tab without a number', async () => {
    qaReadCall.mockRejectedValue(networkError())
    const w = await mountFrame(SELECTIONS.v01)
    expect(tabLabels(w)).toContain('Q&A')
  })

  it('a Node without a document shows an empty Q&A tab and reads nothing', async () => {
    const w = await mountFrame(SELECTIONS.v01NoDocument)
    expect(tabLabels(w)).toContain('Q&A')
    await openTab(w, 'Q&A')
    expect(w.get('[data-test="designer-inspector-tab-state"]').attributes('data-state')).toBe('empty')
    expect(qaReadCall).not.toHaveBeenCalled()
  })

  it.each(['graph', 'unassigned', 'conflict'] as const)('the %s Flow shows the Q&A tab too (a document feature)', async (name) => {
    const w = await mountFrame(SELECTIONS[name])
    expect(tabLabels(w).some((label) => label.startsWith('Q&A'))).toBe(true)
  })

  it('no team handling, resume or AI answer request appears', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    await openTab(w, 'Q&A')
    const text = w.text()
    for (const word of ['팀 내 처리', '재개', 'AI 답변 요청', '멘트 복사', '위임']) expect(text).not.toContain(word)
  })
})
