/**
 * flowgate.v02.0053 T#6 — a person asks on the Q&A tab (0053 CH §2), with `@/features/document` mocked as a
 * small server that also starts the AI run and refuses.
 * flowgate.v02.0056 F1 (0056 CH §3) — registering a question and asking an AI are two steps:
 *
 * - the question line (field + [+]) is at the top of the tab, always; a document without questions shows
 *   only the line, no sentence; [+] is an icon whose name / tooltip is 질문 등록, closed while blank;
 * - [+] or Enter registers the question (asker 'human', trimmed) and reads the questions and the guard
 *   again — no AI is asked by itself; the line clears; Enter ending an IME composition does not send;
 * - every open question has its AI line: a picker of the project's AIs (the project default picked first)
 *   and [AI 답변 요청]; the request names the picked AI, the button shows it on its way, a running chip says
 *   the answer is awaited until it arrives (`qna_answer_registered`) or the run ends; a refusal is an amber
 *   ! icon beside the button whose tooltip says why;
 * - a refused question keeps the text and shows a red ! icon on the line whose tooltip says why;
 * - asking and requesting close while something is on its way, on a read-only Flow, a disposed group or an
 *   AI run, with the reason as the tooltip; the AI-asks / person-answers flow stays as it was;
 * - in the real Frame, a card just placed registers a question and gets the AI answer on request.
 * flowgate.v02.0070 T#5 (B5) — the AI line (and the answer form) is in the question's ↗ window only, offered while the
 * document's card line says it has a body: the targets carry such a line (`bodyLine`); the list keeps 열기.
 */
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphCardDocument, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import QnaTab from '@/features/designer/inspector/features/qna/QnaTab.vue'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { SELECTIONS, V01_GRAPH, V01_TASK_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

// The calls are mocked; normalization and error reading stay real.
const qaReadCall = vi.fn()
const qaAnswerCall = vi.fn()
const qaAskCall = vi.fn()
const qaAiCall = vi.fn()
const activityRead = vi.fn()
const documentRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
const attachmentsReadCall = vi.fn()
const settingsNodeRead = vi.fn()
const choicesRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    qaRead: (...args: unknown[]) => qaReadCall(...args),
    qaAnswer: (...args: unknown[]) => qaAnswerCall(...args),
    qaAsk: (...args: unknown[]) => qaAskCall(...args),
    qaRequestAiAnswer: (...args: unknown[]) => qaAiCall(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    reviewDocumentRead: (...args: unknown[]) => documentRead(...args),
    settingsChoicesRead: (...args: unknown[]) => choicesRead(...args),
    // The other parts of the Frame in the placed-card case.
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    attachmentsRead: (...args: unknown[]) => attachmentsReadCall(...args),
    settingsNodeRead: (...args: unknown[]) => settingsNodeRead(...args),
  }
})

// Anything else the Frame reads answers nothing.
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  const offline = () => Promise.reject(Object.assign(new Error('offline'), { request: {} }))
  return { ...actual, getRequest: vi.fn(offline), postRequest: vi.fn(offline), putRequest: vi.fn(offline), patchRequest: vi.fn(offline) }
})

const { toQaThread, toQaAnswerResult, toQaAskResult, toQaAiRequestResult, toReviewActivity, toReviewDocument, toDocumentDetail, toAttachmentsList, toSettingsChoices } =
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
let itemSeq = 30
/** Questions an AI run was started for: the run answers them when the test lets it. */
const pendingAi: { docId: string; itemId: number; providerId: string | null }[] = []
const refusals = new Map<string, unknown>()
/** Every call to the server, in order. */
const calls: string[] = []

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error' })
const qRefusal = (status: number, message: string) => axiosError(status, { ok: false, http_status: status, error_message: message })
const admission = (code: string) => axiosError(409, { ok: false, http_status: 409, error_message: 'm', code, message: 'm' })
const locked = () => axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'm' } })

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
    { id: 10, seq: 1, title: '조사 범위 확인', body: '어느 범위를 조사할까요?', asker_kind: 'ai', options: [{ id: 'o1', label: '코드 품질' }], asker_provider: null, answers: [] },
  ])
  threads.set(OTHER_DOC, [])
}

/** The AI run answers every question it was started for and ends. */
function aiAnswers(body = 'AI 답입니다.') {
  for (const { docId, itemId, providerId } of pendingAi.splice(0)) {
    const entry = threads.get(docId)!.find((candidate) => candidate.id === itemId)!
    entry.answers.push({
      id: 900 + itemId,
      body,
      author_kind: 'ai',
      author_id: null,
      selected_options: [],
      author_provider: { ai_run_id: 'aiv_9', ai_provider_id: providerId ?? 'p1', ai_provider_name: providerId === 'p2' ? 'Codex' : 'Claude Opus 5.5' },
    })
  }
  running = false
}

