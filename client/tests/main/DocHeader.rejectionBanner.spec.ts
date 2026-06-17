import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const TAB = {
  id: 'flowgate.default.0031.0003-NR',
  title: '반려 UI 4개 이슈 현황조사',
  path: '',
  type: 'md',
  typeCode: 'NR',
}

let rejectionHistory: Array<{
  reason: string
  rejected_at: string
  rejected_by: string | null
}> = []
let docReviewStatus = 'rejected'
let rejectionReason: string | null = '긴 반려 사유 원문'

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: TAB as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  rejectionHistory = [{
    reason: '긴 반려 사유 원문',
    rejected_at: '2026-06-12T10:00:00Z',
    rejected_by: 'reviewer',
  }]
  docReviewStatus = 'rejected'
  rejectionReason = '긴 반려 사유 원문'
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) {
      return Promise.resolve({
        data: {
          doc_id: TAB.id,
          title: TAB.title,
          status: 'open',
          type_code: 'NR',
          doc_review_status: docReviewStatus,
          rejection_reason: rejectionReason,
          rejection_history: rejectionHistory,
          is_editable: true,
          project_id: 'flowgate',
          group_id: 'flowgate.default.0031',
        },
      })
    }
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader rejection banner', () => {
  it('shows a rejection status without repeating the reason', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    const banner = wrapper.find('.rejection-banner')
    expect(banner.text()).toContain('The document was rejected.')
    expect(banner.text()).not.toContain('긴 반려 사유 원문')
    wrapper.unmount()
  })

  it('shows the rejection count after the document is rejected again', async () => {
    rejectionHistory.push({
      reason: '수정된 반려 사유',
      rejected_at: '2026-06-12T11:00:00Z',
      rejected_by: 'reviewer',
    })
    const wrapper = mountHeader()
    await flushPromises()

    expect(wrapper.find('.rejection-banner').text()).toContain('The document was rejected 2 times.')
    wrapper.unmount()
  })

  it('shows that the document was revised after a rejected document is edited', async () => {
    docReviewStatus = 'revised'
    rejectionReason = null
    const wrapper = mountHeader()
    await flushPromises()

    expect(wrapper.find('.rejection-banner').text()).toContain('The document was revised.')
    wrapper.unmount()
  })
})
