import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewRejectDialog from '@main/components/ReviewRejectDialog.vue'

describe('ReviewRejectDialog save completion', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('closes the dialog after the rejection reason is saved', async () => {
    const wrapper = mount(ReviewRejectDialog, {
      props: {
        visible: true,
        docId: 'flowgate.default.0031.0003-NR',
        docName: '반려 UI 4개 이슈 현황조사',
        docType: 'NR',
      },
      global: {
        plugins: [i18n],
        stubs: { teleport: true },
      },
    })

    ;(wrapper.vm as any).notifySaved()

    expect(wrapper.emitted('update:visible')).toEqual([[false]])
    wrapper.unmount()
  })
})

// 0419 T0006 (NR0003 후속 T 권고 1/3, TR0005 rev2 시안): the sidebar [수정] entry
// point reopens this same dialog in editMode to correct the latest rejection's
// wording — it must not offer the reject dropdown (copy-mention/invoke-ai/invoke-
// command), which stays scoped to the original reject action.
describe('ReviewRejectDialog editMode', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function mountDialog(editMode?: boolean) {
    return mount(ReviewRejectDialog, {
      props: {
        visible: true,
        docId: 'flowgate.default.0031.0003-NR',
        docName: '반려 UI 4개 이슈 현황조사',
        docType: 'NR',
        existingReason: '기존 반려 사유',
        ...(editMode !== undefined ? { editMode } : {}),
      },
      global: {
        plugins: [i18n],
        stubs: { teleport: true },
      },
    })
  }

  it('hides the reject dropdown when editMode is true', () => {
    const wrapper = mountDialog(true)
    expect(wrapper.find('.rrd-split-wrap').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the reject dropdown for the ordinary (non-edit) reject flow', () => {
    const wrapper = mountDialog(false)
    expect(wrapper.find('.rrd-split-wrap').exists()).toBe(true)
    wrapper.unmount()
  })

  it('still emits save-reason in editMode (parent decides which endpoint to call)', async () => {
    const wrapper = mountDialog(true)
    await wrapper.find('.rrd-textarea').setValue('수정된 반려 사유')
    await wrapper.find('.rrd-footer > .btn.btn-danger').trigger('click')
    expect(wrapper.emitted('save-reason')).toEqual([['수정된 반려 사유']])
    wrapper.unmount()
  })
})
