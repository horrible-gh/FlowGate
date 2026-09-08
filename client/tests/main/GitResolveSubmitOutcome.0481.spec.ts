// flowgate.default.0481 T0010 rev2 — "해소 제출했는데 반응이 없는데 어떻게 된거지?
// 예전에 되던거 아냐?"
//
// It used to work, and then it stopped, and the reason is a version skew the screen
// refused to notice. 0481 T0008 changed what a resolved general merge answers with:
// `merged` (commit already made) → `resolved_pending_review` (a human approves first).
// Every submit handler was a chain of `else if (status === ...)` with no final `else`,
// so a screen built before that change simply fell out of the chain — no toast, no
// error, no closed dialog. The operator pressed the button and the app said nothing.
//
// These tests pin the two halves of the fix:
//   1. the state the server actually answers with today is handled on every surface;
//   2. a state this build does not know is REPORTED rather than swallowed, so the next
//      protocol change shows up as a sentence instead of a dead button.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitFinalizePanel from '@main/components/GitFinalizePanel.vue'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'
import FileExplorer from '@main/components/FileExplorer.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import { useLayoutStore } from '@main/stores/layout'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, apiGet, apiPost, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: apiPost, patch: vi.fn(), delete: vi.fn() },
  getRequest,
  postRequest,
  patchRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(async () => []), uploadFiles: vi.fn() }),
}))

const GROUP = 'flowgate.default.0481'
const CLEAN = 'a resolved line\n'
const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  apiGet.mockReset()
  apiPost.mockReset()
  showToast.mockReset()
})

// ── surface 1: the group's finalize panel (where the reviewer pressed it) ──────
const FINALIZE_STATE = {
  group_id: GROUP, branch: 'group/0481', base_branch: 'main',
  status: 'conflict', choices: [], ahead_count: 1, behind_count: 0, merge_id: 7,
}
async function mountFinalize() {
  useProjectStore().setCurrentProject('flowgate')
  getRequest.mockImplementation((url: string) => {
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { ok: true, state: FINALIZE_STATE } })
    if (url.includes('/git/merge/7/conflicts')) {
      return Promise.resolve({ data: { ok: true, files: [{ path: 'README.md', content: CLEAN, conflict_count: 0 }] } })
    }
    return Promise.reject(new Error('unexpected GET ' + url))
  })
  const wrapper = mount(GitFinalizePanel, {
    props: { groupId: GROUP },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
  await flushPromises()
  await (wrapper.vm as any).openConflictDialog()
  await flushPromises()
  return wrapper
}
async function submitFinalize(wrapper: any) {
  wrapper.findComponent(GitConflictResolverDialog).vm.$emit('submit', false)
  await flushPromises()
}

describe('[해소 제출] outcome — GitFinalizePanel', () => {
  // 0481 T0010 rev5 (반려 #3) — this used to assert the "승인 대기 화면에서 확인 후 승인하세요"
  // toast, which is the sentence the reviewer answered with "알아서 승인화면으로 가세요 할게
  // 아니라 대려다줘야 할거 아냐?". The outcome being pinned here is the same one, one step
  // further: the panel opens that screen. GitConflictHandover.0481.spec.ts pins the handover
  // itself (this file's dialogs are stubbed, so it also checks the approval dialog mounts).
  it('closes and OPENS the approval gate when the server answers resolved_pending_review', async () => {
    const wrapper = await mountFinalize()
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'resolved_pending_review' } } })
    await submitFinalize(wrapper)

    expect(showToast).toHaveBeenCalledWith(t('main.git_review.resolved_pending_opened'), 'success')
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    wrapper.unmount()
  })

  it('says so instead of going quiet when the result state is one this build does not know', async () => {
    const wrapper = await mountFinalize()
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'some_future_state' } } })
    await submitFinalize(wrapper)

    const expected = t('main.git_finalize.resolve_unknown_result', { status: 'some_future_state' })
    // the message must really be a sentence naming the state — not the bare key path a
    // missing i18n entry would return, which would make the comparison below vacuous.
    expect(expected).toContain('some_future_state')
    expect(expected).not.toContain('main.git_finalize')
    expect(showToast).toHaveBeenCalledWith(expected, 'danger')
    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    expect(dialog.exists()).toBe(true)
    expect(dialog.props('errorMessage')).toBe(expected)
    wrapper.unmount()
  })

  it('lists the files the server says are still unresolved', async () => {
    const wrapper = await mountFinalize()
    postRequest.mockResolvedValue({
      data: { ok: true, result: { status: 'conflict', remaining_conflicts: ['a.txt', 'b.txt'] } },
    })
    await submitFinalize(wrapper)

    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    expect(dialog.exists()).toBe(true)
    const expected = t('main.git_finalize.resolve_remaining', { paths: 'a.txt, b.txt' })
    expect(expected).toContain('a.txt, b.txt')
    expect(expected).not.toContain('main.git_finalize')
    expect(dialog.props('errorMessage')).toBe(expected)
    wrapper.unmount()
  })
})

