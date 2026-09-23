// flowgate.default.0594 T0016 §8.1 C1-C7 — Branch Control Center frontend regression.
// GitBranchManager is the single owner of branch create/merge/delete/current-target;
// none of this was covered by a dedicated spec before this change (TR0015's own gap).
import { config, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitBranchManager from '@main/components/GitBranchManager.vue'

const { getRequest, postRequest, putRequest, deleteRequest, dialogConfirm, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  deleteRequest: vi.fn(),
  dialogConfirm: vi.fn(() => Promise.resolve(true)),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest,
  postRequest,
  putRequest,
  deleteRequest,
}))

vi.mock('@main/composables/useDialogStack', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  confirm: dialogConfirm,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const originalGlobalStubs = { ...config.global.stubs }

const CATALOG = {
  ok: true,
  base_branch: 'main',
  default_merge_target: 'flowgate-v0.2',
  branches: [
    { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    { name: 'flowgate-v0.2', kind: 'local', can_delete: true, can_be_create_source: true },
    { name: 'flowgate_default_0599', kind: 'internal_slot', can_delete: false, can_be_create_source: false, connected_group_id: 'flowgate.default.0599' },
    { name: 'stale-feature', kind: 'local', can_delete: true, can_be_create_source: true, has_remote_counterpart: true },
    { name: 'old-remote', kind: 'remote_only', can_delete: false, delete_blocked_reason: 'remote_only', can_be_create_source: false },
  ],
}

function mountManager() {
  return mount(GitBranchManager, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true } },
  })
}

beforeEach(() => {
  config.global.stubs = { ...originalGlobalStubs, teleport: true }
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  deleteRequest.mockReset()
  dialogConfirm.mockReset()
  dialogConfirm.mockResolvedValue(true)
  showToast.mockReset()
  getRequest.mockResolvedValue({ data: CATALOG })
})

afterEach(() => {
  config.global.stubs = { ...originalGlobalStubs }
})

