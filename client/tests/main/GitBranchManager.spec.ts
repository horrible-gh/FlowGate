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

    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()

    expect(dialogConfirm).toHaveBeenCalled()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'stale-feature', target_branch: 'flowgate-v0.2', push: true },
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
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
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
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
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
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
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

    const button = wrapper.find('[data-test="merge-btn"]')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('C5. delete requires confirmation and never fires without it; a protected branch cannot be deleted at all', async () => {
    const wrapper = mountManager()
    await flushPromises()

    // T0018 — delete lives only in the danger zone: pick the branch, then confirm.
    await wrapper.get('[data-test="delete-select"]').setValue('stale-feature')
    dialogConfirm.mockResolvedValueOnce(false)
    await wrapper.get('[data-test="delete-btn"]').trigger('click')
    await flushPromises()
    expect(dialogConfirm).toHaveBeenCalled()
    expect(deleteRequest).not.toHaveBeenCalled()

    dialogConfirm.mockResolvedValueOnce(true)
    deleteRequest.mockResolvedValueOnce({ data: { ok: true, deleted: true } })
    await wrapper.get('[data-test="delete-btn"]').trigger('click')
    await flushPromises()
    expect(deleteRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/stale-feature',
    )

    // base branch: pickable (so the zone can say why) but can_delete=false keeps the
    // button disabled — and a click on it must not reach the API either.
    await wrapper.get('[data-test="delete-select"]').setValue('main')
    const baseDeleteBtn = wrapper.get('[data-test="delete-btn"]')
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
    await wrapper.get('[data-test="delete-select"]').setValue('stale-feature')
    await wrapper.get('[data-test="delete-btn"]').trigger('click')
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
  it('C5c. the current integration target cannot be deleted from the danger zone', async () => {
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

    await wrapper.get('[data-test="delete-select"]').setValue('flowgate-v0.2')
    const deleteBtn = wrapper.get('[data-test="delete-btn"]')
    const reason = i18n.global.t('main.git_branch_manager.delete_blocked.branch_is_default_merge_target')
    expect((deleteBtn.element as HTMLButtonElement).disabled).toBe(true)
    expect(deleteBtn.attributes('title')).toBe(reason)
    expect(wrapper.get('[data-test="delete-blocked-reason"]').text()).toBe(reason)
    await deleteBtn.trigger('click')
    await flushPromises()
    expect(dialogConfirm).not.toHaveBeenCalled()
    expect(deleteRequest).not.toHaveBeenCalled()
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

// flowgate.default.0594 T0018 — the host panel splits this component across tabs.
function mountView(view: 'all' | 'branches' | 'manage' | 'hidden') {
  return mount(GitBranchManager, {
    props: { projectId: 'flowgate', view },
    global: { plugins: [i18n], stubs: { AppIcon: true } },
  })
}

describe('GitBranchManager zones and views (T0018)', () => {
  it('D1. each action has its own zone, and no destructive control sits in the list', async () => {
    const wrapper = mountManager()
    await flushPromises()

    for (const zone of ['list', 'create', 'merge', 'danger']) {
      expect(wrapper.find(`[data-test="branch-zone-${zone}"]`).exists()).toBe(true)
    }
    const list = wrapper.get('[data-test="branch-zone-list"]')
    expect(list.findAll('.btn-danger')).toHaveLength(0)
    expect(list.find('[data-test="delete-btn"]').exists()).toBe(false)
    // create lives in the create zone, merge in the merge zone, delete only in the danger zone
    expect(wrapper.get('[data-test="branch-zone-create"]').find('input').exists()).toBe(true)
    expect(wrapper.get('[data-test="branch-zone-merge"]').find('[data-test="merge-btn"]').exists()).toBe(true)
    const danger = wrapper.get('[data-test="branch-zone-danger"]')
    expect(danger.find('[data-test="delete-btn"]').exists()).toBe(true)
    expect(wrapper.findAll('.btn-danger')).toHaveLength(1)
    expect(danger.classes()).toContain('branch-zone--danger')
    expect(danger.text()).toContain(i18n.global.t('main.git_branch_manager.danger_title'))
    wrapper.unmount()
  })

  it('D2. view=branches shows list + create only; view=manage shows merge + danger only', async () => {
    const branches = mountView('branches')
    await flushPromises()
    expect(branches.find('[data-test="branch-zone-list"]').exists()).toBe(true)
    expect(branches.find('[data-test="branch-zone-create"]').exists()).toBe(true)
    expect(branches.find('[data-test="branch-zone-merge"]').exists()).toBe(false)
    expect(branches.find('[data-test="branch-zone-danger"]').exists()).toBe(false)
    expect(branches.find('.branch-summary').exists()).toBe(false)
    branches.unmount()

    const manage = mountView('manage')
    await flushPromises()
    expect(manage.find('[data-test="branch-zone-list"]').exists()).toBe(false)
    expect(manage.find('[data-test="branch-zone-create"]').exists()).toBe(false)
    expect(manage.find('[data-test="branch-zone-merge"]').exists()).toBe(true)
    expect(manage.find('[data-test="branch-zone-danger"]').exists()).toBe(true)
    manage.unmount()
  })

  it('D3. view=hidden renders no control but still loads and reports the catalog summary', async () => {
    const wrapper = mountView('hidden')
    await flushPromises()
    expect(wrapper.find('[data-test="branch-manager"]').exists()).toBe(false)
    expect(wrapper.findAll('button, input, select')).toHaveLength(0)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/flowgate/git/branches')
    expect(wrapper.emitted('catalog')?.at(-1)).toEqual([
      { state: 'ready', base_branch: 'main', default_merge_target: 'flowgate-v0.2' },
    ])

    // switching the host tab later shows the already-loaded catalog without a refetch
    await wrapper.setProps({ view: 'branches' })
    expect(wrapper.findAll('.branch-row')).toHaveLength(CATALOG.branches.length)
    expect(getRequest).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('D4. a catalog failure shows inside whichever branch view is open and is reported as error', async () => {
    getRequest.mockReset()
    getRequest.mockRejectedValue({ response: { data: { error: { message: 'catalog down' } } } })
    for (const view of ['branches', 'manage'] as const) {
      const wrapper = mountView(view)
      await flushPromises()
      expect(wrapper.get('[data-test="branch-manager"] .branch-error').text()).toBe('catalog down')
      expect(wrapper.emitted('catalog')?.at(-1)?.[0]).toMatchObject({ state: 'error' })
      wrapper.unmount()
    }
  })

  it('D5. create fields expose visible context and use the shared control style', async () => {
    const wrapper = mountView('branches')
    await flushPromises()
    const zone = wrapper.get('[data-test="branch-zone-create"]')
    expect(zone.text()).toContain(i18n.global.t('main.git_branch_manager.create_name_label'))
    expect(zone.text()).toContain(i18n.global.t('main.git_branch_manager.create_source_label'))
    expect(zone.get('input').attributes('placeholder')).toBe(
      i18n.global.t('main.git_branch_manager.create_name_placeholder'),
    )
    expect(zone.get('input').classes()).toContain('branch-control')
    expect(zone.get('select').classes()).toContain('branch-control')
    wrapper.unmount()
  })

  it('D6. merge shows contextual source/target fields when selectable', async () => {
    const wrapper = mountView('manage')
    await flushPromises()
    const zone = wrapper.get('[data-test="branch-zone-merge"]')
    expect(zone.find('[data-test="merge-empty"]').exists()).toBe(false)
    expect(zone.findAll('select')).toHaveLength(2)
    expect(zone.text()).toContain(i18n.global.t('main.git_branch_manager.source_hint'))
    expect(zone.text()).toContain(i18n.global.t('main.git_branch_manager.target_hint'))
    expect(zone.findAll('select').every((select) => select.classes().includes('branch-control'))).toBe(true)
    expect(wrapper.get('[data-test="delete-select"]').classes()).toContain('branch-control')
    wrapper.unmount()
  })

  it('D7. merge renders a clear empty state instead of empty selects', async () => {
    getRequest.mockResolvedValueOnce({
      data: {
        ...CATALOG,
        default_merge_target: null,
        branches: [{ name: 'main', kind: 'base', can_delete: false, can_be_create_source: true }],
      },
    })
    const wrapper = mountView('manage')
    await flushPromises()
    const zone = wrapper.get('[data-test="branch-zone-merge"]')
    expect(zone.get('[data-test="merge-empty"]').text()).toBe(
      i18n.global.t('main.git_branch_manager.merge_empty'),
    )
    expect(zone.findAll('select')).toHaveLength(0)
    expect(zone.get('[data-test="merge-btn"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('D8. merge still calls the same API from the manage view', async () => {
    const wrapper = mountView('manage')
    await flushPromises()
    postRequest.mockResolvedValueOnce({ data: { ok: true } })
    const zone = wrapper.get('[data-test="branch-zone-merge"]')
    await zone.findAll('select')[0].setValue('stale-feature')
    await zone.findAll('select')[1].setValue('flowgate-v0.2')
    await zone.trigger('submit')
    await flushPromises()
    expect(dialogConfirm).toHaveBeenCalled()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'stale-feature', target_branch: 'flowgate-v0.2', push: true },
    )
    // the result banner stays inside the merge zone, not in the danger zone
    expect(zone.find('.branch-result').exists()).toBe(true)
    expect(wrapper.get('[data-test="branch-zone-danger"]').find('.branch-result').exists()).toBe(false)
    wrapper.unmount()
  })
})

// flowgate.default.0612 T0004 (source_nr: 0003-NR §12/§13) — merge selection sync
// regressions. SYNC_CATALOG starts with default_merge_target: null (matching a
// freshly-provisioned project's real GET response) so mergeSource and mergeTarget
// land on different branches at mount time. The default CATALOG above cannot be
// reused for T1/T9: its default_merge_target ('flowgate-v0.2') happens to collide
// with the branch mergeSource lands on at mount, which masks the defects these
// tests target.
const SYNC_CATALOG = {
  ok: true,
  base_branch: 'main',
  default_merge_target: null as string | null,
  branches: [
    { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    { name: 'flowgate-v0.2', kind: 'local', can_delete: true, can_be_create_source: true },
    { name: 'stale-feature', kind: 'local', can_delete: true, can_be_create_source: true },
  ],
}

function mergeSelects(wrapper: ReturnType<typeof mountManager>) {
  const zone = wrapper.get('[data-test="branch-zone-merge"]')
  const selects = zone.findAll('select')
  return { source: selects[0], target: selects[1] }
}

describe('GitBranchManager merge selection sync (T0004 §3, source_nr 0003-NR §1/§6/§7)', () => {
  it('T1. setDefaultTarget 이후 Merge 폼 Target도 새 target으로 동기화된다', async () => {
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: SYNC_CATALOG })
    const wrapper = mountManager()
    await flushPromises()
    expect((wrapper.vm as any).mergeSource).toBe('flowgate-v0.2')
    expect((wrapper.vm as any).mergeTarget).toBe('main')

    putRequest.mockResolvedValueOnce({ data: { ok: true, default_merge_target: 'stale-feature' } })
    getRequest.mockResolvedValueOnce({ data: { ...SYNC_CATALOG, default_merge_target: 'stale-feature' } })
    const row = wrapper.findAll('.branch-row').find((r) => r.text().includes('stale-feature'))!
    await row.find('button.btn-secondary').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="current-target-value"]').text()).toBe('stale-feature')
    expect((wrapper.vm as any).mergeTarget).toBe('stale-feature')
    const { target } = mergeSelects(wrapper)
    expect((target.element as HTMLSelectElement).value).toBe('stale-feature')
    wrapper.unmount()
  })

  it('T2. mergeSource/mergeTarget이 동시에 base_branch로 폴백될 때 invariant가 깨지지 않는다', async () => {
    getRequest.mockReset()
    getRequest.mockResolvedValue({
      data: { ...SYNC_CATALOG, default_merge_target: 'stale-feature' },
    })
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'main'
    ;(wrapper.vm as any).mergeTarget = 'stale-feature'
    await flushPromises()

    getRequest.mockResolvedValueOnce({
      data: {
        ...SYNC_CATALOG,
        default_merge_target: 'main',
        branches: SYNC_CATALOG.branches.filter((b) => b.name !== 'stale-feature'),
      },
    })
    await wrapper.find('[data-test="branch-refresh"]').trigger('click')
    await flushPromises()

    expect((wrapper.vm as any).mergeSource).not.toBe((wrapper.vm as any).mergeTarget)
    const { source, target } = mergeSelects(wrapper)
    expect((source.element as HTMLSelectElement).value).not.toBe('')
    expect((target.element as HTMLSelectElement).value).not.toBe('')
    wrapper.unmount()
  })

  it('T3. main을 source로, 다른 local branch를 target으로 merge할 수 있다 (실제 <select> 조작)', async () => {
    const wrapper = mountManager()
    await flushPromises()
    const { source, target } = mergeSelects(wrapper)
    await source.setValue('main')
    await target.setValue('flowgate-v0.2')
    await flushPromises()
    expect((source.element as HTMLSelectElement).value).toBe('main')
    expect((target.element as HTMLSelectElement).value).toBe('flowgate-v0.2')
    postRequest.mockResolvedValueOnce({ data: { ok: true } })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'main', target_branch: 'flowgate-v0.2', push: true },
    )
    wrapper.unmount()
  })

  it('T4. 다른 local branch를 source로, main을 target으로 merge할 수 있다 (실제 <select> 조작)', async () => {
    const wrapper = mountManager()
    await flushPromises()
    const { source, target } = mergeSelects(wrapper)
    await target.setValue('main')
    await source.setValue('flowgate-v0.2')
    await flushPromises()
    expect((source.element as HTMLSelectElement).value).toBe('flowgate-v0.2')
    expect((target.element as HTMLSelectElement).value).toBe('main')
    postRequest.mockResolvedValueOnce({ data: { ok: true } })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'flowgate-v0.2', target_branch: 'main', push: true },
    )
    wrapper.unmount()
  })

  it('T10. 일반 브랜치가 main/test 두 개뿐이어도 실제 <select> 조작만으로 양방향 전환이 가능하다 (rej_01M3E5RFNW2BXG5E)', async () => {
    getRequest.mockReset()
    const TWO_BRANCH_CATALOG = {
      ok: true,
      base_branch: 'main',
      default_merge_target: null as string | null,
      branches: [
        { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
        { name: 'test', kind: 'local', can_delete: true, can_be_create_source: true },
      ],
    }
    getRequest.mockResolvedValue({ data: TWO_BRANCH_CATALOG })
    const wrapper = mountManager()
    await flushPromises()

    // Mount-time default lands on test -> main (this direction always worked).
    const { source, target } = mergeSelects(wrapper)
    expect((source.element as HTMLSelectElement).value).toBe('test')
    expect((target.element as HTMLSelectElement).value).toBe('main')

    // Before the fix, Source's <option> list excluded the current Target
    // ('main'), so 'main' was never a selectable <option> here at all and
    // this setValue() could not reach the value it asks for.
    await source.setValue('main')
    await flushPromises()
    expect((source.element as HTMLSelectElement).value).toBe('main')
    expect((target.element as HTMLSelectElement).value).toBe('test')
    postRequest.mockResolvedValueOnce({ data: { ok: true } })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'main', target_branch: 'test', push: true },
    )

    // And back again, purely through the Source <select>, proving the
    // direction is not a one-way trapdoor.
    await source.setValue('test')
    await flushPromises()
    expect((source.element as HTMLSelectElement).value).toBe('test')
    expect((target.element as HTMLSelectElement).value).toBe('main')
    wrapper.unmount()
  })

  it('T5. local -> local merge가 가능하다', async () => {
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'flowgate-v0.2'
    await flushPromises()
    postRequest.mockResolvedValueOnce({ data: { ok: true } })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'stale-feature', target_branch: 'flowgate-v0.2', push: true },
    )
    wrapper.unmount()
  })

  it('T6. source와 target이 같으면 merge 버튼이 비활성화되고 API가 호출되지 않는다', async () => {
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'stale-feature'
    await flushPromises()
    const button = wrapper.find('[data-test="merge-btn"]')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('T7. internal_slot 브랜치는 merge Source/Target 옵션 목록에 없다', async () => {
    const wrapper = mountManager()
    await flushPromises()
    const { source, target } = mergeSelects(wrapper)
    const sourceNames = source.findAll('option').map((o) => o.element.value)
    const targetNames = target.findAll('option').map((o) => o.element.value)
    expect(sourceNames).not.toContain('flowgate_default_0599')
    expect(targetNames).not.toContain('flowgate_default_0599')
    wrapper.unmount()
  })

  it('T8. remote_only 브랜치는 merge Source/Target 옵션 목록에 없다', async () => {
    const wrapper = mountManager()
    await flushPromises()
    const { source, target } = mergeSelects(wrapper)
    const sourceNames = source.findAll('option').map((o) => o.element.value)
    const targetNames = target.findAll('option').map((o) => o.element.value)
    expect(sourceNames).not.toContain('old-remote')
    expect(targetNames).not.toContain('old-remote')
    wrapper.unmount()
  })

  it('T9. 성공한 일반 refresh는 유효한 수동 Target 선택을 default_merge_target 변경으로 덮어쓰지 않는다', async () => {
    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: SYNC_CATALOG })
    const wrapper = mountManager()
    await flushPromises()
    expect((wrapper.vm as any).mergeSource).toBe('flowgate-v0.2')
    expect((wrapper.vm as any).mergeTarget).toBe('main')
    ;(wrapper.vm as any).mergeTarget = 'stale-feature'
    await flushPromises()

    getRequest.mockResolvedValueOnce({ data: { ...SYNC_CATALOG, default_merge_target: 'main' } })
    await wrapper.find('[data-test="branch-refresh"]').trigger('click')
    await flushPromises()

    expect((wrapper.vm as any).mergeTarget).toBe('stale-feature')
    const { target } = mergeSelects(wrapper)
    expect((target.element as HTMLSelectElement).value).toBe('stale-feature')
    wrapper.unmount()
  })
})

