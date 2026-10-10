/**
 * flowgate.v02.0048 T#8 — the attachments tab's calls in features/document/api/attachments.ts (D#1 §6.3,
 * §12.1): the request each one sends to the existing v0.1 endpoint (path with the names encoded, the
 * multipart `file` parts), the `{ data }` answers -> camelCase (the attachment object of P0011 §1-3:
 * stored and uploaded names, size, type, an empty list, objects without a stored name dropped), the
 * download as a blob with a refusal blob read back into its JSON envelope, and how a refusal is read
 * — 409 DOCUMENT_NOT_MUTABLE with its reason, 423 GROUP_AI_RUN_LOCKED, 409 ATTACHMENT_EXISTS, 413, 404,
 * 403, 400 / 422, 5xx and no answer.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const postFormRequest = vi.fn()
const deleteRequest = vi.fn()
const downloadBlobRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postFormRequest: (...args: unknown[]) => postFormRequest(...args),
  deleteRequest: (...args: unknown[]) => deleteRequest(...args),
  downloadBlobRequest: (...args: unknown[]) => downloadBlobRequest(...args),
}))

import {
  ATTACHMENTS_MAX_COUNT_PER_DOC,
  ATTACHMENTS_MAX_FILES_PER_REQUEST,
  ATTACHMENTS_MAX_UPLOAD_BYTES,
  attachmentsDelete,
  attachmentsDownload,
  attachmentsErrorOf,
  attachmentsRead,
  attachmentsUpload,
  toAttachmentsDeleteResult,
  toAttachmentsList,
  toAttachmentsUploadResult,
} from '@/features/document'

const DOC = 'test2.default.0017.0002-N'

/** An attachment object as `_attachment_object` (documents/attachments/service.py) answers it. */
const ROW = {
  doc_id: DOC,
  original_filename: '요구사항 정리.xlsx',
  filename: '요구사항 정리.xlsx',
  size: 18342,
  content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  content_sha256: 'a'.repeat(64),
  path: 'attachments/test2/test2.default.0017.0002-N/요구사항 정리.xlsx',
  path_base: 'storage',
  uploaded_by: 'u-test',
  uploaded_at: '2026-10-09T10:00:00Z',
}

const LIST = { data: { doc_id: DOC, attachments: [ROW, { ...ROW, filename: '요구사항 정리 (1).xlsx', size: 1024 }], count: 2 } }

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const envelope = (code: string, details: Record<string, unknown> = {}) => ({ error: { code, message: 'server text', details } })

beforeEach(() => {
  for (const mock of [getRequest, postFormRequest, deleteRequest, downloadBlobRequest]) mock.mockReset()
})

