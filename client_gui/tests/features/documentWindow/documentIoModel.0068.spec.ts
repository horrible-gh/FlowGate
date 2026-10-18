/**
 * flowgate.v02.0068 T#1 — the document upload / download rules without a screen (0068 L §1.3, §2.12, §2.15, §4.3,
 * §4.4): the parameters as L fixes them, the picked file check, the saved text (only a leading BOM dropped), the
 * error → notice keys, the download file name, and each button's availability with its tooltip (the L §2.15 table).
 */
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { DocumentError, DocumentInfo } from '@/features/document'
import {
  FILE_PICK_CANCEL_GRACE_MS,
  REFLECT_TIMEOUT_MS,
  UPLOAD_ACCEPT,
  UPLOAD_EXT_ALLOWED,
  UPLOAD_MAX_BYTES,
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
  type DocumentIoInfo,
} from '@/features/documentWindow/documentIoModel'

registerAppMessages(i18n)

const err = (status: number | null, kind: DocumentError['kind'] = 'other'): DocumentError => ({ status, kind, code: null })
const record = (fields: Partial<DocumentInfo> = {}): DocumentInfo => ({
  docId: 'd',
  projectId: 'p',
  groupId: 'g',
  typeCode: 'T',
  seq: 1,
  title: 't',
  status: 'open',
  docReviewStatus: null,
  revisionNo: 0,
  rejectionReason: null,
  supersededBy: null,
  updatedAt: null,
  isEditable: true,
  ...fields,
})
const info = (state: DocumentIoInfo['state'], fields: Partial<DocumentInfo> | null = {}, uploadDenied = false): DocumentIoInfo => ({
  state,
  record: state === 'READY' && fields !== null ? record(fields) : null,
  uploadDenied,
})

describe('parameters (L §1.3)', () => {
  it('are the L values', () => {
    expect(UPLOAD_ACCEPT).toBe('.md,text/markdown,text/plain')
    expect([...UPLOAD_EXT_ALLOWED].sort()).toEqual(['markdown', 'md', 'txt'])
    expect(FILE_PICK_CANCEL_GRACE_MS).toBe(1000)
    expect(REFLECT_TIMEOUT_MS).toBe(10000)
    expect(documentIoFallbackName('flowgate.v02.0068.0007-T')).toBe('flowgate.v02.0068.0007-T.md')
  })

  it('UPLOAD_MAX_BYTES stays TBD (the server content save has no limit): no size check', () => {
    expect(UPLOAD_MAX_BYTES).toBeNull()
    expect(documentIoTooLarge(50 * 1024 * 1024)).toBe(false)
    expect(documentIoTooLarge(11, 10)).toBe(true)
    expect(documentIoTooLarge(10, 10)).toBe(false)
  })
})

describe('the picked file and the saved text (L §2.12)', () => {
  it.each([
    ['a.md', '', true],
    ['A.MARKDOWN', '', true],
    ['notes.TXT', '', true],
    ['x.bin', 'text/plain', true],
    ['x', 'text/x-markdown', true],
    ['x.pdf', 'application/pdf', false],
    ['x.docx', '', false],
    ['md', '', false],
  ])('%s (%s) → %s', (name, type, ok) => {
    expect(documentIoAccepts({ name, type })).toBe(ok)
  })

  it('only a leading BOM is dropped; CRLF and frontmatter stay as they are', () => {
    const body = '﻿---\r\ntitle: x\r\n---\r\n# 본문\r\n'
    expect(documentIoUploadText(body)).toBe('---\r\ntitle: x\r\n---\r\n# 본문\r\n')
    expect(documentIoUploadText('a﻿b')).toBe('a﻿b')
    expect(documentIoUploadText('plain')).toBe('plain')
  })
})