// ── surface 2: the header Git status panel ────────────────────────────────────
function gitStatus() {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'ready',
    ahead_count: 0, behind_count: 0, slots: [],
    pending: [{ group_id: GROUP, branch: 'group/0481', status: 'conflict', default_action: 'merge', merge_id: 7 }],
    pending_count: 1,
  }
}
async function mountStatusPanel() {
  getRequest.mockImplementation((url: string) => {
    if (url.endsWith('/git/status')) return Promise.resolve({ data: { ok: true, status: gitStatus() } })
    if (url.includes('/git/merge/7/conflicts')) {
      return Promise.resolve({ data: { ok: true, files: [{ path: 'README.md', content: CLEAN, conflict_count: 0 }] } })
    }
    return Promise.reject(new Error('unexpected GET ' + url))
  })
  const wrapper = mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
  await flushPromises()
  await wrapper.find('.git-status-row-main .btn-danger').trigger('click')
  await flushPromises()
  return wrapper
}

describe('[해소 제출] outcome — GitStatusPanel', () => {
  it('reports an unknown result state rather than leaving the dialog mute', async () => {
    const wrapper = await mountStatusPanel()
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'some_future_state' } } })
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('submit', false)
    await flushPromises()

    const expected = t('main.git_finalize.resolve_unknown_result', { status: 'some_future_state' })
    expect(showToast).toHaveBeenCalledWith(expected, 'danger')
    expect(wrapper.findComponent(GitConflictResolverDialog).props('errorMessage')).toBe(expected)
    wrapper.unmount()
  })

  it('names the still-unresolved files when the server answers conflict', async () => {
    const wrapper = await mountStatusPanel()
    postRequest.mockResolvedValue({
      data: { ok: true, result: { status: 'conflict', remaining_conflicts: ['a.txt'] } },
    })
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('submit', false)
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).props('errorMessage')).toBe(
      t('main.git_finalize.resolve_remaining', { paths: 'a.txt' }),
    )
    wrapper.unmount()
  })
})

// ── surface 3: the explorer's group-update conflict ───────────────────────────
// The explorer re-reads the group's finalize badge after every submit, and reopens the
// dialog while that badge still carries a merge_id — so the fixture has to let the merge
// end, exactly as the server would once the update is committed.
let explorerMergeId: number | null = 41
async function mountExplorer() {
  explorerMergeId = 41
  useLayoutStore().setFileExplorerCollapsed(false)
  getRequest.mockImplementation(async (url: string) => {
    if (url.includes('/git/status')) {
      return { data: { status: { slots: [{ group_id: GROUP, branch: 'fg-0481', status: 'none', writable: true }] } } }
    }
    if (url.includes('/git/groups/')) {
      if (url.endsWith('/changes')) return { data: { data: { changes: [] } } }
      return { data: { data: { branch: 'fg-0481', commit: 'c1', nodes: [], worktree_untracked: [] } } }
    }
    return { data: { data: { nodes: [] } } }
  })
  apiGet.mockImplementation(async (url: string) => {
    if (url.includes('/conflicts')) return { data: { files: [{ path: 'README.md', content: CLEAN, conflict_count: 0 }] } }
    return { data: { state: { ahead_count: 0, status: 'none', merge_id: explorerMergeId } } }
  })
  const wrapper = mount(FileExplorer, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { ContextMenu: true, ContextMenuItem: true, CreateFileFolderModal: true } },
  })
  await flushPromises()
  await wrapper.get('.fx-group-select').setValue(GROUP)
  await flushPromises()
  return wrapper
}

describe('[해소 제출] outcome — FileExplorer group update', () => {
  it('does not claim the update landed when the body still says conflict', async () => {
    const wrapper = await mountExplorer()
    apiPost.mockResolvedValue({ data: { ok: true, result: { status: 'conflict', remaining_conflicts: ['README.md'] } } })

    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    expect(dialog.exists()).toBe(true)
    dialog.vm.$emit('submit', false)
    await flushPromises()

    expect(showToast).not.toHaveBeenCalledWith(t('main.explorer.git_updated'), 'success')
    const stillOpen = wrapper.findComponent(GitConflictResolverDialog)
    expect(stillOpen.exists()).toBe(true)
    expect(stillOpen.props('errorMessage')).toBe(
      t('main.git_finalize.resolve_remaining', { paths: 'README.md' }),
    )
    wrapper.unmount()
  })

  it('still closes and reports success on the updated result', async () => {
    const wrapper = await mountExplorer()
    apiPost.mockImplementation(async () => {
      explorerMergeId = null
      return { data: { ok: true, result: { status: 'updated', merge_commit: 'abc1234' } } }
    })

    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('submit', false)
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith(t('main.explorer.git_updated'), 'success')
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    wrapper.unmount()
  })
})
