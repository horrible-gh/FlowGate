import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AttachmentRequestItem from '@/features/attachmentRequests/AttachmentRequestItem.vue'
import { attachmentFileKey } from '@/features/attachmentRequests/registeredFile'
import TimelineAttachments from '@/features/conversation/TimelineAttachments.vue'
import { ATTACHMENT_REQUEST_ROW, type AttachmentRequestActivity } from '@/features/conversation/types'
import { registerAttachmentFocus } from '@/features/designer/flowAttachments/focus'
import { toAttachmentRequest, type AttachmentRequest } from '@/features/document/api/attachmentRequests'

// v02test.0074 card "6-화면" (1·3 연결): the file name of an APPROVED request row goes to that file in the Flow attachment
// window — focusAttachment(flowId, key) with the key card 1·2 put on the name. The same row component is drawn in the
// chat's request block and in the window's waiting band, so both get it. Any other state has no file: a plain name.
registerAppMessages(i18n)

const FLOW = 'fgp.v02.0001'
const DOC = 'fgp.v02.0001.0002-R'
const RAW = {
  request_id: 'aur_0123456789abcdef01234567',
  origin: 'chat',
  status: 'approved',
  flow_id: FLOW,
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
const request = (extra: Record<string, unknown> = {}): AttachmentRequest => toAttachmentRequest({ ...RAW, ...extra })!
const KEY = attachmentFileKey(DOC, 'report (2).md')

const handler = vi.fn(async (_flowId: string, _key: string) => true)
let unregister: (() => void) | null = null
let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  handler.mockReset()
  handler.mockResolvedValue(true)
  unregister = registerAttachmentFocus(handler)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  unregister?.()
  unregister = null
})

const mountItem = (row: AttachmentRequest) => {
  wrapper = mount(AttachmentRequestItem, { props: { request: row, showTarget: true }, attachTo: document.body, global: { plugins: [i18n] } })
  return wrapper
}
const name = () => wrapper!.get('[data-test="ar-name"]')

describe('the file name of an approved request row', () => {
  it('is a button that shows the registered file in the Flow attachment window', async () => {
    mountItem(request())
    expect(name().element.tagName).toBe('BUTTON')
    expect(name().attributes('type')).toBe('button')
    expect(name().text()).toBe('report.md')
    expect(name().attributes('data-attachment-key')).toBe(KEY)
    // the tooltip names the file as it was registered ("둘 다 유지" renamed it).
    expect(name().attributes('title')).toBe('첨부파일 창에서 보기: report (2).md')
    await name().trigger('click')
    await flushPromises()
    expect(handler.mock.calls).toEqual([[FLOW, KEY]])
  })

  it('changes nothing when the window cannot show the file, or when no Designer is there', async () => {
    handler.mockResolvedValue(false)
    mountItem(request())
    await name().trigger('click')
    await flushPromises()
    expect(wrapper!.find('[data-test="ar-error"]').exists()).toBe(false)
    unregister?.()
    await name().trigger('click')
    await flushPromises()
    expect(handler).toHaveBeenCalledTimes(1)
    expect(wrapper!.find('[data-test="ar-error"]').exists()).toBe(false)
  })

  it('stays a plain name while the request waits, and for a rejected / expired / failed one', async () => {
    for (const status of ['pending', 'approving', 'rejected', 'expired', 'failed']) {
      mountItem(request({ status, final_doc_id: null, final_filename: null }))
      expect(name().element.tagName, status).toBe('SPAN')
      expect(name().attributes('data-attachment-key'), status).toBeUndefined()
      await name().trigger('click')
      wrapper!.unmount()
      wrapper = null
    }
    // approved, but the server gave no registered file: nothing to go to.
    mountItem(request({ final_doc_id: null, final_filename: null }))
    expect(name().element.tagName).toBe('SPAN')
    await name().trigger('click')
    await flushPromises()
    expect(handler).not.toHaveBeenCalled()
  })

  it('does not ask for a file without a Flow on the request', async () => {
    mountItem(request({ flow_id: null }))
    await name().trigger('click')
    await flushPromises()
    expect(handler).not.toHaveBeenCalled()
  })

  it('becomes the link right after this row approved the request', async () => {
    mountItem(request({ status: 'pending', final_doc_id: null, final_filename: null }))
    expect(name().element.tagName).toBe('SPAN')
    await wrapper!.setProps({ request: request() })
    expect(name().element.tagName).toBe('BUTTON')
    await name().trigger('click')
    await flushPromises()
    expect(handler.mock.calls).toEqual([[FLOW, KEY]])
  })
})

describe('the chat\'s request block draws the same row', () => {
  it('an approved name in the chat card goes to the attachment window; a waiting one does not', async () => {
    const approved = request()
    const waiting = request({ request_id: 'aur_1123456789abcdef01234567', status: 'pending', filename: 'next.md', final_doc_id: null, final_filename: null })
    const activity = { kind: 'attachments', id: 'run-1', runId: 'run-1', createdAt: null, pending: 1, requests: [approved, waiting] } as unknown as AttachmentRequestActivity
    wrapper = mount(TimelineAttachments, {
      props: { activity },
      attachTo: document.body,
      global: { plugins: [i18n], provide: { [ATTACHMENT_REQUEST_ROW]: { value: AttachmentRequestItem } } },
    })
    const names = wrapper.findAll('[data-test="ar-name"]')
    expect(names.map((el) => el.element.tagName)).toEqual(['BUTTON', 'SPAN'])
    await names[1].trigger('click')
    await names[0].trigger('click')
    await flushPromises()
    expect(handler.mock.calls).toEqual([[FLOW, KEY]])
  })
})
