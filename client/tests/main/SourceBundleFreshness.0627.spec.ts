import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { getRequest } from '@shared/api'
import GroupInfoModal from '@main/components/GroupInfoModal.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

vi.mock('@shared/api', () => ({
  default: { get: vi.fn(), post: vi.fn() }, getRequest: vi.fn(), postRequest: vi.fn(),
}))

let wrapper: ReturnType<typeof mount> | null = null
function bundle(id: string, freshness: string) {
  return {
    bundle_id: id, status: 'created', source_revision: 'abc', source_dirty: false,
    created_at: '2026-09-27', expires_at: '2026-09-28', file_count: 1, byte_size: 3,
    exclusion_policy_version: 'source-bundle-v1', content_fingerprint: 'contenthash',
    bundle_sha256: 'bundlehash', freshness, origin: 'automatic_source_access',
    failure_code: null, failure_reason: null, cleanup_state: 'active', deleted_at: null,
  }
}
function mountModal() {
  wrapper = mount(GroupInfoModal, {
    attachTo: document.body,
    props: { visible: false, projectId: 'flowgate', groupId: 'flowgate.default.0517',
      groupName: 'Test Group', documents: [] },
    global: { plugins: [i18n] },
  })
  return wrapper
}
beforeEach(() => {
  i18n.global.locale.value = 'ko'
  vi.mocked(getRequest).mockReset()
  document.body.innerHTML = '<div id="dialog-root"></div>'
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  resetDialogSystem()
  document.body.innerHTML = ''
})

describe('read-only Source Bundle freshness', () => {
  it('shows stale Bundle metadata without a legacy Snapshot warning', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { ok: true, bundles: [bundle('sb_stale', 'stale')] } } as any)
    const w = mountModal()
    await w.setProps({ visible: true })
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith('/api/v1/source-bundles', {
      project_id: 'flowgate', group_id: 'flowgate.default.0517',
    })
    expect(document.body.textContent).toContain('sb_stale')
    expect(document.body.textContent).toContain('stale')
    expect(document.body.querySelector('[data-test="snap-stale-warning"]')).toBeNull()
  })

  it('drops an earlier group response after changing groups', async () => {
    let resolveA: ((value: unknown) => void) | null = null
    const pendingA = new Promise((resolve) => { resolveA = resolve })
    vi.mocked(getRequest).mockImplementation((_url: string, params?: Record<string, unknown>) =>
      params?.group_id === 'flowgate.default.0900'
        ? Promise.resolve({ data: { ok: true, bundles: [bundle('sb_group_b', 'current')] } }) as any
        : pendingA as any)
    const w = mountModal()
    await w.setProps({ visible: true })
    await w.setProps({ groupId: 'flowgate.default.0900' })
    await flushPromises()
    resolveA!({ data: { ok: true, bundles: [bundle('sb_group_a', 'stale')] } })
    await flushPromises()
    expect(document.body.textContent).toContain('sb_group_b')
    expect(document.body.textContent).not.toContain('sb_group_a')
  })

  it('does not resurrect closed modal data from a late response', async () => {
    let resolve: ((value: unknown) => void) | null = null
    vi.mocked(getRequest).mockReturnValue(new Promise((done) => { resolve = done }) as any)
    const w = mountModal()
    await w.setProps({ visible: true })
    await w.setProps({ visible: false })
    resolve!({ data: { ok: true, bundles: [bundle('sb_late', 'stale')] } })
    await flushPromises()
    expect(document.body.textContent).not.toContain('sb_late')
  })
})
