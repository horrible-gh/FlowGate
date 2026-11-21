import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import * as api from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// The Designer's "내 AI" window (mine-only): my own AI and nothing else, for an administrator too.
const systemAi: ProviderItem[] = [
  { id: 'sys-local', name: 'System local', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude -p', exec_location: 'local' },
  { id: 'sys-remote', name: 'System remote', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'remote' },
]
const myAi: ProviderItem[] = [
  { id: 'my-remote', name: 'My agent', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'remote', target_agent_id: 'agt' },
  { id: 'my-local', name: 'My local', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'local' },
]

const names = (wrapper: ReturnType<typeof mount>) => wrapper.findAll('.provider-card').map((card) => card.find('.name-title-row b').text())

async function mountPanel({ order = [] as string[], admin = false, mine = myAi } = {}) {
  setCurrentUser({ username: admin ? 'admin' : 'member', email: null, isAdmin: admin })
  if (admin) vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-local', updated_at: null })
  else vi.spyOn(api, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-local' })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: mine, default_provider_id: null, updated_at: null })
  vi.spyOn(api, 'fetchMyAiOrder').mockResolvedValue({ order })
  const wrapper = mount(AiProvidersPanel, { props: { mineOnly: true }, global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

describe('v02test.0055 내 AI window list (mine-only)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
  })
  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it('shows only my AI in my order, numbered among themselves, with no owner filter', async () => {
    const wrapper = await mountPanel({ order: ['sys-local', 'my-local', 'sys-remote', 'my-remote'] })
    expect(names(wrapper)).toEqual(['My local', 'My agent'])
    expect(wrapper.findAll('.provider-card .rank').map((r) => r.text())).toEqual(['1', '2'])
    expect(wrapper.find('.segmented').exists()).toBe(false)
    expect(wrapper.get('.count-hint').text()).not.toContain('시스템')
    wrapper.unmount()
  })

  it('hides system AI from an administrator too and keeps the plain order hint', async () => {
    const wrapper = await mountPanel({ admin: true })
    expect(names(wrapper)).toEqual(['My agent', 'My local'])
    expect(wrapper.find('.mode-icon.owner.system').exists()).toBe(false)
    expect(wrapper.get('[data-test="ai-order-hint"]').text()).not.toContain('폴백 순서')
    wrapper.unmount()
  })

  it('says so when I have no AI of my own, instead of showing system AI', async () => {
    const wrapper = await mountPanel({ mine: [] })
    expect(wrapper.findAll('.provider-card')).toHaveLength(0)
    expect(wrapper.get('.empty-row').text()).toBe(i18n.global.t('gui.ai.owned.empty_mine'))
    wrapper.unmount()
  })

  it('moving my AI keeps the system AI where they are in my whole order', async () => {
    const save = vi.spyOn(api, 'saveMyAiOrder').mockImplementation(async (order: string[]) => ({ order, system_order_changed: false }))
    const wrapper = await mountPanel({ order: ['sys-local', 'my-remote', 'sys-remote', 'my-local'] })
    await wrapper.findAll('button.grip')[1].trigger('keydown', { key: 'ArrowUp' })
    await flushPromises()
    expect(save).toHaveBeenCalledWith(['sys-local', 'my-local', 'my-remote', 'sys-remote'])
    expect(names(wrapper)).toEqual(['My local', 'My agent'])
    wrapper.unmount()
  })

  it('an administrator adding from here can only add my AI', async () => {
    const wrapper = await mountPanel({ admin: true })
    await wrapper.get('.detail-actions button').trigger('click')
    await flushPromises()
    const dialog = wrapper.findComponent({ name: 'AiProviderEditDialog' })
    expect(dialog.props('ownerChoices')).toEqual(['user'])
    expect(dialog.props('owner')).toBe('user')
    wrapper.unmount()
  })
})
