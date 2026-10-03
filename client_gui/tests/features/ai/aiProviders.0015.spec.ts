import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiProviderEditDialog from '@/features/ai/providers/AiProviderEditDialog.vue'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import * as api from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// AI 목록 (flowgate.v02.0017 CH0006 (2)): system AI and my AI in one list. The 0015 project
// modes (inherit / custom / disabled) were retired with project-owned AI.

const systemAi: ProviderItem[] = [
  { id: 'sys-1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true },
  { id: 'sys-2', name: 'GPT-4o', exec_type: 'api', kind: 'openai', enabled: true, api_key_set: true },
]

const myAi: ProviderItem[] = [
  { id: 'my-1', name: 'My Gemini', exec_type: 'api', kind: 'gemini', enabled: true, api_key_set: false },
  { id: 'my-2', name: 'My Haiku', exec_type: 'cli', kind: 'claude', enabled: true },
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
    nameInput.value = 'New Test Provider'
    nameInput.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()

    expect(saveBtn.disabled).toBe(false)
    saveBtn.click()

    expect(wrapper.emitted('save')?.[0][0]).toMatchObject({
      name: 'New Test Provider',
      exec_type: 'cli',
    })
    wrapper.unmount()
  })
})
