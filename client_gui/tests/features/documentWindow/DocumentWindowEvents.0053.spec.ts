/**
 * flowgate.v02.0053 T#3 (rework) — an open document window follows the server through the real v0.2 path:
 * DocumentWindows as the App Shell mounts it, the real project store (Pinia) and the real project event hub
 * (features/project projectEvents.ts) on the fake EventSource of the shared Inspector test support. Only
 * `@/features/document` is mocked (the stored files and records below).
 * - A report that opened empty shows what the AI wrote once its document_explorer_refresh arrives, or once
 *   the run on its task finishes having reached it (ai_invoke_finished), and its state dot moves with it.
 * - A review elsewhere (doc_review_status_changed) moves the title line; another document's event reads nothing.
 * - An editor's unsaved text is kept when the document changes on the server.
 * - Opening the window again, or a reconnect of the stream, reads the body and the title line again.
 * - One subscription per window on the document's project stream; closing the window drops it.
 * Rework 2 — the server paths as they are:
 * - A run result is a new document: the card line is rebound to it and the empty report only gets
 *   `superseded_by` (card_documents/lines.py adopt_result), the finished run names the result
 *   (reached_doc_ids) and the run's group_view_refresh names the Flow. The window opened on the report
 *   follows the link: it shows, edits and saves the result; a window already open on the result is the
 *   one kept; unsaved editor text keeps its document.
 * - The v0.1 content save (PATCH /documents/content) sends no event: the open window reads again every
 *   DOCUMENT_WINDOW_POLL_MS (not while minimized; never over unsaved editor text; stopped on close).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DocumentWindows, closeAllDocumentWindows, documentWindowList, openDocumentWindow } from '@/features/documentWindow'
import {
  DOCUMENT_WINDOW_POLL_MS,
  DOCUMENT_WINDOW_REFRESH_COALESCE_MS,
} from '@/features/documentWindow/useDocumentWindowEvents'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../designer/inspector/support/projectEventStream'
import { setCurrentUser } from '@/core/auth/currentUser'

const recordRead = vi.fn()
const contentRead = vi.fn()
const contentSave = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentRead: (...args: unknown[]) => recordRead(...args),
    documentContentRead: (...args: unknown[]) => contentRead(...args),
    documentContentSave: (...args: unknown[]) => contentSave(...args),
  }
})

const { toDocumentRecord } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

registerAppMessages(i18n)

const FLOW = 'flowgate.v02.0053'
const REPORT = 'flowgate.v02.0053.0007-TR'
const EMPTY_REPORT = 'flowgate.v02.0053.0009-TR'
const OTHER = 'flowgate.v02.0053.0008-T'
/** The run result that supersedes EMPTY_REPORT (a different document id). */
const RESULT = 'flowgate.v02.0053.0012-TR'
const FRONT = '---\ntitle: 레포트\n---'

/** The stored files (doc_id → body; a missing entry is a file not written yet) and records. */
const files = new Map<string, string>()
const records = new Map<string, Record<string, unknown>>()
const record = (docId: string, fields: Record<string, unknown>) =>
  // flowgate.v02.0070 T#3: the view footer offers [편집] only to a signed-in person on an editable document.
  records.set(docId, { doc_id: docId, project_id: 'flowgate', group_id: FLOW, type_code: 'TR', is_editable: true, ...fields })

let stream: ProjectEventStream
let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentWindows()
  setCurrentUser({ username: 'worker', email: null, isAdmin: false })
  stream = installProjectEventStream()
  files.clear()
  records.clear()
  files.set(REPORT, `${FRONT}\n# 처음\n`)
  record(REPORT, { title: '작업레포트 — T#2', status: 'open', doc_review_status: 'pending_review' })
  record(EMPTY_REPORT, { title: '레포트', status: 'draft', doc_review_status: null })
  record(OTHER, { type_code: 'T', title: '작업지시', status: 'open', doc_review_status: null })
  recordRead.mockReset()
  contentRead.mockReset()
  contentSave.mockReset()
  recordRead.mockImplementation(async (docId: string) => {
    const row = records.get(docId)
    if (!row) throw { isAxiosError: true, response: { status: 404, data: {} } }
    return toDocumentRecord({ ...row })
  })
  contentRead.mockImplementation(async (docId: string) =>
    files.has(docId) ? { docId, content: files.get(docId)!, missing: false } : { docId, content: '', missing: true },
  )
  contentSave.mockImplementation(async (docId: string, content: string) => {
    files.set(docId, content)
  })
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n, createPinia()] }, attachTo: document.body })
})

