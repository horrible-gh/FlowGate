/**
 * flowgate.v02.0056 F1 — a document's 검수·반려 이력 window (0056 CH §2): the v0.1 '검수·반려 전체보기' dialog
 * brought to v0.2 on ModelessShell, mounted by DocumentWindows beside the document windows, with
 * `@/features/document` mocked. One timeline of rejections (the AI response under each) and AI reviews,
 * newest first; the 전체 / 반려 / AI 검수 filter with its counts; every entry folds to a short preview; a
 * failed read shows its reason with a retry; a second open request is the same window, read again; the
 * window closes on its own × and with the document windows.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import {
  DocumentWindows,
  closeAllDocumentWindows,
  documentHistoryWindowList,
  documentWindowList,
  openDocumentHistoryWindow,
} from '@/features/documentWindow'
import { documentHistoryWhen } from '@/features/documentWindow/documentHistoryModel'

const historyRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentHistoryRead: (...args: unknown[]) => historyRead(...args),
  }
})

const { toDocumentHistory } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

registerAppMessages(i18n)

const DOC = 'flowgate.v02.0056.0007-TR'
const BARE = 'flowgate.v02.0056.0008-TR'
const GONE = 'flowgate.v02.0056.0099-TR'

const AT = {
  review1: '2026-10-09T15:00:00+09:00',
  reject1: '2026-10-09T15:05:00+09:00',
  response1: '2026-10-09T16:00:00+09:00',
  review2: '2026-10-09T17:00:00+09:00',
  reject2: '2026-10-09T18:00:00+09:00',
}

const base = (docId: string) => ({
  doc_id: docId,
  project_id: 'flowgate',
  group_id: 'flowgate.v02.0056',
  type_code: 'TR',
  title: '작업레포트 — T#2',
  status: 'open',
  doc_review_status: 'rejected',
})

/**
 * The v0.1 document responses (the history parts as the server sends them — flowgate.v02.0056 F2-2: the AI reviews
 * newest first, the rejections oldest first, O3 0009-TR §4-4).
 */
const RESPONSES: Record<string, Record<string, unknown>> = {
  [DOC]: {
    ...base(DOC),
    ai_review_history: [
      {
        id: 2,
        revision_no: 2,
        verdict: 'pass',
        finding_count: 0,
        findings: [],
        comment: null,
        review_intent: 'rerun',
        superseded_review_id: 1,
        review_provider: { requested_provider_id: 'p-a', actual_provider_id: 'p-b', actual_provider_name: 'B' },
        reviewed_at: AT.review2,
      },
      {
        id: 1,
        revision_no: 1,
        verdict: 'issues',
        finding_count: 2,
        findings: [{ locus: '§2', note: '테스트 누락' }, { note: '오타' }],
        comment: '보완 필요',
        review_intent: 'normal',
        reviewer_name: 'Codex',
        review_provider: { requested_provider_id: 'p-codex', actual_provider_id: 'p-codex', actual_provider_name: 'Codex1' },
        reviewed_at: AT.review1,
      },
    ],
    rejection_history: [
      {
        rejection_id: 'r1',
        reason: '구조 검사 누락',
        rejected_at: AT.reject1,
        review_id: 1,
        rejection_provider: { ai_provider_name: 'Codex1' },
        ai_response: '검사를 추가했습니다.',
        responded_at: AT.response1,
        response_provider: { ai_provider_id: 'p-opus' },
      },
      { rejection_id: 'r2', reason: '사용자 반려', rejected_at: AT.reject2, rejected_by: 'u1' },
    ],
  },
  [BARE]: { ...base(BARE), title: '레포트', ai_review_history: [], rejection_history: [] },
}

const axiosError = (status: number) => ({ isAxiosError: true, response: { status, data: { detail: 'x' } } })

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentWindows()
  historyRead.mockReset()
  historyRead.mockImplementation(async (docId: string) => {
    if (!RESPONSES[docId]) throw axiosError(404)
    return toDocumentHistory(RESPONSES[docId])
  })
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
})

// --- DOM helpers (the windows are teleported to <body>) ---------------------------------------------

const panels = () => [...document.body.querySelectorAll<HTMLElement>('.ui-modeless-panel')]
function panelOf(docId: string): HTMLElement {
  const panel = panels().find((node) => node.querySelector(`[data-test="history-window"][data-doc="${docId}"]`))
  if (!panel) throw new Error(`no history window for ${docId}`)
  return panel
}
const q = (root: ParentNode, id: string) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const qa = (root: ParentNode, id: string) => [...root.querySelectorAll<HTMLElement>(`[data-test="${id}"]`)]
const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? null
async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}
async function open(docId: string) {
  openDocumentHistoryWindow(docId)
  await flushPromises()
  return panelOf(docId)
}
const entries = (panel: HTMLElement) => qa(panel, 'history-entry').map((node) => [node.getAttribute('data-kind'), node.getAttribute('data-key')])
const entry = (panel: HTMLElement, key: string) => panel.querySelector<HTMLElement>(`[data-test="history-entry"][data-key="${key}"]`)!
const counts = (panel: HTMLElement) => qa(panel, 'history-filter-count').map((node) => node.textContent)

