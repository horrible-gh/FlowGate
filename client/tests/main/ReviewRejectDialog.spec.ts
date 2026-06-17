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