afterEach(() => {
  vi.useRealTimers()
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  setCurrentUser(null)
  removeProjectEventStream()
  document.body.querySelectorAll('.ui-modal-overlay').forEach((node) => node.remove())
})

// --- helpers (the windows are teleported to <body>) ----------------------------------------------------

function panelOf(docId: string): HTMLElement {
  const panel = [...document.body.querySelectorAll<HTMLElement>('.ui-modeless-panel')].find((node) =>
    node.querySelector(`[data-test="document-window"][data-doc="${docId}"]`),
  )
  if (!panel) throw new Error(`no window for ${docId}`)
  return panel
}
const q = (root: ParentNode, id: string) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const bodyText = (docId: string) => q(panelOf(docId), 'md-viewer-content')?.textContent ?? null
const tone = (docId: string) => q(panelOf(docId), 'document-window-dot')!.getAttribute('data-tone')
const reads = (fn: typeof recordRead, docId: string) => fn.mock.calls.filter(([id]) => id === docId).length

async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}

/** Opens a window and lets its first reads and its stream subscription land; the server accepts the stream. */
async function open(docId: string, mode: 'view' | 'edit' = 'view') {
  openDocumentWindow(docId, mode)
  await flushPromises()
  stream.open()
  return panelOf(docId)
}

/** Past the title line coalescing and the MD Viewer's own debounce, then every read answered. */
async function settle() {
  await new Promise((resolve) => setTimeout(resolve, DOCUMENT_WINDOW_REFRESH_COALESCE_MS + 250))
  await flushPromises()
}

/** The server event of an AI / editor write of the document (inbox edit, documents.py). */
const updated = (docId: string, revision: number) =>
  stream.emit('document_explorer_refresh', {
    docId,
    groupId: FLOW,
    payload: { operation: 'updated', doc_id: docId, status: 'open', revision_no: revision },
  })

const textarea = (docId: string) => q(panelOf(docId), 'document-window-textarea') as HTMLTextAreaElement
const shownOf = (docId: string) => q(panelOf(docId), 'document-window')!.getAttribute('data-shown')
const h1 = (docId: string) => q(panelOf(docId), 'md-viewer-content')?.querySelector('h1')?.textContent ?? null

async function type(docId: string, text: string) {
  const area = textarea(docId)
  area.value = text
  area.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}

/**
 * The server's run result path: a new result document is written, the card line is rebound to it and the
 * empty report is marked superseded by it (its own body stays empty); then the run's end is broadcast —
 * ai_invoke_finished naming the result, group_view_refresh naming the Flow (ai_invoke/terminal.py publish).
 */
function runResultSupersedes(empty: string, result: string, body: string) {
  files.set(result, `${FRONT}\n${body}`)
  record(result, { title: '작업레포트 — T#3', status: 'open', doc_review_status: 'pending_review' })
  records.set(empty, { ...records.get(empty)!, superseded_by: result })
  stream.emit('ai_invoke_finished', {
    docId: OTHER,
    groupId: FLOW,
    payload: { doc_ref: OTHER, group_id: FLOW, outcome: 'reached', reached_doc_ids: [result] },
  })
  stream.emit('group_view_refresh', { docId: OTHER, groupId: FLOW, payload: { group_id: FLOW, reason: 'ai_invoke_finished' } })
}

/** Fake timers that leave flushPromises' scheduler (setImmediate) real. */
function fakeTime() {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
}

