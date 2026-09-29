import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiEvaluationPanel from '../../src/settings/components/AiEvaluationPanel.vue'
import AiGroupsPanel from '../../src/settings/components/AiGroupsPanel.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const { getRequest, postRequest, putRequest, deleteRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), deleteRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest, deleteRequest }))
vi.mock('@main/composables/useDialogStack', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@main/composables/useDialogStack')>()),
  confirm: vi.fn(async () => true),
}))

afterEach(() => {
  vi.clearAllMocks()
  resetDialogSystem()
  document.body.innerHTML = ''
})

describe('AI Evaluation settings', () => {
  it('ignores a late Provider response after selection changes', async () => {
    let releaseOld!: (value: any) => void
    const old = new Promise(resolve => { releaseOld = resolve })
    getRequest.mockImplementation((url: string) => {
      if (url.includes('provider_id=p1')) return old
      if (url.includes('/current')) return Promise.resolve({ data: { current: {
        evaluation_id: 'new', project_id: 'project', provider_id: 'p2', lifecycle: 'pending', verdict: null,
      }, latest_history: null } })
      return Promise.resolve({ data: { items: [], next_cursor: null } })
    })
    const wrapper = mount(AiEvaluationPanel, { props: { projectId: 'project', providers: [
      { id: 'p1', name: 'One' }, { id: 'p2', name: 'Two' },
    ] }, global: { plugins: [i18n] } })
    await wrapper.get('#evaluation-provider').setValue('p2')
    await flushPromises()
    releaseOld({ data: { current: { evaluation_id: 'old', lifecycle: 'failed', verdict: null }, latest_history: null,
      items: [], next_cursor: null } })
    await flushPromises()
    expect(wrapper.text()).toContain(i18n.global.t('settings.project.ai_v02.status_pending'))
    expect(wrapper.text()).not.toContain(i18n.global.t('settings.project.ai_v02.status_failed'))
    wrapper.unmount()
  })

  it('adopts the evaluation_id in duplicate 409', async () => {
    getRequest.mockImplementation((url: string) => url.includes('/current')
      ? Promise.resolve({ data: { current: null, latest_history: null } })
      : url.includes('/eval1')
        ? Promise.resolve({ data: { evaluation_id: 'eval1', project_id: 'project', provider_id: 'p1', lifecycle: 'running', verdict: null } })
        : Promise.resolve({ data: { items: [], next_cursor: null } }))
    postRequest.mockRejectedValue({ response: { status: 409, data: { error: { code: 'evaluation_already_running', evaluation_id: 'eval1' } } } })
    const wrapper = mount(AiEvaluationPanel, { props: { projectId: 'project', providers: [{ id: 'p1', name: 'One' }] }, global: { plugins: [i18n] } })
    await flushPromises()
    await wrapper.find('.ai-evaluation-actions button').trigger('click')
    await flushPromises()
    expect(getRequest.mock.calls.some(([url]) => String(url).includes('/eval1'))).toBe(true)
    wrapper.unmount()
  })
})

describe('AI Group settings', () => {
  it('shows broken roles and saves in the group modal', async () => {
    getRequest.mockResolvedValue({ data: { items: [{ ai_group_id: 'g1', name: 'Broken', description: null,
      work_executor: { provider_id: 'gone', provider_name: null, is_available: false }, reviewer: null, reworker: null,
    }] } })
    putRequest.mockResolvedValue({ data: {} })
    const wrapper = mount(AiGroupsPanel, { props: { projectId: 'project', providers: [{ id: 'p1', name: 'One' }] }, global: { plugins: [i18n] } })
    await flushPromises()
    expect(wrapper.text()).toContain('gone')
    expect(wrapper.text()).toContain(i18n.global.t('settings.project.ai_v02.unavailable'))
    await wrapper.find('.ai-group-actions button').trigger('click')
    await flushPromises()
    // DialogShell teleports to the dialog host, so the open dialog is read from the document.
    const dialog = document.body.querySelector('.fg-dialog-surface[role="dialog"]')
    expect(dialog).not.toBeNull()
    const select = dialog!.querySelector('select') as HTMLSelectElement
    select.value = 'p1'
    select.dispatchEvent(new Event('change'))
    await flushPromises()
    document.body.querySelector<HTMLButtonElement>('[data-dialog-action-id="save"]')!.click()
    await flushPromises()
    expect(putRequest).toHaveBeenCalledWith('/api/v1/projects/project/ai-groups/g1', expect.objectContaining({ work_executor_provider_id: 'p1' }))
    wrapper.unmount()
  })
})
