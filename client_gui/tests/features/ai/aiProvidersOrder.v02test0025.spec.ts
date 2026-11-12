import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import * as api from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// flowgate.v02test.0025 (3): system AI and my AI, local and remote, in ONE order of mine. Any card can go above any
// other; an administrator's change of the system AI's relative order is also the fallback order (linked).
const systemAi: ProviderItem[] = [
  { id: 'sys-local', name: 'System local', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude -p', exec_location: 'local' },
  { id: 'sys-remote', name: 'System remote', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'remote' },
]
const myAi: ProviderItem[] = [
  { id: 'my-remote', name: 'My agent', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'remote', target_agent_id: 'agt' },
  { id: 'my-local', name: 'My local', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'local' },
]

const names = (wrapper: ReturnType<typeof mount>) => wrapper.findAll('.provider-card').map((card) => card.find('.name-title-row b').text())

async function mountPanel(order: string[] = [], admin = false) {
  setCurrentUser({ username: admin ? 'admin' : 'member', email: null, isAdmin: admin })
  if (admin) vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-local', updated_at: null })
  else vi.spyOn(api, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-local' })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: myAi, default_provider_id: null, updated_at: null })
  vi.spyOn(api, 'fetchMyAiOrder').mockResolvedValue({ order })
  const wrapper = mount(AiProvidersPanel, { global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

describe('v02test.0025 AI 목록 in my own order', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
  })
  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it('shows my saved order, mixing owners and modes, with one rank sequence', async () => {
    const wrapper = await mountPanel(['my-remote', 'sys-remote', 'my-local', 'sys-local'])
    expect(names(wrapper)).toEqual(['My agent', 'System remote', 'My local', 'System local'])
    expect(wrapper.findAll('.provider-card .rank').map((r) => r.text())).toEqual(['1', '2', '3', '4'])
    wrapper.unmount()
  })

  it('drops my remote agent AI above a system local AI', async () => {
    const save = vi.spyOn(api, 'saveMyAiOrder').mockImplementation(async (order: string[]) => ({ order, system_order_changed: false }))
    const wrapper = await mountPanel()
    const cards = wrapper.findAll('.provider-card')
    await cards[2].trigger('dragstart')
    await cards[0].trigger('dragover')
    await cards[0].trigger('drop')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(['my-remote', 'sys-local', 'sys-remote', 'my-local'])
    expect(names(wrapper)).toEqual(['My agent', 'System local', 'System remote', 'My local'])
    wrapper.unmount()
  })

  it('an owner filter still moves within my whole order', async () => {
    const save = vi.spyOn(api, 'saveMyAiOrder').mockImplementation(async (order: string[]) => ({ order, system_order_changed: false }))
    const wrapper = await mountPanel(['sys-local', 'my-remote', 'sys-remote', 'my-local'])
    await wrapper.findAll('.segmented button')[2].trigger('click') // 내 AI only
    expect(names(wrapper)).toEqual(['My agent', 'My local'])
    await wrapper.findAll('button.grip')[1].trigger('keydown', { key: 'ArrowUp' })
    await flushPromises()
    expect(save).toHaveBeenCalledWith(['sys-local', 'my-local', 'my-remote', 'sys-remote'])
    wrapper.unmount()
  })

  it('a failed save puts the order back and says why', async () => {
    vi.spyOn(api, 'saveMyAiOrder').mockRejectedValue(new Error('AI 순서를 저장하지 못했습니다.'))
    const wrapper = await mountPanel()
    await wrapper.findAll('button.grip')[3].trigger('keydown', { key: 'ArrowUp' })
    await flushPromises()
    expect(names(wrapper)).toEqual(['System local', 'System remote', 'My agent', 'My local'])
    expect(document.body.textContent).toContain('AI 순서를 저장하지 못했습니다.')
    wrapper.unmount()
  })

  it('an administrator is told when the fallback order moved with the drag, and the system list is read again', async () => {
    const save = vi.spyOn(api, 'saveMyAiOrder').mockResolvedValue({ order: ['sys-remote', 'sys-local', 'my-remote', 'my-local'], system_order_changed: true })
    const wrapper = await mountPanel([], true)
    expect(wrapper.find('[data-test="ai-order-hint"]').text()).toContain('폴백 순서')
    await wrapper.findAll('button.grip')[1].trigger('keydown', { key: 'ArrowUp' })
    await flushPromises()
    expect(save).toHaveBeenCalledWith(['sys-remote', 'sys-local', 'my-remote', 'my-local'])
    expect(api.fetchSystemAi).toHaveBeenCalledTimes(2)
    expect(document.body.textContent).toContain('시스템 폴백 순서도 함께 변경되었습니다.')
    wrapper.unmount()
  })

  it('no notice when the fallback order did not change', async () => {
    vi.spyOn(api, 'saveMyAiOrder').mockImplementation(async (order: string[]) => ({ order, system_order_changed: false }))
    const wrapper = await mountPanel([], true)
    await wrapper.findAll('button.grip')[3].trigger('keydown', { key: 'ArrowUp' })
    await flushPromises()
    expect(document.body.textContent).not.toContain('시스템 폴백 순서도 함께 변경되었습니다.')
    expect(api.fetchSystemAi).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
