/**
 * flowgate.v02.0053 T#3 — the document window as the App Shell mounts it (DocumentWindows) with
 * `@/features/document` mocked: one modeless window per document in this tab; the title line is name ·
 * state dot · ✎ · minimize · close; the body is the MD Viewer. ✎ turns the same window to a text editor of
 * the body (frontmatter kept aside and put back), 저장 writes it with the v0.1 content save and goes back to
 * the view, 취소 drops it; unsaved text is asked about once (취소 / close). A report before its run (no file
 * yet) opens as an empty document and edits the same way. Several windows stack; the same document twice is
 * one window.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import {
  DocumentWindows,
  closeAllDocumentWindows,
  documentWindowList,
  openDocumentWindow,
} from '@/features/documentWindow'
import { setCurrentUser } from '@/core/auth/currentUser'

const recordRead = vi.fn()
const contentRead = vi.fn()
const contentSave = vi.fn()
const diagnose = vi.fn()
const recoverBody = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentRead: (...args: unknown[]) => recordRead(...args),
    documentContentRead: (...args: unknown[]) => contentRead(...args),
    documentContentSave: (...args: unknown[]) => contentSave(...args),
    bodyRecoveryDiagnose: (...args: unknown[]) => diagnose(...args),
    bodyRecoveryRecover: (...args: unknown[]) => recoverBody(...args),
  }
})

const { toDocumentRecord } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

registerAppMessages(i18n)

const REPORT = 'flowgate.v02.0053.0007-TR'
const EMPTY_REPORT = 'flowgate.v02.0053.0009-TR'
const ORDER = 'flowgate.v02.0053.0008-T'
const GONE = 'flowgate.v02.0053.0099-TR'
/** flowgate.v02.0070 T#3: a document whose body cannot be read (file not found), and one the person may not edit. */
const UNREADABLE = 'flowgate.v02.0053.0010-R'
const LOCKED = 'flowgate.v02.0053.0011-R'

const FRONT = '---\ntitle: 작업레포트 — T#2\n---'

/** The stored files (doc_id → body); a missing entry is a file not written yet. */
const files = new Map<string, string>()
const RECORDS: Record<string, unknown> = {
  [REPORT]: { doc_id: REPORT, type_code: 'TR', title: '작업레포트 — T#2', status: 'open', doc_review_status: 'pending_review', is_editable: true },
  [EMPTY_REPORT]: { doc_id: EMPTY_REPORT, type_code: 'TR', title: '레포트', status: 'draft', doc_review_status: null, is_editable: true },
  [ORDER]: { doc_id: ORDER, type_code: 'T', title: '작업지시 — T#3', status: 'closed', doc_review_status: 'approved', is_editable: true },
  [UNREADABLE]: { doc_id: UNREADABLE, type_code: 'R', title: '요구사항', status: 'draft', doc_review_status: null, is_editable: true },
  [LOCKED]: { doc_id: LOCKED, type_code: 'R', title: '잠긴 요구사항', status: 'draft', doc_review_status: null, is_editable: false },
}
/** Documents whose body read answers "unreadable" (file not found), and whose read fails outright. */
const unreadable = new Set<string>()
const failing = new Set<string>()

const axiosError = (status: number) => ({ isAxiosError: true, response: { status, data: { detail: 'x' } } })

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentWindows()
  files.clear()
  files.set(REPORT, `${FRONT}\n# 결과\n\n본문입니다.\n`)
  files.set(ORDER, '# 지시\n')
  files.set(LOCKED, '# 잠김\n')
  unreadable.clear()
  failing.clear()
  recordRead.mockReset()
  contentRead.mockReset()
  contentSave.mockReset()
  diagnose.mockReset()
  recoverBody.mockReset()
  setCurrentUser({ username: 'worker', email: null, isAdmin: false })
  recordRead.mockImplementation(async (docId: string) => {
    if (!RECORDS[docId]) throw axiosError(404)
    return toDocumentRecord(RECORDS[docId])
  })
  contentRead.mockImplementation(async (docId: string) => {
    if (failing.has(docId)) throw axiosError(500)
    if (unreadable.has(docId)) return { docId, content: '', missing: true, state: 'unreadable', reason: 'file_not_found' }
    return files.has(docId) ? { docId, content: files.get(docId)!, missing: false } : { docId, content: '', missing: true }
  })
  diagnose.mockResolvedValue({ items: [] })
  contentSave.mockImplementation(async (docId: string, content: string) => {
    files.set(docId, content)
  })
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  setCurrentUser(null)
  document.body.querySelectorAll('.ui-modal-overlay').forEach((node) => node.remove())
})

