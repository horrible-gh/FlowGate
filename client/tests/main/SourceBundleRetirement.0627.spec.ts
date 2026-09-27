import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { getRequest, postRequest } from '@shared/api'
import NotificationCenter from '@main/components/NotificationCenter.vue'
import GroupInfoModal from '@main/components/GroupInfoModal.vue'
import { useProjectStore } from '@main/stores/project'
import { useNotificationsStore } from '@main/stores/notifications'
import { resetDialogSystem } from '@main/composables/useDialogStack'

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), serverLogout: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), currentRoute: { value: { path: '/' } } }),
}))
vi.mock('@main/composables/useDashboardNavigation', () => ({
  useDashboardNavigation: () => ({ openDashboardTarget: vi.fn() }),
}))

const mounted: { unmount: () => void }[] = []

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  vi.mocked(getRequest).mockReset()
  vi.mocked(postRequest).mockReset()
  document.body.innerHTML = '<div id="dialog-root"></div>'
})
afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
  resetDialogSystem()
  document.body.innerHTML = ''
})

describe('Source Bundle retirement UI', () => {
  it('keeps the bell free of Snapshot pending actions and ignores the legacy refresh signal', async () => {
    const notifications = useNotificationsStore()
    vi.spyOn(notifications, 'fetchFeed').mockResolvedValue()
    vi.spyOn(notifications, 'markSeen').mockResolvedValue()
    const wrapper = mount(NotificationCenter, { attachTo: document.body, global: { plugins: [i18n] } })
    mounted.push(wrapper)
    await wrapper.get('.notif-bell').trigger('click')
    window.dispatchEvent(new CustomEvent('fg:snapshot_refresh'))
    await flushPromises()
    expect(wrapper.find('[data-test="notif-section-snapshot"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="snap-pending-item"]').exists()).toBe(false)
    expect(document.body.querySelector('.snap-approval-dialog, .snap-reject-dialog')).toBeNull()
    expect(vi.mocked(getRequest).mock.calls.some(([url]) => String(url).includes('/snapshots/pending'))).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('shows Bundle metadata read-only without approving or notifying', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, bundles: [{
      bundle_id: 'sb_observed', status: 'created', source_revision: 'abc123',
      source_dirty: true, created_at: '2026-09-27', expires_at: '2026-09-28',
      file_count: 3, byte_size: 128, exclusion_policy_version: 'source-bundle-v1',
      content_fingerprint: 'contenthash', bundle_sha256: 'bundlehash',
      freshness: 'current', origin: 'automatic_source_access',
      failure_code: null, failure_reason: null, cleanup_state: 'active', deleted_at: null,
    }] } } as any)
    const wrapper = mount(GroupInfoModal, {
      attachTo: document.body, global: { plugins: [i18n] },
      props: { visible: true, projectId: 'flowgate', groupId: 'flowgate.default.0627', groupName: '0627', documents: [] },
    })
    mounted.push(wrapper)
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith('/api/v1/source-bundles', {
      project_id: 'flowgate', group_id: 'flowgate.default.0627',
    })
    const text = document.body.textContent ?? ''
    for (const value of ['sb_observed', 'abc123', 'contenthash', 'bundlehash', 'current', 'automatic_source_access']) {
      expect(text).toContain(value)
    }
    expect(wrapper.find('[data-test="source-bundle-observability"] button').exists()).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
  })
})