// flowgate.default.0612 T0006 §10 T8/T9 — Branch Manager merge/push separation.
// merge_branches() now takes an explicit `push`; these cover the checkbox's
// default, its effect on the request payload and confirm wording, and the
// pushed/local badge on a successful result. Server-side push=false behavior
// (local ref preserved, no push call, base-branch target) is covered by
// server/tests/test_git_branches_0594.py instead of here.
describe('GitBranchManager merge push selection (T0006)', () => {
  it('P1. push 체크박스는 기본적으로 켜져 있다', async () => {
    const wrapper = mountManager()
    await flushPromises()
    const checkbox = wrapper.get('[data-test="merge-push-checkbox"]')
    expect((checkbox.element as HTMLInputElement).checked).toBe(true)
    wrapper.unmount()
  })

  it('P2. push 체크박스를 해제하면 merge API에 push:false가 전달된다', async () => {
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'flowgate-v0.2'
    await wrapper.get('[data-test="merge-push-checkbox"]').setValue(false)
    await flushPromises()

    postRequest.mockResolvedValueOnce({
      data: { ok: true, source_branch: 'stale-feature', target_branch: 'flowgate-v0.2', pushed: false },
    })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches/merge',
      { source_branch: 'stale-feature', target_branch: 'flowgate-v0.2', push: false },
    )
    wrapper.unmount()
  })

  it('P3. push 여부에 따라 확인창 문구가 달라진다', async () => {
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'flowgate-v0.2'
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: { ok: true, pushed: true } })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(dialogConfirm).toHaveBeenLastCalledWith(
      expect.objectContaining({
        message: i18n.global.t('main.git_branch_manager.merge_confirm_message_push', {
          source: 'stale-feature', target: 'flowgate-v0.2',
        }),
      }),
    )

    await wrapper.get('[data-test="merge-push-checkbox"]').setValue(false)
    postRequest.mockResolvedValueOnce({ data: { ok: true, pushed: false } })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(dialogConfirm).toHaveBeenLastCalledWith(
      expect.objectContaining({
        message: i18n.global.t('main.git_branch_manager.merge_confirm_message_no_push', {
          source: 'stale-feature', target: 'flowgate-v0.2',
        }),
      }),
    )
    wrapper.unmount()
  })

  it('P4. 성공 응답의 pushed 값에 따라 결과 배지가 달라진다', async () => {
    const wrapper = mountManager()
    await flushPromises()
    ;(wrapper.vm as any).mergeSource = 'stale-feature'
    ;(wrapper.vm as any).mergeTarget = 'flowgate-v0.2'
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: { ok: true, pushed: true } })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[data-test="merge-result-pushed"]').text()).toBe(
      i18n.global.t('main.git_branch_manager.merge_result_pushed'),
    )

    await wrapper.get('[data-test="merge-push-checkbox"]').setValue(false)
    postRequest.mockResolvedValueOnce({ data: { ok: true, pushed: false } })
    await wrapper.find('[data-test="branch-zone-merge"]').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[data-test="merge-result-pushed"]').text()).toBe(
      i18n.global.t('main.git_branch_manager.merge_result_local'),
    )
    wrapper.unmount()
  })
})