// --- DOM helpers (the windows and the confirm dialog are teleported to <body>) -----------------------

const panels = () => [...document.body.querySelectorAll<HTMLElement>('.ui-modeless-panel')]
function panelOf(docId: string): HTMLElement {
  const panel = panels().find((node) => node.querySelector(`[data-test="document-window"][data-doc="${docId}"]`))
  if (!panel) throw new Error(`no window for ${docId}`)
  return panel
}
const q = (root: ParentNode, id: string) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}
async function type(node: HTMLElement | null, value: string) {
  const area = node as HTMLTextAreaElement
  area.value = value
  area.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}
const confirmButtons = () => [...document.body.querySelectorAll<HTMLElement>('.ui-confirm-actions .ui-btn')]
const confirmTitle = () => document.body.querySelector('.ui-confirm-title')?.textContent ?? null
const windows = () => documentWindowList().map((win) => [win.docId, win.mode])
/** The view footer's buttons in order (flowgate.v02.0070 T#3). */
const footer = (panel: HTMLElement) =>
  [...q(panel, 'document-window-viewer-bar')!.querySelectorAll('button')].map((button) => button.getAttribute('data-test'))

async function open(docId: string, mode: 'view' | 'edit' = 'view') {
  openDocumentWindow(docId, mode)
  await flushPromises()
  return panelOf(docId)
}

