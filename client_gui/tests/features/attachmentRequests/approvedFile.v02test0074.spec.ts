import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// v02test.0074 card 1 (the approval card keeps [미리보기][다운로드] after the approval): an approved request row still
// previews and downloads, now from the attachment it became on the target document (its staged copy is gone, the
// server answers 410 for a closed request); a rejected / expired / failed row has no file, so no buttons.
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const downloadBlobRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, getRequest, postRequest, downloadBlobRequest }
})

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AttachmentRequestItem from '@/features/attachmentRequests/AttachmentRequestItem.vue'
import { attachmentFileKey } from '@/features/attachmentRequests/registeredFile'
import { toAttachmentRequest, type AttachmentRequest } from '@/features/document/api/attachmentRequests'

registerAppMessages(i18n)

const DOC = 'fgp.v02.0001.0002-R'
const RAW = {
  request_id: 'aur_0123456789abcdef01234567',
  origin: 'chat',
  status: 'approved',
  flow_id: 'fgp.v02.0001',
  target_doc_id: DOC,
  target_title: 'Report card',
  provider_name: 'Claude',
  filename: 'report.md',
  size: 2048,
  content_type: 'text/markdown',
  content_sha256: 'a'.repeat(64),
  decision: 'keep_both',
  final_doc_id: DOC,
  final_filename: 'report (2).md',
  preview: 'text',
}
const REGISTERED = `/api/v1/documents/${DOC}/attachments/${encodeURIComponent('report (2).md')}`
const request = (extra: Record<string, unknown> = {}): AttachmentRequest => toAttachmentRequest({ ...RAW, ...extra })!

let wrapper: VueWrapper | null = null
const mountItem = (row: AttachmentRequest) => {
  wrapper = mount(AttachmentRequestItem, { props: { request: row, showTarget: true }, global: { plugins: [i18n] } })
  return wrapper
}
const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
const clicks = vi.fn()

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  downloadBlobRequest.mockReset()
  clicks.mockReset()
  URL.createObjectURL = vi.fn(() => 'blob:registered')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    clicks(this.download)
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.restoreAllMocks()
})

describe('an approved request row keeps preview and download', () => {
  it('shows both buttons once approved', () => {
    mountItem(request())
    expect(find('ar-state').text()).toBe('승인됨')
    expect(find('ar-preview').exists()).toBe(true)
    expect(find('ar-download').exists()).toBe(true)
    expect(find('ar-approve').exists()).toBe(false)
  })

  it('keeps them on the row that has just been approved here', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, request: { ...RAW } } })
    mountItem(request({ status: 'pending', decision: null, final_doc_id: null, final_filename: null }))
    expect(find('ar-preview').exists()).toBe(true)
    await find('ar-approve').trigger('click')
    await flushPromises()
    expect(find('ar-state').text()).toBe('승인됨')
    expect(find('ar-preview').exists()).toBe(true)
    expect(find('ar-download').exists()).toBe(true)
  })

  it('previews text from the registered attachment, never from the closed request', async () => {
    getRequest.mockResolvedValueOnce({ data: { data: { kind: 'text', content: '<b>not html</b>', truncated: false } } })
    mountItem(request())
    await find('ar-preview').trigger('click')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(getRequest).toHaveBeenCalledWith(`${REGISTERED}/read?mode=auto`)
    expect(getRequest.mock.calls[0][0]).not.toContain('/attachment-requests/')
    expect(find('ar-preview-text').text()).toBe('<b>not html</b>')
    expect(find('ar-preview-text').html()).not.toContain('<b>not')
  })

  it('shows an image from the registered attachment bytes', async () => {
    downloadBlobRequest.mockResolvedValueOnce({ data: new Blob(['x'], { type: 'image/png' }) })
    mountItem(request({ filename: 'shot.png', final_filename: 'shot.png', content_type: 'image/png', preview: 'image' }))
    await find('ar-preview').trigger('click')
    await flushPromises()
    expect(getRequest).not.toHaveBeenCalled()
    expect(downloadBlobRequest).toHaveBeenCalledWith(`/api/v1/documents/${DOC}/attachments/shot.png`)
    expect(find('ar-preview-image').attributes('src')).toBe('blob:registered')
  })

  it('says it cannot be previewed (download only) for a file the server will not read inline', async () => {
    getRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 413, data: {} } })
    mountItem(request({ filename: 'big.bin', final_filename: 'big.bin', preview: null }))
    await find('ar-preview').trigger('click')
    await flushPromises()
    expect(find('ar-preview-binary').exists()).toBe(true)
  })

  it('shows a failed read as failed, not as an empty preview', async () => {
    getRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: {} } })
    mountItem(request())
    await find('ar-preview').trigger('click')
    await flushPromises()
    expect(find('ar-preview-text').exists()).toBe(false)
    expect(find('ar-preview-body').text()).toContain('미리보기')
  })

  it('downloads the registered attachment under its final (kept-both) name', async () => {
    downloadBlobRequest.mockResolvedValueOnce({ data: new Blob(['x']) })
    mountItem(request())
    await find('ar-download').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest).toHaveBeenCalledWith(REGISTERED)
    expect(clicks).toHaveBeenCalledWith('report (2).md')
  })

  it('carries the attachment window file key on the file name for the later link', () => {
    mountItem(request())
    const key = find('ar-name').attributes('data-attachment-key')
    expect(key).toBe(Buffer.from(`${DOC}\nreport (2).md`).toString('base64url'))
    expect(key).toBe(attachmentFileKey(DOC, 'report (2).md'))
    expect(key).not.toMatch(/[+/=]/)
  })

  it('has no key on a waiting row (no attachment yet)', () => {
    mountItem(request({ status: 'pending', decision: null, final_doc_id: null, final_filename: null }))
    expect(find('ar-name').attributes('data-attachment-key')).toBeUndefined()
  })
})

describe('a request without a real file has no buttons', () => {
  it.each(['rejected', 'expired', 'failed'])('hides preview and download on a %s row', (status) => {
    mountItem(request({ status, decision: null, final_doc_id: null, final_filename: null }))
    expect(find('ar-preview').exists()).toBe(false)
    expect(find('ar-download').exists()).toBe(false)
  })

  it('hides them on an approved row that names no registered file', () => {
    mountItem(request({ final_doc_id: null, final_filename: null }))
    expect(find('ar-preview').exists()).toBe(false)
    expect(find('ar-download').exists()).toBe(false)
  })
})

describe('a waiting request still reads its staged copy', () => {
  const waiting = () => request({ status: 'pending', decision: null, final_doc_id: null, final_filename: null })

  it('previews from the request endpoint', async () => {
    getRequest.mockResolvedValueOnce({ data: { ok: true, kind: 'text', content: 'hello', truncated: false } })
    mountItem(waiting())
    await find('ar-preview').trigger('click')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/attachment-requests/${RAW.request_id}/preview`)
    expect(find('ar-preview-text').text()).toBe('hello')
  })

  it('downloads from the request endpoint under the requested name', async () => {
    downloadBlobRequest.mockResolvedValueOnce({ data: new Blob(['x']) })
    mountItem(waiting())
    await find('ar-download').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest).toHaveBeenCalledWith(`/api/v1/attachment-requests/${RAW.request_id}/content`)
    expect(clicks).toHaveBeenCalledWith('report.md')
  })
})
