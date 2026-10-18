// flowgate.v02.0068 T#1 — the document upload / download coordinator (0068 D §2.1 / §3.1, L §2.12, §2.14,
// §2.15, §4.2 ~ §4.4, §4.6): the one entry the Inspector's document lines and the document window's header
// call. No new server API: the upload is the content save (the whole stored body), the download the canonical
// Markdown download, the buttons' judgement the document read.
// - Upload of one document id fixed at the press: check 1 (no window of it holds unsaved or saving text) →
//   the file pick → check 2 and the guard in one synchronous turn → read the file as text (a leading BOM
//   dropped, nothing else changed) → check 3 → save → reflect (every window of the document reads the new
//   stored body; on a failed save too: a save without an answer may have been stored) → release. After every
//   await the token must still be alive (checkpoints C1 ~ C4), else nothing is saved, reflected, sent, told or
//   changed; the release clears only what the token holds.
// - Download of one document id: the server's stored file only; a window of it with unsaved text is told
//   about (not in the file). No path ends with a rejected promise.
// - The buttons' state per document (info): the document read's answer, kept per document; READY is reused
//   when a line or a window draws the document again, every other state is read again.
// Notices are a queue of this tab, shown by DocumentWindows (one toast host for both screens).
import { reactive, ref } from 'vue'
import { normalizeApiInput } from '@shared/apiErrors'
import {
  documentContentSave,
  documentDownload,
  documentErrorOf,
  documentInfoRead,
  documentRead,
  type DocumentDownload,
} from '@/features/document'
import {
  documentIoAlive,
  documentIoBusy,
  documentIoConflict,
  documentIoDesignerExit,
  documentIoDownloading,
  documentIoDropInflight,
  documentIoEndDownload,
  documentIoEpoch,
  documentIoGuard,
  documentIoNewToken,
  documentIoReflectAll,
  documentIoRelease,
  documentIoResume,
  documentIoSetPhase,
  documentIoStartDownload,
  documentIoStartUpload,
  documentIoUnsaved,
  type DocumentIoToken,
} from './documentEditRegistry'
import {
  FILE_PICK_CANCEL_GRACE_MS,
  UPLOAD_ACCEPT,
  documentIoAccepts,
  documentIoDownloadCheckNotice,
  documentIoDownloadNotice,
  documentIoFallbackName,
  documentIoFilename,
  documentIoSafeName,
  documentIoSaveNotice,
  documentIoTooLarge,
  documentIoUploadText,
  documentIoView,
  type DocumentIoDownloadNotice,
  type DocumentIoInfo,
  type DocumentIoInfoState,
  type DocumentIoView,
} from './documentIoModel'

// --- Notices --------------------------------------------------------------------------------------------

export type DocumentIoNoticeTone = 'success' | 'warning' | 'danger'

export interface DocumentIoNotice {
  readonly id: number
  /** Message key under `gui.document_io.notice`. */
  readonly key: string
  readonly tone: DocumentIoNoticeTone
}

const notices = reactive<DocumentIoNotice[]>([])
let noticeSeq = 0

/** The notices to show, oldest first (reactive). */
export function documentIoNotices(): readonly DocumentIoNotice[] {
  return notices
}

export function dismissDocumentIoNotice(id: number): void {
  const index = notices.findIndex((item) => item.id === id)
  if (index !== -1) notices.splice(index, 1)
}

function notify(key: string, tone: DocumentIoNoticeTone): void {
  notices.push({ id: ++noticeSeq, key, tone })
}

// --- The buttons' state per document (L §2.15 / §4.6) ---------------------------------------------------

interface InfoEntry extends DocumentIoInfo {
  /** The request number of the latest read; an answer of an older read is dropped. */
  readonly seq: number
  readonly reading: boolean
}

const UNKNOWN: DocumentIoInfo = Object.freeze({ state: 'LOADING', record: null, uploadDenied: false })

const infos = reactive(new Map<string, InfoEntry>())
let infoSeq = 0
const infoGeneration = ref(0)

/** A mounted group re-draws after cache invalidation, even when its document did not change. */
export function documentIoInfoGeneration(): number {
  return infoGeneration.value
}

/** What the buttons know about the document (LOADING until a read answered). */
export function documentIoInfo(docId: string): DocumentIoInfo {
  return infos.get(docId) ?? UNKNOWN
}

function stateOf(status: number | null): DocumentIoInfoState {
  if (status === 403) return 'DENIED'
  if (status === 404) return 'GONE'
  return 'ERROR'
}

function readInfo(docId: string): void {
  const seq = ++infoSeq
  const current = infos.get(docId)
  infos.set(docId, current ? { ...current, seq, reading: true } : { ...UNKNOWN, seq, reading: true })
  const latest = () => infos.get(docId)?.seq === seq
  // Started in a microtask so a feature without the read (a test double) lands in the error answer.
  void Promise.resolve()
    .then(() => documentInfoRead(docId))
    .then(
      (record) => {
        if (latest()) infos.set(docId, { state: 'READY', record, uploadDenied: false, seq, reading: false })
      },
      (error) => {
        if (latest()) infos.set(docId, { state: stateOf(documentErrorOf(error).status), record: null, uploadDenied: false, seq, reading: false })
      },
    )
}

