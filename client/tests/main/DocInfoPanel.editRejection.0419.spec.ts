// 0419 T0006 (NR0003 후속 T 권고 2 / TR0005 rev1 반려): the [수정] entry point for
// correcting a rejection's wording lives in the sidebar's AI 검수·반려 header, next
// to [전체보기] — NOT in the action bar (rev1 반려: "왜 액션바에 수정버튼 달아놔").
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

function mountPanel(reviewStatus: string | null) {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0015.0002-D',
      typeCode: 'D',
      reviewStatus,
      rejectReason: '반려 사유',
      rejectionHistory: [
        { reason: '반려 사유', rejected_at: '2026-06-12T20:43:00+09:00', rejected_by: null },
      ],
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

function mergedSection(wrapper: ReturnType<typeof mountPanel>) {
  return wrapper.findAll('.dip-section')
    .find((s) => s.find('.dip-sec-toggle').exists() && s.find('.dip-sec-toggle').text().includes(i18n.global.t('main.doc_info_panel.section_review_reject')))!
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('DocInfoPanel [수정] rejection-edit entry point (0419 T0006)', () => {
  it('shows [수정] next to [전체보기] while the document is rejected', () => {
    const wrapper = mountPanel('rejected')
    const editBtn = mergedSection(wrapper).find('.dip-rr-edit')
    expect(editBtn.exists()).toBe(true)
    expect(editBtn.classes()).toContain('dip-qa-act')
    wrapper.unmount()
  })

  it('emits edit-rejection when clicked', async () => {
    const wrapper = mountPanel('rejected')
    await mergedSection(wrapper).find('.dip-rr-edit').trigger('click')
    expect(wrapper.emitted('edit-rejection')).toHaveLength(1)
    wrapper.unmount()
  })

  it.each(['approved', 'revised', 'pending_review', null])(
    'hides [수정] once the document is no longer rejected (reviewStatus=%s)',
    (status) => {
      const wrapper = mountPanel(status)
      expect(mergedSection(wrapper).find('.dip-rr-edit').exists()).toBe(false)
      wrapper.unmount()
    },
  )
})
