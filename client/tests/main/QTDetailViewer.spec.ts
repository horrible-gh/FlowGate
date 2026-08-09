import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import QTDetailViewer from '@main/components/QTDetailViewer.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

describe('QTDetailViewer read-only contract', () => {
  beforeEach(() => {
    getRequest.mockReset().mockResolvedValue({
      data: {
        q_id: 'flowgate.default.0398.0002-Q',
        status: 'done',
        title: 'Question',
        items: [{
          id: 1,
          seq: 1,
          body: 'Original question',
          answer_count: 1,
          answers: [{ id: 10, body: '**Existing answer**', answered_by: 'tester', answered_at: '2026-08-09T00:00:00+09:00' }],
        }],
      },
    })
    postRequest.mockReset()
  })

  it('shows the question and existing answer but no textarea or save action', async () => {
    const wrapper = mount(QTDetailViewer, {
      props: { qId: 'flowgate.default.0398.0002-Q', readOnly: true },
      global: { plugins: [i18n, createPinia()] },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Original question')
    expect(wrapper.text()).toContain('Existing answer')
    expect(wrapper.find('.q-answer-textarea').exists()).toBe(false)
    expect(wrapper.find('.btn-save-ans').exists()).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
  })
})