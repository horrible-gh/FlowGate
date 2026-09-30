import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as sharedApi from '@shared/api'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import * as aiApi from '@/features/ai/api'
import AiStartDialog from '@/features/ai/AiStartDialog.vue'

registerAppMessages(i18n)

describe('v0.2 continuous Start dialog', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
  })

  it('loads a preset, permits a concrete provider override, and submits both kinds of IDs', async () => {
    vi.spyOn(aiApi, 'fetchProjectAiSettings').mockResolvedValue({
      mode: 'inherit', providers: null, default_provider_id: 'executor',
      effective: { source: 'system', default_provider_id: 'executor', registered_count: 3,
        providers: ['executor', 'override', 'reviewer', 'reworker'].map(id => ({ id, name: id, kind: 'cli', exec_type: 'cli' as const, enabled: true })) },
    })
    vi.spyOn(aiApi, 'listAiGroups').mockResolvedValue([{
      ai_group_id: 'aig_team', project_id: 'flowgate', name: 'Team', description: null,
      work_executor: { provider_id: 'executor', provider_name: 'executor', is_available: true },
      reviewer: { provider_id: 'reviewer', provider_name: 'reviewer', is_available: true },
      reworker: { provider_id: 'reworker', provider_name: 'reworker', is_available: true },
    }, {
      ai_group_id: 'aig_broken', project_id: 'flowgate', name: 'Broken', description: null,
      work_executor: { provider_id: 'executor', provider_name: 'executor', is_available: true },
      reviewer: { provider_id: 'missing', provider_name: 'missing', is_available: false },
      reworker: null,
    }])
    const steps = vi.spyOn(aiApi, 'listSequenceSteps').mockResolvedValue([
      { item_seq: 1, type: 'W', status: 'done' }, { item_seq: 2, type: 'W', status: 'pending' },
      { item_seq: 3, type: 'W', status: 'pending' }, { item_seq: 4, type: 'W', status: 'pending' },
    ])
    const post = vi.spyOn(sharedApi, 'postRequest').mockResolvedValue({ data: { run_id: 'run-1' } } as never)
    const wrapper = mount(AiStartDialog, {
      attachTo: document.body,
      props: { open: true, project: { id: 'flowgate', name: 'FlowGate', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [{ name: 'v02', title: 'v0.2' }] } },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    const selects = document.body.querySelectorAll<HTMLSelectElement>('.start-body select')
    const brokenOption = Array.from(selects[3].options).find(option => option.value === 'aig_broken')
    expect(brokenOption?.disabled).toBe(true)
    expect(brokenOption?.textContent).toContain('사용할 수 없는 그룹')
    selects[3].value = 'aig_team'
    selects[3].dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()
    expect(selects[4].value).toBe('executor')
    selects[4].value = 'override'
    selects[4].dispatchEvent(new Event('change', { bubbles: true }))
    const inputs = document.body.querySelectorAll<HTMLInputElement>('.start-body input')
    inputs[0].value = '0015'
    inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
    inputs[1].value = 'flowgate.v02.0015.0001-R'
    inputs[1].dispatchEvent(new Event('input', { bubbles: true }))
    inputs[2].value = '3'
    inputs[2].dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    expect(document.body.querySelector('.provider-grid input')).toBeNull()
    const start = Array.from(document.body.querySelectorAll<HTMLButtonElement>('.start-footer button')).find(button => button.textContent?.includes('시작'))!
    expect(start.disabled).toBe(false)
    start.click()
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/api/v1/ai-invoke/start', expect.objectContaining({
      requested_ai_group_id: 'aig_team', provider_id: 'override',
      continuation_reworker_provider_id: 'reworker',
      continuation_reviewer_overrides: { 2: 'reviewer', 3: 'reviewer' },
      continuation_review_mode: false,
    }))
    expect(steps).toHaveBeenCalledWith('flowgate.v02.0015.0001-R')
    expect(document.body.textContent).toContain('run-1')
    wrapper.unmount()
  })
})