describe('attachments API — requests (flowgate.v02.0048 T#8)', () => {
  it('list reads GET /api/v1/documents/{doc_id}/attachments with the doc_id encoded', async () => {
    getRequest.mockResolvedValue({ data: LIST })
    const read = await attachmentsRead('flow/a b.0001-T')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/flow%2Fa%20b.0001-T/attachments')
    expect(read.items.map((item) => item.filename)).toEqual(['요구사항 정리.xlsx', '요구사항 정리 (1).xlsx'])
  })

  it('upload posts one multipart `file` part per file to the same path', async () => {
    postFormRequest.mockResolvedValue({ data: { data: { doc_id: DOC, attachments: [ROW], count: 1 } } })
    const one = new File(['a'], 'a.txt', { type: 'text/plain' })
    const two = new File(['bb'], 'b.png', { type: 'image/png' })
    const result = await attachmentsUpload(DOC, [one, two])
    const [path, form] = postFormRequest.mock.calls[0]
    expect(path).toBe(`/api/v1/documents/${encodeURIComponent(DOC)}/attachments`)
    expect(form).toBeInstanceOf(FormData)
    expect((form as FormData).getAll('file').map((file) => (file as File).name)).toEqual(['a.txt', 'b.png'])
    expect([...(form as FormData).keys()]).toEqual(['file', 'file'])
    expect(result).toEqual({ docId: DOC, items: [expect.objectContaining({ filename: '요구사항 정리.xlsx', size: 18342 })] })
  })

  it('delete sends DELETE …/attachments/{filename} with the stored name encoded', async () => {
    deleteRequest.mockResolvedValue({
      data: { data: { doc_id: DOC, filename: 'a b#?.txt', path: 'x', path_base: 'storage', file_deleted: true, metadata_deleted: true, deleted_at: '2026-10-09T10:01:00Z' } },
    })
    const result = await attachmentsDelete(DOC, 'a b#?.txt')
    expect(deleteRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(DOC)}/attachments/a%20b%23%3F.txt`)
    expect(result).toEqual({ docId: DOC, filename: 'a b#?.txt', deletedAt: '2026-10-09T10:01:00Z' })
  })

  it('open downloads GET …/attachments/{filename} as a blob and names it with the stored name', async () => {
    const blob = new Blob(['hello'], { type: 'text/plain' })
    downloadBlobRequest.mockResolvedValue({ data: blob })
    const file = await attachmentsDownload(DOC, '보고서 (1).pdf')
    expect(downloadBlobRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(DOC)}/attachments/${encodeURIComponent('보고서 (1).pdf')}`)
    expect(file).toEqual({ filename: '보고서 (1).pdf', blob })
  })

  it('a download refusal arrives as a blob and is read back into its envelope before it is thrown', async () => {
    const refusal = axiosError(404, new Blob([JSON.stringify(envelope('ATTACHMENT_NOT_FOUND'))], { type: 'application/json' }))
    downloadBlobRequest.mockRejectedValue(refusal)
    const thrown = await attachmentsDownload(DOC, 'gone.txt').catch((error: unknown) => error)
    expect(thrown).toBe(refusal)
    expect(attachmentsErrorOf(thrown)).toEqual({ kind: 'not_found', status: 404, code: 'ATTACHMENT_NOT_FOUND', reason: null })
  })

  it('a download refusal that is not JSON keeps its status', async () => {
    downloadBlobRequest.mockRejectedValue(axiosError(500, new Blob(['<html>'], { type: 'text/html' })))
    const thrown = await attachmentsDownload(DOC, 'x').catch((error: unknown) => error)
    expect(attachmentsErrorOf(thrown).kind).toBe('server')
  })

  it.each([
    ['list', () => attachmentsRead(DOC), getRequest],
    ['upload', () => attachmentsUpload(DOC, [new File(['a'], 'a.txt')]), postFormRequest],
    ['delete', () => attachmentsDelete(DOC, 'a.txt'), deleteRequest],
  ] as const)('%s throws the transport error as it is', async (_name, call, mock) => {
    const refusal = axiosError(409, envelope('DOCUMENT_NOT_MUTABLE', { reason: 'group_ai_running' }))
    mock.mockRejectedValue(refusal)
    await expect(call()).rejects.toBe(refusal)
  })

  it('the client limits are the server values (documents/attachments/constants.py)', () => {
    expect([ATTACHMENTS_MAX_UPLOAD_BYTES, ATTACHMENTS_MAX_FILES_PER_REQUEST, ATTACHMENTS_MAX_COUNT_PER_DOC]).toEqual([20971520, 20, 200])
  })
})

describe('attachments API — normalization (flowgate.v02.0048 T#8)', () => {
  it('the list answer -> camelCase items in the server order', () => {
    expect(toAttachmentsList(LIST)).toEqual({
      docId: DOC,
      items: [
        {
          filename: '요구사항 정리.xlsx',
          originalFilename: '요구사항 정리.xlsx',
          size: 18342,
          contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          sha256: 'a'.repeat(64),
          uploadedBy: 'u-test',
          uploadedAt: '2026-10-09T10:00:00Z',
        },
        expect.objectContaining({ filename: '요구사항 정리 (1).xlsx', originalFilename: '요구사항 정리.xlsx', size: 1024 }),
      ],
    })
  })

  it('no attachment is an empty list (200, not 404)', () => {
    expect(toAttachmentsList({ data: { doc_id: DOC, attachments: [], count: 0 } })).toEqual({ docId: DOC, items: [] })
  })

  it('an object without a stored name is dropped; missing fields become null / 0', () => {
    const read = toAttachmentsList({ data: { doc_id: DOC, attachments: [{ original_filename: 'x' }, { filename: 'y.bin', size: -3 }, 'junk'] } })
    expect(read.items).toEqual([{ filename: 'y.bin', originalFilename: null, size: 0, contentType: null, sha256: null, uploadedBy: null, uploadedAt: null }])
  })

  it('an answer without doc_id is refused', () => {
    expect(() => toAttachmentsList({ data: { attachments: [] } })).toThrow()
    expect(() => toAttachmentsList(null)).toThrow()
  })

  it('upload and delete answers', () => {
    expect(toAttachmentsUploadResult({ data: { doc_id: DOC, attachments: [ROW], count: 1 } }).items).toHaveLength(1)
    expect(toAttachmentsUploadResult({})).toEqual({ docId: null, items: [] })
    expect(toAttachmentsDeleteResult({ data: { doc_id: DOC, filename: 'a', deleted_at: 't' } })).toEqual({ docId: DOC, filename: 'a', deletedAt: 't' })
  })
})

