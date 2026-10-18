/**
 * flowgate.v02.0068 T#1 — the document upload / download coordinator (0068 D §3.1, L §2.12, §2.14, §2.15, §4.3,
 * §4.4, §4.6, §5.4 U1 ~ U4 · D1 · B1 ~ B3) with `@/features/document` mocked and stand-in windows registered in the
 * edit state registry: the upload's checks 1 ~ 3, the guard, the save of the file text as it is, the reflect after
 * every attempted save, the release on every end, the notices; the Designer epochs (the checkpoints C1 ~ C4 and
 * the previous Designer's save); the download of the server's file; the buttons' document state.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const contentSave = vi.fn()
const download = vi.fn()
const infoRead = vi.fn()
const recordRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentContentSave: (...args: unknown[]) => contentSave(...args),
    documentDownload: (...args: unknown[]) => download(...args),
    documentInfoRead: (...args: unknown[]) => infoRead(...args),
    documentRead: (...args: unknown[]) => recordRead(...args),
  }
})

const { toDocumentInfo } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

import {
  documentIoBusy,
  documentIoDesignerExit,
  documentIoGuarded,
  registerDocumentEditEntry,
  resetDocumentEditRegistry,
  type DocumentEditEntry,
  type DocumentReflectResult,
} from '@/features/documentWindow/documentEditRegistry'
import {
  documentIoButtons,
  documentIoDownload,
  documentIoDraw,
  documentIoInfo,
  documentIoLeave,
  documentIoNotices,
  documentIoRefresh,
  documentIoUpload,
  resetDocumentIo,
} from '@/features/documentWindow/documentIo'

const A = 'flowgate.v02.0068.0007-T'
const B = 'flowgate.v02.0068.0008-TR'

const http = (status: number, data: unknown = { detail: 'x' }) => ({ isAxiosError: true, response: { status, data } })
const network = () => ({ isAxiosError: true })

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const md = (text: string, name = 'upload.md', type = 'text/markdown') => new File([text], name, { type })

/** The stand-in windows' unregister functions (their unmount). */
const registered: (() => void)[] = []

/** Closes every stand-in window (the registry's other state stays). */
function closeWindows() {
  for (const unregister of registered.splice(0)) unregister()
}

interface Win extends DocumentEditEntry {
  shown: string[]
  isDirty: boolean
  isSaving: boolean
  log: string[]
}

function win(shown: string[], reflectResult: DocumentReflectResult = 'ok'): Win {
  const w: Win = {
    shown,
    isDirty: false,
    isSaving: false,
    log: [],
    related: () => w.shown,
    editor: () => 'ready',
    dirty: () => w.isDirty,
    saving: () => w.isSaving,
    onGuard: (docId) => w.log.push(`guard:${docId}`),
    onRelease: (docId) => w.log.push(`release:${docId}`),
    reflect: (docId) => {
      w.log.push(`reflect:${docId}`)
      return Promise.resolve(reflectResult)
    },
  }
  registered.push(registerDocumentEditEntry(w))
  return w
}

const notices = () => documentIoNotices().map((item) => item.key)
const changed: string[] = []
const onChanged = (event: Event) => changed.push(`${(event as CustomEvent<{ doc_id: string }>).detail.doc_id}:${documentIoGuarded(A) ? 'guarded' : 'free'}`)

beforeEach(() => {
  registered.length = 0
  resetDocumentEditRegistry()
  resetDocumentIo()
  contentSave.mockReset().mockResolvedValue(undefined)
  download.mockReset()
  infoRead.mockReset().mockImplementation(async (docId: string) => toDocumentInfo({ doc_id: docId, is_editable: true, download_available: true }))
  recordRead.mockReset()
  changed.length = 0
  window.addEventListener('fg:document_content_changed', onChanged)
})

afterEach(() => {
  window.removeEventListener('fg:document_content_changed', onChanged)
  vi.useRealTimers()
  resetDocumentEditRegistry()
  resetDocumentIo()
})

// --- Upload -----------------------------------------------------------------------------------------------

