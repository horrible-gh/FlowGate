import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

// 0085: ConversationView gains an "auto-copy mention" checkbox to the left of the
// "Copy mention" button. When on, a successful send() fires the same copy-mention
// event the manual button does (with { auto: true }); it never fires on a failed send.
const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

import ConversationView from '@main/components/ConversationView.vue'

const AUTO_COPY_KEY = 'flowgate.chat.autoCopyMention'
const DOC_ID = 'flowgate.default.0085.0009-CH'
const OTHER_DOC_ID = 'flowgate.default.0224.0005-CH'

function draftKey(docId = DOC_ID, userId = 'guest') {
  return 'flowgate.user.' + userId + '.chat.drafts.' + docId
}

function mountView(docId = DOC_ID) {
  return mount(ConversationView, {
    props: { docId, projectId: 'flowgate' },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  localStorage.clear()
  delete window.__accessToken__
  getRequest.mockReset().mockResolvedValue({ data: { content: '' } })
  postRequest.mockReset().mockResolvedValue({ data: { content: '' } })
  showToast.mockReset()
})

describe('ConversationView auto-copy', () => {
  it('renders the auto-copy checkbox left of the copy-mention button', async () => {
    const wrapper = mountView()
    await flushPromises()
    const cb = wrapper.find('input[type="checkbox"]')
    expect(cb.exists()).toBe(true)
    // The checkbox must come before the copy button in the assist row.
    const html = wrapper.find('.conv-assist').html()
    expect(html.indexOf('checkbox')).toBeLessThan(html.indexOf('conv-assist-btn'))
  })

  it('emits copy-mention with { auto: true } after a successful send when checked', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('copy-mention')).toEqual([[{ auto: true }]])
  })
  it('posts the trimmed message body and speaker to the conversation turn endpoint', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('  hello worker  ')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/documents/flowgate.default.0085.0009-CH/conversation/turn',
      { body: 'hello worker', speaker: 'user' },
    )
  })

  it('renders FastAPI validation details as readable toast text', async () => {
    postRequest.mockRejectedValueOnce({
      response: {
        data: {
          detail: [
            { loc: ['body', 'body'], msg: 'Field required', type: 'missing' },
            { loc: ['body', 'speaker'], msg: 'Input should be user or ai' },
          ],
        },
      },
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(showToast).toHaveBeenCalledWith(
      'Failed to send message: body.body: Field required (missing); body.speaker: Input should be user or ai',
      'danger',
    )
  })

  it('does NOT auto-copy when the checkbox is off', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('copy-mention')).toBeUndefined()
  })

  it('does NOT auto-copy when the send fails', async () => {
    postRequest.mockRejectedValueOnce(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('textarea').setValue('hello worker')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.emitted('copy-mention')).toBeUndefined()
  })

  it('still emits copy-mention (no payload) from the manual button', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.conv-assist-btn').trigger('click')
    expect(wrapper.emitted('copy-mention')).toEqual([[]])
  })

  it('persists the toggle to localStorage and restores it on remount', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('input[type="checkbox"]').setValue(true)
    expect(localStorage.getItem(AUTO_COPY_KEY)).toBe('1')

    const wrapper2 = mountView()
    await flushPromises()
    expect((wrapper2.find('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(true)
  })
})

describe('ConversationView draft persistence', () => {
  it('restores the exact draft after unmount and remount', async () => {
    const text = '  first line\nsecond line  '
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue(text)
    expect(localStorage.getItem(draftKey())).toBe(text)

    wrapper.unmount()
    const restored = mountView()
    await flushPromises()
    expect((restored.find('textarea').element as HTMLTextAreaElement).value).toBe(text)
  })

  it('keeps drafts isolated by document and restores them when docId changes', async () => {
    localStorage.setItem(draftKey(DOC_ID), 'draft A')
    localStorage.setItem(draftKey(OTHER_DOC_ID), 'draft B')
    const wrapper = mountView(DOC_ID)
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('draft A')

    await wrapper.setProps({ docId: OTHER_DOC_ID })
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('draft B')
  })

  it('uses the signed-in username to isolate the storage key', async () => {
    const payload = btoa(JSON.stringify({ username: 'alice' }))
    window.__accessToken__ = 'header.' + payload + '.signature'
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('alice draft')
    expect(localStorage.getItem(draftKey(DOC_ID, 'alice'))).toBe('alice draft')
    expect(localStorage.getItem(draftKey())).toBeNull()
  })

  it('removes the stored draft after a successful send', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('send me')
    expect(localStorage.getItem(draftKey())).toBe('send me')

    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(localStorage.getItem(draftKey())).toBeNull()
  })

  it('keeps the stored draft when send fails', async () => {
    postRequest.mockRejectedValueOnce(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('retry me')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('retry me')
    expect(localStorage.getItem(draftKey())).toBe('retry me')
  })

  it('removes the storage entry when the input is cleared', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('textarea').setValue('temporary')
    await wrapper.find('textarea').setValue('')
    expect(localStorage.getItem(draftKey())).toBeNull()
  })

  it('continues rendering and sending when localStorage throws', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked')
    })
    const wrapper = mountView()
    await flushPromises()
    getItem.mockRestore()

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota')
    })
    await wrapper.find('textarea').setValue('still sends')
    setItem.mockRestore()
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/documents/flowgate.default.0085.0009-CH/conversation/turn',
      { body: 'still sends', speaker: 'user' },
    )
  })
})