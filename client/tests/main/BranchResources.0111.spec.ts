// 0111 T#7 ⓙ: real consumers of the branch catalog must exclude attempt/external resources.
import { config, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import { useExplorerStore } from '@main/stores/explorer'
import { useLayoutStore } from '@main/stores/layout'
import FileExplorer from '@main/components/FileExplorer.vue'
import GitBranchManager from '@main/components/GitBranchManager.vue'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'

const { getRequest, apiGet, postRequest, putRequest, deleteRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), apiGet: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), deleteRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest, postRequest, putRequest, deleteRequest, patchRequest: vi.fn(), downloadBlobRequest: vi.fn(),
}))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(async () => []), uploadFiles: vi.fn() }),
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const resources = [
  // Even stale/malformed capability booleans must never make these kinds selectable.
  { name: 'graph_attempt_fixture', kind: 'internal_attempt', can_delete: true, can_be_create_source: true },
  { name: 'worktree-agent-fixture', kind: 'external_worktree', can_delete: true, can_be_create_source: true },
]
const catalog = {
  base_branch: 'main', default_merge_target: 'feature/user',
  branches: [
    { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    { name: 'feature/user', kind: 'local', can_delete: true, can_be_create_source: true },
    { name: 'slot', kind: 'internal_slot', connected_group_id: 'p.default.1', can_delete: false, can_be_create_source: false },
    ...resources,
  ],
}
const wrappers: Array<{ unmount: () => void }> = []
const oldStubs = { ...config.global.stubs }
const oldLocale = i18n.global.locale.value
beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  config.global.stubs = { ...oldStubs, teleport: true }
  vi.clearAllMocks()
  getRequest.mockImplementation(async (url: string) => {
    if (url.includes('/git/branches')) return { data: catalog }
    if (url.includes('/git/status')) return { data: { status: { slots: [] } } }
    if (url.includes('/git/finalize')) return { data: { ok: true, state: {
      branch: 'slot', base_branch: 'main', status: 'awaiting_choice', default_action: 'merge', choices: ['merge', 'merge_only', 'wait'],
    } } }
    return { data: { data: { nodes: [] } } }
  })
  apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })
})
afterEach(() => {
  while (wrappers.length) wrappers.pop()!.unmount()
  config.global.stubs = { ...oldStubs }
  i18n.global.locale.value = oldLocale
})

describe('0111 resource branches stay out of every v0.1 choice', () => {
  it.each(['ko', 'en', 'ja'] as const)('localizes locked resource kinds and delete reasons in %s', async (locale) => {
    i18n.global.locale.value = locale
    const wrapper = mount(GitBranchManager, { props: { projectId: 'p' }, global: { plugins: [i18n], stubs: { AppIcon: true } } })
    wrappers.push(wrapper)
    await flushPromises()
    const vm = wrapper.vm as any
    for (const row of resources) {
      const key = `main.git_branch_manager.kind.${row.kind}`
      expect(i18n.global.te(key, locale)).toBe(true)
      const label = vm.kindLabel(row)
      expect(label).toBe(i18n.global.t(key))
      expect(label).not.toContain(row.kind)
      const rendered = wrapper.findAll('.branch-row').find(r => r.text().includes(row.name))!
      expect(rendered.get('.badge').text()).toBe(label)
    }
    for (const code of ['branch_is_internal_attempt', 'internal_attempt', 'external_worktree']) {
      const key = `main.git_branch_manager.delete_blocked.${code}`
      expect(i18n.global.te(key, locale)).toBe(true)
      const label = vm.deleteReasonText({ can_delete: false, delete_blocked_reason: code })
      expect(label).toBe(i18n.global.t(key))
      expect(label).not.toContain(code)
      expect(vm.deleteResultReasonText({ code })).toBe(label)
    }
  })

  it('filters the explorer store catalog while retaining base/local/group slots', async () => {
    const result = await useExplorerStore().fetchBranchCatalog('p')
    expect(result.branches.map(b => b.name)).toEqual(['main', 'feature/user', 'slot'])
  })

  it('does not render resources in the actual explorer selector', async () => {
    useLayoutStore().setFileExplorerCollapsed(false)
    const wrapper = mount(FileExplorer, { props: { projectId: 'p' }, global: { plugins: [i18n] } })
    wrappers.push(wrapper)
    await flushPromises()
    const options = wrapper.get('.fx-group-select').findAll('option').map(o => (o.element as HTMLOptionElement).value)
    expect(options).toContain('local:feature/user')
    for (const row of resources) expect(options.join(' ')).not.toContain(row.name)
  })

  it('locks visible catalog resource rows and excludes create/delete/merge/default-target choices', async () => {
    const wrapper = mount(GitBranchManager, { props: { projectId: 'p' }, global: { plugins: [i18n], stubs: { AppIcon: true } } })
    wrappers.push(wrapper)
    await flushPromises()
    const vm = wrapper.vm as any
    for (const key of ['createCandidates', 'deleteCandidates', 'sourceCandidates', 'targetCandidates']) {
      for (const row of resources) expect(vm[key].map((b: any) => b.name)).not.toContain(row.name)
    }
    expect(vm.mergeCandidates.map((b: any) => b.name)).toContain('slot')
    for (const row of resources) {
      const rendered = wrapper.findAll('.branch-row').find(r => r.text().includes(row.name))!
      expect(rendered.exists()).toBe(true)
      expect(rendered.find('button').exists()).toBe(false)
    }
    const options = wrapper.findAll('select option').map(o => (o.element as HTMLOptionElement).value)
    for (const row of resources) expect(options).not.toContain(row.name)
  })

  it('keeps both resource kinds out of the approval merge-target selector', async () => {
    const wrapper = mount(ReviewActionBar, { props: {
      docId: 'p.default.1.0001-AC', projectId: 'p', groupId: 'p.default.1', docRef: 'p.default.1.0001-AC',
      docType: 'AC', reviewStatus: 'pending_review', mode: 'review',
    }, global: { plugins: [i18n] } })
    wrappers.push(wrapper)
    await flushPromises()
    await wrapper.get('button.btn-success.btn-sm').trigger('click')
    await flushPromises()
    const options = wrapper.get('select#ab-git-target').findAll('option').map(o => (o.element as HTMLOptionElement).value)
    expect(options).toEqual(['main', 'feature/user'])
  })
})