describe('attachments API — reading a refusal (flowgate.v02.0048 T#8)', () => {
  it.each([
    ['409 DOCUMENT_NOT_MUTABLE group_disposed', axiosError(409, envelope('DOCUMENT_NOT_MUTABLE', { doc_id: DOC, reason: 'group_disposed' })), 'not_mutable', 409, 'DOCUMENT_NOT_MUTABLE', 'group_disposed'],
    ['409 DOCUMENT_NOT_MUTABLE group_ai_running', axiosError(409, envelope('DOCUMENT_NOT_MUTABLE', { reason: 'group_ai_running' })), 'not_mutable', 409, 'DOCUMENT_NOT_MUTABLE', 'group_ai_running'],
    [
      '409 DOCUMENT_NOT_MUTABLE as the dev server answered it (2026-10-09, DELETE of a reserved WorkPlan name)',
      axiosError(409, {
        error: {
          code: 'DOCUMENT_NOT_MUTABLE',
          message: 'Reserved WorkPlan attachments are removed only by the WorkPlan lifecycle.',
          details: { doc_id: DOC, filename: '__wp_pre_instruction__probe.txt', reason: 'reserved_work_plan_attachment' },
        },
      }),
      'not_mutable',
      409,
      'DOCUMENT_NOT_MUTABLE',
      'reserved_work_plan_attachment',
    ],
    ['409 DOCUMENT_NOT_MUTABLE without reason',axiosError(409, envelope('DOCUMENT_NOT_MUTABLE')), 'not_mutable', 409, 'DOCUMENT_NOT_MUTABLE', null],
    ['423 GROUP_AI_RUN_LOCKED (mutation policy)', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'm', run_live: true } }), 'running', 423, 'GROUP_AI_RUN_LOCKED', null],
    ['423 without code', axiosError(423, {}), 'running', 423, null, null],
    ['409 ATTACHMENT_EXISTS per_document_limit', axiosError(409, envelope('ATTACHMENT_EXISTS', { reason: 'per_document_limit', limit: 200 })), 'limit', 409, 'ATTACHMENT_EXISTS', 'per_document_limit'],
    ['413 ATTACHMENT_TOO_LARGE', axiosError(413, envelope('ATTACHMENT_TOO_LARGE', { limit_bytes: 20971520 })), 'too_large', 413, 'ATTACHMENT_TOO_LARGE', null],
    ['404 DOCUMENT_NOT_FOUND', axiosError(404, envelope('DOCUMENT_NOT_FOUND')), 'not_found', 404, 'DOCUMENT_NOT_FOUND', null],
    ['404 ATTACHMENT_NOT_FOUND', axiosError(404, envelope('ATTACHMENT_NOT_FOUND')), 'not_found', 404, 'ATTACHMENT_NOT_FOUND', null],
    ['403', axiosError(403, { detail: 'forbidden' }), 'forbidden', 403, null, null],
    ['400 INVALID_FILENAME', axiosError(400, envelope('INVALID_FILENAME')), 'invalid', 400, 'INVALID_FILENAME', null],
    ['422 INVALID_REQUEST', axiosError(422, envelope('INVALID_REQUEST')), 'invalid', 422, 'INVALID_REQUEST', null],
    ['500 ATTACHMENT_STORE_FAILED', axiosError(500, envelope('ATTACHMENT_STORE_FAILED', { reason: 'storage' })), 'server', 500, 'ATTACHMENT_STORE_FAILED', 'storage'],
    ['another 409', axiosError(409, envelope('SOMETHING_ELSE')), 'other', 409, 'SOMETHING_ELSE', null],
    ['no answer', { isAxiosError: true, message: 'Network Error' }, 'network', null, null, null],
  ])('%s', (_name, error, kind, status, code, reason) => {
    expect(attachmentsErrorOf(error)).toEqual({ kind, status, code, reason })
  })
})