/**
 * A line or a window draws the document (first time, again after a re-selection or a re-open, or another
 * document now): a READY answer is reused, anything else is read again.
 */
export function documentIoDraw(docId: string): void {
  if (!docId) return
  const current = infos.get(docId)
  if (current && (current.state === 'READY' || current.reading)) return
  readInfo(docId)
}

/** The document may have changed (its events, a reconnect, a request's answer): read it again. */
export function documentIoRefresh(docId: string): void {
  if (docId) readInfo(docId)
}

function mark(docId: string, patch: Partial<DocumentIoInfo>): void {
  const current = infos.get(docId) ?? UNKNOWN
  // A request result is newer than every information read already started. Those responses must not
  // clear a refusal. A later explicit draw/event/refresh can check permission again.
  infos.set(docId, { ...current, ...patch, ...(patch.state ? { record: null } : {}), seq: ++infoSeq, reading: false })
}

/** Forgets what the buttons know (a project switch, leaving the Designer). */
export function documentIoForget(): void {
  infos.clear()
  infoGeneration.value += 1
}

/** Both buttons of the document: usable or why not, and their progress (reactive). */
export function documentIoButtons(docId: string): DocumentIoView {
  return documentIoView(documentIoInfo(docId), documentIoBusy(docId), documentIoDownloading(docId))
}

// A saved body (here or in the document window) is read again by the lines and windows that show it.
if (typeof window !== 'undefined') {
  window.addEventListener('fg:document_content_changed', (event) => {
    const docId = (event as CustomEvent<{ doc_id?: string | null }>).detail?.doc_id
    if (docId && infos.has(docId)) readInfo(docId)
  })
}

function contentChanged(docId: string): void {
  window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId } }))
}

// --- The file pick and read (L §4.2) --------------------------------------------------------------------

/**
 * Opens the browser's file picker. A pick ends it with the file; the picker's cancel event, or no pick within
 * FILE_PICK_CANCEL_GRACE_MS after the window has the focus back (browsers without the event), ends it with
 * null. A pick after that is dropped.
 */
export function documentIoPickFile(accept: string = UPLOAD_ACCEPT): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = accept
    input.style.display = 'none'
    let settled = false
    let grace: ReturnType<typeof setTimeout> | null = null
    const onFocus = () => {
      if (grace === null) grace = setTimeout(() => finish(null), FILE_PICK_CANCEL_GRACE_MS)
    }
    const finish = (file: File | null) => {
      if (settled) return
      settled = true
      if (grace !== null) clearTimeout(grace)
      window.removeEventListener('focus', onFocus)
      input.remove()
      resolve(file)
    }
    input.addEventListener('change', () => finish(input.files?.[0] ?? null))
    input.addEventListener('cancel', () => finish(null))
    document.body.appendChild(input)
    // The picker takes the focus while open: the focus coming back is listened for once it is open.
    setTimeout(() => window.addEventListener('focus', onFocus), 0)
    input.click()
  })
}

/** Reads a picked file as UTF-8 text (bytes that are not UTF-8 read as replacement characters, as v0.1). */
export function documentIoReadFile(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsText(file)
  })
}

/** Hands a downloaded file to the browser's save (an object URL on a download link, no new window). */
export function documentIoSaveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

// --- Upload (L §2.12) -----------------------------------------------------------------------------------

export interface DocumentIoUploadOptions {
  /** The file picker (documentIoPickFile). */
  readonly pick?: (accept: string) => Promise<File | null>
  /** The file reader (documentIoReadFile). */
  readonly read?: (file: File) => Promise<string>
}

type SaveOutcome = { readonly ok: true } | { readonly ok: false; readonly error: unknown }

async function settle(save: () => Promise<void>): Promise<SaveOutcome> {
  try {
    await save()
    return { ok: true }
  } catch (error) {
    return { ok: false, error }
  }
}

/** The previous Designer's save answered (L §2.12 orphan_settled): a content change signal, no notice. */
function orphanSettled(token: DocumentIoToken, outcome: SaveOutcome): void {
  if (!documentIoDropInflight(token)) return
  if (outcome.ok || documentErrorOf(outcome.error).kind === 'network') contentChanged(token.docId)
  documentIoResume(token.docId)
}