describe('notice keys (L §4.3 / §4.4)', () => {
  it('a refused upload save', () => {
    expect(documentIoSaveNotice(err(422), null)).toBe('locked')
    expect(documentIoSaveNotice(err(409, 'conflict'), 'file_exists_unrecorded')).toBe('file_exists_unrecorded')
    expect(documentIoSaveNotice(err(409, 'conflict'), 'x')).toBe('busy')
    expect(documentIoSaveNotice(err(403, 'forbidden'), null)).toBe('forbidden')
    expect(documentIoSaveNotice(err(404, 'not_found'), null)).toBe('gone')
    expect(documentIoSaveNotice(err(null, 'network'), null)).toBe('network')
    expect(documentIoSaveNotice(err(500), null)).toBe('save')
  })

  it('a failed download: 404 asks for the document read to tell a missing file from a missing document', () => {
    expect(documentIoDownloadNotice(err(403, 'forbidden'))).toBe('forbidden')
    expect(documentIoDownloadNotice(err(404, 'not_found'))).toBe('check')
    expect(documentIoDownloadNotice(err(422))).toBe('invalid_id')
    expect(documentIoDownloadNotice(err(null, 'network'))).toBe('network')
    expect(documentIoDownloadNotice(err(500))).toBe('download_failed')
    expect(documentIoDownloadCheckNotice(null)).toBe('no_file')
    expect(documentIoDownloadCheckNotice(err(404, 'not_found'))).toBe('gone')
    expect(documentIoDownloadCheckNotice(err(403, 'forbidden'))).toBe('forbidden')
    expect(documentIoDownloadCheckNotice(err(null, 'network'))).toBe('network')
    expect(documentIoDownloadCheckNotice(err(500))).toBe('download_failed')
  })

  it('every key the rules give has ko / en / ja wording', () => {
    const keys = [
      ...['upload_hold', 'upload_type', 'upload_too_large', 'upload_read_failed', 'upload_empty', 'upload_done', 'download_unsaved_excluded'],
      ...['locked', 'file_exists_unrecorded', 'busy', 'forbidden', 'gone', 'network', 'save'].map((key) => `save.${key}`),
      ...['forbidden', 'gone', 'no_file', 'invalid_id', 'network', 'download_failed'].map((key) => `download.${key}`),
    ]
    for (const locale of ['ko', 'en', 'ja']) {
      for (const key of keys) expect(i18n.global.te(`gui.document_io.notice.${key}`, locale), `${locale} ${key}`).toBe(true)
      for (const key of ['uploading', 'downloading', 'forbidden', 'locked', 'no_file']) {
        expect(i18n.global.te(`gui.document_io.disabled.${key}`, locale), `${locale} ${key}`).toBe(true)
      }
      for (const key of ['upload', 'download', 'upload_hint', 'download_hint']) expect(i18n.global.te(`gui.document_io.${key}`, locale)).toBe(true)
    }
    expect(i18n.global.t('gui.document_io.upload_hint', {}, { locale: 'ko' })).toBe('파일 내용으로 문서 전체를 교체')
  })
})

describe('download file name (L §2.14)', () => {
  it("filename*=UTF-8'' first, then filename=", () => {
    expect(documentIoFilename("attachment; filename=\"a.md\"; filename*=UTF-8''%EB%AC%B8%EC%84%9C.md")).toBe('문서.md')
    expect(documentIoFilename('attachment; filename="0007-T_document.md"')).toBe('0007-T_document.md')
    expect(documentIoFilename('attachment; filename=plain.md')).toBe('plain.md')
    expect(documentIoFilename("attachment; filename*=UTF-8''%E0%A4%A; filename=\"fallback.md\"")).toBe('fallback.md')
    expect(documentIoFilename('attachment')).toBeNull()
    expect(documentIoFilename(null)).toBeNull()
  })

  it('separators and control characters become _', () => {
    expect(documentIoSafeName('../a/b\\c\u0001.md')).toBe('.._a_b_c_.md')
  })
})

describe('button availability and tooltips (L §2.15)', () => {
  it('LOADING / ERROR: both usable (the press is judged by the server)', () => {
    for (const state of ['LOADING', 'ERROR'] as const) {
      const view = documentIoView(info(state), false, false)
      expect([view.upload.enabled, view.download.enabled]).toEqual([true, true])
      expect([view.upload.reason, view.download.reason]).toEqual([null, null])
    }
  })

  it('READY: upload needs is_editable true, not disposed, not TR2; download needs download_available ≠ false', () => {
    expect(documentIoView(info('READY'), false, false).upload.enabled).toBe(true)
    expect(documentIoView(info('READY', { isEditable: false }), false, false).upload.reason).toBe('locked')
    expect(documentIoView(info('READY', { isEditable: undefined }), false, false).upload.reason).toBe('locked')
    expect(documentIoView(info('READY', { groupDisposed: true }), false, false).upload.reason).toBe('locked')
    expect(documentIoView(info('READY', { typeCode: 'TR2' }), false, false).upload.reason).toBe('locked')
    expect(documentIoView(info('READY', { downloadAvailable: false }), false, false).download.reason).toBe('no_file')
    expect(documentIoView(info('READY', { downloadAvailable: true }), false, false).download.enabled).toBe(true)
    expect(documentIoView(info('READY', {}), false, false).download.enabled).toBe(true)
  })

  it('B1 DENIED: both disabled "권한 없음"; GONE: 편집 불가 상태 / 파일 없음', () => {
    expect(documentIoView(info('DENIED'), false, false)).toMatchObject({ upload: { enabled: false, reason: 'forbidden' }, download: { enabled: false, reason: 'forbidden' } })
    expect(documentIoView(info('GONE'), false, false)).toMatchObject({ upload: { enabled: false, reason: 'locked' }, download: { enabled: false, reason: 'no_file' } })
  })

  it('B3 upload refused with 403: only the upload is disabled (권한 없음); the download keeps the READY rule', () => {
    const view = documentIoView(info('READY', {}, true), false, false)
    expect(view.upload).toMatchObject({ enabled: false, reason: 'forbidden' })
    expect(view.download.enabled).toBe(true)
  })

  it('busy and downloading come first (업로드 처리 중 / progress), whatever the state', () => {
    const view = documentIoView(info('DENIED'), true, true)
    expect(view.upload).toMatchObject({ enabled: false, busy: true, reason: 'uploading' })
    expect(view.download).toMatchObject({ enabled: false, busy: true, reason: 'downloading' })
  })
})