describe('upload — the lifecycle (D §3.1)', () => {
  it('saves the file text as it is (only a leading BOM dropped) to the fixed id, reflects, releases, then tells', async () => {
    const a = win([A])
    const b = win([B])
    await documentIoUpload(A, { pick: async () => md('﻿---\r\ntitle: 새 문서\r\n---\r\n# 본문\r\n') })
    expect(contentSave).toHaveBeenCalledWith(A, '---\r\ntitle: 새 문서\r\n---\r\n# 본문\r\n')
    expect(a.log).toEqual([`guard:${A}`, `reflect:${A}`, `release:${A}`])
    expect(b.log).toEqual([])
    // The content change is sent once the guard is gone.
    expect(changed).toEqual([`${A}:free`])
    expect(notices()).toEqual(['upload_done'])
    expect(documentIoBusy(A)).toBe(false)
  })

  it('check 1: a window of the document with unsaved or saving text → held, nothing picked', async () => {
    const a = win([A])
    a.isDirty = true
    const pick = vi.fn()
    await documentIoUpload(A, { pick })
    expect(pick).not.toHaveBeenCalled()
    expect(notices()).toEqual(['upload_hold'])
    a.isDirty = false
    a.isSaving = true
    await documentIoUpload(A, { pick })
    expect(pick).not.toHaveBeenCalled()
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('check 1 looks at the document only: unsaved text of another document does not hold it', async () => {
    const b = win([B])
    b.isDirty = true
    await documentIoUpload(A, { pick: async () => md('# a') })
    expect(contentSave).toHaveBeenCalledWith(A, '# a')
  })

  it('a second press while the upload runs is ignored (same document); another document runs on its own', async () => {
    const pick = deferred<File | null>()
    const first = documentIoUpload(A, { pick: () => pick.promise })
    const again = vi.fn()
    await documentIoUpload(A, { pick: again })
    expect(again).not.toHaveBeenCalled()
    await documentIoUpload(B, { pick: async () => md('# b') })
    expect(contentSave).toHaveBeenCalledWith(B, '# b')
    pick.resolve(md('# a'))
    await first
    expect(contentSave).toHaveBeenCalledWith(A, '# a')
  })

  it('pick cancelled → nothing changes, no notice, released', async () => {
    const a = win([A])
    await documentIoUpload(A, { pick: async () => null })
    expect(contentSave).not.toHaveBeenCalled()
    expect(notices()).toEqual([])
    expect(a.log).toEqual([])
    expect(documentIoBusy(A)).toBe(false)
  })

  it('a file that is not Markdown / text → told, no guard', async () => {
    const a = win([A])
    await documentIoUpload(A, { pick: async () => md('%PDF', 'x.pdf', 'application/pdf') })
    expect(notices()).toEqual(['upload_type'])
    expect(a.log).toEqual([])
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('check 2: the editor turned dirty while the file was picked → held, the draft stays, no guard', async () => {
    const a = win([A])
    const pick = deferred<File | null>()
    const run = documentIoUpload(A, { pick: () => pick.promise })
    a.isDirty = true
    pick.resolve(md('# a'))
    await run
    expect(notices()).toEqual(['upload_hold'])
    expect(a.log).toEqual([])
    expect(contentSave).not.toHaveBeenCalled()
    expect(documentIoBusy(A)).toBe(false)
  })

  it('the guard starts with check 2, before the file is read', async () => {
    const a = win([A])
    const read = deferred<string>()
    const run = documentIoUpload(A, { pick: async () => md('x'), read: () => read.promise })
    await vi.waitFor(() => expect(a.log).toEqual([`guard:${A}`]))
    expect(documentIoGuarded(A)).toBe(true)
    read.resolve('# a')
    await run
    expect(documentIoGuarded(A)).toBe(false)
  })

  it('reading fails → told, released, nothing saved or reflected', async () => {
    const a = win([A])
    await documentIoUpload(A, { pick: async () => md('x'), read: async () => Promise.reject(new Error('io')) })
    expect(notices()).toEqual(['upload_read_failed'])
    expect(a.log).toEqual([`guard:${A}`, `release:${A}`])
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('an empty file (BOM and blanks only) → not saved, released', async () => {
    const a = win([A])
    await documentIoUpload(A, { pick: async () => md('﻿  \r\n\t\n') })
    expect(notices()).toEqual(['upload_empty'])
    expect(a.log).toEqual([`guard:${A}`, `release:${A}`])
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('check 3: a save started before the guard is still running → not saved, released', async () => {
    const a = win([A])
    const read = deferred<string>()
    const run = documentIoUpload(A, { pick: async () => md('x'), read: () => read.promise })
    await vi.waitFor(() => expect(documentIoGuarded(A)).toBe(true))
    a.isSaving = true
    read.resolve('# a')
    await run
    expect(notices()).toEqual(['upload_hold'])
    expect(contentSave).not.toHaveBeenCalled()
    expect(a.log).toEqual([`guard:${A}`, `release:${A}`])
  })

  it.each([
    [http(403), 'save.forbidden'],
    [http(404), 'save.gone'],
    [http(409, { detail: 'busy' }), 'save.busy'],
    [http(409, { detail: 'file_exists_unrecorded' }), 'save.file_exists_unrecorded'],
    [http(422), 'save.locked'],
    [network(), 'save.network'],
    [http(500), 'save.save'],
  ])('a refused save still reflects (the server may have stored it), releases and tells: %#', async (error, key) => {
    const a = win([A])
    contentSave.mockRejectedValue(error)
    await expect(documentIoUpload(A, { pick: async () => md('# a') })).resolves.toBeUndefined()
    expect(a.log).toEqual([`guard:${A}`, `reflect:${A}`, `release:${A}`])
    expect(notices()).toEqual([key])
    expect(changed).toEqual([])
    expect(documentIoBusy(A)).toBe(false)
  })

  it('a window closed while the guard holds is not waited for (released all the same)', async () => {
    const a = win([A])
    a.reflect = () => {
      a.log.push('reflect')
      return new Promise(() => {})
    }
    const run = documentIoUpload(A, { pick: async () => md('# a') })
    await vi.waitFor(() => expect(a.log).toContain('reflect'))
    expect(documentIoGuarded(A)).toBe(true)
    closeWindows()
    await run
    expect(documentIoGuarded(A)).toBe(false)
    expect(notices()).toEqual(['upload_done'])
  })

  it('a window that never answers holds the guard REFLECT_TIMEOUT_MS at most', async () => {
    vi.useFakeTimers()
    const a = win([A])
    a.reflect = () => new Promise(() => {})
    // (the file reader of jsdom runs on timers: a plain reader here)
    const run = documentIoUpload(A, { pick: async () => md('# a'), read: async () => '# a' })
    await vi.advanceTimersByTimeAsync(0)
    expect(documentIoGuarded(A)).toBe(true)
    await vi.advanceTimersByTimeAsync(9999)
    expect(documentIoGuarded(A)).toBe(true)
    await vi.advanceTimersByTimeAsync(1)
    await run
    expect(documentIoGuarded(A)).toBe(false)
    expect(notices()).toEqual(['upload_done'])
  })

  it('the default reader reads the picked file as UTF-8 text', async () => {
    await documentIoUpload(A, { pick: async () => md('﻿# 한글 본문\n') })
    expect(contentSave).toHaveBeenCalledWith(A, '# 한글 본문\n')
  })
})

// --- Designer epochs (L §5.4 U1 ~ U4) ------------------------------------------------------------------

describe('leaving the Designer during an upload', () => {
  it('U1: reading → leave → upload the same document again → the old read ends late: no save, no notice; the new one holds', async () => {
    const oldRead = deferred<string>()
    const old = documentIoUpload(A, { pick: async () => md('old'), read: () => oldRead.promise })
    await vi.waitFor(() => expect(documentIoGuarded(A)).toBe(true))
    documentIoLeave()
    expect(documentIoGuarded(A)).toBe(false)
    const newRead = deferred<string>()
    const fresh = documentIoUpload(A, { pick: async () => md('new'), read: () => newRead.promise })
    await vi.waitFor(() => expect(documentIoGuarded(A)).toBe(true))
    oldRead.resolve('# old')
    await old
    expect(contentSave).not.toHaveBeenCalled()
    expect(documentIoGuarded(A)).toBe(true)
    expect(documentIoBusy(A)).toBe(true)
    newRead.resolve('# new')
    await fresh
    expect(contentSave).toHaveBeenCalledTimes(1)
    expect(contentSave).toHaveBeenCalledWith(A, '# new')
    expect(notices()).toEqual(['upload_done'])
  })

  it('U2: the save was sent → leave → the document stays guarded in the new Designer until it answers; then a change signal, no notice', async () => {
    const save = deferred<void>()
    contentSave.mockReturnValue(save.promise)
    const old = documentIoUpload(A, { pick: async () => md('# a') })
    await vi.waitFor(() => expect(contentSave).toHaveBeenCalled())
    documentIoLeave()
    const a = win([A])
    expect(documentIoGuarded(A)).toBe(true)
    expect(documentIoButtons(A).upload).toMatchObject({ enabled: false, reason: 'uploading' })
    const pick = vi.fn()
    await documentIoUpload(A, { pick })
    expect(pick).not.toHaveBeenCalled()
    save.resolve()
    await old
    expect(documentIoGuarded(A)).toBe(false)
    expect(changed).toEqual([`${A}:free`])
    expect(notices()).toEqual([])
    expect(a.log).toEqual([`release:${A}`])
    await documentIoUpload(A, { pick: async () => md('# again') })
    expect(contentSave).toHaveBeenLastCalledWith(A, '# again')
  })

  it('U2: a previous save that failed without an answer also signals the change; a refused one does not', async () => {
    for (const [error, signals] of [[network(), true], [http(403), false]] as const) {
      changed.length = 0
      const save = deferred<void>()
      contentSave.mockReturnValueOnce(save.promise)
      const old = documentIoUpload(A, { pick: async () => md('# a') })
      await vi.waitFor(() => expect(documentIoGuarded(A)).toBe(true))
      await vi.waitFor(() => expect(contentSave).toHaveBeenCalled())
      documentIoDesignerExit()
      save.reject(error)
      await old
      expect(changed.length > 0).toBe(signals)
      expect(documentIoGuarded(A)).toBe(false)
      contentSave.mockClear()
    }
    expect(notices()).toEqual([])
  })

  it('U3: the file is picked after the Designer was left → nothing happens', async () => {
    const pick = deferred<File | null>()
    const run = documentIoUpload(A, { pick: () => pick.promise })
    documentIoLeave()
    pick.resolve(md('# a'))
    await run
    expect(contentSave).not.toHaveBeenCalled()
    expect(notices()).toEqual([])
    expect(documentIoBusy(A)).toBe(false)
  })

  it('U4: leaving while the windows reflect → no change signal, no notice', async () => {
    const a = win([A])
    a.reflect = () => {
      a.log.push('reflect')
      return new Promise(() => {})
    }
    const run = documentIoUpload(A, { pick: async () => md('# a') })
    await vi.waitFor(() => expect(a.log).toContain('reflect'))
    documentIoLeave()
    await run
    expect(changed).toEqual([])
    expect(notices()).toEqual([])
    expect(documentIoGuarded(A)).toBe(false)
  })
})

// --- Download (L §2.14 / §4.4) -------------------------------------------------------------------------

describe('download', () => {
  it('saves the server file under the server’s name, for the fixed id', async () => {
    const blob = new Blob(['---\ntitle: x\n---\n# 저장본\n'])
    download.mockResolvedValue({ blob, disposition: 'attachment; filename="0007-T_document.md"' })
    const save = vi.fn()
    await documentIoDownload(A, { save })
    expect(download).toHaveBeenCalledWith(A)
    expect(save).toHaveBeenCalledWith(blob, '0007-T_document.md')
    expect(notices()).toEqual([])
  })

  it('no file name in the answer → {doc_id}.md', async () => {
    download.mockResolvedValue({ blob: new Blob(['x']), disposition: null })
    const save = vi.fn()
    await documentIoDownload(A, { save })
    expect(save).toHaveBeenCalledWith(expect.any(Blob), `${A}.md`)
  })

  it('a window of the document with unsaved text: the download goes on and tells the text is not in it', async () => {
    const a = win([A])
    a.isDirty = true
    download.mockResolvedValue({ blob: new Blob(['x']), disposition: null })
    const save = vi.fn()
    await documentIoDownload(A, { save })
    expect(save).toHaveBeenCalled()
    expect(notices()).toEqual(['download_unsaved_excluded'])
  })

  it('works while an upload guards the document (the server file only)', async () => {
    const read = deferred<string>()
    const run = documentIoUpload(A, { pick: async () => md('x'), read: () => read.promise })
    await vi.waitFor(() => expect(documentIoGuarded(A)).toBe(true))
    download.mockResolvedValue({ blob: new Blob(['x']), disposition: null })
    const save = vi.fn()
    await documentIoDownload(A, { save })
    expect(save).toHaveBeenCalled()
    read.resolve('# a')
    await run
  })

  it.each([
    ['ok', 'no_file'],
    ['404', 'gone'],
    ['403', 'forbidden'],
    ['network', 'network'],
    ['500', 'download_failed'],
  ])('D1: 404 → the document read %s → %s; the promise resolves and the button is usable again', async (read, key) => {
    download.mockRejectedValue(http(404))
    recordRead.mockImplementation(async () => {
      if (read === 'ok') return { docId: A }
      if (read === 'network') throw network()
      throw http(Number(read))
    })
    await expect(documentIoDownload(A, { save: vi.fn() })).resolves.toBeUndefined()
    expect(notices()).toEqual([`download.${key}`])
    expect(documentIoButtons(A).download.busy).toBe(false)
  })

  it.each([
    [http(403), 'forbidden', 'DENIED'],
    [http(422), 'invalid_id', null],
    [network(), 'network', null],
    [http(500), 'download_failed', null],
  ])('the download’s own refusal → its notice and state: %#', async (error, key, state) => {
    infoRead.mockImplementation(() => new Promise(() => {}))
    download.mockRejectedValue(error)
    await documentIoDownload(A, { save: vi.fn() })
    expect(notices()).toEqual([`download.${key}`])
    if (state) expect(documentIoInfo(A).state).toBe(state)
  })

  it('the browser save fails → download_failed', async () => {
    download.mockResolvedValue({ blob: new Blob(['x']), disposition: null })
    await documentIoDownload(A, {
      save: () => {
        throw new Error('blocked')
      },
    })
    expect(notices()).toEqual(['download.download_failed'])
  })

  it('answered after the Designer was left → no file, no notice', async () => {
    const answer = deferred<{ blob: Blob; disposition: string | null }>()
    download.mockReturnValue(answer.promise)
    const save = vi.fn()
    const run = documentIoDownload(A, { save })
    documentIoLeave()
    answer.resolve({ blob: new Blob(['x']), disposition: null })
    await run
    expect(save).not.toHaveBeenCalled()
    expect(notices()).toEqual([])
  })

  it('a second press while it runs is ignored', async () => {
    const answer = deferred<{ blob: Blob; disposition: string | null }>()
    download.mockReturnValue(answer.promise)
    const run = documentIoDownload(A, { save: vi.fn() })
    expect(documentIoButtons(A).download).toMatchObject({ busy: true, enabled: false, reason: 'downloading' })
    await documentIoDownload(A, { save: vi.fn() })
    expect(download).toHaveBeenCalledTimes(1)
    answer.resolve({ blob: new Blob(['x']), disposition: null })
    await run
  })
})

// --- The buttons' document state (L §2.15 / §4.6, §5.4 B1 ~ B3) --------------------------------------

describe('the buttons’ document state', () => {
  it('LOADING until the read answers (both usable), then READY', async () => {
    const read = deferred<unknown>()
    infoRead.mockReturnValue(read.promise)
    documentIoDraw(A)
    expect(documentIoInfo(A).state).toBe('LOADING')
    expect(documentIoButtons(A)).toMatchObject({ upload: { enabled: true }, download: { enabled: true } })
    read.resolve(toDocumentInfo({ doc_id: A, is_editable: false, download_available: false }))
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('READY'))
    expect(documentIoButtons(A)).toMatchObject({ upload: { enabled: false, reason: 'locked' }, download: { enabled: false, reason: 'no_file' } })
  })

  it('B1: read 403 → DENIED, both disabled; drawn again (re-selection) → read again', async () => {
    infoRead.mockRejectedValueOnce(http(403))
    documentIoDraw(A)
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('DENIED'))
    expect(documentIoButtons(A)).toMatchObject({ upload: { reason: 'forbidden' }, download: { reason: 'forbidden' } })
    documentIoDraw(A)
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('READY'))
    expect(infoRead).toHaveBeenCalledTimes(2)
  })

  it('B2: read fails without an answer → ERROR, both usable; the press’s answer updates the state', async () => {
    infoRead.mockRejectedValue(network())
    documentIoDraw(A)
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('ERROR'))
    expect(documentIoButtons(A)).toMatchObject({ upload: { enabled: true }, download: { enabled: true } })
    download.mockRejectedValue(http(403))
    await documentIoDownload(A, { save: vi.fn() })
    expect(documentIoInfo(A).state).toBe('DENIED')
  })

  it('B3: upload save 403 → only the upload is disabled (권한 없음), the download stays', async () => {
    documentIoDraw(A)
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('READY'))
    contentSave.mockRejectedValue(http(403))
    await documentIoUpload(A, { pick: async () => md('# a') })
    expect(documentIoButtons(A)).toMatchObject({ upload: { enabled: false, reason: 'forbidden' }, download: { enabled: true } })
  })

  it('upload save 404 → GONE', async () => {
    contentSave.mockRejectedValue(http(404))
    await documentIoUpload(A, { pick: async () => md('# a') })
    expect(documentIoInfo(A).state).toBe('GONE')
  })

  it('READY is reused when drawn again; a refresh always reads; an older answer is dropped', async () => {
    documentIoDraw(A)
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('READY'))
    documentIoDraw(A)
    expect(infoRead).toHaveBeenCalledTimes(1)
    const slow = deferred<unknown>()
    const fast = deferred<unknown>()
    infoRead.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise)
    documentIoRefresh(A)
    documentIoRefresh(A)
    await vi.waitFor(() => expect(infoRead).toHaveBeenCalledTimes(3))
    fast.resolve(toDocumentInfo({ doc_id: A, is_editable: true }))
    await vi.waitFor(() => expect(documentIoInfo(A).record?.isEditable).toBe(true))
    slow.resolve(toDocumentInfo({ doc_id: A, is_editable: false }))
    await Promise.resolve()
    await Promise.resolve()
    expect(documentIoInfo(A).record?.isEditable).toBe(true)
  })

  it('a content change of a known document reads it again (and clears an upload refusal)', async () => {
    documentIoDraw(A)
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('READY'))
    contentSave.mockRejectedValue(http(403))
    await documentIoUpload(A, { pick: async () => md('# a') })
    expect(documentIoInfo(A).uploadDenied).toBe(true)
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: A } }))
    await vi.waitFor(() => expect(documentIoInfo(A).uploadDenied).toBe(false))
  })

  it('leaving the Designer forgets the state (read again next time)', async () => {
    documentIoDraw(A)
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('READY'))
    documentIoLeave()
    expect(documentIoInfo(A).state).toBe('LOADING')
    documentIoDraw(A)
    expect(infoRead).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(infoRead).toHaveBeenCalledTimes(2))
  })
})


