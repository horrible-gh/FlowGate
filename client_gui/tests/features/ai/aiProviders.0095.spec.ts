import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import * as transport from '@shared/api'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiProviderEditDialog from '@/features/ai/providers/AiProviderEditDialog.vue'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { validateProvider } from '@/features/ai/providers/providerValidation'
import * as api from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)
const cli = (id: string): ProviderItem => ({ id, name: id, exec_type: 'cli', kind: 'custom', enabled: true, cli_command: 'fake-cli' })
const apiItem: ProviderItem = { id: 'api', name: 'API', exec_type: 'api', kind: 'custom', enabled: true, api_model: 'fake-model', api_key_set: true }
const wrappers: VueWrapper[] = []
function keep<T extends VueWrapper>(w: T): T { wrappers.push(w); return w }
function input(selector: string, value: string) {
  const el = document.querySelector(selector) as HTMLInputElement
  el.value = value
  el.dispatchEvent(new Event('input'))
}
const saveButton = () => document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement
const confirmButton = () => document.querySelector('.ui-confirm-actions .ui-btn--danger') as HTMLButtonElement

beforeEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'ko'
  setCurrentUser({ username: 'admin', email: null, isAdmin: true })
})
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  document.body.innerHTML = ''
  setCurrentUser(null)
})

function mockLists(system: ProviderItem[], mine: ProviderItem[] = []) {
  vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: system, default_provider_id: system[0]?.id ?? null, updated_at: null })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: mine, default_provider_id: mine[0]?.id ?? null, updated_at: null })
}
async function panel() {
  const w = keep(mount(AiProvidersPanel, { global: { plugins: [i18n] } }))
  await flushPromises()
  return w
}

function dialog(provider: ProviderItem | null = apiItem) {
  return keep(mount(AiProviderEditDialog, { props: { open: true, provider }, global: { plugins: [i18n] }, attachTo: document.body }))
}

describe('0095 provider field contract', () => {
  it.each([
    ['cli_command', '', 'required_for_cli'], ['cli_command', 'x'.repeat(4001), 'too_long'],
    ['name', 'x'.repeat(101), 'too_long'],
  ])('rejects invalid CLI %s', (field, value, reason) => {
    expect(validateProvider({ ...cli('cli'), [field]: value })).toContainEqual({ field, reason })
  })
  it.each([
    ['api_model', ' ', 'required_for_api'], ['api_model', 'x'.repeat(201), 'too_long'],
    ['api_base_url', 'x'.repeat(501), 'too_long'], ['api_key', 'x'.repeat(1001), 'too_long'],
  ])('rejects invalid API %s', (field, value, reason) => {
    expect(validateProvider({ ...apiItem, [field]: value })).toContainEqual({ field, reason })
  })
  it('accepts exact field lengths and counts Unicode like the server', () => {
    expect(validateProvider({ ...cli('cli'), name: '😀'.repeat(100), cli_command: 'x'.repeat(4000) })).toEqual([])
    expect(validateProvider({ ...apiItem, api_model: 'x'.repeat(200), api_base_url: 'x'.repeat(500), api_key: 'x'.repeat(1000) })).toEqual([])
  })
  it('prevents an empty command/model from emitting a save and enables valid input', async () => {
    const w = dialog(null)
    input('#provider-name', 'CLI')
    await flushPromises()
    expect(saveButton().disabled).toBe(true)
    input('#provider-command', 'fake-cli')
    await flushPromises()
    expect(saveButton().disabled).toBe(false)
    saveButton().click()
    await flushPromises()
    expect(w.emitted('save')?.[0][0]).toMatchObject({ cli_command: 'fake-cli' })
    await w.setProps({ provider: { ...apiItem, api_model: '' } })
    expect(saveButton().disabled).toBe(true)
    expect(document.querySelector('.dialog-form')?.textContent).toContain('모델')
    input('#provider-model', 'fake-model')
    await flushPromises()
    expect(saveButton().disabled).toBe(false)
  })
})

