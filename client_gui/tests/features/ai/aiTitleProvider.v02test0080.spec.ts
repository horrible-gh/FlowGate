import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import * as transport from '@shared/api'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiOtherSettingsPanel from '@/features/ai/manage/AiOtherSettingsPanel.vue'
import * as api from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// v02test.0080 제목 짓기 AI (system setting ai_title_provider). v02test.0096 F96-6: the system value left AI 관리 ›
// 기타 설정 for 환경 설정 (SystemGeneralSettingsView, administrators); its screen checks live in
// tests/features/settings/systemSettings.0076.spec.ts. Here: 기타 설정 no longer shows or reads it, and the contract.
const systemAi: ProviderItem[] = [
  { id: 'sys-a', name: 'System A', exec_type: 'api', kind: 'claude', enabled: true, exec_location: 'local' },
]
const policy = { repeat_count_min: 1, repeat_count_max: 5, repeat_count_hard_max: 30 }
const prefs = {
  title_provider: 'auto', title_provider_effective: 'executor',
  repeat_count_max: null, repeat_count_max_effective: 5, repeat_count_system_max: 5, repeat_count_min: 1,
}

async function mountPanel(admin: boolean) {
  setCurrentUser({ username: admin ? 'admin' : 'member', email: null, isAdmin: admin })
  if (admin) vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a', updated_at: null, execution_policy: policy } as never)
  else vi.spyOn(api, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a' })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: [], default_provider_id: null, updated_at: null })
  vi.spyOn(api, 'fetchMyAiOrder').mockResolvedValue({ order: [] })
  const fetchTitle = vi.spyOn(api, 'fetchTitleProvider').mockResolvedValue('executor')
  vi.spyOn(api, 'fetchMyAiPreferences').mockResolvedValue({ ...prefs })
  const wrapper = mount(AiOtherSettingsPanel, { global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return { wrapper, fetchTitle }
}

describe('v02test.0080 제목 짓기 AI — not in AI 관리 › 기타 설정 since v02test.0096 F96-6', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
  })
  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it.each([true, false])('admin=%s: only my own title AI select, the system value is neither shown nor read', async (admin) => {
    const { wrapper, fetchTitle } = await mountPanel(admin)
    expect(wrapper.findAll('select')).toHaveLength(1)
    expect(wrapper.find('[data-test="my-title-ai-select"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="title-ai-select"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="title-ai-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="system-title-readonly"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('시스템')
    expect(fetchTitle).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

describe('v02test.0080 ai_title_provider contract', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('reads only the ai_title_provider row of GET /system/settings', async () => {
    const get = vi.spyOn(transport, 'getRequest').mockResolvedValue({
      data: { settings: [
        { setting_key: 'ai_repeat_count_max', setting_value: '5', value_type: 'integer' },
        { setting_key: 'ai_title_provider', setting_value: 'sys-a', value_type: 'string' },
      ] },
    } as never)
    await expect(api.fetchTitleProvider()).resolves.toBe('sys-a')
    expect(get).toHaveBeenCalledWith('/api/v1/system/settings')
    get.mockResolvedValue({ data: { settings: [] } } as never)
    await expect(api.fetchTitleProvider()).resolves.toBeNull()
  })

  it('saves the value alone on PATCH /system/settings', async () => {
    const patch = vi.spyOn(transport, 'patchRequest').mockResolvedValue({ data: {} } as never)
    await api.saveTitleProvider('executor')
    expect(patch).toHaveBeenCalledWith('/api/v1/system/settings', { updates: { ai_title_provider: 'executor' } })
  })
})
