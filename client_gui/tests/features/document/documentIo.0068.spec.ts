/**
 * flowgate.v02.0068 T#1 — what features/document adds for the document upload / download (0068 D §3.1, L §2.14 /
 * §2.15): the buttons' document read keeps `download_available` (a boolean only) beside the record — the record
 * itself stays as other readers compare it — and the download is the existing canonical Markdown download as a
 * blob; a refusal (also a blob) is read back into its JSON envelope before it is thrown.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const downloadBlobRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  downloadBlobRequest: (...args: unknown[]) => downloadBlobRequest(...args),
}))

import { documentDownload, documentErrorOf, documentInfoRead, toDocumentInfo, toDocumentRecord } from '@/features/document'

const ROW = { doc_id: 'flowgate.v02.0068.0007-T', title: '작업지시', type_code: 'T', is_editable: true, group_disposed: false }

beforeEach(() => {
  getRequest.mockReset()
  downloadBlobRequest.mockReset()
})

describe('toDocumentInfo / documentInfoRead (L §2.15)', () => {
  it('keeps download_available and is_editable when they are booleans', () => {
    expect(toDocumentInfo({ ...ROW, download_available: true })).toMatchObject({ isEditable: true, groupDisposed: false, downloadAvailable: true })
    expect(toDocumentInfo({ ...ROW, download_available: false }).downloadAvailable).toBe(false)
  })

  it('leaves it out when the server did not send a boolean (unknown → the button stays usable)', () => {
    for (const value of [undefined, null, 'true', 1]) {
      expect('downloadAvailable' in toDocumentInfo({ ...ROW, download_available: value })).toBe(false)
    }
  })

  it('the record of documentRead keeps its shape (no new field)', () => {
    expect('downloadAvailable' in toDocumentRecord({ ...ROW, download_available: true })).toBe(false)
  })

  it('reads the same v0.1 document path as documentRead', async () => {
    getRequest.mockResolvedValue({ data: { ...ROW, download_available: true } })
    await expect(documentInfoRead('a b/c')).resolves.toMatchObject({ docId: ROW.doc_id, downloadAvailable: true })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/documents/a%20b%2Fc')
  })
})

describe('documentDownload (L §2.14)', () => {
  it('GETs the canonical download as a blob and hands over the Content-Disposition', async () => {
    const blob = new Blob(['---\ntitle: x\n---\n# 본문\n'], { type: 'text/markdown' })
    downloadBlobRequest.mockResolvedValue({ data: blob, headers: { 'content-disposition': 'attachment; filename="0007-T.md"' } })
    const file = await documentDownload('flowgate.v02.0068.0007-T')
    expect(downloadBlobRequest).toHaveBeenCalledWith('/api/v1/document/flowgate.v02.0068.0007-T/download')
    expect(file.blob).toBe(blob)
    expect(file.disposition).toBe('attachment; filename="0007-T.md"')
  })

  it('no header → disposition null', async () => {
    downloadBlobRequest.mockResolvedValue({ data: new Blob(['x']), headers: {} })
    expect((await documentDownload('d')).disposition).toBeNull()
  })

  it('a refusal arrives as a blob: it is read back into the envelope and thrown as it is', async () => {
    const refusal = {
      isAxiosError: true,
      response: { status: 404, data: new Blob([JSON.stringify({ ok: false, http_status: 404, error_message: 'x' })], { type: 'application/json' }) },
    }
    downloadBlobRequest.mockRejectedValue(refusal)
    await expect(documentDownload('d')).rejects.toBe(refusal)
    expect(refusal.response.data).toMatchObject({ http_status: 404 })
    expect(documentErrorOf(refusal)).toMatchObject({ kind: 'not_found', status: 404 })
  })

  it('a refusal body that is not JSON keeps the status readable', async () => {
    const refusal = { isAxiosError: true, response: { status: 403, data: new Blob(['<html>']) } }
    downloadBlobRequest.mockRejectedValue(refusal)
    await expect(documentDownload('d')).rejects.toBe(refusal)
    expect(documentErrorOf(refusal).status).toBe(403)
  })

  it('no response (network) is thrown as it is', async () => {
    const network = { isAxiosError: true }
    downloadBlobRequest.mockRejectedValue(network)
    await expect(documentDownload('d')).rejects.toBe(network)
    expect(documentErrorOf(network).kind).toBe('network')
  })
})
