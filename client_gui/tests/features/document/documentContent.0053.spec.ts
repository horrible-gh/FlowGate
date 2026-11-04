/**
 * flowgate.v02.0053 T#3 — the document body read and save of features/document, on the v0.1 endpoints the
 * v0.1 main screen uses: GET /api/v1/documents/content?doc_id= and PATCH /api/v1/documents/content
 * { doc_id, content }. A file that is not written yet (404) reads as an empty body; other errors are
 * thrown as they are.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const patchRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  patchRequest: (...args: unknown[]) => patchRequest(...args),
}))

import * as documentApi from '@/features/document'
import { documentContentRead, documentErrorOf } from '@/features/document'

const DOC = 'flowgate.v02.0053.0007-TR'
const axiosError = (status: number) => ({ isAxiosError: true, response: { status, data: { detail: 'x' } } })

beforeEach(() => {
  getRequest.mockReset()
  patchRequest.mockReset()
})

describe('documentContentRead (flowgate.v02.0053 T#3; the v1 save was removed by flowgate.v02.0127 T#5)', () => {
  it('reads the stored file as it is, frontmatter included', async () => {
    getRequest.mockResolvedValue({ data: { content: '---\ntitle: x\n---\n# 본문\n' } })
    await expect(documentContentRead(DOC)).resolves.toEqual({ docId: DOC, content: '---\ntitle: x\n---\n# 본문\n', missing: false })
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/documents/content?doc_id=${encodeURIComponent(DOC)}`)
  })

  it('a file that is not written yet (404) is an empty body, so the document opens empty', async () => {
    getRequest.mockRejectedValue(axiosError(404))
    await expect(documentContentRead(DOC)).resolves.toEqual({ docId: DOC, content: '', missing: true })
  })

  it('other read failures are thrown as they are', async () => {
    getRequest.mockRejectedValue(axiosError(403))
    const error = await documentContentRead(DOC).catch((thrown: unknown) => thrown)
    expect(documentErrorOf(error).kind).toBe('forbidden')
  })

  it('flowgate.v02.0127 T#5: there is no v1 whole-body save left (PATCH /api/v1/documents/content); a body is saved only through the card document session', () => {
    expect('documentContentSave' in documentApi).toBe(false)
    expect(typeof documentApi.cardDocumentRevise).toBe('function')
    expect(patchRequest).not.toHaveBeenCalled()
  })
})
