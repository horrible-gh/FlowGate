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

import { documentContentRead, documentContentSave, documentErrorOf } from '@/features/document'

const DOC = 'flowgate.v02.0053.0007-TR'
const axiosError = (status: number) => ({ isAxiosError: true, response: { status, data: { detail: 'x' } } })

beforeEach(() => {
  getRequest.mockReset()
  patchRequest.mockReset()
})

describe('documentContentRead / documentContentSave (flowgate.v02.0053 T#3)', () => {
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

  it('saves the whole body with the doc_id; a refusal is thrown as it is', async () => {
    patchRequest.mockResolvedValue({ data: {} })
    await documentContentSave(DOC, '---\na: 1\n---\n본문')
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/documents/content', { doc_id: DOC, content: '---\na: 1\n---\n본문' })
    patchRequest.mockRejectedValue(axiosError(422))
    const error = await documentContentSave(DOC, 'x').catch((thrown: unknown) => thrown)
    expect(documentErrorOf(error).status).toBe(422)
  })
})
