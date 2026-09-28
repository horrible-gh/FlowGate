import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  postRequest: vi.fn().mockResolvedValue({ data: {} }),
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
})

describe('WorkflowDecisionModal T2 compact display (0590)', () => {
  it('keeps the T2 label on one line and shortens the automatic TR2 hint', () => {
    const wrapper = mount(WorkflowDecisionModal, {
      props: { visible: true },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })

    const t2 = wrapper.findAll('.wdm-type-btn').find((button) => button.find('.doc-tag').text() === 'T2')
    expect(t2).toBeTruthy()
    expect(t2!.get('.wdm-type-name').classes()).toContain('wdm-type-name--nowrap')
    expect(t2!.get('.wdm-auto-hint').text()).toBe('→ TR2 자동')

    wrapper.unmount()
  })

  it('uses the short TR2 automatic hint in both workflow namespaces for all locales', () => {
    const expected = {
      ko: '→ TR2 자동',
      en: '→ TR2 auto',
      ja: '→ TR2 自動',
    } as const

    for (const locale of ['ko', 'en', 'ja'] as const) {
      i18n.global.locale.value = locale
      expect(i18n.global.t('main.workflow_decision_modal.auto_hint_TR2')).toBe(expected[locale])
      expect(i18n.global.t('main.workflow_edit_modal.auto_hint_TR2')).toBe(expected[locale])
    }
  })
})
