import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NewRequirementModal from '@main/components/NewRequirementModal.vue'
import { useProjectStore } from '@main/stores/project'
import { useExplorerStore } from '@main/stores/explorer'

const { getRequest, postUrlEncoded } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postUrlEncoded: vi.fn(),
}))

vi.mock('@shared/api', () => ({ getRequest, postUrlEncoded }))

// R0001 group 0111: pressing the "use group name" button must drop the pure group
// title into the document title field (not copy it to the clipboard).
describe('NewRequirementModal — use group name as title', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    useProjectStore().setCurrentProject('flowgate')
    i18n.global.locale.value = 'en'
    getRequest.mockReset()
    postUrlEncoded.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/projects') {
        return Promise.resolve({ data: [{ project: 'flowgate', modules: ['default'] }] })
      }
      return Promise.resolve({ data: { data: { nodes: [] } } })
    })
    // Seed an existing group with no R/B child so it is an eligible group option.
    const explorer = useExplorerStore()
    explorer.groupTreeCache['flowgate:main'] = [
      {
        id: 'flowgate.default.0111',
        parent_id: 'default',
        node_type: 'group',
        type_code: null,
        number: '0111',
        filename: null,
        label: 'User Login',
        title: 'User Login',
        has_md: false,
        md_path: null,
      },
    ] as any
  })

  it('fills the empty title input with the selected group name', async () => {
    const wrapper = mount(NewRequirementModal, { global: { plugins: [i18n] } })
    await flushPromises()

    // Switch to "existing group" mode and select the seeded group.
    const toggles = wrapper.findAll('.group-toggle-btn')
    await toggles[0].trigger('click')
    const select = wrapper.find('select.form-ctrl')
    await select.setValue('flowgate.default.0111')

    const titleInput = wrapper.find('input#newReqTitle')
    expect((titleInput.element as HTMLInputElement).value).toBe('')

    // Group 0369 adds a second, always-visible document-type fill button with the
    // same class/icon, so the group-name button must be found by its own tooltip.
    const fillBtn = wrapper.find(`[aria-label="${i18n.global.t('main.new_requirement_modal.use_group_name')}"]`)
    expect(fillBtn.exists()).toBe(true)
    await fillBtn.trigger('click')

    expect((titleInput.element as HTMLInputElement).value).toBe('User Login')
  })

  it('hides the group-name button when no group is selected (default new-group mode, empty name)', async () => {
    const wrapper = mount(NewRequirementModal, { global: { plugins: [i18n] } })
    await flushPromises()
    // Default mode is "new" with an empty new-group name → nothing to fill from.
    expect(wrapper.find(`[aria-label="${i18n.global.t('main.new_requirement_modal.use_group_name')}"]`).exists()).toBe(false)
    // The document-type title button is unconditional and stays visible.
    expect(wrapper.find('.title-fill-btn').exists()).toBe(true)
  })
})
