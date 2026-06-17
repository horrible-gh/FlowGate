import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NewRequirementModal from '@main/components/NewRequirementModal.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postUrlEncoded } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postUrlEncoded: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  getRequest,
  postUrlEncoded,
}))

describe('NewRequirementModal workflow-root tabs', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    useProjectStore().setCurrentProject('flowgate')
    i18n.global.locale.value = 'en'
    getRequest.mockReset()
    postUrlEncoded.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/projects') {
        return Promise.resolve({
          data: [{ project: 'flowgate', modules: ['default'] }],
        })
      }
      return Promise.resolve({ data: { data: { nodes: [] } } })
    })
    postUrlEncoded.mockResolvedValue({
      data: {
        ok: true,
        result: { doc_id: 'flowgate.default.0001.0001-B' },
      },
    })
  })

  it('switches to B copy and submits doc_type=B', async () => {
    const wrapper = mount(NewRequirementModal, {
      global: { plugins: [i18n] },
    })
    await flushPromises()

    const tabs = wrapper.findAll('.root-tab')
    expect(tabs).toHaveLength(2)
    await tabs[1].trigger('click')

    expect(wrapper.find('.modal-title').text()).toContain('New Bug')
    expect(wrapper.find('input#newReqTitle').attributes('placeholder')).toBe('Enter a bug title')
    expect(wrapper.find('.req-start-info').classes()).toContain('bug')

    await wrapper.find('input#newReqTitle').setValue('Login fails')
    await wrapper.find('button.btn-primary').trigger('click')
    await flushPromises()

    expect(postUrlEncoded).toHaveBeenCalledWith(
      '/api/v1/outbox/create',
      expect.objectContaining({
        doc_type: 'B',
        template: 'default',
        title: 'Login fails',
      }),
    )
    expect(wrapper.emitted('created')?.[0]).toEqual([
      {
        docId: 'flowgate.default.0001.0001-B',
        openAfter: true,
      },
    ])
  })
})