describe('document window follows the server on the v0.2 project event hub (flowgate.v02.0053 T#3 rework)', () => {
  it('subscribes once per window to its document project stream and drops it when the window closes', async () => {
    const panel = await open(REPORT)
    const [connection] = stream.connections()
    expect(stream.connections()).toHaveLength(1)
    expect(connection.url).toContain('project=flowgate')
    expect(connection.listened('document_explorer_refresh')).toBe(1)
    expect(connection.listened('doc_review_status_changed')).toBe(1)
    await click(panel.querySelector<HTMLElement>('[aria-label="Close"]'))
    expect(stream.connections()).toHaveLength(0)
  })

  it('a report that opened empty shows what the AI wrote, and its state dot moves, once the update arrives', async () => {
    await open(EMPTY_REPORT)
    expect(bodyText(EMPTY_REPORT)).toBe('')
    expect(tone(EMPTY_REPORT)).toBe('neutral')

    // The AI run writes the report and submits it for review.
    files.set(EMPTY_REPORT, `${FRONT}\n# AI 결과\n\n채워진 본문.\n`)
    record(EMPTY_REPORT, { title: '작업레포트 — T#3', status: 'open', doc_review_status: 'pending_review' })
    updated(EMPTY_REPORT, 1)
    await settle()

    expect(q(panelOf(EMPTY_REPORT), 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('AI 결과')
    expect(bodyText(EMPTY_REPORT)).toContain('채워진 본문.')
    expect(tone(EMPTY_REPORT)).toBe('warning')
    expect(q(panelOf(EMPTY_REPORT), 'document-window-title')!.textContent).toBe('작업레포트 — T#3')
  })

  it('an AI run on the task that wrote the report (ai_invoke_finished, reached_doc_ids) fills the open empty report', async () => {
    await open(EMPTY_REPORT)
    files.set(EMPTY_REPORT, `${FRONT}\n# 작업 실행 결과\n`)
    record(EMPTY_REPORT, { title: '레포트', status: 'open', doc_review_status: 'pending_review' })
    // Broadcast to every viewer; its doc_id is the task the run was started on.
    stream.emit('ai_invoke_finished', {
      docId: OTHER,
      groupId: FLOW,
      payload: { doc_ref: OTHER, outcome: 'reached', reached_doc_ids: [EMPTY_REPORT] },
    })
    await settle()

    expect(q(panelOf(EMPTY_REPORT), 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('작업 실행 결과')
    expect(tone(EMPTY_REPORT)).toBe('warning')
  })

  it('a review elsewhere moves the title line without reading the body; another document event reads nothing', async () => {
    await open(REPORT)
    expect(tone(REPORT)).toBe('warning')
    const contentReads = reads(contentRead, REPORT)
    const recordReads = reads(recordRead, REPORT)

    stream.emit('doc_review_status_changed', { docId: OTHER, payload: { new_status: 'approved' } })
    updated(OTHER, 3)
    await settle()
    expect(reads(recordRead, REPORT)).toBe(recordReads)
    expect(reads(contentRead, REPORT)).toBe(contentReads)

    record(REPORT, { title: '작업레포트 — T#2', status: 'closed', doc_review_status: 'approved' })
    stream.emit('doc_review_status_changed', { docId: REPORT, payload: { new_status: 'approved' } })
    await settle()
    expect(tone(REPORT)).toBe('success')
    expect(reads(recordRead, REPORT)).toBe(recordReads + 1)
    expect(reads(contentRead, REPORT)).toBe(contentReads)
  })

  it('an editor keeps its unsaved text when the document changes on the server; the view then shows the server body', async () => {
    const panel = await open(REPORT, 'edit')
    const area = q(panel, 'document-window-textarea') as HTMLTextAreaElement
    area.value = '# 처음\n\n저장 안 한 글\n'
    area.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    const contentReads = reads(contentRead, REPORT)

    files.set(REPORT, `${FRONT}\n# 서버에서 바뀜\n`)
    record(REPORT, { title: '작업레포트 — T#2', status: 'open', doc_review_status: 'rejected' })
    updated(REPORT, 2)
    await settle()

    expect((q(panel, 'document-window-textarea') as HTMLTextAreaElement).value).toBe('# 처음\n\n저장 안 한 글\n')
    expect(reads(contentRead, REPORT)).toBe(contentReads)
    expect(tone(REPORT)).toBe('danger')
    expect(contentSave).not.toHaveBeenCalled()

    // Leaving the editor (버리기) shows what the server has now.
    await click(q(panel, 'document-window-cancel'))
    await click([...document.body.querySelectorAll<HTMLElement>('.ui-confirm-actions .ui-btn')][1])
    expect(q(panel, 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('서버에서 바뀜')
  })

  it('opening the window again reads the body and the title line again (an update that was missed)', async () => {
    await open(EMPTY_REPORT)
    expect(reads(contentRead, EMPTY_REPORT)).toBe(1)
    expect(reads(recordRead, EMPTY_REPORT)).toBe(1)

    files.set(EMPTY_REPORT, `${FRONT}\n# 다시 열어 보임\n`)
    record(EMPTY_REPORT, { title: '레포트', status: 'open', doc_review_status: 'approved' })
    openDocumentWindow(EMPTY_REPORT)
    await settle()

    expect(reads(contentRead, EMPTY_REPORT)).toBe(2)
    expect(reads(recordRead, EMPTY_REPORT)).toBe(2)
    expect(q(panelOf(EMPTY_REPORT), 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('다시 열어 보임')
    expect(tone(EMPTY_REPORT)).toBe('success')
  })

  it('a reconnect of the stream (events may be missing) reads the body and the title line again', async () => {
    await open(EMPTY_REPORT)
    files.set(EMPTY_REPORT, `${FRONT}\n# 끊긴 사이에 채워짐\n`)
    record(EMPTY_REPORT, { title: '레포트', status: 'open', doc_review_status: 'pending_review' })
    stream.reconnect()
    await settle()

    expect(q(panelOf(EMPTY_REPORT), 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('끊긴 사이에 채워짐')
    expect(tone(EMPTY_REPORT)).toBe('warning')
  })
})

describe('a report opened before its run follows the result that superseded it (flowgate.v02.0053 T#3 rework 2)', () => {
  it('shows the result body, title and state after the run end, and again when the report is opened again', async () => {
    await open(EMPTY_REPORT)
    expect(bodyText(EMPTY_REPORT)).toBe('')
    expect(tone(EMPTY_REPORT)).toBe('neutral')

    runResultSupersedes(EMPTY_REPORT, RESULT, '# AI 결과\n\n채워진 본문.\n')
    await settle()

    // The report's own file is still empty: what shows is the result document.
    expect(files.has(EMPTY_REPORT)).toBe(false)
    expect(shownOf(EMPTY_REPORT)).toBe(RESULT)
    expect(h1(EMPTY_REPORT)).toBe('AI 결과')
    expect(bodyText(EMPTY_REPORT)).toContain('채워진 본문.')
    expect(q(panelOf(EMPTY_REPORT), 'document-window-title')!.textContent).toBe('작업레포트 — T#3')
    expect(tone(EMPTY_REPORT)).toBe('warning')
    expect(reads(contentRead, RESULT)).toBeGreaterThan(0)

    // Opening the report again (its card line, an old link) stays on the result.
    openDocumentWindow(EMPTY_REPORT)
    await settle()
    expect(documentWindowList()).toHaveLength(1)
    expect(h1(EMPTY_REPORT)).toBe('AI 결과')
    // So does opening the result itself: the same window, not a second one.
    openDocumentWindow(RESULT)
    await settle()
    expect(documentWindowList().map((win) => [win.docId, win.shown])).toEqual([[EMPTY_REPORT, RESULT]])
  })

  it('a later ✎ edits and saves the result, never the superseded report', async () => {
    await open(EMPTY_REPORT)
    runResultSupersedes(EMPTY_REPORT, RESULT, '# AI 결과\n')
    await settle()

    await click(q(panelOf(EMPTY_REPORT), 'document-window-edit'))
    expect(textarea(EMPTY_REPORT).value).toBe('# AI 결과\n')
    await type(EMPTY_REPORT, '# AI 결과\n\n고침\n')
    await click(q(panelOf(EMPTY_REPORT), 'document-window-save'))

    expect(contentSave).toHaveBeenCalledTimes(1)
    expect(contentSave).toHaveBeenCalledWith(RESULT, `${FRONT}\n# AI 결과\n\n고침\n`)
    await settle()
    expect(bodyText(EMPTY_REPORT)).toContain('고침')
  })

  it('a clean editor opened on the empty report moves to the result', async () => {
    await open(EMPTY_REPORT, 'edit')
    expect(textarea(EMPTY_REPORT).value).toBe('')
    runResultSupersedes(EMPTY_REPORT, RESULT, '# AI 결과\n')
    await settle()

    expect(shownOf(EMPTY_REPORT)).toBe(RESULT)
    expect(textarea(EMPTY_REPORT).value).toBe('# AI 결과\n')
    await type(EMPTY_REPORT, '# AI 결과\n덧붙임\n')
    await click(q(panelOf(EMPTY_REPORT), 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(RESULT, `${FRONT}\n# AI 결과\n덧붙임\n`)
  })

  it('unsaved editor text keeps its document; dropping it shows the result', async () => {
    await open(EMPTY_REPORT, 'edit')
    await type(EMPTY_REPORT, '쓰던 글\n')
    runResultSupersedes(EMPTY_REPORT, RESULT, '# AI 결과\n')
    await settle()

    expect(textarea(EMPTY_REPORT).value).toBe('쓰던 글\n')
    expect(shownOf(EMPTY_REPORT)).toBe(EMPTY_REPORT)
    expect(contentSave).not.toHaveBeenCalled()

    await click(q(panelOf(EMPTY_REPORT), 'document-window-cancel'))
    await click([...document.body.querySelectorAll<HTMLElement>('.ui-confirm-actions .ui-btn')][1])
    await settle()
    expect(shownOf(EMPTY_REPORT)).toBe(RESULT)
    expect(h1(EMPTY_REPORT)).toBe('AI 결과')
  })

  it('a window already open on the result is the one kept (no second window of the same document)', async () => {
    await open(EMPTY_REPORT)
    // The result exists already (e.g. opened from another card line) and has its own window.
    files.set(RESULT, `${FRONT}\n# AI 결과\n`)
    record(RESULT, { title: '작업레포트 — T#3', status: 'open', doc_review_status: 'pending_review' })
    await open(RESULT, 'edit')
    await type(RESULT, '# AI 결과\n결과 창에서 쓰는 중\n')
    expect(documentWindowList()).toHaveLength(2)

    runResultSupersedes(EMPTY_REPORT, RESULT, '# AI 결과\n')
    await settle()

    expect(documentWindowList().map((win) => [win.docId, win.shown, win.mode])).toEqual([[RESULT, RESULT, 'edit']])
    expect(textarea(RESULT).value).toBe('# AI 결과\n결과 창에서 쓰는 중\n')
  })

  it('a result that is itself superseded (its next revision) is followed to the end of the chain', async () => {
    const NEXT = 'flowgate.v02.0053.0014-TR'
    await open(EMPTY_REPORT)
    runResultSupersedes(EMPTY_REPORT, RESULT, '# 첫 결과\n')
    await settle()
    expect(h1(EMPTY_REPORT)).toBe('첫 결과')

    files.set(NEXT, `${FRONT}\n# 다시 낸 결과\n`)
    record(NEXT, { title: '작업레포트 — T#3', status: 'open', doc_review_status: 'revised' })
    records.set(RESULT, { ...records.get(RESULT)!, superseded_by: NEXT })
    stream.emit('group_view_refresh', { groupId: FLOW, payload: { group_id: FLOW, reason: 'ai_invoke_finished' } })
    await settle()

    expect(shownOf(EMPTY_REPORT)).toBe(NEXT)
    expect(h1(EMPTY_REPORT)).toBe('다시 낸 결과')
  })

  it("another Flow's refresh reads nothing; a cancelled result is not followed", async () => {
    await open(EMPTY_REPORT)
    const recordReads = reads(recordRead, EMPTY_REPORT)
    stream.emit('group_view_refresh', { groupId: 'flowgate.v02.0052', payload: { group_id: 'flowgate.v02.0052' } })
    await settle()
    expect(reads(recordRead, EMPTY_REPORT)).toBe(recordReads)

    record(RESULT, { title: '취소됨', status: 'cancelled' })
    records.set(EMPTY_REPORT, { ...records.get(EMPTY_REPORT)!, superseded_by: RESULT })
    stream.emit('group_view_refresh', { groupId: FLOW, payload: { group_id: FLOW } })
    await settle()
    expect(reads(recordRead, EMPTY_REPORT)).toBe(recordReads + 1)
    expect(shownOf(EMPTY_REPORT)).toBe(EMPTY_REPORT)
  })
})

describe('an open window reads again on a period, for writes that send no event (flowgate.v02.0053 T#3 rework 2)', () => {
  /** A save from another tab: PATCH /documents/content writes the file and the row, and broadcasts nothing. */
  function savedElsewhere(docId: string, body: string, fields: Record<string, unknown> = {}) {
    files.set(docId, `${FRONT}\n${body}`)
    records.set(docId, { ...records.get(docId)!, ...fields })
  }

  it('shows a body saved elsewhere within one period, with no event at all', async () => {
    fakeTime()
    await open(REPORT)
    expect(h1(REPORT)).toBe('처음')

    savedElsewhere(REPORT, '# 다른 탭에서 저장\n', { doc_review_status: 'approved' })
    await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS - 1)
    expect(h1(REPORT)).toBe('처음')
    await vi.advanceTimersByTimeAsync(1 + DOCUMENT_WINDOW_REFRESH_COALESCE_MS + 200)
    await flushPromises()

    expect(h1(REPORT)).toBe('다른 탭에서 저장')
    expect(tone(REPORT)).toBe('success')
  })

  it('also follows a supersede link that arrived without any event', async () => {
    fakeTime()
    await open(EMPTY_REPORT)
    files.set(RESULT, `${FRONT}\n# 조용히 바뀐 결과\n`)
    record(RESULT, { title: '작업레포트 — T#3', status: 'open', doc_review_status: 'pending_review' })
    records.set(EMPTY_REPORT, { ...records.get(EMPTY_REPORT)!, superseded_by: RESULT })
    await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS * 2)
    await flushPromises()

    expect(shownOf(EMPTY_REPORT)).toBe(RESULT)
    expect(h1(EMPTY_REPORT)).toBe('조용히 바뀐 결과')
  })

  it('never replaces unsaved editor text', async () => {
    fakeTime()
    await open(REPORT, 'edit')
    await type(REPORT, '# 처음\n\n저장 안 한 글\n')
    savedElsewhere(REPORT, '# 다른 탭에서 저장\n')
    await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS * 3)
    await flushPromises()

    expect(textarea(REPORT).value).toBe('# 처음\n\n저장 안 한 글\n')
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('reads nothing while minimized and stops when the window closes', async () => {
    fakeTime()
    const panel = await open(REPORT)
    documentWindowList()[0].minimized = true
    await flushPromises()
    const contentReads = reads(contentRead, REPORT)
    const recordReads = reads(recordRead, REPORT)

    await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS * 3)
    await flushPromises()
    expect(reads(contentRead, REPORT)).toBe(contentReads)
    expect(reads(recordRead, REPORT)).toBe(recordReads)

    documentWindowList()[0].minimized = false
    await flushPromises()
    await click(panel.querySelector<HTMLElement>('[aria-label="Close"]'))
    expect(documentWindowList()).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS * 3)
    await flushPromises()
    expect(reads(contentRead, REPORT)).toBe(contentReads)
    expect(reads(recordRead, REPORT)).toBe(recordReads)
  })
})
