import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { resolveApiError } from '@shared/apiErrors'
import AiProviderListEditor from '@/settings/components/AiProviderListEditor.vue'
import { PROVIDERS_MAX } from '@/settings/components/aiProviderLimits'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const catalog = { exec_types: ['cli', 'api'], kinds: { cli: ['custom'], api: ['custom'] } }
const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  resetDialogSystem()
  document.body.innerHTML = ''
  i18n.global.locale.value = 'ko'
})

describe('0095 legacy list limit', () => {
  it.each([19, 20, 21, 299, 300])('adds from %i only below the owner limit of 300', async count => {
    expect(PROVIDERS_MAX).toBe(300)
    const providers = Array.from({ length: count }, (_, i) => ({ id: `fake-${i}`, name: `fake-${i}`, exec_type: 'cli', kind: 'custom', enabled: true, cli_command: 'fake-cli' }))
    const w = mount(AiProviderListEditor, { props: { providers, defaultIndex: 0, catalog }, global: { plugins: [i18n] }, attachTo: document.body })
    wrappers.push(w)
    ;(w.vm as unknown as { openAdd: () => void }).openAdd()
    await flushPromises()
    const surface = document.querySelector('.fg-dialog-surface')!
    const name = surface.querySelector<HTMLInputElement>('input.form-ctrl')!
    name.value = 'new-fake'
    name.dispatchEvent(new Event('input'))
    const command = surface.querySelector<HTMLInputElement>('input.mono')!
    command.value = 'fake-cli'
    command.dispatchEvent(new Event('input'))
    await flushPromises()
    ;(document.querySelector('[data-dialog-action-id="save"]') as HTMLButtonElement).click()
    await flushPromises()
    if (count < 300) {
      const emitted = w.emitted('update:providers')!
      expect(emitted[0][0]).toHaveLength(count + 1)
    } else {
      expect(w.emitted('update:providers')).toBeUndefined()
      expect(document.body.textContent).toContain('300')
    }
  })
  it.each(['ko', 'en', 'ja'] as const)('explains a referenced-provider conflict in %s', locale => {
    i18n.global.locale.value = locale
    const error = { response: { status: 409, data: { detail: { code: 'provider_in_use' } } } }
    expect(resolveApiError(error, i18n.global.t, 'common.toast.settings_save_failed')).toBe(i18n.global.t('common.toast.ai_provider_in_use'))
  })
})
