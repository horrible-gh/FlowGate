// flowgate.v02.0068 T#1 — what the document upload / download decides without a screen or a server call
// (0068 L §1.3, §2.12, §2.14, §2.15, §4.3, §4.4): the parameters, the file checks, the error → notice keys,
// the file name of a download, and whether each button is usable (with its tooltip key).
import type { DocumentError, DocumentInfo } from '@/features/document'

/** The file picker's filter (the v0.1 GenericDocumentBody upload). */
export const UPLOAD_ACCEPT = '.md,text/markdown,text/plain'
/** Extensions accepted after the pick (case-insensitive); a `text/` MIME type is accepted as well. */
export const UPLOAD_EXT_ALLOWED: ReadonlySet<string> = new Set(['md', 'markdown', 'txt'])
/** Without a cancel event: no pick within this time after the window has the focus back is a cancel. */
export const FILE_PICK_CANCEL_GRACE_MS = 1000
/** How long the upload waits for the open windows to answer its reflect request before it releases. */
export const REFLECT_TIMEOUT_MS = 10000
/**
 * Upload file size limit. The server's content save has none (0068 T#1 DEFERRED, see the TR): the body
 * model is a plain `str`, the save writes the bytes as they are and no middleware bounds a request body.
 * null keeps L §2.12's TBD branch: no size check before the save.
 */
export const UPLOAD_MAX_BYTES: number | null = null

/** The file name of a download whose response names none (the server's own rule). */
export function documentIoFallbackName(docId: string): string {
  return `${docId}.md`
}

/** A picked file the upload takes: a Markdown / text extension, or a `text/` MIME type. */
export function documentIoAccepts(file: { readonly name: string; readonly type?: string }): boolean {
  const dot = file.name.lastIndexOf('.')
  const ext = dot === -1 ? '' : file.name.slice(dot + 1).toLowerCase()
  return UPLOAD_EXT_ALLOWED.has(ext) || (file.type ?? '').toLowerCase().startsWith('text/')
}

/** A file over the limit (never while the limit is TBD). */
export function documentIoTooLarge(size: number, limit: number | null = UPLOAD_MAX_BYTES): boolean {
  return limit !== null && size > limit
}

/** The text saved for an upload: the file as read, without a leading BOM. Nothing else changes. */
export function documentIoUploadText(text: string): string {
  return text.startsWith('﻿') ? text.slice(1) : text
}

/** Message key under `gui.document_io.notice.save` for a refused upload save (L §4.3). */
export type DocumentIoSaveNotice = 'locked' | 'file_exists_unrecorded' | 'busy' | 'forbidden' | 'gone' | 'network' | 'save'

export function documentIoSaveNotice(error: DocumentError, detail: unknown): DocumentIoSaveNotice {
  if (error.status === 422) return 'locked'
  if (error.status === 409 && detail === 'file_exists_unrecorded') return 'file_exists_unrecorded'
  if (error.status === 409) return 'busy'
  if (error.status === 403) return 'forbidden'
  if (error.status === 404) return 'gone'
  if (error.kind === 'network') return 'network'
  return 'save'
}

/** Message key under `gui.document_io.notice.download` for a failed download (L §4.4). */
export type DocumentIoDownloadNotice = 'forbidden' | 'gone' | 'no_file' | 'invalid_id' | 'network' | 'download_failed'

/**
 * The download's own answer: a key, or 'check' for a 404 — a document without a file and a document that is
 * gone both answer 404, so the caller reads the document to tell them apart (documentIoDownloadCheckNotice).
 */
export function documentIoDownloadNotice(error: DocumentError): DocumentIoDownloadNotice | 'check' {
  if (error.status === 403) return 'forbidden'
  if (error.status === 404) return 'check'
  if (error.status === 422) return 'invalid_id'
  if (error.kind === 'network') return 'network'
  return 'download_failed'
}

/** After a download 404: the document read answered (null: it was read, the document is there). */
export function documentIoDownloadCheckNotice(readError: DocumentError | null): DocumentIoDownloadNotice {
  if (readError === null) return 'no_file'
  if (readError.status === 404) return 'gone'
  if (readError.status === 403) return 'forbidden'
  if (readError.kind === 'network') return 'network'
  return 'download_failed'
}

/** The file name a `Content-Disposition` header gives: `filename*=UTF-8''…` first, else `filename="…"`. */
export function documentIoFilename(disposition: string | null): string | null {
  if (!disposition) return null
  const extended = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(disposition)
  if (extended) {
    try {
      const name = decodeURIComponent(extended[1].trim().replace(/^"|"$/g, ''))
      if (name) return name
    } catch {
      // A broken percent encoding: the plain name below.
    }
  }
  const plain = /filename\s*=\s*("([^"]*)"|[^;]+)/.exec(disposition)
  const name = (plain?.[2] ?? plain?.[1] ?? '').trim()
  return name || null
}

/** A file name the browser may save under: path separators and control characters become `_`. */
export function documentIoSafeName(name: string): string {
  return name.replace(/[\\/\u0000-\u001f\u007f]/g, '_')
}

// --- Button availability (L §2.15) ------------------------------------------------------------------

/** What the buttons know about the document (the document read's answer). */
export type DocumentIoInfoState = 'LOADING' | 'READY' | 'DENIED' | 'GONE' | 'ERROR'

export interface DocumentIoInfo {
  readonly state: DocumentIoInfoState
  readonly record: DocumentInfo | null
  /** The upload save was refused with 403 (the document reads, it cannot be changed by this user). */
  readonly uploadDenied: boolean
}

/** Tooltip key under `gui.document_io.disabled` of a disabled button. */
export type DocumentIoDisabledKey = 'uploading' | 'downloading' | 'forbidden' | 'locked' | 'no_file'

export interface DocumentIoButtonView {
  readonly enabled: boolean
  readonly busy: boolean
  /** Why the button is disabled (its tooltip), or null when usable. */
  readonly reason: DocumentIoDisabledKey | null
}

export interface DocumentIoView {
  readonly upload: DocumentIoButtonView
  readonly download: DocumentIoButtonView
}

const editable = (record: DocumentInfo | null): boolean =>
  !!record && record.isEditable === true && record.groupDisposed !== true && record.typeCode !== 'TR2'

function uploadReason(info: DocumentIoInfo, busy: boolean): DocumentIoDisabledKey | null {
  if (busy) return 'uploading'
  if (info.state === 'DENIED' || info.uploadDenied) return 'forbidden'
  if (info.state === 'GONE') return 'locked'
  if (info.state === 'READY' && !editable(info.record)) return 'locked'
  return null
}

function downloadReason(info: DocumentIoInfo, downloading: boolean): DocumentIoDisabledKey | null {
  if (downloading) return 'downloading'
  if (info.state === 'DENIED') return 'forbidden'
  if (info.state === 'GONE') return 'no_file'
  if (info.state === 'READY' && info.record?.downloadAvailable === false) return 'no_file'
  return null
}

/**
 * Both buttons of one document. While the read runs or failed for a passing reason (LOADING / ERROR) both
 * are usable — the server's answer to the press decides. An upload refused with 403 stays disabled until
 * the document is read again.
 */
export function documentIoView(info: DocumentIoInfo, busy: boolean, downloading: boolean): DocumentIoView {
  const up = uploadReason(info, busy)
  const down = downloadReason(info, downloading)
  return {
    upload: { enabled: up === null, busy, reason: up },
    download: { enabled: down === null, busy: downloading, reason: down },
  }
}