describe('0095 API key intent', () => {
  it('emits null to keep, new text to replace, and empty text only for explicit clear', async () => {
    const w = dialog()
    saveButton().click()
    await flushPromises()
    expect(w.emitted('save')?.[0][0]).toMatchObject({ api_key: null })
    input('#provider-api-key', 'fake-replacement')
    await flushPromises()
    saveButton().click()
    await flushPromises()
    expect(w.emitted('save')?.[1][0]).toMatchObject({ api_key: 'fake-replacement' })
    const clear = document.querySelector('.dialog-form input[type=checkbox]') as HTMLInputElement
    clear.click()
    await flushPromises()
    expect((document.querySelector('#provider-api-key') as HTMLInputElement).disabled).toBe(true)
    saveButton().click()
    await flushPromises()
    expect(w.emitted('save')?.[2][0]).toMatchObject({ api_key: '' })
    await w.setProps({ open: false })
    await w.setProps({ open: true })
    expect((document.querySelector('.dialog-form input[type=checkbox]') as HTMLInputElement).checked).toBe(false)
    saveButton().click()
    await flushPromises()
    expect(w.emitted('save')?.[3][0]).toMatchObject({ api_key: null })
  })
})

describe('0095 full-list deletion', () => {
  it('disables delete during a pending save and does not lose an operation silently', async () => {
    const items = [cli('one'), cli('two')]
    mockLists(items)
    let finish!: (data: { providers: ProviderItem[]; default_provider_id: string; updated_at: null }) => void
    const save = vi.spyOn(api, 'saveSystemAi').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const w = await panel()
    await w.find('.provider-card .ui-toggle').trigger('click')
    expect(w.find('.danger-icon').attributes('disabled')).toBeDefined()
    await w.find('.danger-icon').trigger('click')
    expect(document.querySelector('.ui-confirm-actions')).toBeNull()
    finish({ providers: [{ ...items[0], enabled: false }, items[1]], default_provider_id: 'one', updated_at: null })
    await flushPromises()
    expect(w.find('.danger-icon').attributes('disabled')).toBeUndefined()
    await w.find('.danger-icon').trigger('click')
    expect(document.querySelector('.ui-confirm-actions')).not.toBeNull()
    expect(save).toHaveBeenCalledTimes(1)
  })
  it('keeps the list/default and confirmation on failure; retries then adopts the server list', async () => {
    const items = [cli('one'), cli('two')]
    mockLists(items, [cli('mine')])
    const save = vi.spyOn(api, 'saveSystemAi')
      .mockRejectedValueOnce(new api.AiRequestError('Referenced history retained', 'provider_in_use', []))
      .mockResolvedValueOnce({ providers: [items[1]], default_provider_id: 'two', updated_at: null })
    const w = await panel()
    await w.find('.danger-icon').trigger('click')
    confirmButton().click()
    await flushPromises()
    expect(w.findAll('.provider-card')).toHaveLength(3)
    expect(w.findAll('.provider-card')[0].find('.system-default').exists()).toBe(true)
    expect(document.querySelector('.ui-confirm-actions')).not.toBeNull()
    expect(document.body.textContent).toContain('Referenced history retained')
    confirmButton().click()
    await flushPromises()
    expect(save).toHaveBeenNthCalledWith(2, [items[1]], 'two')
    expect(w.findAll('.name-title-row b').map(n => n.text())).toEqual(['two', 'mine'])
    expect(document.querySelector('.ui-confirm-actions')).toBeNull()
  })
  it('holds the confirmation while saving and rejects repeated confirm/cancel', async () => {
    const items = [cli('one')]
    mockLists(items)
    let finish!: (data: { providers: ProviderItem[]; default_provider_id: null; updated_at: null }) => void
    const save = vi.spyOn(api, 'saveSystemAi').mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const w = await panel()
    await w.find('.danger-icon').trigger('click')
    confirmButton().click()
    await flushPromises()
    expect(confirmButton().disabled).toBe(true)
    expect((document.querySelector('.ui-confirm-actions .ui-btn--secondary') as HTMLButtonElement).disabled).toBe(true)
    // A duplicate emitted event must also be harmless, regardless of button state.
    w.findComponent(ConfirmDialog).vm.$emit('confirm')
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    expect(w.findAll('.provider-card')).toHaveLength(1)
    finish({ providers: [], default_provider_id: null, updated_at: null })
    await flushPromises()
    expect(w.findAll('.provider-card')).toHaveLength(0)
    expect(document.querySelector('.ui-confirm-actions')).toBeNull()
  })
  it.each([19, 20, 21, 300])('deletes from %i items while search is active without dropping hidden rows or another owner', async count => {
    const items = Array.from({ length: count }, (_, i) => cli(`system-${i}`))
    const mine = [cli('mine')]
    mockLists(items, mine)
    const target = items[Math.floor(count / 2)]
    const remaining = items.filter(p => p.id !== target.id)
    const save = vi.spyOn(api, 'saveSystemAi').mockResolvedValue({ providers: remaining, default_provider_id: items[0].id, updated_at: null })
    const saveMine = vi.spyOn(api, 'saveMyAi')
    const w = await panel()
    expect(w.findAll('.provider-card')).toHaveLength(count + 1)
    await w.find('.detail-search input').setValue(target.id)
    // Names may share prefixes; select the exact card for the target.
    const card = w.findAll('.provider-card').find(c => c.find('.name-title-row b').text() === target.name)!
    await card.find('.danger-icon').trigger('click')
    confirmButton().click()
    await flushPromises()
    expect(save).toHaveBeenCalledWith(remaining, items[0].id)
    expect(saveMine).not.toHaveBeenCalled()
    await w.find('.detail-search input').setValue('')
    expect(w.findAll('.provider-card')).toHaveLength(count)
    expect(w.findAll('.name-title-row b').map(n => n.text())).toContain('mine')
  })
  it('applies the 300 limit independently to both owners', async () => {
    const list = Array.from({ length: 300 }, (_, i) => cli(`item-${i}`))
    mockLists(list, list.map(p => ({ ...p, id: `my-${p.id}` })))
    const w = await panel()
    expect(w.find('.detail-actions button').attributes('disabled')).toBeDefined()
    expect(w.findAll('.provider-card')).toHaveLength(600)
  })
})

