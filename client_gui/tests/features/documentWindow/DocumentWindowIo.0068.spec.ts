/**
 * flowgate.v02.0068 T#1 — the document window with the upload / download (0068 D §3.1, §6.2, §7 A; L §2.13, §5.4
 * U5 ~ U7) as the App Shell mounts it (DocumentWindows), `@/features/document` mocked: the header's
 * [업로드][다운로드] │ before the existing icons and hidden while minimized, the bottom bars unchanged; the
 * upload's hold of unsaved text, the guard (no edit entry, no input, no save; an Inspector edit request opens the
 * view), the late editor read dropped, the reflect into a clean editor (frontmatter, base and text — a later save
 * edits the uploaded body), the release on every end, other documents untouched, the fixed target across a
 * supersede, the editor kept loading after the reflect time limit; the download of the stored file.
 * flowgate.v02.0074 T#2: a work plan's header — [다운로드] the stored plan body as `<docId>.json`, [업로드] the plan window's
 * [JSON 가져오기] (off as locked without the Designer's opener); other documents as before.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { openDocument } from '@/features/designer/inspector/openDocument'
import {
  DocumentWindows,
  closeAllDocumentWindows,
  documentWindowList,
  openDocumentWindow,
  setDocumentWindowMinimized,
} from '@/features/documentWindow'
import { documentIoGuarded, resetDocumentEditRegistry } from '@/features/documentWindow/documentEditRegistry'
import { documentIoDownload, documentIoLeave, documentIoNotices, documentIoReadFile, documentIoUpload, resetDocumentIo } from '@/features/documentWindow/documentIo'
import { setCurrentUser } from '@/core/auth/currentUser'
import { closeAllWorkPlanEditWindows, setWorkPlanEditOpener, workPlanEditWindowList } from '@/features/workPlanWindow/workPlanEditWindows'
import { DOC as WP_DOC, FLOW as WP_FLOW, hiddenPlanBody, readAnswer } from '../workPlanWindow/workPlanFixtures'

const recordRead = vi.fn()
const contentRead = vi.fn()
const contentSave = vi.fn()
const infoRead = vi.fn()
const download = vi.fn()
const planEditRead = vi.fn()
const planDocRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentRead: (...args: unknown[]) => recordRead(...args),
    documentContentRead: (...args: unknown[]) => contentRead(...args),
    documentContentSave: (...args: unknown[]) => contentSave(...args),
    documentInfoRead: (...args: unknown[]) => infoRead(...args),
    documentDownload: (...args: unknown[]) => download(...args),
    // flowgate.v02.0074 T#2: a work plan's download, edit window and body view read the plan
    workPlanEditRead: (...args: unknown[]) => planEditRead(...args),
    workPlanDocumentRead: (...args: unknown[]) => planDocRead(...args),
  }
})

const { toDocumentInfo, toDocumentRecord, toWorkPlanDocument } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

registerAppMessages(i18n)

const DOC = 'flowgate.v02.0068.0007-T'
const NEXT = 'flowgate.v02.0068.0009-T'
const OTHER = 'flowgate.v02.0068.0008-TR'

const OLD = '---\ntitle: 옛 제목\n---\n# 옛 본문\n'
const NEW = '---\ntitle: 새 제목\n---\n# 새 본문\n'

const files = new Map<string, string>()
const records: Record<string, Record<string, unknown>> = {}
const access: Record<string, number> = {}

const http = (status: number) => ({ isAxiosError: true, response: { status, data: { detail: 'x' } } })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const md = (text: string) => new File([text], 'upload.md', { type: 'text/markdown' })
const upload = (docId: string, text: string | Promise<string>, pick?: Promise<File | null>) =>
  documentIoUpload(docId, { pick: () => pick ?? Promise.resolve(md('x')), read: () => Promise.resolve(text) })

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentWindows()
  resetDocumentEditRegistry()
  resetDocumentIo()
  files.clear()
  files.set(DOC, OLD)
  files.set(OTHER, '# 다른 문서\n')
  files.set(NEXT, '# 이어진 문서\n')
  for (const key of Object.keys(records)) delete records[key]
  records[DOC] = { doc_id: DOC, type_code: 'T', title: '작업지시', status: 'open', is_editable: true }
  records[OTHER] = { doc_id: OTHER, type_code: 'TR', title: '다른 문서', status: 'open', is_editable: true }
  records[NEXT] = { doc_id: NEXT, type_code: 'T', title: '이어진 문서', status: 'open', is_editable: true }
  for (const key of Object.keys(access)) delete access[key]
  recordRead.mockReset().mockImplementation(async (docId: string) => {
    if (access[docId]) throw http(access[docId])
    if (!records[docId]) throw http(404)
    return toDocumentRecord(records[docId])
  })
  infoRead.mockReset().mockImplementation(async (docId: string) => {
    if (access[docId]) throw http(access[docId])
    return toDocumentInfo({ ...records[docId], download_available: true })
  })
  contentRead.mockReset().mockImplementation(async (docId: string) => ({ docId, content: files.get(docId) ?? '', missing: !files.has(docId) }))
  contentSave.mockReset().mockImplementation(async (docId: string, content: string) => {
    files.set(docId, content)
  })
  download.mockReset()
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})

afterEach(() => {
  vi.useRealTimers()
  wrapper?.unmount()
  wrapper = null
  closeAllDocumentWindows()
  resetDocumentEditRegistry()
  resetDocumentIo()
  document.body.querySelectorAll('.ui-modal-overlay').forEach((node) => node.remove())
})

// --- DOM helpers ------------------------------------------------------------------------------------------

const panels = () => [...document.body.querySelectorAll<HTMLElement>('.ui-modeless-panel')]
function panelOf(docId: string): HTMLElement {
  const panel = panels().find((node) => node.querySelector(`[data-test="document-window"][data-doc="${docId}"]`))
  if (!panel) throw new Error(`no window for ${docId}`)
  return panel
}
const q = (root: ParentNode, id: string) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const titlebar = (docId: string) => panelOf(docId).querySelector<HTMLElement>('.ui-modeless-titlebar')!
const bar = (docId: string, mode: 'viewer' | 'editor') => panelOf(docId).querySelector<HTMLElement>(`.document-window-${mode}-bar`)!
const area = (docId: string) => q(panelOf(docId), 'document-window-textarea') as HTMLTextAreaElement | null
async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}
async function type(docId: string, value: string) {
  const node = area(docId)!
  node.value = value
  node.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}
async function open(docId: string, mode: 'view' | 'edit' = 'view') {
  openDocumentWindow(docId, mode)
  await flushPromises()
  return panelOf(docId)
}
const notices = () => documentIoNotices().map((item) => item.key)
const toastText = () => document.body.querySelector('.ui-toast-host')?.textContent ?? ''
const saveButton = (docId: string) => q(bar(docId, 'editor'), 'document-window-save') as HTMLButtonElement
const editButton = (docId: string) => q(bar(docId, 'viewer'), 'document-window-edit') as HTMLButtonElement

// --- Header -------------------------------------------------------------------------------------------------

describe('header — [업로드][다운로드] │ [copy / edit][minimize][close] (D §0-1, §6.2)', () => {
  it('the group leads the actions, parted by a line; the existing icons keep their order', async () => {
    await open(DOC)
    const order = [...titlebar(DOC).querySelectorAll('button, [data-test="document-io-divider"]')].map(
      (node) => node.getAttribute('data-test') ?? node.getAttribute('aria-label'),
    )
    expect(order.slice(0, 3)).toEqual(['document-io-upload', 'document-io-download', 'document-io-divider'])
    // flowgate.v02.0088 F06: the shell's controls are named in the app's language (ko in this spec).
    expect(order.slice(-2)).toEqual(['최소화', '닫기'])
    const up = q(titlebar(DOC), 'document-io-upload')!
    expect(up.getAttribute('aria-label')).toBe('업로드')
    expect(up.getAttribute('title')).toBe('파일 내용으로 문서 전체를 교체')
    expect(q(titlebar(DOC), 'document-io-download')!.getAttribute('aria-label')).toBe('다운로드')
  })

  it('in the editor the group comes before ✎ (the edit icon); the group acts on the edited document', async () => {
    await open(DOC, 'edit')
    const order = [...titlebar(DOC).querySelectorAll('button')].map((node) => node.getAttribute('data-test') ?? node.getAttribute('aria-label'))
    // flowgate.v02.0088 F06: the shell's controls are named in the app's language (ko in this spec).
    expect(order).toEqual(['document-io-upload', 'document-io-download', 'document-window-edit', '최소화', '닫기'])
    expect(q(titlebar(DOC), 'document-io')!.getAttribute('data-doc')).toBe(DOC)
  })

  it('minimized: the group is hidden, restore and close stay (the rest of the title bar as before)', async () => {
    await open(DOC)
    const before = [...titlebar(DOC).querySelectorAll('button')].map((node) => node.getAttribute('aria-label'))
    setDocumentWindowMinimized(DOC, true)
    await flushPromises()
    expect(q(titlebar(DOC), 'document-io')).toBeNull()
    const after = [...titlebar(DOC).querySelectorAll('button')].map((node) => node.getAttribute('aria-label'))
    // flowgate.v02.0088 F06: the shell's controls are named in the app's language (ko in this spec).
    expect(after.slice(-2)).toEqual(['복원', '닫기'])
    expect(after.slice(0, -2)).toEqual(before.slice(2, -2))
    setDocumentWindowMinimized(DOC, false)
    await flushPromises()
    expect(q(titlebar(DOC), 'document-io')).not.toBeNull()
  })

  it('the bottom bars stay as they are: view [편집][닫기], editor [취소][저장][닫기]', async () => {
    await open(DOC)
    expect([...bar(DOC, 'viewer').querySelectorAll('button')].map((node) => node.textContent?.trim())).toEqual(['편집', '닫기'])
    await click(editButton(DOC))
    expect([...bar(DOC, 'editor').querySelectorAll('button')].map((node) => node.textContent?.trim())).toEqual(['취소', '저장', '닫기'])
  })

  it('a press on the group does not start a title bar drag (mousedown stops at the group)', async () => {
    await open(DOC)
    const event = new MouseEvent('mousedown', { bubbles: true, cancelable: true })
    const stop = vi.spyOn(event, 'stopPropagation')
    q(titlebar(DOC), 'document-io-upload')!.dispatchEvent(event)
    expect(stop).toHaveBeenCalled()
  })

  it('disabled buttons stay with their reason: 권한 없음 (read 403)', async () => {
    access[DOC] = 403
    openDocumentWindow(DOC)
    await flushPromises()
    const up = q(titlebar(DOC), 'document-io-upload') as HTMLButtonElement
    const down = q(titlebar(DOC), 'document-io-download') as HTMLButtonElement
    expect([up.disabled, down.disabled]).toEqual([true, true])
    expect([up.getAttribute('title'), down.getAttribute('title')]).toEqual(['권한 없음', '권한 없음'])
    // The reason is on the wrapper too (a disabled button hears no pointer).
    expect(up.parentElement!.getAttribute('title')).toBe('권한 없음')
  })

  it('a document that is not editable: upload disabled 편집 불가 상태, download usable', async () => {
    records[DOC].is_editable = false
    await open(DOC)
    expect((q(titlebar(DOC), 'document-io-upload') as HTMLButtonElement).disabled).toBe(true)
    expect(q(titlebar(DOC), 'document-io-upload')!.getAttribute('title')).toBe('편집 불가 상태')
    expect((q(titlebar(DOC), 'document-io-download') as HTMLButtonElement).disabled).toBe(false)
  })

  it('the upload button picks a Markdown / text file (the L §1.3 filter) and uploads it', async () => {
    await open(DOC)
    let input: HTMLInputElement | null = null
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(function (this: HTMLInputElement) {
      input = this
    })
    await click(q(titlebar(DOC), 'document-io-upload'))
    expect(input).not.toBeNull()
    expect(input!.type).toBe('file')
    expect(input!.accept).toBe('.md,text/markdown,text/plain')
    Object.defineProperty(input!, 'files', { value: [md(NEW)] })
    input!.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(contentSave).toHaveBeenCalledWith(DOC, NEW))
    clickSpy.mockRestore()
  })
})

// --- Upload: hold, guard, reflect ------------------------------------------------------------------------

describe('upload — unsaved text is held (D §7 A)', () => {
  it('a dirty editor holds the upload: the draft stays, nothing saved', async () => {
    await open(DOC, 'edit')
    await type(DOC, '# 고치는 중\n')
    await upload(DOC, NEW)
    await flushPromises()
    expect(notices()).toEqual(['upload_hold'])
    expect(toastText()).toContain('저장하지 않은 편집 내용이 있습니다')
    expect(area(DOC)!.value).toBe('# 고치는 중\n')
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('race: the editor turns dirty while the file is picked → held at check 2, the draft stays', async () => {
    await open(DOC, 'edit')
    const pick = deferred<File | null>()
    const run = upload(DOC, NEW, pick.promise)
    await type(DOC, '# 고르는 동안 입력\n')
    pick.resolve(md(NEW))
    await run
    await flushPromises()
    expect(notices()).toEqual(['upload_hold'])
    expect(area(DOC)!.value).toBe('# 고르는 동안 입력\n')
    expect(area(DOC)!.hasAttribute('readonly')).toBe(false)
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('a save in progress holds the upload (check 1)', async () => {
    await open(DOC, 'edit')
    await type(DOC, '# 저장할 내용\n')
    const save = deferred<void>()
    contentSave.mockImplementationOnce(async (docId: string, content: string) => {
      await save.promise
      files.set(docId, content)
    })
    await click(saveButton(DOC))
    await upload(DOC, NEW)
    expect(notices()).toEqual(['upload_hold'])
    save.resolve()
    await flushPromises()
    expect(files.get(DOC)).toBe('---\ntitle: 옛 제목\n---\n# 저장할 내용\n')
  })
})

describe('upload — the guard (D §3.1, L §2.13)', () => {
  it('while guarded: an open clean editor is read only and [저장] disabled; a view window’s [편집] disabled; then released', async () => {
    await open(DOC, 'edit')
    const read = deferred<string>()
    const run = documentIoUpload(DOC, { pick: async () => md(NEW), read: () => read.promise })
    await flushPromises()
    expect(documentIoGuarded(DOC)).toBe(true)
    expect(area(DOC)!.hasAttribute('readonly')).toBe(true)
    expect(saveButton(DOC).disabled).toBe(true)
    expect(saveButton(DOC).getAttribute('title')).toBe('업로드 처리 중')
    expect((q(titlebar(DOC), 'document-io-upload') as HTMLButtonElement).disabled).toBe(true)
    expect(q(titlebar(DOC), 'document-io-upload')!.getAttribute('title')).toBe('업로드 처리 중')
    expect(q(titlebar(DOC), 'document-io-upload')!.querySelector('.document-io-spinner')).not.toBeNull()
    // Ctrl+S does not save either.
    area(DOC)!.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true }))
    await flushPromises()
    expect(contentSave).not.toHaveBeenCalled()
    // [취소] still works (a clean editor has nothing to lose).
    expect((q(bar(DOC, 'editor'), 'document-window-cancel') as HTMLButtonElement).disabled).toBe(false)
    read.resolve(NEW)
    await run
    await flushPromises()
    expect(area(DOC)!.hasAttribute('readonly')).toBe(false)
    expect(saveButton(DOC).disabled).toBe(false)
  })

  it('a view window: [편집] is disabled while guarded and the Inspector’s edit request opens the view', async () => {
    await open(DOC)
    const read = deferred<string>()
    const run = documentIoUpload(DOC, { pick: async () => md(NEW), read: () => read.promise })
    await flushPromises()
    expect(editButton(DOC).disabled).toBe(true)
    expect(editButton(DOC).getAttribute('title')).toBe('업로드 처리 중')
    await click(editButton(DOC))
    openDocument(DOC, 'edit')
    await flushPromises()
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[DOC, 'view']])
    read.resolve(NEW)
    await run
    await flushPromises()
    expect(editButton(DOC).disabled).toBe(false)
    openDocument(DOC, 'edit')
    await flushPromises()
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[DOC, 'edit']])
  })

  it('a window of another document is not guarded nor reflected', async () => {
    await open(OTHER, 'edit')
    await open(DOC)
    contentRead.mockClear()
    const read = deferred<string>()
    const run = documentIoUpload(DOC, { pick: async () => md(NEW), read: () => read.promise })
    await flushPromises()
    expect(area(OTHER)!.hasAttribute('readonly')).toBe(false)
    expect(saveButton(OTHER).disabled).toBe(false)
    read.resolve(NEW)
    await run
    await flushPromises()
    expect(contentRead.mock.calls.map(([id]) => id)).not.toContain(OTHER)
    expect(files.get(OTHER)).toBe('# 다른 문서\n')
  })

  it('race: an editor read pending when the guard starts is dropped; only the reflect’s read fills the editor', async () => {
    const late = deferred<{ docId: string; content: string; missing: boolean }>()
    contentRead.mockImplementationOnce(() => late.promise)
    await open(DOC, 'edit')
    expect(q(panelOf(DOC), 'document-window-editor-loading')).not.toBeNull()
    const save = deferred<void>()
    contentSave.mockImplementationOnce(async (docId: string, content: string) => {
      await save.promise
      files.set(docId, content)
    })
    const run = upload(DOC, NEW)
    await flushPromises()
    // The old read answers while the save runs: dropped.
    late.resolve({ docId: DOC, content: OLD, missing: false })
    await flushPromises()
    expect(q(panelOf(DOC), 'document-window-editor-loading')).not.toBeNull()
    expect(area(DOC)).toBeNull()
    save.resolve()
    await run
    await flushPromises()
    expect(area(DOC)!.value).toBe('# 새 본문\n')
  })

  it('an editor entered while guarded (another way in) waits for the release, then reads', async () => {
    await open(DOC)
    const read = deferred<string>()
    const run = documentIoUpload(DOC, { pick: async () => md(NEW), read: () => read.promise })
    await flushPromises()
    // Not the [편집] button: an edit open of the window list itself.
    openDocumentWindow(DOC, 'edit')
    await flushPromises()
    expect(q(panelOf(DOC), 'document-window-editor-loading')).not.toBeNull()
    read.resolve('   ')
    await run
    await flushPromises()
    expect(notices()).toEqual(['upload_empty'])
    expect(area(DOC)!.value).toBe('# 옛 본문\n')
  })
})

describe('upload — the reflect (D §3.1, §7 A)', () => {
  it('success: a clean editor takes the new frontmatter, base and text, stays clean; a later save edits the uploaded body', async () => {
    await open(DOC, 'edit')
    expect(area(DOC)!.value).toBe('# 옛 본문\n')
    await upload(DOC, NEW)
    await flushPromises()
    expect(files.get(DOC)).toBe(NEW)
    expect(area(DOC)!.value).toBe('# 새 본문\n')
    expect(notices()).toEqual(['upload_done'])
    expect(toastText()).toContain('파일 내용으로 문서를 교체했습니다')
    // Clean: [취소] goes straight back to the view (no discard question).
    await type(DOC, '# 새 본문\n\n덧붙임\n')
    await click(saveButton(DOC))
    expect(files.get(DOC)).toBe('---\ntitle: 새 제목\n---\n# 새 본문\n\n덧붙임\n')
  })

  it('success: a view window reads the new body', async () => {
    await open(DOC)
    expect(q(panelOf(DOC), 'md-viewer-content')!.textContent).toContain('옛 본문')
    await upload(DOC, NEW)
    await flushPromises()
    await vi.waitFor(() => expect(q(panelOf(DOC), 'md-viewer-content')!.textContent).toContain('새 본문'))
  })

  it('a refused save (403): the clean editor reads the server again, the guard ends, editing works again', async () => {
    await open(DOC, 'edit')
    files.set(DOC, NEW) // the server holds something else than the editor read (e.g. saved elsewhere)
    contentSave.mockRejectedValueOnce(http(403))
    await upload(DOC, '# 업로드\n')
    await flushPromises()
    expect(notices()).toEqual(['save.forbidden'])
    expect(area(DOC)!.value).toBe('# 새 본문\n')
    expect(area(DOC)!.hasAttribute('readonly')).toBe(false)
    expect(documentIoGuarded(DOC)).toBe(false)
    expect((q(titlebar(DOC), 'document-io-upload') as HTMLButtonElement).getAttribute('title')).toBe('권한 없음')
  })

  it('a save without an answer that was stored after all: the editor shows the stored upload (not the old base)', async () => {
    await open(DOC, 'edit')
    contentSave.mockImplementationOnce(async (docId: string, content: string) => {
      files.set(docId, content)
      throw { isAxiosError: true }
    })
    await upload(DOC, NEW)
    await flushPromises()
    expect(notices()).toEqual(['save.network'])
    expect(area(DOC)!.value).toBe('# 새 본문\n')
  })

  it('cancelled pick: nothing changes, the editor keeps its text', async () => {
    await open(DOC, 'edit')
    await documentIoUpload(DOC, { pick: async () => null })
    await flushPromises()
    expect(area(DOC)!.value).toBe('# 옛 본문\n')
    expect(area(DOC)!.hasAttribute('readonly')).toBe(false)
    expect(notices()).toEqual([])
  })

  it('the window closed while guarded: the upload ends and the guard is released', async () => {
    await open(DOC)
    const read = deferred<string>()
    const run = documentIoUpload(DOC, { pick: async () => md(NEW), read: () => read.promise })
    await flushPromises()
    await click(q(bar(DOC, 'viewer'), 'document-window-close'))
    expect(panels()).toHaveLength(0)
    read.resolve(NEW)
    await run
    expect(documentIoGuarded(DOC)).toBe(false)
    expect(files.get(DOC)).toBe(NEW)
    expect(notices()).toEqual(['upload_done'])
  })

  it('a supersede while the file is picked: the save goes to the document fixed at the press; the moved window is not guarded', async () => {
    await open(DOC)
    const pick = deferred<File | null>()
    const run = upload(DOC, NEW, pick.promise)
    records[DOC].superseded_by = NEXT
    openDocumentWindow(DOC) // a second open request reads the chain again
    await flushPromises()
    expect(documentWindowList()[0].shown).toBe(NEXT)
    pick.resolve(md(NEW))
    await run
    await flushPromises()
    expect(contentSave).toHaveBeenCalledWith(DOC, NEW)
    expect(files.get(NEXT)).toBe('# 이어진 문서\n')
    expect(editButton(DOC).disabled).toBe(false)
  })
})

describe('upload — the reflect time limit (L §5.4 U5 ~ U7)', () => {
  it('U5: the reflect read is late → the guard ends at 10 s, the editor stays loading until its read answers, then clean', async () => {
    await open(DOC, 'edit')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const late = deferred<{ docId: string; content: string; missing: boolean }>()
    contentRead.mockImplementationOnce(() => late.promise)
    const run = upload(DOC, NEW)
    await vi.advanceTimersByTimeAsync(0)
    expect(documentIoGuarded(DOC)).toBe(true)
    expect(q(panelOf(DOC), 'document-window-editor-loading')).not.toBeNull()
    await vi.advanceTimersByTimeAsync(10000)
    await run
    expect(documentIoGuarded(DOC)).toBe(false)
    expect(notices()).toEqual(['upload_done'])
    // Released, but this window is still loading: no input, [저장] disabled.
    expect(area(DOC)).toBeNull()
    expect(saveButton(DOC).disabled).toBe(true)
    late.resolve({ docId: DOC, content: NEW, missing: false })
    await vi.advanceTimersByTimeAsync(0)
    expect(area(DOC)!.value).toBe('# 새 본문\n')
    expect(saveButton(DOC).disabled).toBe(false)
  })

  it('U6: the late reflect read fails → the error and [다시 시도]; the old base never becomes editable', async () => {
    await open(DOC, 'edit')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const late = deferred<{ docId: string; content: string; missing: boolean }>()
    contentRead.mockImplementationOnce(() => late.promise)
    const run = upload(DOC, NEW)
    await vi.advanceTimersByTimeAsync(10000)
    await run
    late.reject({ isAxiosError: true })
    await vi.advanceTimersByTimeAsync(0)
    expect(q(panelOf(DOC), 'document-window-editor-error')).not.toBeNull()
    expect(q(panelOf(DOC), 'document-window-editor-retry')).not.toBeNull()
    expect(area(DOC)).toBeNull()
    expect(saveButton(DOC).disabled).toBe(true)
  })

  it('U7: the same document is uploaded again before the late answer → only the new reflect fills the editor', async () => {
    await open(DOC, 'edit')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const late = deferred<{ docId: string; content: string; missing: boolean }>()
    contentRead.mockImplementationOnce(() => late.promise)
    const first = upload(DOC, NEW)
    await vi.advanceTimersByTimeAsync(10000)
    await first
    const second = upload(DOC, '# 두 번째\n')
    await vi.advanceTimersByTimeAsync(0)
    late.resolve({ docId: DOC, content: NEW, missing: false })
    await vi.advanceTimersByTimeAsync(0)
    await second
    await vi.advanceTimersByTimeAsync(0)
    expect(area(DOC)!.value).toBe('# 두 번째\n')
  })

  it('U7: the new upload ends before its save (empty file) → the window reads again on the release', async () => {
    await open(DOC, 'edit')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const late = deferred<{ docId: string; content: string; missing: boolean }>()
    contentRead.mockImplementationOnce(() => late.promise)
    const first = upload(DOC, NEW)
    await vi.advanceTimersByTimeAsync(10000)
    await first
    const second = upload(DOC, '  ')
    await vi.advanceTimersByTimeAsync(0)
    await second
    await vi.advanceTimersByTimeAsync(0)
    late.resolve({ docId: DOC, content: OLD, missing: false })
    await vi.advanceTimersByTimeAsync(0)
    expect(notices()).toEqual(['upload_done', 'upload_empty'])
    expect(area(DOC)!.value).toBe('# 새 본문\n')
  })
})

// --- Download -------------------------------------------------------------------------------------------

describe('download (D §3.1)', () => {
  it('the header button downloads the server’s file of the shown document; unsaved text is not in it and is told', async () => {
    const created = vi.fn(() => 'blob:test')
    const revoked = vi.fn()
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: revoked })
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const blob = new Blob([OLD])
    download.mockResolvedValue({ blob, disposition: 'attachment; filename="0007-T_document.md"' })
    await open(DOC, 'edit')
    await type(DOC, '# 저장 안 한 내용\n')
    await click(q(titlebar(DOC), 'document-io-download'))
    await vi.waitFor(() => expect(anchorClick).toHaveBeenCalled())
    expect(download).toHaveBeenCalledWith(DOC)
    expect(created).toHaveBeenCalledWith(blob)
    expect(notices()).toEqual(['download_unsaved_excluded'])
    expect(area(DOC)!.value).toBe('# 저장 안 한 내용\n')
    anchorClick.mockRestore()
  })

  it('a download never rejects (D1 through the window: 404 then the document read 403 → 권한 없음)', async () => {
    await open(DOC)
    download.mockRejectedValue(http(404))
    access[DOC] = 403
    await expect(documentIoDownload(DOC, { save: vi.fn() })).resolves.toBeUndefined()
    await flushPromises()
    expect(notices()).toEqual(['download.forbidden'])
    expect((q(titlebar(DOC), 'document-io-download') as HTMLButtonElement).disabled).toBe(true)
  })
})


describe('Q3 review regression — clean editor survives Designer exit during save', () => {
  it.each(['success', 'network', 'read-error'] as const)('%s: deferred release read blocks input/save through its outcome', async (outcome) => {
    await open(DOC, 'edit')
    expect(area(DOC)!.value).toContain('옛 본문')
    const sent = deferred<void>()
    const reread = deferred<{ docId: string; content: string; missing: boolean }>()
    contentSave.mockImplementationOnce(() => sent.promise)
    const run = upload(DOC, NEW)
    await vi.waitFor(() => expect(contentSave).toHaveBeenCalledTimes(1))
    contentRead.mockReturnValueOnce(reread.promise)
    documentIoLeave()
    await flushPromises()
    expect(documentIoGuarded(DOC)).toBe(true)
    expect(saveButton(DOC).disabled).toBe(true)
    expect(area(DOC)!.readOnly).toBe(true)
    files.set(DOC, NEW) // the server stored it even when the connection lost its answer
    if (outcome === 'network') sent.reject({ isAxiosError: true })
    else sent.resolve()
    await run
    await flushPromises()
    expect(documentIoGuarded(DOC)).toBe(false)
    expect(q(panelOf(DOC), 'document-window-textarea')).toBeNull()
    expect(saveButton(DOC).disabled).toBe(true)
    saveButton(DOC).click()
    await flushPromises()
    expect(contentSave).toHaveBeenCalledTimes(1)
    expect(notices()).not.toContain('upload_done')
    if (outcome === 'read-error') {
      reread.reject(http(403))
      await flushPromises()
      expect(q(panelOf(DOC), 'document-window-editor-error')).not.toBeNull()
      expect(saveButton(DOC).disabled).toBe(true)
      expect(q(panelOf(DOC), 'document-window-textarea')).toBeNull()
    } else {
      reread.resolve({ docId: DOC, content: NEW, missing: false })
      await flushPromises()
      expect(area(DOC)!.value).toContain('새 본문')
      expect(saveButton(DOC).disabled).toBe(false)
      saveButton(DOC).click()
      await flushPromises()
      expect(files.get(DOC)).toBe(NEW)
      expect(contentSave).toHaveBeenLastCalledWith(DOC, NEW)
    }
  })
})

// --- flowgate.v02.0074 T#2: a work plan in the window's header ----------------------------------------------------------

describe('a work plan’s header [업로드][다운로드] (flowgate.v02.0074 T#2, D#1 §6.3 / §7.5)', () => {
  const PLAN = WP_DOC

  function capture() {
    const blobs: Blob[] = []
    const names: string[] = []
    Object.assign(URL, {
      createObjectURL: vi.fn((blob: Blob) => {
        blobs.push(blob)
        return 'blob:wp'
      }),
      revokeObjectURL: vi.fn(),
    })
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download)
    })
    return { blobs, names, restore: () => anchorClick.mockRestore() }
  }

  beforeEach(() => {
    records[PLAN] = { doc_id: PLAN, type_code: 'WP', title: '작업계획', status: 'open', is_editable: true, project_id: 'flowgate', group_id: WP_FLOW }
    planEditRead.mockReset().mockImplementation(async () => readAnswer({ body: hiddenPlanBody() }))
    planDocRead.mockReset().mockImplementation(async () => toWorkPlanDocument(readAnswer({ body: hiddenPlanBody() })))
  })

  afterEach(() => {
    setWorkPlanEditOpener(null)
    closeAllWorkPlanEditWindows()
    setCurrentUser(null)
  })

  it('[다운로드]: the stored plan’s body only as <docId>.json, application/json — not the Markdown download', async () => {
    const { blobs, names, restore } = capture()
    try {
      await open(PLAN)
      await click(q(titlebar(PLAN), 'document-io-download'))
      await vi.waitFor(() => expect(names).toHaveLength(1))
      expect(planEditRead).toHaveBeenCalledWith(PLAN)
      expect(download).not.toHaveBeenCalled()
      expect(names).toEqual([`${PLAN}.json`])
      expect(blobs[0].type).toBe('application/json;charset=utf-8')
      const text = await documentIoReadFile(blobs[0])
      expect(text).toBe(`${JSON.stringify(hiddenPlanBody(), null, 2)}\n`)
      expect(JSON.parse(text)).not.toHaveProperty('revision_no')
      expect(notices()).toEqual([])
    } finally {
      restore()
    }
  })

  it('[업로드] (with the Designer’s opener): the plan window’s [JSON 가져오기]; no file pick, no PATCH', async () => {
    setWorkPlanEditOpener(() => ({ external: () => null }))
    await open(PLAN)
    const up = q(titlebar(PLAN), 'document-io-upload') as HTMLButtonElement
    expect(up.disabled).toBe(false)
    expect(up.getAttribute('title')).toBe('JSON 가져오기')
    await click(up)
    expect(workPlanEditWindowList().map((win) => [win.docId, win.request.projectId, win.request.groupId, win.request.initialMode])).toEqual([
      [PLAN, 'flowgate', WP_FLOW, 'import'],
    ])
    expect(contentSave).not.toHaveBeenCalled()
    // the plan window opens on its import (its own JSON file input); the document upload's Markdown picker never does
    await vi.waitFor(() => expect(document.body.querySelector('[data-test="wp-json-window"]')?.getAttribute('data-mode')).toBe('import'))
    expect(document.body.querySelector('input[type="file"][accept=".md,text/markdown,text/plain"]')).toBeNull()
  })

  it('without a Designer to tell the Flow’s verdict the [업로드] stays, off as locked', async () => {
    await open(PLAN)
    const up = q(titlebar(PLAN), 'document-io-upload') as HTMLButtonElement
    expect(up.disabled).toBe(true)
    expect(up.dataset.reason).toBe('locked')
    expect(up.getAttribute('title')).toBe('지금은 작업계획을 편집할 수 없어 가져올 수 없습니다.')
    await click(up)
    expect(workPlanEditWindowList()).toHaveLength(0)
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('recorded, not changed (T#2 out of scope, Q): the plan’s body view still offers the text [편집]', async () => {
    setCurrentUser({ username: 'worker', email: null, isAdmin: false })
    await open(PLAN)
    expect(editButton(PLAN)).not.toBeNull()
  })

  it('a document that is not a work plan keeps the hint, the file pick and the PATCH', async () => {
    await open(DOC)
    expect(q(titlebar(DOC), 'document-io-upload')!.getAttribute('title')).toBe('파일 내용으로 문서 전체를 교체')
    await upload(DOC, NEW)
    expect(contentSave).toHaveBeenCalledWith(DOC, NEW)
    expect(planEditRead).not.toHaveBeenCalled()
    expect(workPlanEditWindowList()).toHaveLength(0)
  })
})