describe('GitBranchManager (T0016 C1-C7)', () => {
  it('C1. renders the branch list, the current merge target and the base branch', async () => {
    const wrapper = mountManager()
    await flushPromises()

    expect(wrapper.get('[data-test="current-target-value"]').text()).toBe('flowgate-v0.2')
    expect(wrapper.text()).toContain('main')
    const rows = wrapper.findAll('.branch-row')
    expect(rows.map((r) => r.text()).some((t) => t.includes('stale-feature'))).toBe(true)
    expect(rows.map((r) => r.text()).some((t) => t.includes('flowgate_default_0599'))).toBe(true)
    // §3.1 "필요 시 remote tracking 상태" — surfaced only for the branch that has one.
    const staleRow = rows.find((r) => r.text().includes('stale-feature'))!
    expect(staleRow.text()).toContain(i18n.global.t('main.git_branch_manager.has_remote_badge'))
    const targetRow = rows.find((r) => r.text().includes('flowgate-v0.2'))!
    expect(targetRow.text()).not.toContain(i18n.global.t('main.git_branch_manager.has_remote_badge'))
    wrapper.unmount()
  })

  it('C2. setting a branch as the target persists it and it is reflected back after reload', async () => {
    const wrapper = mountManager()
    await flushPromises()

    putRequest.mockResolvedValueOnce({ data: { ok: true, default_merge_target: 'stale-feature' } })
    getRequest.mockResolvedValueOnce({ data: { ...CATALOG, default_merge_target: 'stale-feature' } })

    const row = wrapper.findAll('.branch-row').find((r) => r.text().includes('stale-feature'))!
    await row.find('button.btn-secondary').trigger('click')
    await flushPromises()

    expect(putRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/default-target',
      { branch: 'stale-feature' },
    )
    expect(wrapper.get('[data-test="current-target-value"]').text()).toBe('stale-feature')
    expect(showToast).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('C3. branch merge: confirms, then calls the merge API with the selected source/target and refreshes', async () => {
    const wrapper = mountManager()
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: { ok: true, source_branch: 'stale-feature', target_branch: 'flowgate-v0.2', pushed: true } })

    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'flowgate-v0.2'
    await flushPromises()
    expect(wrapper.get('[data-test="merge-summary"]').text()).toContain('stale-feature')
    expect(wrapper.get('[data-test="merge-summary"]').text()).toContain('flowgate-v0.2')

    await wrapper.find('.branch-action-card:nth-of-type(2)').trigger('submit')
    await flushPromises()

    expect(dialogConfirm).toHaveBeenCalled()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'stale-feature', target_branch: 'flowgate-v0.2' },
    )
    // refresh after success
    expect(getRequest).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('C3b. a branch_merge_conflict response is shown without throwing out of the component', async () => {
    const wrapper = mountManager()
    await flushPromises()
    postRequest.mockRejectedValueOnce({
      response: { data: { error: { code: 'branch_merge_conflict', details: { conflict_files: ['a.txt'] } } } },
    })
    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'flowgate-v0.2'
    await flushPromises()
    await wrapper.find('.branch-action-card:nth-of-type(2)').trigger('submit')
    await flushPromises()

    const result = wrapper.find('.branch-result')
    expect(result.exists()).toBe(true)
    expect(result.classes()).toContain('branch-result--error')
    expect(result.text()).toContain('a.txt')
    wrapper.unmount()
  })

  // T0016 §7 merge 실패 — a non-conflict failure (e.g. the target diverged from
  // its remote) must still surface source/target and the server's code/message
  // instead of being flattened into the same bare "병합 실패" the old shared
  // `run()` catch produced.
  it('C3c. a non-conflict merge failure still shows source/target and the server reason', async () => {
    const wrapper = mountManager()
    await flushPromises()
    postRequest.mockRejectedValueOnce({
      response: { data: { error: { code: 'branch_merge_target_diverged', message: 'target cannot fast-forward' } } },
    })
    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'flowgate-v0.2'
    await flushPromises()
    await wrapper.find('.branch-action-card:nth-of-type(2)').trigger('submit')
    await flushPromises()

    const result = wrapper.find('.branch-result')
    expect(result.exists()).toBe(true)
    expect(result.classes()).toContain('branch-result--error')
    expect(result.text()).toContain('stale-feature')
    expect(result.text()).toContain('flowgate-v0.2')
    expect(result.text()).toContain('branch_merge_target_diverged')
    expect(wrapper.find('[data-test="merge-error-message"]').text()).toBe('target cannot fast-forward')
    expect(wrapper.find('[data-test="merge-push-failed"]').exists()).toBe(false)
    wrapper.unmount()
  })

  // T0016 §7 merge 실패 — a push failure must be identifiable as specifically a
  // push failure, not indistinguishable from a conflict or any other cause.
  it('C3d. a push failure after a successful local merge is identified as a push failure', async () => {
    const wrapper = mountManager()
    await flushPromises()
    postRequest.mockRejectedValueOnce({
      response: { data: { error: { code: 'branch_merge_push_failed', message: 'push rejected' } } },
    })
    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'flowgate-v0.2'
    await flushPromises()
    await wrapper.find('.branch-action-card:nth-of-type(2)').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-test="merge-push-failed"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="merge-push-failed"]').text()).toBe(
      i18n.global.t('main.git_branch_manager.merge_push_failed'),
    )
    wrapper.unmount()
  })

  it('C4. merge cannot run when source and target are the same', async () => {
    const wrapper = mountManager()
    await flushPromises()

    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'stale-feature'
    await flushPromises()

    const button = wrapper.find('.branch-action-card:nth-of-type(2) button.btn-primary')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.find('.branch-action-card:nth-of-type(2)').trigger('submit')
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('C5. delete requires confirmation and never fires without it; a protected branch cannot be deleted at all', async () => {
    const wrapper = mountManager()
    await flushPromises()

    dialogConfirm.mockResolvedValueOnce(false)
    const staleRow = wrapper.findAll('.branch-row').find((r) => r.text().includes('stale-feature'))!
    await staleRow.find('button.btn-danger').trigger('click')
    await flushPromises()
    expect(dialogConfirm).toHaveBeenCalled()
    expect(deleteRequest).not.toHaveBeenCalled()

    dialogConfirm.mockResolvedValueOnce(true)
    deleteRequest.mockResolvedValueOnce({ data: { ok: true, deleted: true } })
    await staleRow.find('button.btn-danger').trigger('click')
    await flushPromises()
    expect(deleteRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/stale-feature',
    )

    // base branch: no delete button rendered at all (kind !== 'remote_only' still
    // true for base, but can_delete=false disables it — and it must stay disabled)
    const baseRow = wrapper.findAll('.branch-row').find((r) => r.text().includes('main'))!
    const baseDeleteBtn = baseRow.find('button.btn-danger')
    expect((baseDeleteBtn.element as HTMLButtonElement).disabled).toBe(true)
    await baseDeleteBtn.trigger('click')
    await flushPromises()
    expect(deleteRequest).toHaveBeenCalledTimes(1)
  })

  // T0016 §7 delete 실패 — a server-side rejection (racing another client, or a
  // protected/in-use reason this catalog snapshot hadn't caught yet) must show
  // which branch and why, not just a generic one-line message.
  it('C5b. a delete failure shows the target branch and the server protected/in-use reason', async () => {
    const wrapper = mountManager()
    await flushPromises()

    dialogConfirm.mockResolvedValueOnce(true)
    deleteRequest.mockRejectedValueOnce({
      response: { data: { error: { code: 'branch_in_use', message: 'blocked by an open merge' } } },
    })
    const staleRow = wrapper.findAll('.branch-row').find((r) => r.text().includes('stale-feature'))!
    await staleRow.find('button.btn-danger').trigger('click')
    await flushPromises()

    const result = wrapper.find('[data-test="delete-result"]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('stale-feature')
    expect(result.text()).toContain('branch_in_use')
    expect(result.text()).toContain(i18n.global.t('main.git_branch_manager.delete_blocked.branch_in_use'))
    wrapper.unmount()
  })

  // T0016 §6.1 — the branch currently pinned as the merge-target suggestion
  // cannot be deleted from this UI either; its delete button stays disabled
  // with the same structural reason as base/internal-slot branches.
  it('C5c. the current integration target cannot be deleted from the branch list', async () => {
    getRequest.mockReset()
    getRequest.mockResolvedValue({
      data: {
        ...CATALOG,
        branches: CATALOG.branches.map((b) => b.name === 'flowgate-v0.2'
          ? { ...b, can_delete: false, delete_blocked_reason: 'branch_is_default_merge_target' }
          : b),
      },
    })
    const wrapper = mountManager()
    await flushPromises()

    const targetRow = wrapper.findAll('.branch-row').find((r) => r.text().includes('flowgate-v0.2'))!
    const deleteBtn = targetRow.find('button.btn-danger')
    expect((deleteBtn.element as HTMLButtonElement).disabled).toBe(true)
    expect(deleteBtn.attributes('title')).toBe(
      i18n.global.t('main.git_branch_manager.delete_blocked.branch_is_default_merge_target'),
    )
    wrapper.unmount()
  })

  it('C6. a catalog load failure shows an error in this section without throwing', async () => {
    getRequest.mockReset()
    getRequest.mockRejectedValue({ response: { data: { error: { message: 'boom' } } } })
    const wrapper = mountManager()
    await flushPromises()

    expect(wrapper.find('.branch-error').exists()).toBe(true)
    expect(wrapper.get('.branch-list').findAll('.branch-row').length).toBe(0)
    wrapper.unmount()
  })

  it('C7. user-visible strings come from the active locale, not hardcoded English', async () => {
    i18n.global.locale.value = 'ko'
    const wrapper = mountManager()
    await flushPromises()

    expect(wrapper.text()).not.toContain('Branch Manager')
    expect(wrapper.text()).not.toContain('Merge failed')
    expect(wrapper.text()).toContain(i18n.global.t('main.git_branch_manager.title'))
    expect(wrapper.text()).toContain(i18n.global.t('main.git_branch_manager.current_target_label'))
    expect(wrapper.find('button.btn-secondary').text()).toBe(i18n.global.t('main.git_branch_manager.refresh'))
    wrapper.unmount()
  })

  it('C6b. a refresh failure after a successful load keeps showing the previously loaded branches (§7)', async () => {
    const wrapper = mountManager()
    await flushPromises()
    expect(wrapper.findAll('.branch-row').length).toBe(CATALOG.branches.length)

    getRequest.mockRejectedValueOnce({ response: { data: { error: { message: 'still broken' } } } })
    await wrapper.find('button.btn-secondary').trigger('click')
    await flushPromises()

    expect(wrapper.find('.branch-error').text()).toBe('still broken')
    expect(wrapper.findAll('.branch-row').length).toBe(CATALOG.branches.length)
    wrapper.unmount()
  })
})