function serve() {
  qaReadCall.mockImplementation(async (docId: string) => {
    calls.push(`read ${docId}`)
    const refusal = refusalOf('read')
    if (refusal) throw refusal
    return toQaThread(treeOf(docId))
  })
  qaAnswerCall.mockImplementation(async (request: { docId: string; itemId: number; body: string; selectedOptionIds: string[] }) => {
    calls.push(`answer ${request.itemId}`)
    const entry = threads.get(request.docId)!.find((candidate) => candidate.id === request.itemId)!
    entry.answers.push({ id: 500, body: request.body, author_kind: 'human', author_id: 'u1', selected_options: request.selectedOptionIds, author_provider: null })
    return toQaAnswerResult({ ok: true, doc_id: request.docId, item_id: request.itemId, answer_id: 500, author_kind: 'human', status: 'pending' })
  })
  qaAskCall.mockImplementation(async (request: { docId: string; body: string }) => {
    calls.push(`ask ${request.body}`)
    const refusal = refusalOf('ask') ?? (disposed ? qRefusal(409, 'disposed') : running ? locked() : null)
    if (refusal) throw refusal
    const list = threads.get(request.docId) ?? []
    const id = ++itemSeq
    list.push({ id, seq: list.length + 1, title: null, body: request.body, asker_kind: 'human', options: [], asker_provider: null, answers: [] })
    threads.set(request.docId, list)
    return toQaAskResult({ ok: true, doc_id: request.docId, added_item_ids: [id] })
  })
  qaAiCall.mockImplementation(async (docId: string, itemId: number, providerId: string | null = null) => {
    calls.push(`ai ${itemId} ${providerId ?? '-'}`)
    const refusal = refusalOf('ai') ?? (running ? admission('run_in_progress') : null)
    if (refusal) throw refusal
    pendingAi.push({ docId, itemId, providerId })
    running = true
    return toQaAiRequestResult({ ok: true, doc_id: docId, item_id: itemId, run_id: 'aiv_9', status: 'running' })
  })
  activityRead.mockImplementation(async (groupId: string) => {
    calls.push('guard')
    return toReviewActivity(running ? { active: true, run_id: 'aiv_9', status: 'running', group_id: groupId } : { active: false, group_id: groupId })
  })
  documentRead.mockImplementation(async (docId: string) => toReviewDocument(rowOf(docId)))
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(rowOf(docId)))
  relationsRead.mockImplementation(async () => [])
  attachmentsReadCall.mockImplementation(async (docId: string) => toAttachmentsList({ data: { doc_id: docId, attachments: [], count: 0 } }))
  settingsNodeRead.mockResolvedValue({ cardKind: 'run', locked: false, fields: { instruction: { value: 'use', stored: null, default: 'use', choices: ['use', 'omit'] } } })
  choicesRead.mockImplementation(async (projectId: string) => {
    calls.push(`choices ${projectId}`)
    const refusal = refusalOf('choices')
    if (refusal) throw refusal
    return toSettingsChoices({
      providers: [
        { id: 'p1', name: 'Claude Opus 5.5' },
        { id: 'p2', name: 'Codex' },
      ],
      default_provider_id: 'p1',
    })
  })
}

const rowOf = (docId: string) => ({
  doc_id: docId,
  group_id: docId.split('.').slice(0, 3).join('.'),
  type_code: 'T',
  title: docId,
  status: 'open',
  doc_review_status: null,
  group_disposed: disposed,
})

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
  pendingAi.length = 0
  calls.length = 0
  running = false
  disposed = false
  itemSeq = 30
  for (const mock of [qaReadCall, qaAnswerCall, qaAskCall, qaAiCall, activityRead, documentRead, detailRead, relationsRead, attachmentsReadCall, settingsNodeRead, choicesRead]) mock.mockReset()
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