describe('review history window (flowgate.v02.0056 F1)', () => {
  it('opens as its own modeless window beside the document windows: the heading, the timeline newest first, the filter counts', async () => {
    const panel = await open(DOC)
    expect(historyRead).toHaveBeenCalledWith(DOC)
    expect([text(panel.querySelector('.history-window-kind')), text(q(panel, 'history-window-title'))]).toEqual(['검수·반려 이력', '작업레포트 — T#2'])
    const window = q(panel, 'history-window')!
    expect([window.getAttribute('data-state'), window.getAttribute('data-filter')]).toEqual(['ready', 'all'])
    expect(entries(panel)).toEqual([
      ['reject', 'reject:r2'],
      ['ai_review', 'ai_review:2'],
      ['reject', 'reject:r1'],
      ['ai_review', 'ai_review:1'],
    ])
    expect(qa(panel, 'history-filter').map((node) => node.getAttribute('data-filter'))).toEqual(['all', 'reject', 'ai_review'])
    expect(counts(panel)).toEqual(['4', '2', '2'])
    expect(documentHistoryWindowList().map((win) => win.docId)).toEqual([DOC])
    expect(documentWindowList()).toEqual([])
  })

  it('a rejection: who · when · the reason, the AI response under it; a person\'s rejection says 사용자 and has no response', async () => {
    const panel = await open(DOC)
    const byAi = entry(panel, 'reject:r1')
    expect(text(q(byAi, 'history-who'))).toBe('AI · Codex1')
    expect(text(q(byAi, 'history-when'))).toBe(documentHistoryWhen(AT.reject1))
    expect(text(q(byAi, 'history-reason'))).toBe('구조 검사 누락')
    const response = q(byAi, 'history-response')!
    expect(text(response)).toContain('AI 대응 AI · p-opus')
    expect(text(response)).toContain(documentHistoryWhen(AT.response1))
    expect(text(q(response, 'history-response-text'))).toBe('검사를 추가했습니다.')

    const byUser = entry(panel, 'reject:r2')
    expect(text(q(byUser, 'history-who'))).toBe('사용자')
    expect(text(q(byUser, 'history-reason'))).toBe('사용자 반려')
    expect(q(byUser, 'history-response')).toBeNull()
  })

  it('an AI review: verdict · intent · rev · when, the reviewer, the comment and the findings; a rerun names what it replaced and a provider mismatch', async () => {
    const panel = await open(DOC)
    const first = entry(panel, 'ai_review:1')
    expect(text(q(first, 'history-verdict'))).toBe('지적 2건')
    expect(text(q(first, 'history-intent'))).toBe('최초 검수')
    expect(text(q(first, 'history-rev'))).toBe('rev 1')
    expect(text(q(first, 'history-when'))).toBe(documentHistoryWhen(AT.review1))
    expect(text(q(first, 'history-reviewer'))).toBe('검수 AI: Codex1')
    expect(q(first, 'history-reviewer')!.classList.contains('is-mismatch')).toBe(false)
    expect(text(q(first, 'history-comment'))).toBe('보완 필요')
    expect([...q(first, 'history-findings')!.querySelectorAll('li')].map((node) => text(node))).toEqual(['§2 — 테스트 누락', '오타'])
    expect(q(first, 'history-supersedes')).toBeNull()

    const rerun = entry(panel, 'ai_review:2')
    expect(text(q(rerun, 'history-verdict'))).toBe('통과')
    expect(text(q(rerun, 'history-intent'))).toBe('재검수')
    expect(q(rerun, 'history-reviewer')!.classList.contains('is-mismatch')).toBe(true)
    expect([...q(rerun, 'history-reviewer')!.children].map((node) => text(node))).toEqual(['검수 AI: B', '요청한 AI: p-a'])
    expect(text(q(rerun, 'history-supersedes'))).toBe(`대체 대상: ${documentHistoryWhen(AT.review1)}`)
    expect(q(rerun, 'history-comment')).toBeNull()
    expect(q(rerun, 'history-findings')).toBeNull()
  })

  it('the filter: 반려 shows the rejections, AI 검수 the reviews, 전체 everything; the counts stay', async () => {
    const panel = await open(DOC)
    const filter = (name: string) => panel.querySelector<HTMLElement>(`[data-test="history-filter"][data-filter="${name}"]`)
    await click(filter('reject'))
    expect(q(panel, 'history-window')!.getAttribute('data-filter')).toBe('reject')
    expect(filter('reject')!.getAttribute('aria-pressed')).toBe('true')
    expect(entries(panel).map(([kind]) => kind)).toEqual(['reject', 'reject'])
    await click(filter('ai_review'))
    expect(entries(panel).map(([kind]) => kind)).toEqual(['ai_review', 'ai_review'])
    expect(counts(panel)).toEqual(['4', '2', '2'])
    await click(filter('all'))
    expect(entries(panel)).toHaveLength(4)
  })

  it('an entry folds to a short preview and unfolds; the AI response folds on its own', async () => {
    const panel = await open(DOC)
    const rejection = entry(panel, 'reject:r1')
    const fold = q(rejection, 'history-fold')!
    expect([fold.getAttribute('aria-expanded'), fold.getAttribute('title')]).toEqual(['true', '접기'])
    await click(fold)
    expect([fold.getAttribute('aria-expanded'), fold.getAttribute('title')]).toEqual(['false', '펼치기'])
    expect(q(rejection, 'history-reason')!.classList.contains('is-folded')).toBe(true)
    expect(q(rejection, 'history-response-text')!.classList.contains('is-folded')).toBe(false)
    await click(q(rejection, 'history-fold-response'))
    expect(q(rejection, 'history-response-text')!.classList.contains('is-folded')).toBe(true)
    await click(fold)
    expect(q(rejection, 'history-reason')!.classList.contains('is-folded')).toBe(false)

    const review = entry(panel, 'ai_review:1')
    await click(q(review, 'history-fold'))
    expect(q(review, 'history-comment')!.classList.contains('is-folded')).toBe(true)
    // A review without a comment has nothing to fold.
    expect(q(entry(panel, 'ai_review:2'), 'history-fold')).toBeNull()
  })

  it('a document without history: the counts are 0 and the list says so', async () => {
    const panel = await open(BARE)
    expect(text(q(panel, 'history-window-title'))).toBe('레포트')
    expect(counts(panel)).toEqual(['0', '0', '0'])
    expect(text(q(panel, 'history-empty'))).toBe('이력이 없습니다.')
    expect(q(panel, 'history-list')).toBeNull()
  })

  it('a failed read shows the reason with a retry; the retry reads again and shows the history', async () => {
    historyRead.mockImplementationOnce(async () => {
      throw axiosError(500)
    })
    const panel = await open(DOC)
    expect(q(panel, 'history-window')!.getAttribute('data-state')).toBe('error')
    expect(q(panel, 'history-error')).not.toBeNull()
    expect(q(panel, 'history-list')).toBeNull()
    await click(q(panel, 'history-retry'))
    expect(historyRead).toHaveBeenCalledTimes(2)
    expect(q(panel, 'history-window')!.getAttribute('data-state')).toBe('ready')
    expect(entries(panel)).toHaveLength(4)
  })

  it('a document that no longer exists: the not-found reason', async () => {
    const panel = await open(GONE)
    expect(q(panel, 'history-window')!.getAttribute('data-state')).toBe('error')
    expect(text(q(panel, 'history-error'))).toContain('문서를 찾을 수 없습니다')
    // The window stays usable: the heading falls back to the id.
    expect(text(q(panel, 'history-window-title'))).toBe(GONE)
  })

  it('a second open request is the same window, restored from minimized and read again; a failed read again keeps what is shown', async () => {
    const panel = await open(DOC)
    await click(panel.querySelector<HTMLElement>('[aria-label="Minimize"]'))
    expect(documentHistoryWindowList()[0].minimized).toBe(true)
    historyRead.mockImplementationOnce(async () => {
      throw axiosError(500)
    })
    await open(DOC)
    expect(panels()).toHaveLength(1)
    expect(documentHistoryWindowList()[0].minimized).toBe(false)
    expect(historyRead).toHaveBeenCalledTimes(2)
    expect(q(panelOf(DOC), 'history-window')!.getAttribute('data-state')).toBe('ready')
    expect(entries(panelOf(DOC))).toHaveLength(4)
  })

  it('× closes the window; several documents are several windows; closeAllDocumentWindows closes them all', async () => {
    const panel = await open(DOC)
    await open(BARE)
    expect(panels()).toHaveLength(2)
    await click(panel.querySelector<HTMLElement>('[aria-label="Close"]'))
    expect(documentHistoryWindowList().map((win) => win.docId)).toEqual([BARE])
    expect(panels()).toHaveLength(1)
    closeAllDocumentWindows()
    await flushPromises()
    expect(panels()).toHaveLength(0)
  })

  it('en: the wording of the window', async () => {
    i18n.global.locale.value = 'en'
    const panel = await open(DOC)
    expect(text(panel.querySelector('.history-window-kind'))).toBe('Review history')
    expect(qa(panel, 'history-filter').map((node) => text(node))).toEqual(['All 4', 'Rejections 2', 'AI reviews 2'])
    expect(text(q(entry(panel, 'ai_review:2'), 'history-verdict'))).toBe('Pass')
    expect(text(q(entry(panel, 'ai_review:1'), 'history-verdict'))).toBe('2 finding(s)')
    expect(text(q(entry(panel, 'reject:r2'), 'history-who'))).toBe('User')
  })
})