/** Replaces the document's whole stored body with a file's text (the document id is fixed here). */
export async function documentIoUpload(docId: string, options: DocumentIoUploadOptions = {}): Promise<void> {
  if (!docId || documentIoBusy(docId)) return
  // Check 1: unsaved or saving text of the document stays; the upload waits for it.
  if (documentIoConflict(docId)) {
    notify('upload_hold', 'warning')
    return
  }
  const token = documentIoNewToken(docId)
  documentIoStartUpload(token)
  let released = false
  try {
    let file: File | null
    try {
      file = await (options.pick ?? documentIoPickFile)(UPLOAD_ACCEPT)
    } catch {
      file = null
    }
    if (!documentIoAlive(token)) return // C1
    if (!file) return
    if (!documentIoAccepts(file)) {
      notify('upload_type', 'warning')
      return
    }
    if (documentIoTooLarge(file.size)) {
      notify('upload_too_large', 'warning')
      return
    }
    // Check 2 and the guard in one synchronous turn: nothing may start between them.
    if (documentIoConflict(docId)) {
      notify('upload_hold', 'warning')
      return
    }
    documentIoGuard(token)
    let text: string
    try {
      text = await (options.read ?? documentIoReadFile)(file)
    } catch {
      if (documentIoAlive(token)) notify('upload_read_failed', 'danger')
      return
    }
    if (!documentIoAlive(token)) return // C2: a read that ended after the Designer was left saves nothing
    text = documentIoUploadText(text)
    if (text.trim() === '') {
      notify('upload_empty', 'warning')
      return
    }
    // Check 3, right before the save (a save started before the guard may still run).
    if (documentIoConflict(docId)) {
      notify('upload_hold', 'warning')
      return
    }
    documentIoSetPhase(token, 'saving')
    const outcome = await settle(() => documentContentSave(docId, text))
    if (token.epoch !== documentIoEpoch()) {
      // C3: the save left before the Designer did and answered after.
      orphanSettled(token, outcome)
      return
    }
    if (!documentIoAlive(token)) return
    documentIoSetPhase(token, 'reflecting')
    // Saved or not: a failure without an answer may have been stored, so every window reads the server.
    await documentIoReflectAll(token)
    if (!documentIoAlive(token)) return // C4
    documentIoRelease(token)
    released = true
    if (outcome.ok) {
      contentChanged(docId)
      notify('upload_done', 'success')
      return
    }
    const error = documentErrorOf(outcome.error)
    notify(`save.${documentIoSaveNotice(error, normalizeApiInput(outcome.error).data.detail)}`, 'danger')
    if (error.status === 403) mark(docId, { uploadDenied: true })
    else if (error.status === 404) mark(docId, { state: 'GONE' })
  } finally {
    if (!released) documentIoRelease(token)
  }
}

// --- Download (L §2.14 / §4.4) --------------------------------------------------------------------------

export interface DocumentIoDownloadOptions {
  /** The browser save (documentIoSaveFile). */
  readonly save?: (blob: Blob, name: string) => void
}

/** Why a download failed; never throws (a failed follow-up read is a key as well). */
async function downloadNotice(error: unknown, docId: string): Promise<DocumentIoDownloadNotice> {
  try {
    const first = documentIoDownloadNotice(documentErrorOf(error))
    if (first !== 'check') return first
    try {
      await documentRead(docId)
      return documentIoDownloadCheckNotice(null)
    } catch (readError) {
      return documentIoDownloadCheckNotice(documentErrorOf(readError))
    }
  } catch {
    return 'download_failed'
  }
}

/** Saves the document's stored Markdown as a file (the server's file, never the editor's text). */
export async function documentIoDownload(docId: string, options: DocumentIoDownloadOptions = {}): Promise<void> {
  if (!docId || documentIoDownloading(docId)) return
  const token = documentIoNewToken(docId)
  documentIoStartDownload(token)
  try {
    let file: DocumentDownload
    try {
      file = await documentDownload(docId)
    } catch (error) {
      const key = await downloadNotice(error, docId)
      if (token.epoch !== documentIoEpoch()) return
      if (key === 'forbidden') mark(docId, { state: 'DENIED' })
      else if (key === 'gone') mark(docId, { state: 'GONE' })
      else if (key === 'no_file') documentIoRefresh(docId)
      notify(`download.${key}`, 'danger')
      return
    }
    if (token.epoch !== documentIoEpoch()) return // answered after the Designer was left: no file, no notice
    const name = documentIoSafeName(documentIoFilename(file.disposition) ?? documentIoFallbackName(docId))
    try {
      ;(options.save ?? documentIoSaveFile)(file.blob, name)
    } catch {
      notify('download.download_failed', 'danger')
      return
    }
    if (documentIoUnsaved(docId)) notify('download_unsaved_excluded', 'warning')
    if (token.epoch === documentIoEpoch()) documentIoRefresh(docId)
  } catch {
    // Defensive: a download never ends with a rejected promise.
  } finally {
    documentIoEndDownload(token)
  }
}

// --- Leaving the Designer ---------------------------------------------------------------------------------

/** The Designer is left (L §2.11 on_designer_exit, §2.15: the buttons' state is forgotten). */
export function documentIoLeave(): void {
  documentIoDesignerExit()
  documentIoForget()
}

/** Test support: no notices and no button state (the registry has its own reset). */
export function resetDocumentIo(): void {
  notices.splice(0, notices.length)
  infos.clear()
}
