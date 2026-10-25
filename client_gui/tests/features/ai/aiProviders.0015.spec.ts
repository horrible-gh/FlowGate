import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiProviderEditDialog from '@/features/ai/providers/AiProviderEditDialog.vue'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import * as api from '@/features/ai/api'
import { AiRequestError } from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// AI 목록 (flowgate.v02.0017 CH0006 (2)): system AI and my AI in one list. The 0015 project
// modes (inherit / custom / disabled) were retired with project-owned AI.

const systemAi: ProviderItem[] = [
  { id: 'sys-1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude -p' },
  { id: 'sys-2', name: 'GPT-4o', exec_type: 'api', kind: 'openai', enabled: true, api_key_set: true },
]

const myAi: ProviderItem[] = [
  { id: 'my-1', name: 'My Gemini', exec_type: 'api', kind: 'gemini', enabled: true, api_key_set: false },
  { id: 'my-2', name: 'My Haiku', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude -p' },
]

function mockLists() {
  vi.spyOn(api, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-1' })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: myAi, default_provider_id: null, updated_at: null })
}

async function mountPanel() {
  const wrapper = mount(AiProvidersPanel, { global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

const cardNames = (wrapper: Awaited<ReturnType<typeof mountPanel>>) =>
  wrapper.findAll('.provider-card').map((card) => card.find('.name-title-row b').text())

describe('AiProvidersPanel (0015 T0003, AI 목록 since 0017 CH0006)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
    setCurrentUser({ username: 'member', email: null, isAdmin: false })
  })

  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it('lists system AI first and then my AI in one list', async () => {
    mockLists()
    const wrapper = await mountPanel()

    expect(cardNames(wrapper)).toEqual(['Claude Sonnet 5', 'GPT-4o', 'My Gemini', 'My Haiku'])
    expect(wrapper.findAll('.provider-card').map((card) => card.text()).join(' ')).not.toContain('sys-1')
    expect(wrapper.findAll('.provider-card').map((card) => card.text()).join(' ')).not.toContain('my-1')
    const owners = wrapper.findAll('.provider-card .mode-icon.owner').map((icon) => icon.attributes('aria-label'))
    expect(owners).toEqual(['시스템', '시스템', '내 AI', '내 AI'])
    // A non-administrator reads system AI with the system (not admin-only) endpoint.
    expect(api.fetchVisibleSystemAi).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('filters by owner and by search query', async () => {
    mockLists()
    const wrapper = await mountPanel()

    const ownerButtons = wrapper.findAll('.segmented button')
    await ownerButtons[2].trigger('click')
    expect(cardNames(wrapper)).toEqual(['My Gemini', 'My Haiku'])

    await ownerButtons[0].trigger('click')
    await wrapper.find('.detail-search input').setValue('sonnet')
    expect(cardNames(wrapper)).toEqual(['Claude Sonnet 5'])
    await wrapper.find('.detail-search input').setValue('sys-2')
    expect(cardNames(wrapper)).toEqual(['GPT-4o'])
    expect(wrapper.find('.provider-card').text()).not.toContain('sys-2')
    wrapper.unmount()
  })

  it('keeps system AI read-only for a non-administrator and saves my AI as a whole list', async () => {
    mockLists()
    const saveMine = vi.spyOn(api, 'saveMyAi').mockResolvedValue({ providers: myAi, default_provider_id: null, updated_at: null })
    const saveSystem = vi.spyOn(api, 'saveSystemAi')
    const wrapper = await mountPanel()

    const cards = wrapper.findAll('.provider-card')
    expect(cards[0].find('.read-only-hint').exists()).toBe(true)
    expect(cards[0].find('.ui-toggle').attributes('disabled')).toBeDefined()
    expect(cards[2].find('.read-only-hint').exists()).toBe(false)

    await cards[2].find('.ui-toggle').trigger('click')
    await flushPromises()
    expect(saveMine).toHaveBeenCalledWith([{ ...myAi[0], enabled: false }, myAi[1]])
    expect(saveSystem).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('reorders within an owner from the keyboard and disables reordering while searching', async () => {
    mockLists()
    const saveMine = vi.spyOn(api, 'saveMyAi').mockResolvedValue({ providers: [myAi[1], myAi[0]], default_provider_id: null, updated_at: null })
    const wrapper = await mountPanel()

    // Only my AI can be reordered by a non-administrator.
    const grips = wrapper.findAll('button.grip')
    expect(grips).toHaveLength(2)
    await grips[0].trigger('keydown', { key: 'ArrowDown' })
    await flushPromises()
    expect(saveMine).toHaveBeenCalledWith([myAi[1], myAi[0]])

    await wrapper.find('.detail-search input').setValue('my')
    expect(wrapper.findAll('button.grip')).toHaveLength(0)
    expect(wrapper.findAll('.grip--static').length).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('surfaces a missing API key on my AI', async () => {
    mockLists()
    const wrapper = await mountPanel()

    const cards = wrapper.findAll('.provider-card')
    expect(cards[2].text()).toContain('API 키 설정 필요')
    expect(cards[1].text()).not.toContain('API 키 설정 필요')
    wrapper.unmount()
  })

  it('lets an administrator change the system default', async () => {
    vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-1', updated_at: null })
    vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: [], default_provider_id: null, updated_at: null })
    const saveSystem = vi.spyOn(api, 'saveSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-2', updated_at: null })
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    const wrapper = await mountPanel()

    const star = wrapper.findAll('.provider-card')[1].find('button[aria-label="시스템 기본으로 지정"]')
    expect(star.exists()).toBe(true)
    await star.trigger('click')
    await flushPromises()
    expect(saveSystem).toHaveBeenCalledWith(systemAi, 'sys-2')
    wrapper.unmount()
  })

  it('validates a new AI name against the selected owner after an administrator switches scopes', async () => {
    vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-1', updated_at: null })
    vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: myAi, default_provider_id: null, updated_at: null })
    const saveMine = vi.spyOn(api, 'saveMyAi').mockResolvedValue({ providers: myAi, default_provider_id: null, updated_at: null })
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    const wrapper = await mountPanel()

    await wrapper.find('.detail-actions button').trigger('click')
    const name = document.querySelector('#provider-name') as HTMLInputElement
    const save = document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement
    const ownerButtons = document.querySelectorAll('.dialog-form .segmented button') as NodeListOf<HTMLButtonElement>

    const command = document.querySelector('#provider-command') as HTMLInputElement
    command.value = 'claude -p'
    command.dispatchEvent(new Event('input'))
    name.value = ' GPT-4o '
    name.dispatchEvent(new Event('input'))
    await flushPromises()
    expect(save.disabled).toBe(true)
    expect(document.querySelector('#provider-name-error')?.textContent).toContain('같은 이름의 AI 연결이 이미 있습니다.')

    ownerButtons[1].click()
    await flushPromises()
    expect(save.disabled).toBe(false)
    expect(document.querySelector('#provider-name-error')).toBeNull()

    name.value = ' my gemini '
    name.dispatchEvent(new Event('input'))
    await flushPromises()
    expect(save.disabled).toBe(true)
    expect(document.querySelector('#provider-name-error')?.textContent).toContain('같은 이름의 AI 연결이 이미 있습니다.')

    ownerButtons[0].click()
    await flushPromises()
    expect(save.disabled).toBe(false)
    expect(document.querySelector('#provider-name-error')).toBeNull()

    ownerButtons[1].click()
    name.value = 'GPT-4o'
    name.dispatchEvent(new Event('input'))
    await flushPromises()
    save.click()
    await flushPromises()
    expect(saveMine).toHaveBeenCalledWith(expect.arrayContaining([expect.objectContaining({ name: 'GPT-4o' })]))
    wrapper.unmount()
  })

  it('keeps the edit dialog open and explains a server duplicate after save', async () => {
    vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-1', updated_at: null })
    vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: [], default_provider_id: null, updated_at: null })
    vi.spyOn(api, 'saveSystemAi')
      .mockRejectedValueOnce(
        new AiRequestError('입력이 올바르지 않습니다.', 'validation_failed', [
          { index: 0, field: 'name', reason: 'duplicate_name' },
        ]),
      )
      .mockResolvedValueOnce({ providers: systemAi, default_provider_id: 'sys-1', updated_at: null })
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    const wrapper = await mountPanel()
    await wrapper.findAll('.provider-card')[0].find('button[title="수정"]').trigger('click')
    const input = document.querySelector('#provider-name') as HTMLInputElement
    input.value = 'A new name'
    input.dispatchEvent(new Event('input'))
    await flushPromises()
    ;(document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).click()
    await flushPromises()
    expect(api.saveSystemAi).toHaveBeenCalled()
    expect(document.querySelector('#provider-name')).toBeTruthy()
    expect(document.querySelector('#provider-name-error')?.textContent).toContain('같은 이름의 AI 연결이 이미 있습니다.')
    expect(document.querySelector('#provider-name-error')?.getAttribute('role')).toBe('alert')
    expect(document.querySelector('#provider-name-error')?.textContent).toContain('다른 이름으로 바꿔 주세요.')

    input.value = 'Another new name'
    input.dispatchEvent(new Event('input'))
    await flushPromises()
    expect(document.querySelector('#provider-name-error')).toBeNull()
    ;(document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).click()
    await flushPromises()
    expect(api.saveSystemAi).toHaveBeenCalledTimes(2)
    expect(document.querySelector('#provider-name')).toBeNull()
    wrapper.unmount()
  })

  it('explains a duplicate connection name while allowing the unchanged name', async () => {
    const wrapper = mount(AiProviderEditDialog, {
      props: {
        open: true,
        provider: { ...systemAi[0], name: 'CodexGPT5.6Sol' },
        owner: 'system',
        existingProvidersByOwner: {
          system: [
            { ...systemAi[0], name: 'CodexGPT5.6Sol' },
            { ...systemAi[1], name: 'Codex GPT 5.6 Sol' },
          ],
          user: [],
        },
      },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })

    const input = document.querySelector('#provider-name') as HTMLInputElement
    const save = document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement
    expect(save.disabled).toBe(false)
    input.value = ' Codex GPT 5.6 Sol '
    input.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    expect(save.disabled).toBe(true)
    expect(document.querySelector('#provider-name-error')?.textContent).toContain('같은 이름의 AI 연결이 이미 있습니다.')
    expect(input.getAttribute('aria-describedby')).toBe('provider-name-error')

    input.value = 'Codex GPT 5.6 Sol 2'
    input.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    expect(save.disabled).toBe(false)
    expect(document.querySelector('#provider-name-error')).toBeNull()
    wrapper.unmount()
  })

  it('validates provider edit dialog fields', async () => {
    const wrapper = mount(AiProviderEditDialog, {
      props: {
        open: true,
        provider: null,
      },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })

    const saveBtn = document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement
    expect(saveBtn).toBeTruthy()
    expect(saveBtn.disabled).toBe(true)

    const nameInput = document.querySelector('#provider-name') as HTMLInputElement
    expect(nameInput).toBeTruthy()
    expect(document.querySelector('#provider-name-error')).toBeNull()
    nameInput.value = '   '
    nameInput.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    expect(saveBtn.disabled).toBe(true)
    expect(document.querySelector('#provider-name-error')?.textContent).toContain('AI 연결 이름을 입력하세요.')

    const commandInput = document.querySelector('#provider-command') as HTMLInputElement
    commandInput.value = 'claude -p'
    commandInput.dispatchEvent(new Event('input'))
    nameInput.value = 'My AI'
    nameInput.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    expect(saveBtn.disabled).toBe(false)
    expect(document.querySelector('#provider-name-error')).toBeNull()
    saveBtn.click()
    expect(wrapper.emitted('save')?.[0][0]).toMatchObject({ name: 'My AI', exec_type: 'cli' })

    nameInput.value = ' My AI '
    nameInput.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()
    saveBtn.click()
    expect(wrapper.emitted('save')?.[1][0]).toMatchObject({ name: 'My AI', exec_type: 'cli' })
    wrapper.unmount()
  })
})

// flowgate.v02.0088 F07/F08: the provider delete confirm names its actions in the current language and 취소 saves
// nothing; the generic kind Custom is a UI word while the kind sent stays 'custom'.
describe('AiProvidersPanel delete confirm and the Custom kind (0088 F07/F08)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setCurrentUser({ username: 'member', email: null, isAdmin: false })
    mockLists()
  })
  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
    i18n.global.locale.value = 'ko'
  })

  it.each([
    ['ko', '삭제', '취소'],
    ['ja', '削除', 'キャンセル'],
    ['en', 'Delete', 'Cancel'],
  ] as const)('the delete confirm says %s: %s / %s, and cancel saves nothing', async (locale, del, cancel) => {
    i18n.global.locale.value = locale
    const save = vi.spyOn(api, 'saveMyAi')
    const wrapper = await mountPanel()
    const trash = wrapper.findAll('.danger-icon')
    await trash[trash.length - 1].trigger('click')
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions .ui-btn')).map((b) => b.textContent?.trim())
    expect(buttons).toEqual([cancel, del])
    ;(document.querySelector('.ui-confirm-actions .ui-btn--secondary') as HTMLButtonElement).click()
    await flushPromises()
    expect(save).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('labels the Custom kind in the current language and keeps the product names and the custom value', async () => {
    i18n.global.locale.value = 'ja'
    const wrapper = mount(AiProviderEditDialog, { props: { open: true, provider: null }, global: { plugins: [i18n] }, attachTo: document.body })
    await flushPromises()
    const options = () => Array.from(document.querySelectorAll<HTMLOptionElement>('#provider-kind option')).map((o) => [o.value, o.textContent?.trim()])
    expect(options()).toContainEqual(['custom', 'カスタム'])
    expect(options()).toContainEqual(['claude', 'Claude (Anthropic)'])
    i18n.global.locale.value = 'ko'
    await flushPromises()
    expect(options()).toContainEqual(['custom', '사용자 정의'])
    wrapper.unmount()
  })
})