async function mountTab(target: InspectorTarget) {
  wrapper = mount(QnaTab, { props: { target }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

/** flowgate.v02.0070 T#5: the card line of a document whose body is there (answering and asking an AI need one). */
const bodyLine = (docId: string): FlowGraphCardDocument => ({
  slot: 'report',
  docType: 'TR',
  author: 'run',
  docId,
  title: docId,
  status: 'open',
  docReviewStatus: null,
  hasBody: true,
  bodyState: null,
})

/** The v0.1 target with its document's card line (a body to check), unless `fields` give the lines. */
const v01Target = (fields: Partial<InspectorTarget> = {}): InspectorTarget => {
  const docId = fields.docId === undefined ? V01_DOC : fields.docId
  return { ...targetOf(SELECTIONS.v01), cardDocuments: docId ? [bodyLine(docId)] : [], ...fields }
}
const emptyTarget = () => v01Target({ docId: OTHER_DOC, docType: 'TR', nodeId: `doc:${OTHER_DOC}` })

const askInput = (w: VueWrapper<any>) => w.get('input[data-test="qna-ask-input"]')
const askButton = (w: VueWrapper<any>) => w.get('[data-test="qna-ask-submit"]')
const isDisabled = (el: { attributes(name: string): string | undefined }) => el.attributes('disabled') !== undefined
const itemEl = (w: VueWrapper<any>, id: number) => w.find(`[data-test="qna-item"][data-item="${id}"]`)
const ids = (w: VueWrapper<any>) => w.findAll('[data-test="qna-item"]').map((el) => Number(el.attributes('data-item')))
const askFailure = (w: VueWrapper<any>) => w.find('[data-test="qna-ask-failure"]')
const inputValue = (w: VueWrapper<any>) => (askInput(w).element as HTMLInputElement).value
// flowgate.v02.0070 T#5: the AI line is in the question's ↗ window (teleported to the body); open it first.
const windowOf = (id: number) => document.body.querySelector<HTMLElement>(`[data-test="qna-window"][data-item="${id}"]`)
const inWindow = (id: number) => new DOMWrapper(windowOf(id) ?? document.createElement('div'))
const aiButton = (_w: VueWrapper<any>, id: number) => inWindow(id).get('[data-test="qna-window-ai-request"]')
const aiPicker = (_w: VueWrapper<any>, id: number) => inWindow(id).get('select[data-test="qna-window-ai-provider"]')
const aiFailure = (_w: VueWrapper<any>, id: number) => inWindow(id).find('[data-test="qna-window-ai-failure"]')
const aiWaiting = (_w: VueWrapper<any>, id: number) => inWindow(id).find('[data-test="qna-window-ai-waiting"]')

/** Opens the question's window with its 열기 button (a window already open stays). */
async function open(w: VueWrapper<any>, id: number) {
  if (!windowOf(id)) {
    await itemEl(w, id).get('[data-test="qna-open"]').trigger('click')
    await flushPromises()
  }
  if (!windowOf(id)) throw new Error(`window ${id} not opened`)
}

/** A refusal icon: its name, its description (the tooltip) and the text it draws. */
function iconView(el: { attributes(name: string): string | undefined; text(): string }) {
  return { role: el.attributes('role'), focusable: el.attributes('tabindex'), name: el.attributes('aria-label'), description: el.attributes('title'), text: el.text() }
}

async function ask(w: VueWrapper<any>, text: string) {
  await askInput(w).setValue(text)
  await askButton(w).trigger('click')
  await flushPromises()
}

async function requestAi(w: VueWrapper<any>, id: number) {
  await open(w, id)
  await aiButton(w, id).trigger('click')
  await flushPromises()
}

/** The AI run answers; the server tells the tab (`qna_answer_registered`, then the run's end). */
async function aiAnswerArrives(docId: string, body?: string) {
  aiAnswers(body)
  stream.emit('qna_answer_registered', { docId, payload: { doc_id: docId } })
  stream.emit('ai_invoke_finished', { groupId: docId.split('.').slice(0, 3).join('.'), docId })
  await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
  await flushPromises()
}

// --- The question line -------------------------------------------------------------------------------

describe('the question line (flowgate.v02.0053 T#6)', () => {
  it('sits at the top of the tab, above the questions: a field and the [+] icon named 질문 등록', async () => {
    const w = await mountTab(v01Target())
    const root = w.get('[data-test="inspector-qna"]').element
    expect(root.firstElementChild?.getAttribute('data-test')).toBe('qna-ask')
    expect(askInput(w).attributes('placeholder')).toBe('질문 입력')
    expect(askInput(w).attributes('aria-label')).toBe('질문 등록')
    const plus = askButton(w)
    expect([plus.attributes('data-icon'), plus.attributes('aria-label'), plus.attributes('title'), plus.text()]).toEqual(['add', '질문 등록', '질문 등록', ''])
    expect(ids(w)).toEqual([10])
  })

  it('a document without questions shows only the line — no "질문 없음" or "연결된 문서 없음" sentence', async () => {
    const w = await mountTab(emptyTarget())
    expect(w.find('[data-test="qna-ask"]').exists()).toBe(true)
    expect(w.find('[data-test="inspector-pane-state"]').exists()).toBe(false)
    expect(w.text()).toBe('')
    for (const word of ['질문 없음', '질문이 없습니다', '연결된 문서']) expect(w.html()).not.toContain(word)
  })

  it('[+] is closed while the text is blank', async () => {
    const w = await mountTab(v01Target())
    expect(isDisabled(askButton(w))).toBe(true)
    await askInput(w).setValue('   ')
    expect(isDisabled(askButton(w))).toBe(true)
    await askInput(w).setValue('질문')
    expect(isDisabled(askButton(w))).toBe(false)
  })
})

// --- Asking --------------------------------------------------------------------------------------------

describe('asking: register the question only (flowgate.v02.0056 F1)', () => {
  it('[+] registers the trimmed question as a person, then reads again — no AI is asked', async () => {
    const w = await mountTab(emptyTarget())
    calls.length = 0
    await ask(w, '  완료 기준은 무엇인가요?  ')
    expect(qaAskCall).toHaveBeenCalledWith({ docId: OTHER_DOC, body: '완료 기준은 무엇인가요?' })
    expect(qaAiCall).not.toHaveBeenCalled()
    // The question first, then the questions and the guard read again.
    expect(calls[0]).toBe('ask 완료 기준은 무엇인가요?')
    expect(calls.slice(1).sort()).toEqual(['guard', `read ${OTHER_DOC}`])
    expect(ids(w)).toEqual([31])
    expect(itemEl(w, 31).attributes('data-state')).toBe('open')
    expect(itemEl(w, 31).get('[data-test="qna-item-title"]').text()).toBe('완료 기준은 무엇인가요?')
    expect(itemEl(w, 31).get('[data-test="qna-item-asker"]').attributes('data-side')).toBe('human')
    expect(inputValue(w)).toBe('')
    expect(askFailure(w).exists()).toBe(false)
    // The registered question offers its AI line (in its window) and waits for nobody yet.
    await open(w, 31)
    expect(aiButton(w, 31).exists()).toBe(true)
    expect(aiWaiting(w, 31).exists()).toBe(false)
    expect(isDisabled(askInput(w))).toBe(false)
  })

  it('Enter sends the question like [+]; Enter that ends an IME composition does not', async () => {
    const w = await mountTab(emptyTarget())
    await askInput(w).setValue('한글 질문')
    await askInput(w).trigger('keydown', { key: 'Enter', isComposing: true })
    await askInput(w).trigger('keydown', { key: 'Enter', keyCode: 229 })
    await flushPromises()
    expect(qaAskCall).not.toHaveBeenCalled()
    await askInput(w).trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(qaAskCall).toHaveBeenCalledTimes(1)
    expect(qaAskCall).toHaveBeenCalledWith({ docId: OTHER_DOC, body: '한글 질문' })
    expect(qaAiCall).not.toHaveBeenCalled()
  })

  it('Enter on a blank line sends nothing', async () => {
    const w = await mountTab(emptyTarget())
    await askInput(w).trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(qaAskCall).not.toHaveBeenCalled()
  })

  it('while the question is on its way the line is closed with the reason as the tooltip; a second click sends nothing', async () => {
    let release: () => void = () => {}
    qaAskCall.mockImplementationOnce(
      (request: { docId: string; body: string }) =>
        new Promise((resolve) => {
          release = () => {
            threads.set(request.docId, [{ id: 31, seq: 1, title: null, body: request.body, asker_kind: 'human', options: [], asker_provider: null, answers: [] }])
            resolve(toQaAskResult({ ok: true, doc_id: request.docId, added_item_ids: [31] }))
          }
        }),
    )
    const w = await mountTab(emptyTarget())
    await askInput(w).setValue('질문')
    await askButton(w).trigger('click')
    await flushPromises()
    expect(isDisabled(askButton(w))).toBe(true)
    expect(isDisabled(askInput(w))).toBe(true)
    expect(askButton(w).attributes('title')).toBe('질문을 보내는 중입니다.')
    expect(askButton(w).attributes('data-busy')).toBe('true')
    await askButton(w).trigger('click')
    expect(qaAskCall).toHaveBeenCalledTimes(1)
    release()
    await flushPromises()
    expect(ids(w)).toEqual([31])
  })

  it('the AI-asks / person-answers flow stays: an AI question is answered by the user as before', async () => {
    const w = await mountTab(v01Target())
    // flowgate.v02.0070 T#5: answered in the question's window.
    expect(itemEl(w, 10).find('[data-test="qna-answer-form"]').exists()).toBe(false)
    await open(w, 10)
    expect(windowOf(10)!.querySelector('[data-test="qna-window-form"]')).not.toBeNull()
    await inWindow(10).get('textarea[data-test="qna-window-body-input"]').setValue('코드 품질만')
    await inWindow(10).get('[data-test="qna-window-submit"]').trigger('click')
    await flushPromises()
    expect(qaAnswerCall).toHaveBeenCalledWith({ docId: V01_DOC, itemId: 10, body: '코드 품질만', selectedOptionIds: [] })
    expect(qaAskCall).not.toHaveBeenCalled()
    expect(qaAiCall).not.toHaveBeenCalled()
    expect(itemEl(w, 10).attributes('data-state')).toBe('answered')
    // An answered question has no AI line.
    expect(windowOf(10)!.querySelector('[data-test="qna-window-ai"]')).toBeNull()
  })
})

// --- Asking an AI --------------------------------------------------------------------------------------

describe('the AI line of an open question: pick an AI, ask it, see its answer (flowgate.v02.0056 F1)', () => {
  it('every open question has the picker (the project AIs, the default first) and [AI 답변 요청]', async () => {
    const w = await mountTab(v01Target())
    expect(choicesRead).toHaveBeenCalledWith('flowgate')
    await open(w, 10)
    const picker = aiPicker(w, 10)
    expect(picker.findAll('option').map((option) => [option.attributes('value'), option.text()])).toEqual([
      ['', '쓸 수 있는 AI 없음'],
      ['p1', 'Claude Opus 5.5'],
      ['p2', 'Codex'],
    ])
    expect((picker.element as HTMLSelectElement).value).toBe('p1')
    expect(picker.attributes('aria-label')).toBe('답변할 AI')
    expect(aiButton(w, 10).text()).toBe('AI 답변 요청')
    expect(isDisabled(aiButton(w, 10))).toBe(false)
    expect(aiButton(w, 10).attributes('title')).toBeUndefined()
  })

  it('[AI 답변 요청] asks the picked AI for that question, then reads again; the row waits for the answer', async () => {
    vi.useFakeTimers()
    const w = await mountTab(emptyTarget())
    stream.open()
    await ask(w, '완료 기준은?')
    await open(w, 31)
    await aiPicker(w, 31).setValue('p2')
    calls.length = 0
    await requestAi(w, 31)
    expect(qaAiCall).toHaveBeenCalledWith(OTHER_DOC, 31, 'p2')
    expect(calls[0]).toBe('ai 31 p2')
    expect(calls.slice(1).sort()).toEqual(['guard', `read ${OTHER_DOC}`])
    expect(itemEl(w, 31).attributes('data-state')).toBe('open')
    expect(itemEl(w, 31).attributes('data-awaiting')).toBe('true')
    expect(aiWaiting(w, 31).text()).toBe('AI 답변 대기 중')
    // The run owns the Flow: asking an AI again (and asking) waits for it.
    expect(isDisabled(aiButton(w, 31))).toBe(true)
    expect(aiButton(w, 31).attributes('title')).toBe('AI 실행 중이라 AI 답변을 요청할 수 없습니다.')
    await aiAnswerArrives(OTHER_DOC, '테스트 통과가 완료 기준입니다.')
    const item = itemEl(w, 31)
    expect(item.attributes('data-state')).toBe('answered')
    expect(item.attributes('data-awaiting')).toBe('false')
    // flowgate.v02.0070 T#5: the answer is read in the window.
    const answer = inWindow(31).get('[data-test="qna-window-answer"]')
    expect(answer.get('.inspector-qna-text').text()).toBe('테스트 통과가 완료 기준입니다.')
    expect(answer.attributes('data-side')).toBe('ai')
    expect(inWindow(31).get('[data-test="qna-window-answer-side"]').text()).toBe('답: AI · Codex')
    expect(windowOf(31)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
  })

  it('without a pick the project default AI is asked', async () => {
    const w = await mountTab(v01Target())
    await requestAi(w, 10)
    expect(qaAiCall).toHaveBeenCalledWith(V01_DOC, 10, 'p1')
  })

  it('the run ending without an answer takes the waiting chip away', async () => {
    vi.useFakeTimers()
    const w = await mountTab(v01Target())
    stream.open()
    await requestAi(w, 10)
    expect(aiWaiting(w, 10).exists()).toBe(true)
    pendingAi.length = 0
    running = false
    stream.emit('ai_invoke_finished', { groupId: V01_FLOW, docId: V01_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(aiWaiting(w, 10).exists()).toBe(false)
    // Question 10 was asked by an AI: it waits for the user before and after — the request moves nothing else.
    expect(itemEl(w, 10).attributes('data-state')).toBe('waiting')
    expect(isDisabled(aiButton(w, 10))).toBe(false)
  })

  it('while the request is on its way the button shows it and a second press sends nothing', async () => {
    let release: () => void = () => {}
    qaAiCall.mockImplementationOnce(
      (docId: string, itemId: number) =>
        new Promise((resolve) => {
          release = () => {
            pendingAi.push({ docId, itemId, providerId: 'p1' })
            running = true
            resolve(toQaAiRequestResult({ ok: true, doc_id: docId, item_id: itemId, run_id: 'aiv_9', status: 'running' }))
          }
        }),
    )
    const w = await mountTab(v01Target())
    await open(w, 10)
    await aiButton(w, 10).trigger('click')
    await flushPromises()
    expect(isDisabled(aiButton(w, 10))).toBe(true)
    expect(aiButton(w, 10).attributes('title')).toBe('AI 답변을 요청하는 중입니다.')
    await aiButton(w, 10).trigger('click')
    expect(qaAiCall).toHaveBeenCalledTimes(1)
    release()
    await flushPromises()
    expect(aiWaiting(w, 10).exists()).toBe(true)
  })

  it.each([
    ['409 no_enabled_provider', admission('no_enabled_provider'), '쓸 수 있는 AI가 없어 AI 답변을 요청하지 못했습니다.'],
    ['409 run_in_progress', admission('run_in_progress'), '다른 AI 실행 중이라 AI 답변 요청이 거절되었습니다.'],
    ['409 disposed', qRefusal(409, 'Modification not allowed: the group has been disposed.'), '폐기된 그룹이라 AI 답변 요청이 거절되었습니다.'],
    ['403', qRefusal(403, 'perm_document_create required'), 'AI 답변을 요청할 권한이 없어 거절되었습니다.'],
    ['404', qRefusal(404, 'question_item does not belong'), '질문을 찾을 수 없어 AI 답변 요청이 거절되었습니다.'],
    ['network', networkError(), '서버에 연결하지 못해 AI 답변을 요청하지 못했습니다.'],
    ['409 another code', admission('worktree_unavailable'), 'AI 답변을 요청하지 못했습니다.'],
  ])('a refused request (%s): the question stays as it was, an amber icon beside the button tells why in its tooltip', async (_label, refusal, text) => {
    refusals.set('ai', refusal)
    const w = await mountTab(v01Target())
    await requestAi(w, 10)
    // The question stays as it was (asked by an AI, waiting for the user) with no answer and nothing awaited.
    expect(itemEl(w, 10).attributes('data-state')).toBe('waiting')
    expect(itemEl(w, 10).attributes('data-awaiting')).toBe('false')
    expect(aiWaiting(w, 10).exists()).toBe(false)
    expect(iconView(aiFailure(w, 10))).toEqual({ role: 'img', focusable: '0', name: 'AI 답변 요청 실패', description: text, text: '' })
    expect(w.get('[data-test="inspector-qna"]').text()).not.toContain(text)
    // Read again after the refusal as well; the button opens again.
    expect(qaReadCall).toHaveBeenCalledTimes(2)
    expect(isDisabled(aiButton(w, 10))).toBe(false)
  })

  it('the next request clears the last refusal', async () => {
    refusals.set('ai', networkError())
    const w = await mountTab(v01Target())
    await requestAi(w, 10)
    expect(aiFailure(w, 10).exists()).toBe(true)
    await requestAi(w, 10)
    expect(aiFailure(w, 10).exists()).toBe(false)
    expect(aiWaiting(w, 10).exists()).toBe(true)
  })

  it('a project without an AI: the picker is empty and closed, the button closed with the reason', async () => {
    choicesRead.mockImplementation(async () => toSettingsChoices({ providers: [], default_provider_id: null }))
    const w = await mountTab(v01Target())
    await open(w, 10)
    const picker = aiPicker(w, 10)
    expect(isDisabled(picker)).toBe(true)
    expect(picker.findAll('option').map((option) => option.text())).toEqual(['쓸 수 있는 AI 없음'])
    expect(isDisabled(aiButton(w, 10))).toBe(true)
    expect(aiButton(w, 10).attributes('title')).toBe('쓸 수 있는 AI가 없습니다.')
    await aiButton(w, 10).trigger('click')
    expect(qaAiCall).not.toHaveBeenCalled()
  })

  // flowgate.v02.0070 T#5: the AI line is the window's only; the list line keeps its waiting mark.
  it('the ↗ window carries the AI line and the waiting chip; the list line has no AI line and says it waits', async () => {
    vi.useFakeTimers()
    const w = await mountTab(v01Target())
    stream.open()
    expect(itemEl(w, 10).find('[data-test="qna-ai"]').exists()).toBe(false)
    expect(itemEl(w, 10).find('[data-test="qna-ai-request"]').exists()).toBe(false)
    await itemEl(w, 10).get('[data-test="qna-open"]').trigger('click')
    await flushPromises()
    const win = document.body.querySelector('[data-test="qna-window"][data-item="10"]')!
    expect(win.querySelector('[data-test="qna-window-ai-request"]')?.textContent).toBe('AI 답변 요청')
    ;(win.querySelector('[data-test="qna-window-ai-request"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(qaAiCall).toHaveBeenCalledWith(V01_DOC, 10, 'p1')
    expect(win.querySelector('[data-test="qna-window-ai-waiting"]')?.textContent).toBe('AI 답변 대기 중')
    expect(itemEl(w, 10).attributes('data-awaiting')).toBe('true')
  })

  it('a document whose body cannot be checked offers no AI line: the window says why in one line', async () => {
    const w = await mountTab(v01Target({ cardDocuments: [{ ...bodyLine(V01_DOC), hasBody: false }] }))
    await open(w, 10)
    expect(windowOf(10)!.querySelector('[data-test="qna-window-ai"]')).toBeNull()
    expect(windowOf(10)!.querySelector('[data-test="qna-window-blocked"]')?.textContent).toBe('본문을 확인할 수 없어 답변할 수 없습니다.')
    expect(qaAiCall).not.toHaveBeenCalled()
    // Asking a question stays open.
    expect(isDisabled(askInput(w))).toBe(false)
  })

  it('en / ja: the AI line wording', async () => {
    const w = await mountTab(v01Target())
    await open(w, 10)
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(aiButton(w, 10).text()).toBe('Ask an AI')
    expect(aiPicker(w, 10).attributes('aria-label')).toBe('Answering AI')
    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(aiButton(w, 10).text()).toBe('AI に回答を依頼')
  })
})

// --- Refusals of a question --------------------------------------------------------------------------

describe('asking: refusals (flowgate.v02.0053 T#6)', () => {
  it.each([
    ['423 AI run', locked(), 'AI 실행 중이라 질문이 거절되었습니다.'],
    ['409 disposed', qRefusal(409, 'Modification not allowed: the group has been disposed.'), '폐기된 그룹이라 질문이 거절되었습니다.'],
    ['403', qRefusal(403, 'perm_document_create required'), '질문할 권한이 없어 거절되었습니다.'],
    ['404', qRefusal(404, 'Document does not exist'), '문서를 찾을 수 없어 질문이 거절되었습니다.'],
    ['400 CH document', qRefusal(400, 'This is a conversation (CH) document'), '질문 내용이 올바르지 않아 거절되었습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
    ['500', qRefusal(500, 'boom'), '질문을 등록하지 못했습니다.'],
  ])('a refused question (%s): nothing stored, the text stays, a red icon tells why in its tooltip', async (_label, refusal, text) => {
    refusals.set('ask', refusal)
    const w = await mountTab(emptyTarget())
    await ask(w, '질문')
    expect(qaAiCall).not.toHaveBeenCalled()
    expect(iconView(askFailure(w))).toEqual({ role: 'img', focusable: '0', name: '질문 등록 실패', description: text, text: '' })
    expect(askFailure(w).element.parentElement?.getAttribute('data-test')).toBe('qna-ask')
    // No sentence in the tab: the reason is only the tooltip.
    expect(w.get('[data-test="inspector-qna"]').text()).not.toContain(text)
    expect(w.find('[data-test="inspector-reason"]').exists()).toBe(false)
    expect(inputValue(w)).toBe('질문')
    expect(ids(w)).toEqual([])
    // Read again after the refusal as well.
    expect(qaReadCall).toHaveBeenCalledTimes(2)
  })

  it('en / ja: the icon name and the tooltip in the locale', async () => {
    refusals.set('ask', locked())
    const w = await mountTab(emptyTarget())
    await ask(w, '질문')
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect([iconView(askFailure(w)).name, iconView(askFailure(w)).description]).toEqual(['Question not registered', 'The question was refused because an AI run is in progress.'])
    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect([iconView(askFailure(w)).name, iconView(askFailure(w)).description]).toEqual(['質問の登録に失敗', 'AI 実行中のため質問が拒否されました。'])
  })

  it('the next question clears the last refusal', async () => {
    refusals.set('ask', networkError())
    const w = await mountTab(emptyTarget())
    await ask(w, '질문')
    expect(askFailure(w).exists()).toBe(true)
    await ask(w, '질문')
    expect(askFailure(w).exists()).toBe(false)
    expect(ids(w)).toEqual([31])
  })
})

// --- When asking is closed --------------------------------------------------------------------------

describe('asking and requesting: closed with the reason as the tooltip', () => {
  // flowgate.v02.0063: a Flow whose answers stay closed shows neither the question line nor the AI line.
  it.each([
    ['ownership conflict', { readOnly: 'conflict' as const }, false],
    ['a disposed group', {}, true],
  ])('%s: no question line and no AI line', async (_label, fields, dispose) => {
    disposed = dispose
    const w = await mountTab(v01Target(fields))
    expect(w.find('[data-test="qna-ask"]').exists()).toBe(false)
    await open(w, 10)
    expect(windowOf(10)!.querySelector('[data-test="qna-window-ai"]')).toBeNull()
    expect(windowOf(10)!.querySelector('[data-test="qna-window-ai-provider"]')).toBeNull()
  })

  it('a revision view asks and requests as the draft view does (0063)', async () => {
    const w = await mountTab(v01Target({ readOnly: 'revision' }))
    expect(isDisabled(askInput(w))).toBe(false)
    expect(askButton(w).attributes('title')).toBe('질문 등록')
    await open(w, 10)
    expect(isDisabled(aiButton(w, 10))).toBe(false)
  })

  it('an AI run of the Flow closes asking and requesting; both open again when the run finishes', async () => {
    vi.useFakeTimers()
    running = true
    const w = await mountTab(v01Target())
    await open(w, 10)
    stream.open()
    expect(isDisabled(askInput(w))).toBe(true)
    expect(askButton(w).attributes('title')).toBe('AI 실행 중이라 질문할 수 없습니다.')
    expect(isDisabled(aiButton(w, 10))).toBe(true)
    expect(aiButton(w, 10).attributes('title')).toBe('AI 실행 중이라 AI 답변을 요청할 수 없습니다.')
    running = false
    stream.emit('ai_invoke_finished', { groupId: V01_FLOW, docId: V01_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(isDisabled(askInput(w))).toBe(false)
    expect(askButton(w).attributes('title')).toBe('질문 등록')
    expect(isDisabled(aiButton(w, 10))).toBe(false)
  })

  it('the unsent question and the AI picks belong to their document', async () => {
    const w = await mountTab(v01Target())
    await askInput(w).setValue('쓰던 질문')
    await open(w, 10)
    await aiPicker(w, 10).setValue('p2')
    await w.setProps({ target: emptyTarget() })
    await flushPromises()
    expect(inputValue(w)).toBe('')
    await w.setProps({ target: v01Target() })
    await flushPromises()
    await open(w, 10)
    expect((aiPicker(w, 10).element as HTMLSelectElement).value).toBe('p1')
  })

  it('a late refusal of the previous Node is not shown on the new one', async () => {
    let refuse: () => void = () => {}
    qaAskCall.mockImplementationOnce(() => new Promise((_resolve, reject) => (refuse = () => reject(networkError()))))
    const w = await mountTab(v01Target())
    await askInput(w).setValue('질문')
    await askButton(w).trigger('click')
    await w.setProps({ target: emptyTarget() })
    await flushPromises()
    refuse()
    await flushPromises()
    expect(askFailure(w).exists()).toBe(false)
  })
})

// --- A card just placed, in the real Frame -----------------------------------------------------------

describe('a card just placed: ask on the Q&A tab, request the AI answer and see it (0053 T#6 완료 기준, 0056 F1)', () => {
  const FLOW = 'flowgate.v02.0060'
  const INSTRUCTION = `${FLOW}.0009-T`

  const line = (slot: string, docType: string, docId: string, author: 'human' | 'run', hasBody?: boolean): FlowGraphCardDocument => ({
    slot,
    docType,
    author,
    docId,
    title: `카드 · ${slot}`,
    status: 'draft',
    docReviewStatus: null,
    ...(hasBody === undefined ? {} : { hasBody }),
  })

  /** A 작업 card as the shown Graph has it right after the save answered its key and T#1's documents. */
  const placedTask = (): FlowGraphNode => ({
    id: 'gn_t',
    kind: 'task',
    title: '새 카드',
    state: 'draft',
    docType: 'T',
    x: 0,
    y: 0,
    structureEditable: true,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: '새 카드', assignee: null, rootBound: false },
    document: { docId: INSTRUCTION, typeCode: 'T', seq: null, title: '카드 · instruction', docReviewStatus: null, status: 'draft', supersedes: [] },
    // flowgate.v02.0070 T#5: the instruction is written (a body to check) — an AI answer is asked only then.
    cardDocuments: [line('instruction', 'T', INSTRUCTION, 'human', true), line('report', 'TR', `${FLOW}.0010-TR`, 'run')],
  })

  const graphOf = (node: FlowGraphNode): FlowGraph => ({
    flowId: FLOW,
    nodes: [node],
    edges: [],
    ownership: 'unassigned',
    viewMode: 'draft',
    draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null },
  })

  it('the Q&A tab of the new card shows only the line; a question is registered, then an AI asked, then answered', async () => {
    vi.useFakeTimers()
    const node = placedTask()
    wrapper = mount(DesignerInspector, {
      props: { selection: selectionOf(graphOf(node), node.id) },
      global: { plugins: [i18n, pinia] },
      attachTo: document.body,
    })
    await flushPromises()
    stream.open()
    await wrapper.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((tab) => tab.text().startsWith('Q&A'))!.trigger('click')
    await flushPromises()
    const w = wrapper
    expect(w.find('[data-test="designer-inspector-tab-state"]').exists()).toBe(false)
    expect(w.get('[data-test="inspector-qna"]').text()).toBe('')
    await ask(w, '이 작업의 완료 기준은?')
    expect(qaAskCall).toHaveBeenCalledWith({ docId: INSTRUCTION, body: '이 작업의 완료 기준은?' })
    expect(qaAiCall).not.toHaveBeenCalled()
    expect(ids(w)).toEqual([31])
    await requestAi(w, 31)
    expect(qaAiCall).toHaveBeenCalledWith(INSTRUCTION, 31, 'p1')
    expect(aiWaiting(w, 31).exists()).toBe(true)
    await aiAnswerArrives(INSTRUCTION, '단위 테스트가 모두 통과하면 완료입니다.')
    expect(itemEl(w, 31).attributes('data-state')).toBe('answered')
    expect(inWindow(31).get('[data-test="qna-window-answer"] .inspector-qna-text').text()).toBe('단위 테스트가 모두 통과하면 완료입니다.')
    expect(inWindow(31).get('[data-test="qna-window-answer-side"]').text()).toBe('답: AI · Claude Opus 5.5')
  })
})