describe('review regression — request results supersede pending information', () => {
  it.each([['upload', 403], ['upload', 404], ['download', 403], ['download', 404]] as const)(
    '%s %i cannot be cleared by an older successful read; an explicit recheck can recover', async (operation, status) => {
      const old = deferred<ReturnType<typeof toDocumentInfo>>()
      infoRead.mockReturnValueOnce(old.promise)
      documentIoDraw(A)
      await vi.waitFor(() => expect(infoRead).toHaveBeenCalledTimes(1))
      if (operation === 'upload') {
        contentSave.mockRejectedValueOnce(http(status))
        await documentIoUpload(A, { pick: async () => md('NEW') })
      } else {
        download.mockRejectedValueOnce(http(status))
        recordRead.mockRejectedValueOnce(http(status))
        await documentIoDownload(A)
      }
      const refused = { ...documentIoInfo(A) }
      expect(documentIoButtons(A).upload.enabled).toBe(false)
      if (status === 404) expect(refused.state).toBe('GONE')
      else if (operation === 'upload') expect(refused.uploadDenied).toBe(true)
      else expect(refused.state).toBe('DENIED')
      old.resolve(toDocumentInfo({ doc_id: A, is_editable: true, download_available: true }))
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
      expect(documentIoInfo(A)).toEqual(refused)
      documentIoDraw(A)
      await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('READY'))
      expect(infoRead).toHaveBeenCalledTimes(2)
      expect(documentIoButtons(A).upload.enabled).toBe(true)
    },
  )

  it('download 200 refreshes the fixed document and clears a cached upload refusal', async () => {
    documentIoDraw(A)
    await vi.waitFor(() => expect(documentIoInfo(A).state).toBe('READY'))
    contentSave.mockRejectedValueOnce(http(403))
    await documentIoUpload(A, { pick: async () => md('NEW') })
    expect(documentIoInfo(A).uploadDenied).toBe(true)
    infoRead.mockResolvedValueOnce(toDocumentInfo({ doc_id: A, is_editable: false, download_available: false }))
    download.mockResolvedValueOnce({ blob: new Blob(['CANONICAL']), disposition: null })
    const save = vi.fn()
    await documentIoDownload(A, { save })
    await vi.waitFor(() => expect(infoRead).toHaveBeenCalledTimes(2))
    expect(infoRead).toHaveBeenLastCalledWith(A)
    await vi.waitFor(() => expect(documentIoInfo(A)).toMatchObject({ state: 'READY', uploadDenied: false, record: { isEditable: false, downloadAvailable: false } }))
    expect(save).toHaveBeenCalledTimes(1)
  })
})
