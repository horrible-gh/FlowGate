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

// v02test.0080 제목 짓기 AI (system setting ai_title_provider). v02test.0096 F96-3: in the 기타 설정 tab, the system column
// of the 제목 짓기 AI row under the 최대 반복 횟수 row.
const systemAi: ProviderItem[] = [
  { id: 'sys-a', name: 'System A', exec_type: 'api', kind: 'claude', enabled: true, exec_location: 'local' },
  { id: 'sys-off', name: 'System off', exec_type: 'cli', kind: 'claude', enabled: false, exec_location: 'local' },
  { id: 'sys-b', name: 'System B', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'remote' },
]
const myAi: ProviderItem[] = [{ id: 'my-a', name: 'My A', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'local' }]
const policy = { repeat_count_min: 1, repeat_count_max: 5, repeat_count_hard_max: 30 }
// v02test.0089: a user's own values (/me/ai-preferences) show in the 내 값 column beside it.
const prefs = {
  title_provider: 'auto', title_provider_effective: 'executor',
  repeat_count_max: null, repeat_count_max_effective: 5, repeat_count_system_max: 5, repeat_count_min: 1,
}

async function mountPanel({ admin = true, value = 'executor' as string | null, order = [] as string[] } = {}) {
  setCurrentUser({ username: admin ? 'admin' : 'member', email: null, isAdmin: admin })
  if (admin) vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a', updated_at: null, execution_policy: policy } as never)
  else vi.spyOn(api, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a' })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: myAi, default_provider_id: null, updated_at: null })
  vi.spyOn(api, 'fetchMyAiOrder').mockResolvedValue({ order })
  const fetchTitle = vi.spyOn(api, 'fetchTitleProvider').mockResolvedValue(value)
  vi.spyOn(api, 'fetchMyAiPreferences').mockResolvedValue({ ...prefs })
  const wrapper = mount(AiOtherSettingsPanel, { global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return { wrapper, fetchTitle }
}

const optionTexts = (wrapper: ReturnType<typeof mount>) =>
  wrapper.findAll('[data-test="title-ai-select"] option').map((o) => o.text())

describe('v02test.0080 제목 짓기 AI dropdown', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
  })
  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it('sits in the system column of the title row, under the repeat row, with the dropdown only', async () => {
    const { wrapper } = await mountPanel()
    const rows = wrapper.findAll('[role="row"]').filter((row) => row.find('[role="rowheader"]').exists())
    expect(rows.map((row) => row.attributes('data-test'))).toEqual(['repeat-row', 'title-row'])
    const card = rows[1].get('[data-test="title-ai-card"]')
    expect(rows[0].find('[data-test="system-repeat-card"]').exists()).toBe(true)
    expect(card.find('small').exists()).toBe(false)
    expect(card.get('select').attributes('aria-label')).toBe('제목 짓기 AI (시스템)')
    expect((card.get('select').element as HTMLSelectElement).value).toBe('executor')
    wrapper.unmount()
  })

  it('lists 끄기, 실행한 AI 그대로, a separator, then the enabled AI in the list order', async () => {
    const { wrapper } = await mountPanel({ order: ['my-a', 'sys-b', 'sys-a'] })
    expect(optionTexts(wrapper)).toEqual(['끄기', '실행한 AI 그대로', '──────────', 'My A', 'System B', 'System A'])
    const options = wrapper.findAll('[data-test="title-ai-select"] option')
    expect((options[2].element as HTMLOptionElement).disabled).toBe(true)
    expect(options.map((o) => (o.element as HTMLOptionElement).value).slice(0, 2)).toEqual(['off', 'executor'])
    wrapper.unmount()
  })

  it('keeps a stored AI that is off or no longer listed selectable as the current value', async () => {
    let { wrapper } = await mountPanel({ value: 'sys-off' })
    expect(optionTexts(wrapper)).toContain('System off')
    expect((wrapper.get('[data-test="title-ai-select"]').element as HTMLSelectElement).value).toBe('sys-off')
    wrapper.unmount()
    ;({ wrapper } = await mountPanel({ value: 'gone-id' }))
    expect((wrapper.get('[data-test="title-ai-select"]').element as HTMLSelectElement).value).toBe('gone-id')
    wrapper.unmount()
  })

  it('saves the choice at once', async () => {
    const save = vi.spyOn(api, 'saveTitleProvider').mockResolvedValue()
    const { wrapper } = await mountPanel({ value: 'off' })
    await wrapper.get('[data-test="title-ai-select"]').setValue('sys-b')
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    expect(save).toHaveBeenCalledWith('sys-b')
    expect((wrapper.get('[data-test="title-ai-select"]').element as HTMLSelectElement).value).toBe('sys-b')
    wrapper.unmount()
  })

  it('puts the previous value back when the save fails', async () => {
    vi.spyOn(api, 'saveTitleProvider').mockRejectedValue(new Error('저장 실패'))
    const { wrapper } = await mountPanel({ value: 'executor' })
    await wrapper.get('[data-test="title-ai-select"]').setValue('off')
    await flushPromises()
    expect((wrapper.get('[data-test="title-ai-select"]').element as HTMLSelectElement).value).toBe('executor')
    expect(document.body.textContent).toContain('저장 실패')
    wrapper.unmount()
  })

  it('is not shown to a non-administrator or while the value is unknown', async () => {
    // A non-administrator sees the system column read-only and their own title AI beside it (v02test.0089).
    let r = await mountPanel({ admin: false })
    expect(r.wrapper.find('[data-test="title-ai-card"]').exists()).toBe(false)
    expect(r.wrapper.find('[data-test="system-title-readonly"]').exists()).toBe(true)
    expect(r.wrapper.find('[data-test="my-title-ai-card"]').exists()).toBe(true)
    expect(r.fetchTitle).not.toHaveBeenCalled()
    r.wrapper.unmount()
    r = await mountPanel({ value: null })
    expect(r.wrapper.find('[data-test="title-ai-card"]').exists()).toBe(false)
    expect(r.wrapper.find('[data-test="system-repeat-card"]').exists()).toBe(true)
    r.wrapper.unmount()
  })

  it('shows the names in English and Japanese', async () => {
    i18n.global.locale.value = 'en'
    let { wrapper } = await mountPanel()
    expect(wrapper.get('[data-test="title-ai-select"]').attributes('aria-label')).toBe('Title AI (system)')
    expect(optionTexts(wrapper).slice(0, 2)).toEqual(['Off', 'Same AI that ran it'])
    wrapper.unmount()
    i18n.global.locale.value = 'ja'
    ;({ wrapper } = await mountPanel())
    expect(wrapper.get('[data-test="title-ai-select"]').attributes('aria-label')).toBe('タイトル生成AI（システム）')
    expect(optionTexts(wrapper).slice(0, 2)).toEqual(['オフ', '実行したAIのまま'])
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

