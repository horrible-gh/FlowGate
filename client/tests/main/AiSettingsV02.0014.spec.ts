import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import { buildAiInvokeStartBody } from '@main/composables/useAiInvokeStarter'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  getRequest, postRequest: vi.fn(), putRequest: vi.fn(), patchRequest: vi.fn(),
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

const sequence = { data: {
  doc_id: 'flowgate.v02.0014.0001-R', decided: true,
  items: [
    { id: 1, item_seq: 1, type: 'R', label: 'Requirement', status: 'done' },
    { id: 2, item_seq: 2, type: 'TR', label: 'Report', status: 'pending' },
  ],
  head: { id: 2, item_seq: 2, type: 'TR', label: 'Report', status: 'pending' },
} }

describe('AI Group Start preset', () => {
  it('prefills concrete Provider and sends Group provenance while preserving an override', async () => {
    getRequest.mockImplementation((path: string) => path.includes('/ai-groups') ? { data: { items: [{
      ai_group_id: 'aig_1', name: 'Preset',
      work_executor: { provider_id: 'p1', is_available: true },
      reviewer: { provider_id: 'p2', is_available: true }, reworker: null,
    }] } } : sequence)
    const wrapper = mount(ContinuousWorkDialog, { props: {
      visible: true, projectId: 'project', docRef: 'flowgate.v02.0014.0001-R',
      selectedProvider: 'p2', providers: [{ id: 'p1', name: 'One' }, { id: 'p2', name: 'Two' }],
    }, global: { plugins: [i18n] } })
    await flushPromises()
    await wrapper.findAll('.cwd-tab').find(button => button.text() === i18n.global.t('main.continuous_work.tab_provider'))!.trigger('click')
    await wrapper.get('[data-test="start-ai-group"]').setValue('aig_1')
    expect(wrapper.emitted('update:provider')?.at(-1)?.[0]).toBe('p1')
    await wrapper.find('[data-dialog-action-role="primary"]').trigger('click')
    const payload = wrapper.emitted('confirm')?.at(-1)?.[0] as any
    expect(payload.requestedAiGroupId).toBe('aig_1')
    const body = buildAiInvokeStartBody({ project: 'project', group: '0014', docRef: 'flowgate.v02.0014.0001-R',
      actionScope: 'new', mode: 'continuous', requestedAiGroupId: payload.requestedAiGroupId,
      providerOverrides: payload.providerOverrides }, { selectedProviderId: 'p2', pinned: false })
    expect(body.requested_ai_group_id).toBe('aig_1')
    expect(body.provider_id).toBe('p2')
  })
})
