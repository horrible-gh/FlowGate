/**
 * flowgate.v02.0068 T#1 — the document edit state registry (0068 L §2.11): entries of open windows, the per
 * document upload (running), guard and the previous Designer's save (inflight), epochs and tokens, the release of
 * one's own token only, the reflect request with its wait (answers, a closed window, the time limit, leaving the
 * Designer) and leaving the Designer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  documentIoAlive,
  documentIoBusy,
  documentIoConflict,
  documentIoDesignerExit,
  documentIoDropInflight,
  documentIoEpoch,
  documentIoGuard,
  documentIoGuarded,
  documentIoInflight,
  documentIoNewToken,
  documentIoReflectAll,
  documentIoRelease,
  documentIoRunning,
  documentIoSetPhase,
  documentIoStartUpload,
  documentIoUnsaved,
  registerDocumentEditEntry,
  resetDocumentEditRegistry,
  type DocumentEditEntry,
  type DocumentReflectResult,
} from '@/features/documentWindow/documentEditRegistry'

const A = 'flowgate.v02.0068.0007-T'
const B = 'flowgate.v02.0068.0008-TR'

interface FakeWindow extends DocumentEditEntry {
  shown: string[]
  isDirty: boolean
  isSaving: boolean
  calls: string[]
  answer: (result: DocumentReflectResult) => void
}

function fakeWindow(shown: string[], answerNow: DocumentReflectResult | null = 'ok'): FakeWindow {
  let resolve: (result: DocumentReflectResult) => void = () => {}
  const win: FakeWindow = {
    shown,
    isDirty: false,
    isSaving: false,
    calls: [],
    answer: (result) => resolve(result),
    related: () => win.shown,
    editor: () => 'ready',
    dirty: () => win.isDirty,
    saving: () => win.isSaving,
    onGuard: (docId) => win.calls.push(`guard:${docId}`),
    onRelease: (docId) => win.calls.push(`release:${docId}`),
    reflect: (docId) => {
      win.calls.push(`reflect:${docId}`)
      if (answerNow) return Promise.resolve(answerNow)
      return new Promise((r) => (resolve = r))
    },
  }
  return win
}

beforeEach(() => resetDocumentEditRegistry())
afterEach(() => {
  vi.useRealTimers()
  resetDocumentEditRegistry()
})

describe('conflict / busy / guarded', () => {
  it('conflict = a window of the document holds unsaved or saving text; another document does not count', () => {
    const win = fakeWindow([A])
    registerDocumentEditEntry(win)
    expect(documentIoConflict(A)).toBe(false)
    win.isDirty = true
    expect(documentIoConflict(A)).toBe(true)
    expect(documentIoUnsaved(A)).toBe(true)
    expect(documentIoConflict(B)).toBe(false)
    win.isDirty = false
    win.isSaving = true
    expect(documentIoConflict(A)).toBe(true)
    expect(documentIoUnsaved(A)).toBe(false)
  })

  it('related() is read at call time (a clean window that moved to another document leaves)', () => {
    const win = fakeWindow([A])
    win.isDirty = true
    registerDocumentEditEntry(win)
    win.shown = [B]
    expect(documentIoConflict(A)).toBe(false)
    expect(documentIoConflict(B)).toBe(true)
  })

  it('an unregistered window counts no more', () => {
    const win = fakeWindow([A])
    win.isDirty = true
    const unregister = registerDocumentEditEntry(win)
    unregister()
    expect(documentIoConflict(A)).toBe(false)
  })

  it('busy while an upload runs; guarded once the guard starts; the guard tells the windows of that document only', () => {
    const a = fakeWindow([A])
    const b = fakeWindow([B])
    registerDocumentEditEntry(a)
    registerDocumentEditEntry(b)
    const token = documentIoNewToken(A)
    documentIoStartUpload(token)
    expect(documentIoBusy(A)).toBe(true)
    expect(documentIoGuarded(A)).toBe(false)
    expect(documentIoRunning(A)?.phase).toBe('picking')
    documentIoGuard(token)
    expect(documentIoGuarded(A)).toBe(true)
    expect(documentIoRunning(A)?.phase).toBe('reading')
    expect(a.calls).toEqual([`guard:${A}`])
    expect(b.calls).toEqual([])
    expect(documentIoBusy(B)).toBe(false)
  })
})

describe('tokens and the release of one’s own token', () => {
  it('release clears the guard and the upload of its token and tells the windows', () => {
    const win = fakeWindow([A])
    registerDocumentEditEntry(win)
    const token = documentIoNewToken(A)
    documentIoStartUpload(token)
    documentIoGuard(token)
    documentIoRelease(token)
    expect(documentIoBusy(A)).toBe(false)
    expect(documentIoGuarded(A)).toBe(false)
    expect(win.calls).toEqual([`guard:${A}`, `release:${A}`])
  })

  it('an older token’s release does not touch a newer upload of the same document', () => {
    const old = documentIoNewToken(A)
    documentIoStartUpload(old)
    documentIoGuard(old)
    documentIoDesignerExit()
    const fresh = documentIoNewToken(A)
    documentIoStartUpload(fresh)
    documentIoGuard(fresh)
    documentIoRelease(old)
    expect(documentIoAlive(fresh)).toBe(true)
    expect(documentIoGuarded(A)).toBe(true)
    expect(documentIoAlive(old)).toBe(false)
  })

  it('alive needs the epoch and the running token', () => {
    const token = documentIoNewToken(A)
    expect(documentIoAlive(token)).toBe(false)
    documentIoStartUpload(token)
    expect(documentIoAlive(token)).toBe(true)
    documentIoSetPhase(token, 'saving')
    expect(documentIoRunning(A)?.phase).toBe('saving')
    documentIoDesignerExit()
    expect(documentIoAlive(token)).toBe(false)
  })
})

describe('reflect (L §2.12 reflect_all)', () => {
  it('asks every window of the document at once and ends when all answered', async () => {
    const a1 = fakeWindow([A], null)
    const a2 = fakeWindow([A], null)
    const b = fakeWindow([B], null)
    ;[a1, a2, b].forEach((win) => registerDocumentEditEntry(win))
    const token = documentIoNewToken(A)
    let done = false
    void documentIoReflectAll(token).then(() => (done = true))
    expect(a1.calls).toEqual([`reflect:${A}`])
    expect(a2.calls).toEqual([`reflect:${A}`])
    expect(b.calls).toEqual([])
    a1.answer('ok')
    await Promise.resolve()
    await Promise.resolve()
    expect(done).toBe(false)
    a2.answer('failed')
    await vi.waitFor(() => expect(done).toBe(true))
  })

  it('a window that closes counts as answered (skipped)', async () => {
    const win = fakeWindow([A], null)
    const unregister = registerDocumentEditEntry(win)
    let done = false
    void documentIoReflectAll(documentIoNewToken(A)).then(() => (done = true))
    unregister()
    await vi.waitFor(() => expect(done).toBe(true))
  })

  it('no window → done at once', async () => {
    await expect(documentIoReflectAll(documentIoNewToken(A))).resolves.toBeUndefined()
  })

  it('ends after REFLECT_TIMEOUT_MS without an answer', async () => {
    vi.useFakeTimers()
    registerDocumentEditEntry(fakeWindow([A], null))
    let done = false
    void documentIoReflectAll(documentIoNewToken(A)).then(() => (done = true))
    await vi.advanceTimersByTimeAsync(9999)
    expect(done).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(done).toBe(true)
  })

  it('ends at once when the Designer is left', async () => {
    registerDocumentEditEntry(fakeWindow([A], null))
    let done = false
    void documentIoReflectAll(documentIoNewToken(A)).then(() => (done = true))
    documentIoDesignerExit()
    await vi.waitFor(() => expect(done).toBe(true))
  })

  it('a window whose reflect throws counts as failed (the wait still ends)', async () => {
    const win = fakeWindow([A])
    win.reflect = () => {
      throw new Error('x')
    }
    registerDocumentEditEntry(win)
    await expect(documentIoReflectAll(documentIoNewToken(A))).resolves.toBeUndefined()
  })
})

describe('leaving the Designer (L §2.11 on_designer_exit)', () => {
  it('a save already sent stays as inflight (still guarded, busy); other phases end; the epoch moves on', () => {
    const saving = documentIoNewToken(A)
    documentIoStartUpload(saving)
    documentIoGuard(saving)
    documentIoSetPhase(saving, 'saving')
    const reading = documentIoNewToken(B)
    documentIoStartUpload(reading)
    documentIoGuard(reading)
    const before = documentIoEpoch()
    documentIoDesignerExit()
    expect(documentIoEpoch()).toBe(before + 1)
    expect(documentIoInflight(A)).toBe(saving)
    expect(documentIoGuarded(A)).toBe(true)
    expect(documentIoBusy(A)).toBe(true)
    expect(documentIoGuarded(B)).toBe(false)
    expect(documentIoBusy(B)).toBe(false)
    expect(documentIoDropInflight(reading)).toBe(false)
    expect(documentIoDropInflight(saving)).toBe(true)
    expect(documentIoGuarded(A)).toBe(false)
  })

  it('an open window whose document was guarded hears the release (its deferred read starts)', () => {
    const win = fakeWindow([B])
    registerDocumentEditEntry(win)
    const reading = documentIoNewToken(B)
    documentIoStartUpload(reading)
    documentIoGuard(reading)
    documentIoDesignerExit()
    expect(win.calls).toEqual([`guard:${B}`, `release:${B}`])
  })

  it('inflight outlives another exit', () => {
    const saving = documentIoNewToken(A)
    documentIoStartUpload(saving)
    documentIoSetPhase(saving, 'saving')
    documentIoDesignerExit()
    documentIoDesignerExit()
    expect(documentIoInflight(A)).toBe(saving)
  })
})