describe('0095 save error presentation', () => {
  it.each(['ko', 'en', 'ja'] as const)('shows row, name, field and reason in %s', async locale => {
    i18n.global.locale.value = locale
    vi.spyOn(transport, 'putRequest').mockRejectedValue({ response: { status: 422, data: { detail: { code: 'validation_failed', errors: [
      { index: 1, field: 'api_model', reason: 'too_long' }, { index: 0, field: 'cli_command', reason: 'required_for_cli' },
    ] } } } })
    try {
      await api.saveSystemAi([cli('one'), apiItem], 'one')
      throw new Error('expected rejection')
    } catch (error) {
      expect(error).toBeInstanceOf(api.AiRequestError)
      const e = error as api.AiRequestError
      expect(e.fieldErrors).toHaveLength(2)
      expect(e.message).toContain('API')
      expect(e.message).toContain('one')
      expect(e.message).toContain('200')
      expect(e.message).toContain('2')
      expect(e.message).not.toContain('settings.ai.')
    }
  })
  it.each([
    [409, 'provider_in_use', 'common.toast.ai_provider_in_use'],
    [403, null, 'error.forbidden'], [401, null, 'error.unauthorized'],
  ] as const)('distinguishes HTTP %i / %s', async (status, code, key) => {
    vi.spyOn(transport, 'putRequest').mockRejectedValue({ response: { status, data: { detail: code ? { code } : 'denied' } } })
    await expect(api.saveMyAi([])).rejects.toThrow(String(i18n.global.t(key)))
  })
  it('distinguishes a network failure', async () => {
    vi.spyOn(transport, 'putRequest').mockRejectedValue({ isAxiosError: true })
    await expect(api.saveSystemAi([], null)).rejects.toThrow(String(i18n.global.t('error.network')))
  })
})
