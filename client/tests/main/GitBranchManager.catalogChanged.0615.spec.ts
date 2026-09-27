// flowgate.default.0615 T0004 §5 — Branch Manager must notify File Explorer immediately
// after a successful ordinary local-branch create/delete so the selector and the tree
// on screen never lag behind (NR0003's "다시 새로고침해야 보임" finding).
import { config, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitBranchManager from '@main/components/GitBranchManager.vue'

const { getRequest, postRequest, deleteRequest, dialogConfirm, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  deleteRequest: vi.fn(),
  dialogConfirm: vi.fn(() => Promise.resolve(true)),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest,
  postRequest,
  putRequest: vi.fn(),
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
  default_merge_target: null,
  branches: [
    { name: 'main', kind: 'base', can_delete: false, can_be_create_source: true },
    { name: 'test-branch', kind: 'local', can_delete: true, can_be_create_source: true },
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
  deleteRequest.mockReset()
  dialogConfirm.mockReset()
  dialogConfirm.mockResolvedValue(true)
  showToast.mockReset()
  getRequest.mockResolvedValue({ data: CATALOG })
})

afterEach(() => {
  config.global.stubs = { ...originalGlobalStubs }
  vi.restoreAllMocks()
})

describe('GitBranchManager dispatches fg:git_branches_changed (0615 T0004 §5)', () => {
  it('dispatches after a successful create, naming this project', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const wrapper = mountManager()
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: { ok: true, branch: 'new-branch', published: false } })
    getRequest.mockResolvedValueOnce({ data: CATALOG })
    await wrapper.get('[data-test="branch-zone-create"] input').setValue('new-branch')
    await wrapper.get('[data-test="branch-zone-create"]').trigger('submit')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/branches',
      expect.objectContaining({ name: 'new-branch' }),
    )
    const changedEvents = dispatchSpy.mock.calls
      .map((c) => c[0] as Event)
      .filter((e) => e.type === 'fg:git_branches_changed')
    expect(changedEvents).toHaveLength(1)
    expect((changedEvents[0] as CustomEvent).detail).toEqual({
      project: 'flowgate', action: 'create', branch: 'new-branch',
    })
  })

  it('dispatches after a successful create even when the follow-up catalog GET fails (rev1 review finding)', async () => {
    // The mutation itself already succeeded server-side by this point -- File Explorer's
    // invalidation must not be gated behind this panel's own re-fetch of its list.
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const wrapper = mountManager()
    await flushPromises()

    postRequest.mockResolvedValueOnce({ data: { ok: true, branch: 'new-branch', published: false } })
    getRequest.mockRejectedValueOnce({ response: { data: { error: { message: 'catalog reload boom' } } } })
    await wrapper.get('[data-test="branch-zone-create"] input').setValue('new-branch')
    await wrapper.get('[data-test="branch-zone-create"]').trigger('submit')
    await flushPromises()

    const changedEvents = dispatchSpy.mock.calls
      .map((c) => c[0] as Event)
      .filter((e) => e.type === 'fg:git_branches_changed')
    expect(changedEvents).toHaveLength(1)
    expect((changedEvents[0] as CustomEvent).detail).toEqual({
      project: 'flowgate', action: 'create', branch: 'new-branch',
    })
  })

  it('dispatches after a successful delete even when the follow-up catalog GET fails (rev1 review finding)', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const wrapper = mountManager()
    await flushPromises()

    deleteRequest.mockResolvedValueOnce({ data: { ok: true, deleted: true } })
    getRequest.mockRejectedValueOnce({ response: { data: { error: { message: 'catalog reload boom' } } } })
    await wrapper.get('[data-test="delete-select"]').setValue('test-branch')
    await wrapper.get('[data-test="delete-btn"]').trigger('click')
    await flushPromises()

    expect(deleteRequest).toHaveBeenCalledWith('/api/v1/projects/flowgate/git/branches/test-branch')
    const changedEvents = dispatchSpy.mock.calls
      .map((c) => c[0] as Event)
      .filter((e) => e.type === 'fg:git_branches_changed')
    expect(changedEvents).toHaveLength(1)
    // rev2 — this is what lets File Explorer invalidate a matching selection
    // synchronously, without waiting on (or depending on the success of) its own
    // catalog refetch: see FileExplorer.localBranch.0615.spec.ts's
    // "drops the stale tree even when the follow-up catalog GET fails" case.
    expect((changedEvents[0] as CustomEvent).detail).toEqual({
      project: 'flowgate', action: 'delete', branch: 'test-branch',
    })
  })

  it('does not dispatch when create fails', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const wrapper = mountManager()
    await flushPromises()

    postRequest.mockRejectedValueOnce({ response: { data: { error: { message: 'boom' } } } })
    await wrapper.get('[data-test="branch-zone-create"] input').setValue('new-branch')
    await wrapper.get('[data-test="branch-zone-create"]').trigger('submit')
    await flushPromises()

    expect(dispatchSpy.mock.calls.some((c) => (c[0] as Event).type === 'fg:git_branches_changed')).toBe(false)
  })

  it('dispatches after a successful delete', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const wrapper = mountManager()
    await flushPromises()

    deleteRequest.mockResolvedValueOnce({ data: { ok: true, deleted: true } })
    getRequest.mockResolvedValueOnce({ data: { ...CATALOG, branches: [CATALOG.branches[0]] } })
    await wrapper.get('[data-test="delete-select"]').setValue('test-branch')
    await wrapper.get('[data-test="delete-btn"]').trigger('click')
    await flushPromises()

    expect(deleteRequest).toHaveBeenCalledWith('/api/v1/projects/flowgate/git/branches/test-branch')
    const changedEvents = dispatchSpy.mock.calls
      .map((c) => c[0] as Event)
      .filter((e) => e.type === 'fg:git_branches_changed')
    expect(changedEvents).toHaveLength(1)
  })

  it('does not dispatch when delete fails', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    const wrapper = mountManager()
    await flushPromises()

    deleteRequest.mockRejectedValueOnce({ response: { data: { error: { code: 'branch_in_use', message: 'blocked' } } } })
    await wrapper.get('[data-test="delete-select"]').setValue('test-branch')
    await wrapper.get('[data-test="delete-btn"]').trigger('click')
    await flushPromises()

    expect(dispatchSpy.mock.calls.some((c) => (c[0] as Event).type === 'fg:git_branches_changed')).toBe(false)
  })
})
