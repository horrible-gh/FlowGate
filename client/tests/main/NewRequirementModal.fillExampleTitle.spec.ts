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

// group 0369 rejection rework: the second magic-wand button is independent of the
// group-name button and fills the localized label of the selected R/B root type.
describe('NewRequirementModal — document-type title fill button', () => {
  const fillTooltip = {
    en: 'Fill title with document type',
    ja: '文書種別をタイトルに入力',
    ko: '제목에 문서 유형 채우기',
  } as const
  const typeLabel = {
    en: { R: 'Requirement', B: 'Bug' },
    ja: { R: '要件定義', B: 'バグ' },
    ko: { R: '요건정의', B: '버그' },
  } as const

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
  })

  function documentTypeFillButton(wrapper: ReturnType<typeof mount>) {
    return wrapper.findAll('.title-fill-btn').find(
      (btn) => btn.attributes('aria-label') === fillTooltip[i18n.global.locale.value as 'en' | 'ja' | 'ko'],
    )!
  }

  it.each(['en', 'ja', 'ko'] as const)('locale %s: follows the selected R/B type, independent of group state', async (locale) => {
    i18n.global.locale.value = locale
    const wrapper = mount(NewRequirementModal, { global: { plugins: [i18n] } })
    await flushPromises()

    // No group selected/typed — the legacy group-name button stays hidden, but the
    // document-type button must still be present and usable.
    expect(wrapper.find(`[aria-label="${i18n.global.t('main.new_requirement_modal.use_group_name')}"]`).exists()).toBe(false)

    const fillBtn = documentTypeFillButton(wrapper)
    expect(fillBtn.exists()).toBe(true)
    expect(fillBtn.attributes('type')).toBe('button')
    expect(fillBtn.attributes('title')).toBe(fillTooltip[locale])

    const titleInput = wrapper.find('input#newReqTitle')
    await titleInput.setValue('draft title')
    await fillBtn.trigger('click')
    expect((titleInput.element as HTMLInputElement).value).toBe(typeLabel[locale].R)

    // Selecting B changes the fill value to the B label; the value is not fixed to Memo.
    await wrapper.find('.root-tab.bug').trigger('click')
    await documentTypeFillButton(wrapper).trigger('click')
    expect((titleInput.element as HTMLInputElement).value).toBe(typeLabel[locale].B)

    // Repeat click stays idempotent and never cycles the locale.
    await documentTypeFillButton(wrapper).trigger('click')
    expect((titleInput.element as HTMLInputElement).value).toBe(typeLabel[locale].B)
    expect(i18n.global.locale.value).toBe(locale)
  })

  it('with a group selected, the group-name and document-type buttons act independently', async () => {
    i18n.global.locale.value = 'en'
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

    const wrapper = mount(NewRequirementModal, { global: { plugins: [i18n] } })
    await flushPromises()

    const toggles = wrapper.findAll('.group-toggle-btn')
    await toggles[0].trigger('click')
    const select = wrapper.find('select.form-ctrl')
    await select.setValue('flowgate.default.0111')

    const titleInput = wrapper.find('input#newReqTitle')
    const groupNameLabel = i18n.global.t('main.new_requirement_modal.use_group_name')
    const groupBtn = wrapper.find(`[aria-label="${groupNameLabel}"]`)
    expect(groupBtn.exists()).toBe(true)
    const exampleBtn = documentTypeFillButton(wrapper)
    expect(exampleBtn.exists()).toBe(true)
    expect(exampleBtn.element).not.toBe(groupBtn.element)

    await exampleBtn.trigger('click')
    expect((titleInput.element as HTMLInputElement).value).toBe('Requirement')

    await groupBtn.trigger('click')
    expect((titleInput.element as HTMLInputElement).value).toBe('User Login')

    await exampleBtn.trigger('click')
    expect((titleInput.element as HTMLInputElement).value).toBe('Requirement')
  })
})
