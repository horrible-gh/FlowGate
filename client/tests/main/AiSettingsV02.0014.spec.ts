import { DOMWrapper, enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  getRequest, postRequest: vi.fn(), putRequest: vi.fn(), patchRequest: vi.fn(),
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

const sequence = { data: {
  doc_id: 'flowgate.v02.0014.0001-R', decided: true,
  items: [
    { id: 1, item_seq: 1, type: 'R', label: 'Requirement', status: 'done' },
    { id: 2, item_seq: 2, type: 'TR', label: 'Report', status: 'pending' },
  ],
  head: { id: 2, item_seq: 2, type: 'TR', label: 'Report', status: 'pending' },
} }
const providers = [
  { id: 'p1', name: 'One' }, { id: 'p2', name: 'Two' }, { id: 'p3', name: 'Three' },
]

enableAutoUnmount(afterEach)
beforeEach(() => {
  i18n.global.locale.value = 'ko'
  getRequest.mockReset().mockResolvedValue(sequence)
})
afterEach(() => { document.body.innerHTML = '' })

async function providerPanel() {
  await flushPromises()
  const dialog = new DOMWrapper(document.body).get('.continuous-work-dialog')
  const tab = dialog.findAll('.cwd-tab').find(button =>
    button.text() === i18n.global.t('main.continuous_work.tab_provider'))!
  await tab.trigger('click')
  const panel = dialog.get('.cwd-provider-block')
  // Positive controls: the real tab and its usable default/step selectors must exist first.
  expect(panel.get('.cwd-provider-select .aip-select-input').element).toBeInstanceOf(HTMLSelectElement)
  expect(panel.findAll('.cwd-override-select .aip-select-input')).toHaveLength(1)
  return panel
}

// v02.0087 B0001 removes the legacy dialog's AI group/reworker choices. The former
// preset auto-fill test is replaced because that feature is intentionally removed here;
// settings AI groups and the shared start-request options remain supported elsewhere.
describe('0087 B0001: continuous providers without AI group or reworker inputs', () => {
  it('keeps default and per-step choices while omitting the removed rows and confirm keys', async () => {
    const wrapper = mount(ContinuousWorkDialog, { props: {
      visible: true, docRef: 'flowgate.v02.0014.0001-R',
      selectedProvider: 'p2', providers,
    }, global: { plugins: [i18n] } })
    const panel = await providerPanel()
    expect(panel.findAll('.cwd-provider-row')).toHaveLength(1)
    expect(panel.get('.cwd-provider-label').text()).toBe(i18n.global.t('main.continuous_work.provider_default_label'))
    expect(panel.find('[data-test="start-ai-group"]').exists()).toBe(false)
    expect(panel.find('[data-test="start-reworker-provider"]').exists()).toBe(false)
    expect(panel.text()).not.toContain(i18n.global.t('settings.project.ai_v02.groups'))
    expect(panel.text()).not.toContain(i18n.global.t('settings.project.ai_v02.role_reworker'))

    await panel.get('.cwd-provider-select .aip-select-input').setValue('p1')
    expect(wrapper.emitted('update:provider')?.at(-1)?.[0]).toBe('p1')
    await wrapper.setProps({ selectedProvider: 'p1' })
    expect((panel.get('.cwd-override-select .aip-select-input').element as HTMLSelectElement).value).toBe('p1')
    await panel.get('.cwd-override-select .aip-select-input').setValue('p3')
    await new DOMWrapper(document.body).get('[data-dialog-action-role="primary"]').trigger('click')
    const payload = wrapper.emitted('confirm')?.at(-1)?.[0] as Record<string, unknown>
    expect(payload).toMatchObject({ targetSeq: 2, providerOverrides: { 2: 'p3' } })
    expect(Object.prototype.hasOwnProperty.call(payload, 'requestedAiGroupId')).toBe(false)
    expect(Object.prototype.hasOwnProperty.call(payload, 'reworkerProviderId')).toBe(false)
  })

  it('does not fetch AI groups on open, cancel/reopen, or a parent project remount', async () => {
    // Project identity belongs to the parent. Remount on project change without bringing
    // back the removed projectId prop on ContinuousWorkDialog.
    const Host = defineComponent({
      props: { project: { type: String, required: true }, visible: { type: Boolean, default: true } },
      emits: ['update:visible'],
      setup(props, { emit }) {
        return () => h(ContinuousWorkDialog, {
          key: props.project, docRef: props.project + '.v02.0014.0001-R',
          visible: props.visible, providers, selectedProvider: 'p2',
          'onUpdate:visible': (visible: boolean) => emit('update:visible', visible),
        })
      },
    })
    const wrapper = mount(Host, { props: { project: 'flowgate' }, global: { plugins: [i18n] } })
    await providerPanel()
    expect(getRequest.mock.calls.some(([url]) => url === '/api/v1/workflow/sequence')).toBe(true)
    await new DOMWrapper(document.body).get('[data-dialog-action-role="cancel"]').trigger('click')
    expect(wrapper.emitted('update:visible')?.at(-1)?.[0]).toBe(false)
    await wrapper.setProps({ visible: false })
    await flushPromises()
    expect(document.querySelector('.continuous-work-dialog')).toBeNull()
    await wrapper.setProps({ visible: true })
    await providerPanel()
    await wrapper.setProps({ project: 'another' })
    await providerPanel()
    expect(wrapper.findComponent(ContinuousWorkDialog).props('docRef')).toBe('another.v02.0014.0001-R')
    expect(getRequest.mock.calls.filter(([url]) => String(url).includes('/ai-groups'))).toEqual([])
  })
})
