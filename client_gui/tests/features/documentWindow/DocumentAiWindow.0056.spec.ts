/**
 * flowgate.v02.0056 F2-2 — the single AI call window of a document (O3 0009-TR §4-3), mounted by DocumentWindows with
 * `@shared/api` mocked:
 * - opening reads the project's AIs (the default first) and the group's live run — the AI is called only by [실행];
 * - [실행] sends action_scope edit · mode single · the pinned AI · the opinion as `messages`, never a review loop;
 * - the run is followed (GET /ai-invoke/{run_id} every DOCUMENT_AI_POLL_MS) until it ends: the window and the line
 *   (documentAiRunning) say the AI works, the opener hears the end;
 * - a live run of the group started elsewhere (another tab or user) is followed the same way until it ends (or is
 *   gone): the line and the window's note let go of "AI 작업 중" without opening the window again;
 * - 409 run_in_progress shows the server's message as it is; the other refusals the code's words with it.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import {
  DOCUMENT_AI_POLL_MS,
  DocumentWindows,
  closeAllDocumentWindows,
  documentAiRunOf,
  documentAiRunning,
  documentAiWindowList,
  openDocumentAiWindow,
} from '@/features/documentWindow'
import { documentAiFailure, documentAiMessages, documentAiPhase, documentAiRunnable } from '@/features/documentWindow/documentAiModel'
import taskMessages from '@/features/designer/inspector/features/taskCard/messages'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

registerAppMessages(i18n)

const FLOW = 'flowgate.v02.0056'
const DOC = `${FLOW}.0021-TR`

let runStatus: Record<string, unknown> = {}
let otherStatus: Record<string, unknown> | 'gone' = {}
let active: Record<string, unknown> = { active: false }
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentWindows()
  api.getRequest.mockReset()
  api.postRequest.mockReset()
  runStatus = { status: 'running' }
  otherStatus = { status: 'running' }
  active = { active: false }
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === '/api/v1/ai-invoke/providers') return { data: { providers: [{ id: 'aip_sonnet', name: 'Sonnet' }, { id: 'aip_opus', name: 'Opus' }], default_provider_id: 'aip_opus' } }
    if (path === '/api/v1/ai-invoke/active') return { data: active }
    if (path === '/api/v1/ai-invoke/run_1') return { data: runStatus }
    if (path === '/api/v1/ai-invoke/run_x') {
      if (otherStatus === 'gone') throw { isAxiosError: true, response: { status: 404, data: { code: 'run_not_found' } } }
      return { data: otherStatus }
    }
    throw new Error(`unexpected ${path}`)
  })
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})

afterEach(() => {
  vi.useRealTimers()
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  document.body.innerHTML = ''
})

const q = (id: string) => document.body.querySelector<HTMLElement>(`[data-test="${id}"]`)
const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? null
async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}

async function open(finished?: () => void) {
  openDocumentAiWindow({ docId: DOC, groupId: FLOW, projectId: 'flowgate', name: '레포트', title: '작업레포트', finished })
  await flushPromises()
}

describe('the AI call window rules (documentAiModel)', () => {
  it('phase, [실행] and the opinion sent', () => {
    expect(documentAiPhase(true, null)).toBe('starting')
    expect(documentAiPhase(false, null)).toBe('idle')
    expect(documentAiPhase(false, { finished: false })).toBe('running')
    expect(documentAiPhase(false, { finished: true })).toBe('finished')
    expect(documentAiRunnable('aip', 'idle')).toBe(true)
    expect(documentAiRunnable('aip', 'finished')).toBe(true)
    expect(documentAiRunnable('aip', 'running')).toBe(false)
    expect(documentAiRunnable('', 'idle')).toBe(false)
    expect(documentAiMessages('  ')).toEqual([])
    expect(documentAiMessages('고쳐 주세요')).toEqual(['고쳐 주세요'])
  })

  it('a refusal: run_in_progress is the server message as it is; others the code words with it', () => {
    expect(documentAiFailure({ code: 'run_in_progress', status: 409, network: false, message: '다른 실행 중' })).toEqual({ key: 'run_in_progress', message: '다른 실행 중', verbatim: true })
    expect(documentAiFailure({ code: 'run_in_progress', status: 409, network: false, message: '' })).toEqual({ key: 'run_in_progress', message: '', verbatim: false })
    expect(documentAiFailure({ code: 'validation_failed', status: 422, network: false, message: 'bad' })).toEqual({ key: 'invalid', message: 'bad', verbatim: false })
    expect(documentAiFailure({ code: 'permission_denied', status: 403, network: false, message: '' })).toMatchObject({ key: 'forbidden' })
    expect(documentAiFailure({ code: null, status: null, network: true, message: '' })).toMatchObject({ key: 'network' })
  })
})

describe('the AI call window (flowgate.v02.0056 F2-2)', () => {
  it('opening reads the AIs and the live run only; the project default is picked; one window per document', async () => {
    await open()
    await open()
    expect(documentAiWindowList()).toHaveLength(1)
    expect(text(q('document-ai-heading'))).toContain('AI 호출')
    expect(text(q('document-ai-doc'))).toBe('레포트 · 작업레포트')
    expect((q('document-ai-provider') as HTMLSelectElement).value).toBe('aip_opus')
    expect(api.getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/providers', { project: 'flowgate' })
    expect(api.getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/active', { group_id: FLOW })
    expect(api.postRequest).not.toHaveBeenCalled()
    expect(text(q('document-ai-hint'))).toContain('검수·대응·카드 승인은 이어지지 않습니다')
  })

  it('[실행]: edit · single · the pinned AI · the opinion, no review loop; followed until it ends', async () => {
    // Only the poll timer is faked: flushPromises keeps its real setImmediate.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const finished = vi.fn()
    await open(finished)
    api.postRequest.mockResolvedValue({ data: { run_id: 'run_1', status: 'running' } })
    const opinion = q('document-ai-opinion') as HTMLTextAreaElement
    opinion.value = '테스트 절을 보강해 주세요'
    opinion.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    await click(q('document-ai-run'))
    expect(api.postRequest).toHaveBeenCalledTimes(1)
    const [path, request] = api.postRequest.mock.calls[0]
    expect(path).toBe('/api/v1/ai-invoke/start')
    expect(request).toEqual({
      project: 'flowgate',
      module: 'v02',
      group: '0056',
      doc_ref: DOC,
      action_scope: 'edit',
      mode: 'single',
      provider_id: 'aip_opus',
      provider_pinned: true,
      messages: ['테스트 절을 보강해 주세요'],
    })
    expect(request).not.toHaveProperty('document_review_loop')
    expect(q('document-ai-window')!.getAttribute('data-phase')).toBe('running')
    expect(text(q('document-ai-status'))).toBe('AI 작업 중 (run_1)')
    expect(documentAiRunning(DOC)).toBe(true)
    expect((q('document-ai-run') as HTMLButtonElement).disabled).toBe(true)

    // Still running at the first read.
    await vi.advanceTimersByTimeAsync(DOCUMENT_AI_POLL_MS)
    expect(api.getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/run_1')
    expect(documentAiRunning(DOC)).toBe(true)
    expect(finished).not.toHaveBeenCalled()

    runStatus = { status: 'finished', end_reason: 'completed' }
    await vi.advanceTimersByTimeAsync(DOCUMENT_AI_POLL_MS)
    await flushPromises()
    expect(documentAiRunOf(DOC)).toMatchObject({ runId: 'run_1', finished: true, endReason: 'completed' })
    expect(documentAiRunning(DOC)).toBe(false)
    expect(finished).toHaveBeenCalledTimes(1)
    expect(text(q('document-ai-status'))).toBe('AI 실행이 끝났습니다 (completed)')
    expect((q('document-ai-run') as HTMLButtonElement).disabled).toBe(false)
  })

  it('the group live run on this document marks the line as working', async () => {
    active = { active: true, run_id: 'run_x', status: 'running', doc_ref: DOC }
    await open()
    expect(documentAiRunning(DOC)).toBe(true)
    expect(text(q('document-ai-busy'))).toBe('이 문서에서 다른 AI 실행이 진행 중입니다.')
  })

  it('a live run started elsewhere is followed until it ends: the line and the note let go of it', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const finished = vi.fn()
    active = { active: true, run_id: 'run_x', status: 'running', doc_ref: DOC }
    await open(finished)
    expect(documentAiRunning(DOC)).toBe(true)
    expect(text(q('document-ai-busy'))).toBe('이 문서에서 다른 AI 실행이 진행 중입니다.')
    const activeReads = () => api.getRequest.mock.calls.filter(([path]) => path === '/api/v1/ai-invoke/active').length
    const runReads = () => api.getRequest.mock.calls.filter(([path]) => path === '/api/v1/ai-invoke/run_x').length
    expect(activeReads()).toBe(1)

    // Still running at the first read: nothing changes.
    await vi.advanceTimersByTimeAsync(DOCUMENT_AI_POLL_MS)
    expect(runReads()).toBe(1)
    expect(documentAiRunning(DOC)).toBe(true)

    // It ends in the other tab: the next read lets go of it and reads the group again.
    otherStatus = { status: 'finished', end_reason: 'completed' }
    active = { active: false }
    await vi.advanceTimersByTimeAsync(DOCUMENT_AI_POLL_MS)
    await flushPromises()
    expect(runReads()).toBe(2)
    expect(activeReads()).toBe(2)
    expect(documentAiRunning(DOC)).toBe(false)
    expect(q('document-ai-busy')).toBeNull()
    expect(finished).toHaveBeenCalledTimes(1)
    expect(documentAiRunOf(DOC)).toBeUndefined()
    expect((q('document-ai-run') as HTMLButtonElement).disabled).toBe(false)

    // Nothing is followed any more.
    await vi.advanceTimersByTimeAsync(DOCUMENT_AI_POLL_MS * 3)
    expect(runReads()).toBe(2)
  })

  it('a live run started elsewhere that is gone (404) is let go too, even before the group read catches up', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    active = { active: true, run_id: 'run_x', status: 'running', doc_ref: `${FLOW}.0099-TR` }
    await open()
    expect(documentAiRunning(`${FLOW}.0099-TR`)).toBe(true)
    expect(text(q('document-ai-busy'))).toBe('이 플로우에서 다른 AI 실행이 진행 중입니다.')
    otherStatus = 'gone'
    // The group read failing afterwards keeps what the run read said.
    api.getRequest.mockImplementation(async (path: string) => {
      if (path === '/api/v1/ai-invoke/run_x') throw { isAxiosError: true, response: { status: 404, data: { code: 'run_not_found' } } }
      throw { isAxiosError: true, response: { status: 500, data: {} } }
    })
    await vi.advanceTimersByTimeAsync(DOCUMENT_AI_POLL_MS)
    await flushPromises()
    expect(documentAiRunning(`${FLOW}.0099-TR`)).toBe(false)
    expect(q('document-ai-busy')).toBeNull()
  })

  it('409 run_in_progress: the server message as it is; nothing is followed', async () => {
    await open()
    api.postRequest.mockRejectedValue({ isAxiosError: true, response: { status: 409, data: { code: 'run_in_progress', message: '이 그룹에서 다른 AI 실행이 진행 중입니다 (run_9)', run_id: 'run_9' } } })
    await click(q('document-ai-run'))
    expect(text(q('document-ai-failure'))).toBe('이 그룹에서 다른 AI 실행이 진행 중입니다 (run_9)')
    expect(q('document-ai-failure')!.getAttribute('data-key')).toBe('run_in_progress')
    expect(documentAiRunOf(DOC)).toBeUndefined()
    expect(documentAiRunning(DOC)).toBe(false)
  })

  it('another refusal: the code words with the server message', async () => {
    await open()
    api.postRequest.mockRejectedValue({ isAxiosError: true, response: { status: 403, data: { code: 'permission_denied', message: '권한 없음' } } })
    await click(q('document-ai-run'))
    expect(text(q('document-ai-failure'))).toBe('AI를 실행할 권한이 없습니다. — 권한 없음')
  })
})

// flowgate.v02.0088 F15 (D0004 §3.2): the AI window's kind follows the language from the line's nameKey; the document
// title, the AI choices and the opinion stay as they are. Without a key, or with one the catalog does not have, the
// name as it came. The window stays keyed by docId: opening it again with a key is the same window.
describe('the kind name of the AI call window (0088 F15)', () => {
  const KEY = 'gui.inspector.taskCard.slot.report'
  for (const locale of ['ko', 'en', 'ja'] as const) {
    i18n.global.mergeLocaleMessage(locale, { gui: { inspector: { taskCard: taskMessages[locale] } } })
  }

  it('changes only the kind on a language switch, keeping the title, the opinion and the window', async () => {
    openDocumentAiWindow({ docId: DOC, groupId: FLOW, projectId: 'flowgate', name: '레포트', nameKey: KEY, title: '작업레포트 원제목' })
    await flushPromises()
    expect(text(q('document-ai-doc'))).toBe('레포트 · 작업레포트 원제목')
    expect(q('document-ai-doc')?.getAttribute('title')).toBe('레포트 · 작업레포트 원제목')
    const opinion = document.body.querySelector<HTMLTextAreaElement>('textarea')
    if (opinion) {
      opinion.value = '의견 원문'
      opinion.dispatchEvent(new Event('input'))
    }
    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(text(q('document-ai-doc'))).toBe('レポート · 작업레포트 원제목')
    expect(q('document-ai-doc')?.getAttribute('title')).toBe('レポート · 작업레포트 원제목')
    if (opinion) expect(opinion.value).toBe('의견 원문')
    openDocumentAiWindow({ docId: DOC, groupId: FLOW, projectId: 'flowgate', name: '레포트', nameKey: KEY, title: '작업레포트 원제목' })
    await flushPromises()
    expect(documentAiWindowList()).toHaveLength(1)
    expect(api.postRequest).not.toHaveBeenCalled()
  })

  it.each([
    ['no nameKey', {}],
    ['an unknown nameKey', { nameKey: 'gui.inspector.taskCard.slot.nope' }],
  ])('shows name as it came for %s', async (_label, extra) => {
    i18n.global.locale.value = 'en'
    openDocumentAiWindow({ docId: DOC, groupId: FLOW, projectId: 'flowgate', name: '레포트', title: '작업레포트', ...extra })
    await flushPromises()
    expect(text(q('document-ai-doc'))).toBe('레포트 · 작업레포트')
  })
}) // the kind name of the AI call window (0088 F15)

it('notifies the opener before submitting, permits guard refusal and reports start failure', async () => {
  const submitting = vi.fn(() => false), rejected = vi.fn()
  openDocumentAiWindow({ docId: DOC, groupId: FLOW, projectId: 'flowgate', name: 'Scenario', title: '', submitting, rejected })
  await flushPromises()
  await click(q('document-ai-run'))
  expect(submitting).toHaveBeenCalledTimes(1)
  expect(api.postRequest).not.toHaveBeenCalled()
  submitting.mockReturnValue(true)
  api.postRequest.mockImplementation(async () => { expect(submitting).toHaveBeenCalledTimes(2); throw { response: { status: 403, data: {} } } })
  await click(q('document-ai-run'))
  expect(api.postRequest).toHaveBeenCalledTimes(1)
  expect(rejected).toHaveBeenCalledTimes(1)
})
