/**
 * flowgate.v02.0048 T#7 — the Q&A tab with `@/features/document` mocked as a small server (D#1
 * §12.1): the questions of the target document in display order with who asked and who answered,
 * answering with prose / an option / both and the questions read again after it, the refusals
 * (423 AI running, 409 disposed, 403, 404, network) shown as a reason with the questions read again,
 * the read-only Flows and the guard (AI running, disposed), the Q&A events read again through the
 * real hub (only for the target document), a failed read with retry, and a Node change. Last, the
 * real registry: the Q&A tab with its number in the Frame (the number follows the events and the
 * answers), and no tab on a Node without a document.
 * flowgate.v02.0052 묶음 A — the v7 shape: one line per question with the last answer in its small
 * line, the inline answer line (field + 보내기) under every question without an answer, the folded
 * 완료 group (open when nothing is pending), and the ↗ modeless window with the full form.
 * flowgate.v02.0070 T#5 (B5) — the list only shows and opens (question, state, who asked, 열기): every answer is
 * written in the ↗ window, under the document id, who asked and when, and the full question. The targets carry the
 * card line of their document with a body (`bodyLine`); a body that is unreadable, blank, not sent or without a card
 * line, and a superseded document, offer no answer — the window shows one line (or 읽기 전용) and stays readable.
 */