describe('document window — view (flowgate.v02.0053 T#3)', () => {
  it('opens in this tab as a modeless window: name · state dot · copy · minimize · close, the MD Viewer as body', async () => {
    const panel = await open(REPORT)
    expect(q(panel, 'document-window-title')!.textContent).toBe('작업레포트 — T#2')
    const dot = q(panel, 'document-window-dot')!
    expect(dot.getAttribute('data-tone')).toBe('warning')
    expect(dot.getAttribute('title')).toBe('검토 중')
    const titlebar = panel.querySelector('.ui-modeless-titlebar')!
    const order = [...titlebar.querySelectorAll('[data-test="document-window-title"], [data-test="document-window-dot"], button')].map(
      (node) => node.getAttribute('data-test') ?? node.getAttribute('aria-label'),
    )
    // flowgate.v02.0056 F1 moved the view's [편집] to the footer; the title line keeps the body copy.
    expect(order).toEqual(['document-window-title', 'document-window-dot', 'md-viewer-copy', 'Minimize', 'Close'])
    expect(footer(panel)).toEqual(['document-window-edit', 'document-window-close'])
    expect(q(panel, 'document-window-edit')!.textContent!.trim()).toBe('편집')
    expect(q(panel, 'document-window-body')!.getAttribute('data-view')).toBe('markdown')
    expect(q(panel, 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('결과')
    // No browser tab wording, no v0.1 screen, no sign-in talk anywhere in the window.
    expect(panel.textContent).not.toMatch(/새 탭|v0\.1|로그인/)
  })

  it('several documents stack as several windows; the same document again stays one window', async () => {
    await open(REPORT)
    await open(ORDER)
    await open(REPORT)
    expect(panels()).toHaveLength(2)
    expect(windows()).toEqual([[REPORT, 'view'], [ORDER, 'view']])
    expect(q(panelOf(ORDER), 'document-window-dot')!.getAttribute('data-tone')).toBe('success')
  })

  it('minimize folds the window to its title line and keeps it; close removes it', async () => {
    const panel = await open(REPORT)
    await click(panel.querySelector<HTMLElement>('[aria-label="Minimize"]'))
    expect(panel.classList.contains('is-minimized')).toBe(true)
    expect(documentWindowList()[0].minimized).toBe(true)
    await click(panel.querySelector<HTMLElement>('[aria-label="Restore"]'))
    expect(panel.classList.contains('is-minimized')).toBe(false)
    await click(panel.querySelector<HTMLElement>('[aria-label="Close"]'))
    expect(panels()).toHaveLength(0)
    expect(windows()).toEqual([])
  })

  it('a document that no longer exists: a short note in place of the body, only [닫기]', async () => {
    const panel = await open(GONE)
    expect(q(panel, 'document-window-gone')!.textContent).toBe('문서를 찾을 수 없습니다')
    expect(footer(panel)).toEqual(['document-window-close'])
  })
})

describe('document window — ✎ editing (flowgate.v02.0053 T#3)', () => {
  it('✎ turns the same window to the editor with the body (frontmatter aside); 저장 writes it back and returns to the view', async () => {
    const panel = await open(REPORT)
    await click(q(panel, 'document-window-edit'))
    expect(windows()).toEqual([[REPORT, 'edit']])
    expect(panels()).toHaveLength(1)
    const area = q(panel, 'document-window-textarea') as HTMLTextAreaElement
    expect(area.value).toBe('# 결과\n\n본문입니다.\n')
    expect(q(panel, 'document-window-edit')!.hasAttribute('disabled')).toBe(true)
    await type(area, '# 결과\n\n고친 본문.\n')
    await click(q(panel, 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(REPORT, `${FRONT}\n# 결과\n\n고친 본문.\n`)
    expect(windows()).toEqual([[REPORT, 'view']])
    expect(q(panel, 'document-window-editor')).toBeNull()
    expect(q(panel, 'md-viewer-content')!.textContent).toContain('고친 본문.')
    // The title line is read again after a save (the review state may have moved).
    expect(recordRead).toHaveBeenCalledTimes(2)
  })

  it('취소 without changes goes back to the view at once', async () => {
    const panel = await open(REPORT, 'edit')
    expect(q(panel, 'document-window-textarea')).not.toBeNull()
    await click(q(panel, 'document-window-cancel'))
    expect(confirmTitle()).toBeNull()
    expect(windows()).toEqual([[REPORT, 'view']])
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('취소 with unsaved text asks once: 계속 편집 keeps the text, 버리기 drops it without saving', async () => {
    const panel = await open(REPORT, 'edit')
    await type(q(panel, 'document-window-textarea'), '저장 안 한 글')
    await click(q(panel, 'document-window-cancel'))
    expect(confirmTitle()).toBe('저장하지 않은 내용을 버릴까요?')
    expect(confirmButtons().map((button) => button.textContent?.trim())).toEqual(['계속 편집', '버리기'])
    await click(confirmButtons()[0])
    expect(confirmTitle()).toBeNull()
    expect((q(panel, 'document-window-textarea') as HTMLTextAreaElement).value).toBe('저장 안 한 글')
    await click(q(panel, 'document-window-cancel'))
    await click(confirmButtons()[1])
    expect(windows()).toEqual([[REPORT, 'view']])
    expect(contentSave).not.toHaveBeenCalled()
    expect(files.get(REPORT)).toBe(`${FRONT}\n# 결과\n\n본문입니다.\n`)
  })

  // flowgate.v02.0056 F1 (0056 CH §5): the editor bar has [닫기] as well — the window's close by button; it takes
  // the × / Esc path, so unsaved text is asked about once. 0017-CH: the bar is [취소] · [저장] · [닫기].
  it('[닫기] in the editor bar: the bar is 취소 · 저장 · 닫기; without changes the window closes at once', async () => {
    const panel = await open(REPORT, 'edit')
    const close = q(panel, 'document-window-close')!
    expect(close.textContent?.trim()).toBe('닫기')
    expect([...close.parentElement!.querySelectorAll('button')].map((button) => button.getAttribute('data-test'))).toEqual([
      'document-window-cancel',
      'document-window-save',
      'document-window-close',
    ])
    await click(close)
    expect(confirmTitle()).toBeNull()
    expect(panels()).toHaveLength(0)
    expect(windows()).toEqual([])
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('[닫기] with unsaved text asks once: 계속 편집 keeps the window and the text, 버리기 closes it without saving', async () => {
    const panel = await open(REPORT, 'edit')
    await type(q(panel, 'document-window-textarea'), '저장 안 한 글')
    await click(q(panel, 'document-window-close'))
    expect(confirmTitle()).toBe('저장하지 않은 내용을 버릴까요?')
    await click(confirmButtons()[0])
    expect(confirmTitle()).toBeNull()
    expect(panels()).toHaveLength(1)
    expect((q(panel, 'document-window-textarea') as HTMLTextAreaElement).value).toBe('저장 안 한 글')
    await click(q(panel, 'document-window-close'))
    await click(confirmButtons()[1])
    expect(panels()).toHaveLength(0)
    expect(contentSave).not.toHaveBeenCalled()
    expect(files.get(REPORT)).toBe(`${FRONT}\n# 결과\n\n본문입니다.\n`)
  })

  it('closing a window with unsaved text asks once; 버리기 closes it', async () => {
    const panel = await open(REPORT, 'edit')
    await type(q(panel, 'document-window-textarea'), '저장 안 한 글')
    await click(panel.querySelector<HTMLElement>('[aria-label="Close"]'))
    expect(confirmTitle()).toBe('저장하지 않은 내용을 버릴까요?')
    expect(panels()).toHaveLength(1)
    await click(confirmButtons()[1])
    expect(panels()).toHaveLength(0)
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('a refused save keeps the editor and its text with a short reason (422: not editable now)', async () => {
    contentSave.mockRejectedValueOnce(axiosError(422))
    const panel = await open(REPORT, 'edit')
    await type(q(panel, 'document-window-textarea'), '고친 글')
    await click(q(panel, 'document-window-save'))
    expect(windows()).toEqual([[REPORT, 'edit']])
    expect(q(panel, 'document-window-save-error')!.textContent).toBe('지금은 수정할 수 없는 문서입니다')
    expect((q(panel, 'document-window-textarea') as HTMLTextAreaElement).value).toBe('고친 글')
  })

  it('opening with ✎ while the view is open turns that window to the editor', async () => {
    const panel = await open(REPORT)
    openDocumentWindow(REPORT, 'edit')
    await flushPromises()
    expect(panels()).toHaveLength(1)
    expect(q(panel, 'document-window-textarea')).not.toBeNull()
  })
})

describe('document window — a report before its run (flowgate.v02.0053 T#3)', () => {
  it('opens as an empty document (no text at all) and ✎ / 저장 work the same: the save writes its file', async () => {
    const panel = await open(EMPTY_REPORT)
    expect(q(panel, 'md-viewer-content')!.textContent).toBe('')
    expect(q(panel, 'document-window-body')!.textContent!.trim()).toBe('')
    expect(footer(panel)).toEqual(['document-window-edit', 'document-window-close'])
    expect(q(panel, 'document-window-dot')!.getAttribute('data-tone')).toBe('neutral')
    await click(q(panel, 'document-window-edit'))
    const area = q(panel, 'document-window-textarea') as HTMLTextAreaElement
    expect(area.value).toBe('')
    await type(area, '# 첫 내용\n')
    await click(q(panel, 'document-window-save'))
    expect(contentSave).toHaveBeenCalledWith(EMPTY_REPORT, '# 첫 내용\n')
    expect(windows()).toEqual([[EMPTY_REPORT, 'view']])
    expect(q(panel, 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('첫 내용')
  })

  it('✎ as the entry point opens the empty report straight in its editor', async () => {
    const panel = await open(EMPTY_REPORT, 'edit')
    expect((q(panel, 'document-window-textarea') as HTMLTextAreaElement).value).toBe('')
    expect(q(panel, 'document-window-save')!.hasAttribute('disabled')).toBe(false)
  })
})

// flowgate.v02.0070 T#3 (B3, 0003-NR §3): the body shows a short state block only; the footer at the bottom right
// is the one action group, by state — unreadable: [복구] (diagnosed recoverable) [본문 다시 입력] [닫기] and no
// [편집]; readable / empty: [편집] [닫기]; not editable (no sign-in, not editable, gone): [닫기]; editing: the
// editor bar. [복구] runs only on its click.
describe('document window — the view footer by the body state (flowgate.v02.0070 T#3)', () => {
  const diagnosed = (method: string, recoverable: boolean) =>
    diagnose.mockResolvedValue({ items: [{ docId: UNREADABLE, method, recoverable }] })

  it('readable: [편집] [닫기]; a person who may not edit gets [닫기] only', async () => {
    expect(footer(await open(REPORT))).toEqual(['document-window-edit', 'document-window-close'])
    expect(footer(await open(LOCKED))).toEqual(['document-window-close'])
    setCurrentUser(null)
    closeAllDocumentWindows()
    expect(footer(await open(REPORT))).toEqual(['document-window-close'])
  })

  it('an empty document: the empty view with [편집] only when allowed, no diagnosis', async () => {
    const panel = await open(EMPTY_REPORT)
    expect(q(panel, 'document-window-body')!.textContent!.trim()).toBe('')
    expect(footer(panel)).toEqual(['document-window-edit', 'document-window-close'])
    expect(diagnose).not.toHaveBeenCalled()
  })

  it('unreadable, diagnosed recoverable: [복구] [본문 다시 입력] [닫기], no [편집], no button in the body; 복구 only on click', async () => {
    unreadable.add(UNREADABLE)
    diagnosed('regenerate_stub', true)
    const panel = await open(UNREADABLE)
    expect(footer(panel)).toEqual(['document-window-recover', 'document-window-reenter', 'document-window-close'])
    expect([...q(panel, 'document-window-viewer-bar')!.querySelectorAll('button')].map((b) => b.textContent!.trim()))
      .toEqual(['복구', '본문 다시 입력', '닫기'])
    expect(q(panel, 'md-viewer-unreadable')!.querySelectorAll('button')).toHaveLength(0)
    expect(q(panel, 'md-viewer-unreadable')!.textContent).toContain('파일 없음 · AI도 이 문서를 읽지 못합니다.')
    expect(diagnose).toHaveBeenCalledWith({ groupId: 'flowgate.v02.0053' })
    expect(recoverBody).not.toHaveBeenCalled()

    unreadable.delete(UNREADABLE)
    files.set(UNREADABLE, '# 복구됨\n')
    recoverBody.mockResolvedValue({ docId: UNREADABLE, status: 'recovered', method: 'regenerate_stub', bodyLoss: 'none', refusal: null })
    await click(q(panel, 'document-window-recover'))
    expect(recoverBody).toHaveBeenCalledTimes(1)
    expect(recoverBody).toHaveBeenCalledWith(UNREADABLE, 'regenerate_stub')
    expect(q(panel, 'md-viewer-content')!.querySelector('h1')!.textContent).toBe('복구됨')
    expect(q(panel, 'md-viewer-recover-notice')!.textContent).toBe('본문을 복구했습니다.')
    expect(footer(panel)).toEqual(['document-window-edit', 'document-window-close'])
  })

  it('unreadable, not recoverable: the guide line and [본문 다시 입력] [닫기]; it opens the editor (취소 · 저장 · 닫기)', async () => {
    unreadable.add(UNREADABLE)
    diagnosed('read_error_manual', false)
    const panel = await open(UNREADABLE)
    expect(footer(panel)).toEqual(['document-window-reenter', 'document-window-close'])
    expect(q(panel, 'md-viewer-recover-guide')!.textContent).toBe(i18n.global.t('gui.document.recover.guide.read_error_manual'))
    await click(q(panel, 'document-window-reenter'))
    expect(windows()).toEqual([[UNREADABLE, 'edit']])
    expect(q(panel, 'document-window-viewer-bar')).toBeNull()
    expect([...q(panel, 'document-window-close')!.parentElement!.querySelectorAll('button')].map((b) => b.getAttribute('data-test')))
      .toEqual(['document-window-cancel', 'document-window-save', 'document-window-close'])
  })

  it('unreadable when no diagnosis answers: [본문 다시 입력] [닫기]', async () => {
    unreadable.add(UNREADABLE)
    diagnose.mockRejectedValue(axiosError(500))
    expect(footer(await open(UNREADABLE))).toEqual(['document-window-reenter', 'document-window-close'])
  })

  it('unreadable without the right to edit: [닫기] only, and no diagnosis is read', async () => {
    unreadable.add(LOCKED)
    diagnosed('regenerate_stub', true)
    const panel = await open(LOCKED)
    expect(q(panel, 'md-viewer-unreadable')).not.toBeNull()
    expect(footer(panel)).toEqual(['document-window-close'])
    expect(diagnose).not.toHaveBeenCalled()
  })

  it('a failed read: the body keeps its retry and the footer has [닫기] only', async () => {
    failing.add(REPORT)
    const panel = await open(REPORT)
    expect(q(panel, 'md-viewer-retry')).not.toBeNull()
    expect(footer(panel)).toEqual(['document-window-close'])
    failing.delete(REPORT)
    await click(q(panel, 'md-viewer-retry'))
    expect(footer(panel)).toEqual(['document-window-edit', 'document-window-close'])
  })
})
