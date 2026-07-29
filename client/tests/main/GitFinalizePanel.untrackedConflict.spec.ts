/**
 * flowgate.default.0350 T0004 (NR0003 §7 item 7 / §6 table): GitFinalizePanel
 * used to toast the `base_untracked_conflict` 409's raw English server string.
 * It now opens the same shared dialog GitActionMenu does, and [실행] retries
 * exactly once after the operator resolves it (commit or delete).
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitFinalizePanel from '@main/components/GitFinalizePanel.vue'
import GitUntrackedConflictDialog from '@main/components/GitUntrackedConflictDialog.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const RAW_SERVER_MESSAGE =
  'the merge is blocked by uncommitted new files in the base checkout; commit or remove them, then retry'

function finalizeState() {
  return {
    group_id: 'flowgate.default.0350',
    branch: 'group/0350',
    base_branch: 'main',
    status: 'awaiting_choice',
    default_action: 'merge_only',
    choices: ['merge_only', 'wait'],
    ahead_count: 1,
    behind_count: 0,
    merge_id: null,
    commit_message: { suggested: 'fix: work', source: 'auto' },
  }
}

function mountPanel() {
  useProjectStore().setCurrentProject('flowgate')
  return mount(GitFinalizePanel, {
    props: { groupId: 'flowgate.default.0350' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockResolvedValue({ data: { ok: true, state: finalizeState() } })
})

describe('GitFinalizePanel × base_untracked_conflict (0350 T0004)', () => {
  it('opens the shared dialog with the blocked files instead of a raw-message toast', async () => {
    postRequest.mockRejectedValue({
      response: {
        data: {
          error: { code: 'base_untracked_conflict', message: RAW_SERVER_MESSAGE, details: { files: ['clash.txt'] } },
        },
      },
    })
    const wrapper = mountPanel()
    await flushPromises()

    void (wrapper.vm as any).runFinalize()
    await flushPromises()

    const dialog = wrapper.findComponent(GitUntrackedConflictDialog)
    expect(dialog.exists()).toBe(true)
    expect((dialog.vm as any).open).toBe(true)
    expect((dialog.vm as any).files).toEqual(['clash.txt'])
    expect(showToast).not.toHaveBeenCalledWith(RAW_SERVER_MESSAGE, expect.anything())
    wrapper.unmount()
  })

  it('retries the finalize exactly once after the operator deletes from the dialog', async () => {
    let cleared = false
    postRequest.mockImplementation(async (url: string) => {
      if (url.includes('/git/finalize')) {
        if (!cleared) {
          return Promise.reject({
            response: {
              data: {
                error: { code: 'base_untracked_conflict', message: RAW_SERVER_MESSAGE, details: { files: ['clash.txt'] } },
              },
            },
          })
        }
        return { data: { ok: true, result: { status: 'merged', pushed: false, merge_commit: 'def5678' } } }
      }
      if (url.includes('/git/base-remove')) {
        cleared = true
        return { data: { ok: true, result: { results: [{ path: 'clash.txt', result: 'removed' }], remaining_untracked: [] } } }
      }
      throw new Error('unexpected POST ' + url)
    })
    const wrapper = mountPanel()
    await flushPromises()

    const runPromise = (wrapper.vm as any).runFinalize()
    await flushPromises()

    const dialog = wrapper.findComponent(GitUntrackedConflictDialog)
    await (dialog.vm as any).choose('remove')
    await runPromise
    await flushPromises()

    const finalizeCalls = postRequest.mock.calls.filter(([u]) => String(u).includes('/git/finalize'))
    expect(finalizeCalls).toHaveLength(2)
    const expectedToast = i18n.global.t('main.git_finalize.merged_local_toast', { commit: 'def5678' })
    expect(showToast).toHaveBeenCalledWith(expectedToast, 'success')
    wrapper.unmount()
  })
})