import { DOMWrapper, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import type { FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import QnaTab from '@/features/designer/inspector/features/qna/QnaTab.vue'
import { QNA_EVENTS, QNA_GUARD_EVENTS, useQna } from '@/features/designer/inspector/features/qna/useQna'
import type { InspectorCardDocument, InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { SELECTIONS, V01_GRAPH, V01_TASK_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'

// The calls are mocked; normalization and error reading stay real.
const qaReadCall = vi.fn()
const qaAnswerCall = vi.fn()
const qaAiCall = vi.fn()
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
    qaRequestAiAnswer: (...args: unknown[]) => qaAiCall(...args),
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
  created_at?: string
}

/** flowgate.v02.0070 T#5: when question 10 was registered. */
const ASKED_AT = '2026-10-18T09:00:00+09:00'

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
      created_at: ASKED_AT,
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
  for (const mock of [qaReadCall, qaAnswerCall, qaAiCall, activityRead, documentRead, detailRead, relationsRead]) mock.mockReset()
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

/** flowgate.v02.0070 T#5: the card line of a document whose body is there (answering needs one); `fields` change it. */
const bodyLine = (docId: string, fields: Partial<InspectorCardDocument> = {}): InspectorCardDocument => ({
  slot: 'report',
  docType: 'TR',
  author: 'run',
  docId,
  title: docId,
  status: 'open',
  docReviewStatus: 'approved',
  hasBody: true,
  bodyState: null,
  ...fields,
})

/** The v0.1 target with its document's card line (a body to check), unless `fields` give the lines. */
const v01Target = (fields: Partial<InspectorTarget> = {}): InspectorTarget => {
  const docId = fields.docId === undefined ? V01_DOC : fields.docId
  return { ...targetOf(SELECTIONS.v01), cardDocuments: docId ? [bodyLine(docId)] : [], ...fields }
}
const withBody = (target: InspectorTarget): InspectorTarget => ({ ...target, cardDocuments: target.docId ? [bodyLine(target.docId)] : [] })

const itemEl = (w: VueWrapper<any>, id: number) => w.find(`[data-test="qna-item"][data-item="${id}"]`)
const order = (w: VueWrapper<any>) => w.findAll('[data-test="qna-item"]').map((el) => [Number(el.attributes('data-item')), el.attributes('data-state')])
const isDisabled = (el: { attributes(name: string): string | undefined }) => el.attributes('disabled') !== undefined

// flowgate.v02.0070 T#5: a question is answered in its ↗ window (teleported to the body).
const windowOf = (id: number) => document.body.querySelector<HTMLElement>(`[data-test="qna-window"][data-item="${id}"]`)
const windowText = (id: number, test: string) => windowOf(id)?.querySelector(`[data-test="${test}"]`)?.textContent ?? null
const inWindow = (id: number, selector: string) => new DOMWrapper(windowOf(id)!).find(selector)
const submitOf = (id: number) => inWindow(id, '[data-test="qna-window-submit"]')
const fieldOf = (id: number) => windowOf(id)!.querySelector<HTMLTextAreaElement>('textarea[data-test="qna-window-body-input"]')!

/** Opens the question's window with its 열기 button (a window already open stays). */
async function open(w: VueWrapper<any>, id: number): Promise<HTMLElement> {
  if (!windowOf(id)) {
    await itemEl(w, id).find('[data-test="qna-open"]').trigger('click')
    await flushPromises()
  }
  const win = windowOf(id)
  if (!win) throw new Error(`window ${id} not opened`)
  return win
}

async function write(w: VueWrapper<any>, id: number, text: string) {
  await open(w, id)
  await inWindow(id, 'textarea[data-test="qna-window-body-input"]').setValue(text)
}

async function send(w: VueWrapper<any>, id: number) {
  await open(w, id)
  await submitOf(id).trigger('click')
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
    // 0052 T#1: the response proves only answered or not — every unanswered question is 미답변
    // (neutral) whoever asked it; the asker kind is never shown as a team review or a hand-off.
    for (const id of [10, 13, 12]) {
      expect(itemEl(w, id).find('[data-test="qna-item-state"]').text(), String(id)).toBe('미답변')
      expect(itemEl(w, id).find('[data-test="qna-item-state"]').attributes('data-tone'), String(id)).toBe('neutral')
    }
    expect(w.text()).not.toContain('팀 내 검토')
    expect(itemEl(w, 11).find('[data-test="qna-item-state"]').text()).toBe('완료')
    // The answered question folds under the 완료 group while questions are pending.
    expect(w.find('[data-test="qna-done-group"]').attributes('aria-expanded')).toBe('false')
    expect(w.find('[data-test="qna-done-count"]').text()).toBe('1')
    expect(itemEl(w, 11).attributes('style')).toContain('display: none')
    await w.find('[data-test="qna-done-group"]').trigger('click')
    expect(itemEl(w, 11).attributes('style') ?? '').not.toContain('display: none')
  })

  it('a document whose questions are all answered opens the 완료 group', async () => {
    threads.set(V01_DOC, [threads.get(V01_DOC)!.find((entry) => entry.id === 11)!])
    const w = await mountTab(v01Target())
    expect(w.find('[data-test="qna-done-group"]').attributes('aria-expanded')).toBe('true')
    expect(itemEl(w, 11).attributes('style') ?? '').not.toContain('display: none')
  })

  // flowgate.v02.0070 T#5: who answered is read in the ↗ window; the list line keeps who asked only.
  it('shows who asked and who answered — never a guessed provider', async () => {
    const w = await mountTab(v01Target())
    expect(itemEl(w, 11).find('[data-test="qna-answer"]').exists()).toBe(false)
    await open(w, 11)
    expect(windowText(11, 'qna-window-answer-side')).toBe('답: 사람')
    expect(itemEl(w, 10).find('[data-test="qna-item-asker"]').text()).toBe('AI · Claude Opus 5.5')
    expect(itemEl(w, 13).find('[data-test="qna-item-asker"]').text()).toBe('AI(실행 정보 없음)')
    expect(itemEl(w, 12).find('[data-test="qna-item-asker"]').text()).toBe('사용자')
    expect(windowOf(11)!.querySelector('[data-test="qna-window-answer"] .inspector-qna-text')?.textContent).toBe('ok')
  })

  // flowgate.v02.0070 T#5 (B5): the list is for looking over and opening; the form is the ↗ window's.
  it('the list has no options, answer field, send or AI line; an unanswered question’s window has the form, an answered one’s none', async () => {
    const w = await mountTab(v01Target())
    for (const id of [10, 11, 12, 13]) {
      for (const test of ['qna-answer-form', 'qna-option', 'qna-answer-body-input', 'qna-answer-submit', 'qna-ai', 'qna-ai-request', 'qna-answer']) {
        expect(itemEl(w, id).find(`[data-test="${test}"]`).exists(), `${id} ${test}`).toBe(false)
      }
      expect(itemEl(w, id).find('[data-test="qna-open"]').exists(), String(id)).toBe(true)
    }
    for (const id of [10, 12, 13]) {
      await open(w, id)
      expect(windowOf(id)!.querySelector('[data-test="qna-window-form"]'), String(id)).not.toBeNull()
    }
    expect([...windowOf(10)!.querySelectorAll('[data-test="qna-window-option"]')].map((el) => el.textContent)).toEqual(['코드 품질', '성능'])
    expect(windowOf(13)!.querySelectorAll('[data-test="qna-window-option"]')).toHaveLength(0)
    await open(w, 11)
    expect(windowOf(11)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
    expect(qaAnswerCall).not.toHaveBeenCalled()
  })

  // flowgate.v02.0052 묶음 A — v7 ↗ (0035 insp.js openQA): the question in a modeless window.
  describe('the ↗ window', () => {

    // flowgate.v02.0053 T#5 (0053 CH): the button that opens a question's window is the same document icon as
    // the document lines (tooltip 열기), not ↗.
    it('the open button of a question is the document icon with the tooltip 열기', async () => {
      const w = await mountTab(v01Target())
      const button = itemEl(w, 10).get('[data-test="qna-open"]')
      expect([button.attributes('data-icon'), button.attributes('title'), button.attributes('aria-label'), button.text()]).toEqual(['document', '열기', '열기', ''])
      expect(w.text()).not.toContain('↗')
    })

    it('shows the full question, its answers and the full form; no 편집 badge — answering is not editing', async () => {
      const w = await mountTab(v01Target())
      await open(w, 10)
      expect(windowText(10, 'qna-window-body')).toBe('어느 범위를 조사할까요?')
      expect(windowText(10, 'qna-window-asker')).toBe('질문: AI · Claude Opus 5.5')
      // flowgate.v02.0070 T#5: the context above the form — the full document id and when it was asked.
      expect(windowText(10, 'qna-window-doc')).toBe(`문서: ${V01_DOC}`)
      expect(windowText(10, 'qna-window-asked-at')).toBe(`질문 시각: ${new Date(ASKED_AT).toLocaleString('ko')}`)
      expect(windowOf(10)!.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
      expect(windowOf(10)!.querySelector('[data-test="qna-window-options"]')).toBeNull()
      expect(windowOf(10)!.querySelector('[data-test="qna-window-form"]')).not.toBeNull()
      expect(windowOf(10)!.querySelectorAll('[data-test="qna-window-option"]')).toHaveLength(2)
      expect(document.body.querySelector('[data-test="qna-window-mode"]')).toBeNull()
      expect(windowText(10, 'qna-window-state')).toBe('미답변')
      await open(w, 11)
      expect(windowText(11, 'qna-window-body')).toBe('범위를 가정했습니다.')
      // No registration time sent: none shown (the API has no time the question changed).
      expect(windowOf(11)!.querySelector('[data-test="qna-window-asked-at"]')).toBeNull()
      expect(windowOf(11)!.querySelectorAll('[data-test="qna-window-answer"]')).toHaveLength(1)
      expect(windowOf(11)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
      // An answered question of a writable Flow is not "read only" either.
      expect(document.body.querySelector('[data-test="qna-window-mode"]')).toBeNull()
      expect(document.body.querySelectorAll('[data-test="qna-window"]')).toHaveLength(2)
      // ↗ again on an open question keeps the one window.
      await itemEl(w, 10).find('[data-test="qna-open"]').trigger('click')
      expect(document.body.querySelectorAll('[data-test="qna-window"]')).toHaveLength(2)
    })

    it('an answer sent from the window reaches the server and the form closes', async () => {
      const w = await mountTab(v01Target())
      const win = await open(w, 13)
      const field = win.querySelector<HTMLTextAreaElement>('textarea[data-test="qna-window-body-input"]')!
      field.value = ' 창에서 답변 '
      field.dispatchEvent(new Event('input'))
      await flushPromises()
      win.querySelector<HTMLButtonElement>('[data-test="qna-window-submit"]')!.click()
      await flushPromises()
      expect(qaAnswerCall).toHaveBeenCalledWith({ docId: V01_DOC, itemId: 13, body: '창에서 답변', selectedOptionIds: [] })
      expect(itemEl(w, 13).attributes('data-state')).toBe('answered')
      expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
      expect(windowText(13, 'qna-window-state')).toBe('완료')
      expect(document.body.querySelector('[data-test="qna-window-mode"]')).toBeNull()
    })

    it('the title line: the long title is the shrinking part with its full text as a tooltip; the badge stays apart', async () => {
      const long = '아주 긴 질문 제목 '.repeat(12).trim()
      threads.set(V01_DOC, threads.get(V01_DOC)!.map((entry) => (entry.id === 10 ? { ...entry, title: long } : entry)))
      const w = await mountTab(v01Target({ readOnly: 'conflict' }))
      const win = await open(w, 10)
      const panel = win.closest('.ui-modeless-panel')!
      const sub = panel.querySelector<HTMLElement>('[data-test="qna-window-sub"]')!
      expect(sub.textContent).toBe(`Q1 · ${long}`)
      expect(sub.getAttribute('title')).toBe(`Q1 · ${long}`)
      const mode = panel.querySelector<HTMLElement>('[data-test="qna-window-mode"]')!
      // The badge is a sibling after the title, not inside the text that is cut with an ellipsis.
      expect(sub.contains(mode)).toBe(false)
      expect(mode.parentElement).toBe(sub.parentElement)
      expect(mode.textContent).toBe('읽기 전용')
      expect(mode.getAttribute('title')).toBe('Flow 소유가 충돌해 읽기 전용입니다.')
      // flowgate.v02.0063: a Flow whose answers stay closed shows no answer form in the window.
      expect(win.querySelector('[data-test="qna-window-form"]')).toBeNull()
    })

    it('a revision view answers in the window as the draft view does: no 읽기 전용 badge (0063)', async () => {
      const w = await mountTab(v01Target({ readOnly: 'revision' }))
      const win = await open(w, 13)
      expect(document.body.querySelector('[data-test="qna-window-mode"]')).toBeNull()
      expect(win.querySelector('[data-test="qna-window-form"]')).not.toBeNull()
      expect(win.querySelector('[data-test="qna-window-ai"]')).not.toBeNull()
    })

    it('a read-only Flow opens the window read only with the reason; closing removes it; a Node change closes every window', async () => {
      const w = await mountTab(v01Target({ readOnly: 'conflict' }))
      const win = await open(w, 13)
      expect(document.body.querySelector('[data-test="qna-window-mode"]')?.textContent).toBe('읽기 전용')
      expect(document.body.querySelector('[data-test="qna-window-mode"]')?.getAttribute('title')).toBe('Flow 소유가 충돌해 읽기 전용입니다.')
      expect(win.querySelector('[data-test="qna-window-form"]')).toBeNull()
      expect(win.querySelector('[data-test="qna-window-ai"]')).toBeNull()
      win.closest('.ui-modeless-panel')!.querySelector<HTMLButtonElement>('[aria-label="Close"]')!.click()
      await flushPromises()
      expect(windowOf(13)).toBeNull()
      await open(w, 10)
      await w.setProps({ target: v01Target({ readOnly: 'conflict', docId: OTHER_DOC, nodeId: `doc:${OTHER_DOC}` }) })
      await flushPromises()
      expect(document.body.querySelectorAll('[data-test="qna-window"]')).toHaveLength(0)
    })

    // flowgate.v02.0070 T#5 (B5): answering needs a body that can be checked.
    describe('the body of the document (flowgate.v02.0070 T#5)', () => {
      const UNREADABLE = '본문을 읽을 수 없어 답변할 수 없습니다.'
      const UNCONFIRMED = '본문을 확인할 수 없어 답변할 수 없습니다.'

      it.each([
        ['an unreadable body', [bodyLine(V01_DOC, { bodyState: 'file_not_found' })], UNREADABLE],
        ['a blank body', [bodyLine(V01_DOC, { hasBody: false })], UNCONFIRMED],
        ['a body not sent', [bodyLine(V01_DOC, { hasBody: undefined })], UNCONFIRMED],
        ['only another document’s card line', [bodyLine(OTHER_DOC)], UNCONFIRMED],
        ['no card line', [], UNCONFIRMED],
      ])('%s: one line instead of the options, field, send and AI line; the question, options and answers stay readable', async (_label, lines, text) => {
        const w = await mountTab(v01Target({ cardDocuments: lines }))
        const win = await open(w, 10)
        for (const test of ['qna-window-form', 'qna-window-option', 'qna-window-body-input', 'qna-window-submit', 'qna-window-ai']) {
          expect(win.querySelector(`[data-test="${test}"]`), test).toBeNull()
        }
        expect(windowText(10, 'qna-window-blocked')).toBe(text)
        expect(windowText(10, 'qna-window-body')).toBe('어느 범위를 조사할까요?')
        expect(windowText(10, 'qna-window-doc')).toBe(`문서: ${V01_DOC}`)
        expect(windowText(10, 'qna-window-options')).toBe('선택지: 코드 품질, 성능')
        expect(document.body.querySelector('[data-test="qna-window-mode"]')).toBeNull()
        // An answered question keeps its answers; nothing to answer, so no line.
        const done = await open(w, 11)
        expect(done.querySelectorAll('[data-test="qna-window-answer"]')).toHaveLength(1)
        expect(done.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
        // Asking a question is not this rule's.
        expect(w.find('[data-test="qna-ask"]').exists()).toBe(true)
        expect(qaAnswerCall).not.toHaveBeenCalled()
      })

      it('a document a later one superseded is history: 읽기 전용 with the reason, no form and no line; reading stays', async () => {
        documentRead.mockImplementation(async (docId: string) => toReviewDocument({ ...rowOf(docId), superseded_by: 'flowgate.v02.0048.0015-TR' }))
        const w = await mountTab(v01Target())
        const win = await open(w, 10)
        expect(win.querySelector('[data-test="qna-window-form"]')).toBeNull()
        expect(win.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
        const mode = document.body.querySelector('[data-test="qna-window-mode"]')!
        expect(mode.textContent).toBe('읽기 전용')
        expect(mode.getAttribute('title')).toBe('이전 문서의 이력이라 답변할 수 없습니다.')
        expect(windowText(10, 'qna-window-body')).toBe('어느 범위를 조사할까요?')
        expect(windowText(10, 'qna-window-options')).toBe('선택지: 코드 품질, 성능')
      })

      it('the body read again with the Canvas opens the form: the window follows the target', async () => {
        const w = await mountTab(v01Target({ cardDocuments: [bodyLine(V01_DOC, { hasBody: false })] }))
        const win = await open(w, 13)
        expect(win.querySelector('[data-test="qna-window-form"]')).toBeNull()
        await w.setProps({ target: v01Target() })
        await flushPromises()
        expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).not.toBeNull()
        expect(windowOf(13)!.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
      })

      // The document read says whether a later document supersedes this one: until it is back as current, nothing is answered.
      it('the document read not back: one line, no form; back as current, the form opens', async () => {
        let release: () => void = () => {}
        documentRead.mockImplementationOnce((docId: string) => new Promise((resolve) => (release = () => resolve(toReviewDocument(rowOf(docId))))))
        const w = await mountTab(v01Target())
        const win = await open(w, 13)
        for (const test of ['qna-window-form', 'qna-window-body-input', 'qna-window-submit', 'qna-window-ai']) {
          expect(win.querySelector(`[data-test="${test}"]`), test).toBeNull()
        }
        expect(windowText(13, 'qna-window-blocked')).toBe(UNCONFIRMED)
        expect(windowText(13, 'qna-window-body')).toBe('완료 기준은?')
        expect(w.find('[data-test="qna-ask"]').exists()).toBe(true)
        release()
        await flushPromises()
        expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).not.toBeNull()
        expect(windowOf(13)!.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
        expect(qaAnswerCall).not.toHaveBeenCalled()
      })

      it('a failed document read: one line until a read again finds the document current', async () => {
        documentRead.mockRejectedValueOnce(networkError())
        const w = await mountTab(v01Target())
        await open(w, 13)
        expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
        expect(windowText(13, 'qna-window-blocked')).toBe(UNCONFIRMED)
        expect(windowText(13, 'qna-window-body')).toBe('완료 기준은?')
        vi.useFakeTimers()
        stream.open()
        stream.emit('document_explorer_refresh', { groupId: V01_FLOW })
        await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
        await flushPromises()
        expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).not.toBeNull()
        expect(windowOf(13)!.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
      })

      it('a failed AI activity read keeps the superseded document read: 읽기 전용, no form; asking stays', async () => {
        activityRead.mockRejectedValue(networkError())
        documentRead.mockImplementation(async (docId: string) => toReviewDocument({ ...rowOf(docId), superseded_by: 'flowgate.v02.0048.0015-TR' }))
        const w = await mountTab(v01Target())
        const win = await open(w, 13)
        expect(win.querySelector('[data-test="qna-window-form"]')).toBeNull()
        expect(win.querySelector('[data-test="qna-window-ai"]')).toBeNull()
        expect(win.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
        expect(document.body.querySelector('[data-test="qna-window-mode"]')?.textContent).toBe('읽기 전용')
        expect(windowText(13, 'qna-window-body')).toBe('완료 기준은?')
        expect(w.find('[data-test="qna-ask"]').exists()).toBe(true)
      })
    })
  })

  it('a document without questions shows only the question line — no empty sentence (0053 T#6)', async () => {
    const w = await mountTab(v01Target({ docId: 'flowgate.v02.0048.0099-T' }))
    expect(w.find('[data-test="inspector-pane-state"]').exists()).toBe(false)
    expect(w.find('[data-test="qna-ask"]').exists()).toBe(true)
    expect(w.findAll('[data-test="qna-item"]')).toHaveLength(0)
    expect(w.text()).toBe('')
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
    await open(w, 13)
    expect(isDisabled(submitOf(13))).toBe(true)
    await write(w, 13, '   ')
    expect(isDisabled(submitOf(13))).toBe(true)
    await write(w, 13, '  테스트 통과가 완료 기준입니다. ')
    expect(isDisabled(submitOf(13))).toBe(false)
    await send(w, 13)
    expect(qaAnswerCall).toHaveBeenCalledWith({ docId: V01_DOC, itemId: 13, body: '테스트 통과가 완료 기준입니다.', selectedOptionIds: [] })
    expect(qaReadCall).toHaveBeenCalledTimes(2)
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(documentRead).toHaveBeenCalledTimes(2)
    expect(itemEl(w, 13).attributes('data-state')).toBe('answered')
    expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
    expect(windowOf(13)!.querySelector('[data-test="qna-window-answer"] .inspector-qna-text')?.textContent).toBe('테스트 통과가 완료 기준입니다.')
  })

  it('a picked option alone can be sent; picking again takes it back; the answer shows the pick', async () => {
    const w = await mountTab(v01Target())
    await open(w, 10)
    const options = () => new DOMWrapper(windowOf(10)!).findAll('[data-test="qna-window-option"]')
    await options()[1].trigger('click')
    expect(options()[1].attributes('aria-pressed')).toBe('true')
    await options()[1].trigger('click')
    expect(options()[1].attributes('aria-pressed')).toBe('false')
    expect(isDisabled(submitOf(10))).toBe(true)
    await options()[0].trigger('click')
    expect(isDisabled(submitOf(10))).toBe(false)
    await send(w, 10)
    expect(qaAnswerCall).toHaveBeenCalledWith({ docId: V01_DOC, itemId: 10, body: '', selectedOptionIds: ['o1'] })
    const answerEl = windowOf(10)!.querySelector('[data-test="qna-window-answer"]')!
    expect(answerEl.textContent).toContain('선택: 코드 품질')
    expect(answerEl.querySelector('.inspector-qna-text')?.textContent).toBe('코드 품질')
  })

  it('an option and prose are sent together', async () => {
    const w = await mountTab(v01Target())
    await open(w, 10)
    await new DOMWrapper(windowOf(10)!).findAll('[data-test="qna-window-option"]')[1].trigger('click')
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
    await open(w, 10)
    await send(w, 13)
    expect(windowText(13, 'qna-window-failure')?.trim()).toBe(text)
    expect(windowOf(10)!.querySelector('[data-test="qna-window-failure"]')).toBeNull()
    expect(qaReadCall).toHaveBeenCalledTimes(2)
    expect(activityRead).toHaveBeenCalledTimes(2)
    expect(itemEl(w, 13).attributes('data-state')).toBe('waiting')
    expect(fieldOf(13).value).toBe('완료 기준')
  })

  it('after a 423 the guard read again finds the run: answering is disabled with its reason', async () => {
    const w = await mountTab(v01Target())
    await write(w, 13, '완료 기준')
    refusals.set('answer', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED' } }))
    running = true
    await send(w, 13)
    expect(isDisabled(submitOf(13))).toBe(true)
    expect(submitOf(13).attributes('title')).toBe('AI 실행 중이라 답변할 수 없습니다.')
  })

  it('while an answer waits for the server every answer is disabled and a second click sends nothing', async () => {
    let release: () => void = () => {}
    qaAnswerCall.mockImplementationOnce(() => new Promise((resolve) => (release = () => resolve(toQaAnswerResult({ ok: true })))))
    const w = await mountTab(v01Target())
    await open(w, 10)
    await write(w, 13, '완료 기준')
    await submitOf(13).trigger('click')
    await flushPromises()
    expect(isDisabled(submitOf(13))).toBe(true)
    expect(submitOf(10).attributes('title')).toBe('답변을 보내는 중입니다.')
    await submitOf(13).trigger('click')
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    release()
    await flushPromises()
    expect(submitOf(10).attributes('title')).toBeUndefined()
  })

  it('after a saved answer the question cannot be sent again until the read after it is back', async () => {
    const w = await mountTab(v01Target())
    let releaseRead: () => void = () => {}
    qaReadCall.mockImplementationOnce((docId: string) => new Promise((resolve) => (releaseRead = () => resolve(toQaThread(treeOf(docId))))))
    await open(w, 10)
    await write(w, 13, '완료 기준')
    await send(w, 13)
    // Saved on the server; the questions shown are still the read from before the answer.
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    expect(itemEl(w, 13).attributes('data-state')).toBe('waiting')
    expect(isDisabled(submitOf(13))).toBe(true)
    expect(submitOf(10).attributes('title')).toBe('답변을 보내는 중입니다.')
    await submitOf(13).trigger('click')
    await flushPromises()
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    releaseRead()
    await flushPromises()
    expect(itemEl(w, 13).attributes('data-state')).toBe('answered')
    expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
    expect(submitOf(10).attributes('title')).toBeUndefined()
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
    expect(isDisabled(submitOf(13))).toBe(true)
    await submitOf(13).trigger('click')
    await flushPromises()
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
    await write(w, 12, '필요합니다')
    expect(isDisabled(submitOf(12))).toBe(false)
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
    await submitOf(13).trigger('click')
    await w.setProps({ target: v01Target({ docId: OTHER_DOC, nodeId: `doc:${OTHER_DOC}` }) })
    reject(qRefusal(409, 'disposed'))
    await flushPromises()
    expect(order(w)).toEqual([[20, 'waiting']])
    await open(w, 20)
    expect(document.body.querySelector('[data-test="qna-window-failure"]')).toBeNull()
  })
})

// --- Availability ----------------------------------------------------------------------------------

describe('Q&A tab — read-only Flows and the guard (flowgate.v02.0048 T#7)', () => {
  it.each([
    ['ownership conflict', { readOnly: 'conflict' as const }],
  ])('%s: the questions are shown; no question line, answer form or AI line (0063)', async (_label, fields) => {
    const w = await mountTab(v01Target(fields))
    expect(order(w)).toHaveLength(4)
    expect(w.find('[data-test="qna-ask"]').exists()).toBe(false)
    expect(w.text()).not.toContain('읽기 전용입니다')
    await open(w, 13)
    await open(w, 10)
    expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
    expect(windowOf(10)!.querySelector('[data-test="qna-window-ai"]')).toBeNull()
    // 0063: no reason sentence either (0070 T#5's line is the body's only).
    expect(windowOf(13)!.querySelector('[data-test="qna-window-blocked"]')).toBeNull()
    expect(qaAnswerCall).not.toHaveBeenCalled()
  })

  it('a Graph revision view answers as the draft view does (0063)', async () => {
    const w = await mountTab(v01Target({ readOnly: 'revision' }))
    await write(w, 13, '완료 기준')
    expect(isDisabled(submitOf(13))).toBe(false)
    await open(w, 10)
    expect(isDisabled(inWindow(10, '[data-test="qna-window-option"]'))).toBe(false)
    expect(windowOf(10)!.querySelector('[data-test="qna-window-ai"]')).not.toBeNull()
    await send(w, 13)
    expect(qaAnswerCall).toHaveBeenCalledTimes(1)
  })

  it('the real conflict and Graph selections read the same way', async () => {
    threads.set(targetOf(SELECTIONS.conflict).docId!, [{ id: 31, seq: 1, title: 'c', body: '?', asker_kind: 'ai', options: [], asker_provider: null, answers: [] }])
    const conflict = await mountTab(withBody(targetOf(SELECTIONS.conflict)))
    await open(conflict, 31)
    expect(windowOf(31)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
    conflict.unmount()
    wrapper = null
    threads.set(targetOf(SELECTIONS.graph).docId!, [{ id: 30, seq: 1, title: 'g', body: '?', asker_kind: 'ai', options: [], asker_provider: null, answers: [] }])
    const graph = await mountTab(withBody(targetOf(SELECTIONS.graph)))
    // Graph draft view: a document feature, answering is open (blank prose keeps the button off).
    await write(graph, 30, 'g')
    expect(isDisabled(submitOf(30))).toBe(false)
    await graph.setProps({ target: { ...withBody(targetOf(SELECTIONS.graph)), readOnly: 'revision' } })
    expect(isDisabled(submitOf(30))).toBe(false)
  })

  it('an AI run in the Flow disables answering from the start', async () => {
    running = true
    const w = await mountTab(v01Target())
    expect(activityRead).toHaveBeenCalledWith(V01_FLOW)
    await open(w, 13)
    expect(isDisabled(submitOf(13))).toBe(true)
    expect(submitOf(13).attributes('title')).toBe('AI 실행 중이라 답변할 수 없습니다.')
  })

  it('a disposed group shows no answer form (0063)', async () => {
    disposed = true
    const w = await mountTab(v01Target())
    await open(w, 13)
    expect(windowOf(13)!.querySelector('[data-test="qna-window-form"]')).toBeNull()
  })

  it('a failed AI activity read does not block (the server decides) once the document read is back as current', async () => {
    activityRead.mockRejectedValue(networkError())
    const w = await mountTab(v01Target())
    await write(w, 13, 'x')
    expect(isDisabled(submitOf(13))).toBe(false)
    expect(submitOf(13).attributes('title')).toBeUndefined()
  })
})

// flowgate.v02.0070 T#5: the send functions themselves wait for the document read — not only the window's form.
describe('Q&A sending — the document read first (flowgate.v02.0070 T#5)', () => {
  const superseded = async (docId: string) => toReviewDocument({ ...rowOf(docId), superseded_by: 'flowgate.v02.0048.0015-TR' })

  async function withQna(run: (state: ReturnType<typeof useQna>) => Promise<void>) {
    const scope = effectScope()
    const target = v01Target()
    const state = scope.run(() => useQna(() => target))!
    try {
      await flushPromises()
      await run(state)
    } finally {
      scope.stop()
    }
  }

  it.each([
    ['the document read not back', () => documentRead.mockImplementation(() => new Promise(() => {})), 'reason.body_unconfirmed'],
    ['the document read failed', () => documentRead.mockRejectedValue(networkError()), 'reason.body_unconfirmed'],
    ['the AI activity read failed and the document read says superseded', () => {
      activityRead.mockRejectedValue(networkError())
      documentRead.mockImplementation(superseded)
    }, 'reason.history'],
  ] as const)('%s: answering, picking and asking an AI send nothing; reading and asking stay', async (_label, arrange, reason) => {
    arrange()
    await withQna(async (state) => {
      expect(state.items.value?.map((item) => item.id)).toEqual([10, 13, 12, 11])
      expect(state.availability.value.answer).toEqual({ visible: false, enabled: false, reason })
      expect(state.availability.value.ai_request).toEqual({ visible: false, enabled: false, reason })
      expect(state.availability.value.ask.enabled).toBe(true)
      expect(await state.answer(13, '완료 기준', [])).toBe(false)
      expect(await state.answer(10, '', ['o1'])).toBe(false)
      expect(await state.requestAi(13, null)).toBe(false)
      expect(qaAnswerCall).not.toHaveBeenCalled()
      expect(qaAiCall).not.toHaveBeenCalled()
    })
  })

  it('back as current the answer is sent; a failed document read after it closes answering again until one is back', async () => {
    await withQna(async (state) => {
      expect(state.availability.value.answer.enabled).toBe(true)
      documentRead.mockRejectedValueOnce(networkError())
      expect(await state.answer(13, '완료 기준', [])).toBe(true)
      expect(qaAnswerCall).toHaveBeenCalledTimes(1)
      expect(state.availability.value.answer).toEqual({ visible: false, enabled: false, reason: 'reason.body_unconfirmed' })
      expect(await state.answer(12, '필요합니다', [])).toBe(false)
      expect(qaAnswerCall).toHaveBeenCalledTimes(1)
      await state.reload()
      expect(state.availability.value.answer.enabled).toBe(true)
    })
  })

  it('a superseded document read stays when a later document read fails', async () => {
    documentRead.mockImplementationOnce(superseded).mockRejectedValue(networkError())
    await withQna(async (state) => {
      expect(state.availability.value.answer.reason).toBe('reason.history')
      await state.reload()
      expect(state.availability.value.answer.reason).toBe('reason.history')
      expect(await state.answer(13, '완료 기준', [])).toBe(false)
      expect(qaAnswerCall).not.toHaveBeenCalled()
    })
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
    await open(w, 13)
    expect(windowText(13, 'qna-window-answer-side')).toBe('답: AI · Codex')
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
    await open(w, 13)
    stream.open()
    running = true
    stream.emit('ai_invoke_started', { groupId: V01_FLOW, docId: OTHER_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(submitOf(13).attributes('title')).toBe('AI 실행 중이라 답변할 수 없습니다.')
    expect(qaReadCall).toHaveBeenCalledTimes(1)
    running = false
    stream.emit('ai_invoke_finished', { groupId: V01_FLOW, docId: OTHER_DOC })
    await vi.advanceTimersByTimeAsync(INSPECTOR_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(submitOf(13).attributes('title')).toBeUndefined()
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
    await open(w, 13)
    expect(fieldOf(13).value).toBe('')
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
    // flowgate.v02.0070 T#5: the Node's card line says its document has a body.
    const w = await mountFrame({ ...SELECTIONS.v01, node: { ...SELECTIONS.v01.node, cardDocuments: [bodyLine(V01_DOC)] } })
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

  // flowgate.v02.0056 F1: the AI answer request is a per-question operation now ([AI 답변 요청], qnaAsk spec).
  it('no team handling, resume or mention copy appears', async () => {
    const w = await mountFrame(SELECTIONS.v01)
    await openTab(w, 'Q&A')
    const text = w.text()
    for (const word of ['팀 내 처리', '재개', '멘트 복사', '위임']) expect(text).not.toContain(word)
  })
})
