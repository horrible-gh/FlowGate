import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

// flowgate.default.0578 T0010 §2.5: submit() used to fall back to
// e?.response?.data?.message (raw server text) when extractApiErrorMessage found no code.
// That fallback argument is now always this screen's own translation key.

const { postRequest } = vi.hoisted(() => ({ postRequest: vi.fn() }))
vi.mock('@shared/api', async () => {
  const actual = await vi.importActual<typeof import('@shared/api')>('@shared/api')
  return { ...actual, postRequest }
})

async function mountModal() {
  const CreateFileFolderModal = (await import('@main/components/CreateFileFolderModal.vue')).default
  const wrapper = mount(CreateFileFolderModal, {
    props: { visible: true, type: 'file', projectId: 'prj_1', parentPath: '/' },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
  await flushPromises()
  return wrapper
}

describe('CreateFileFolderModal save error (0578 T0010)', () => {
  it('never shows the raw response.data.message; shows this screen fallback instead', async () => {
    postRequest.mockRejectedValue({ response: { data: { message: 'RAW SERVER TEXT' } } })
    const wrapper = await mountModal()
    await wrapper.get('input.form-ctrl').setValue('new-file.txt')
    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('RAW SERVER TEXT')
    expect(wrapper.text()).toContain(i18n.global.t('main.create_file_folder_modal.error_save_failed'))
  })

  // flowgate.default.0578.0011-TR rev1 (T0010 task 4.3): errorMessage used to store the
  // already-translated string, so it stayed in whatever locale was active when the error
  // occurred. It must now re-render in the newly active locale.
  it('re-renders the persistent save error in the new locale after a locale change', async () => {
    i18n.global.locale.value = 'ko'
    postRequest.mockRejectedValue({ response: { data: { message: 'RAW SERVER TEXT' } } })
    const wrapper = await mountModal()
    await wrapper.get('input.form-ctrl').setValue('new-file.txt')
    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()

    const koText = i18n.global.t('main.create_file_folder_modal.error_save_failed')
    expect(wrapper.text()).toContain(koText)

    i18n.global.locale.value = 'en'
    await flushPromises()
    const enText = i18n.global.t('main.create_file_folder_modal.error_save_failed')
    expect(wrapper.text()).toContain(enText)
    expect(wrapper.text()).not.toContain(koText)
  })

  // The client-side "name required" validation message must re-render too -- it's the
  // same errorMessage sink, just fed a translation key instead of an api error.
  it('re-renders the name-required validation error in the new locale after a locale change', async () => {
    i18n.global.locale.value = 'ko'
    const wrapper = await mountModal()
    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()

    const koText = i18n.global.t('main.create_file_folder_modal.error_name_required')
    expect(wrapper.text()).toContain(koText)

    i18n.global.locale.value = 'en'
    await flushPromises()
    const enText = i18n.global.t('main.create_file_folder_modal.error_name_required')
    expect(wrapper.text()).toContain(enText)
    expect(wrapper.text()).not.toContain(koText)
  })
})
