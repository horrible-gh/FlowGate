/**
 * flowgate.default.0350 T0004 (NR0003 §7 item 7 / §6 table): before this fix,
 * GitActionMenu rendered the `base_untracked_conflict` 409's raw English server
 * message as a toast and never offered a way to resolve it from here. It now
 * gets the same non-auto-resolve dialog treatment as `base_dirty`: the
 * operator picks commit or delete, and [execute] retries exactly once.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitActionMenu from '@main/components/GitActionMenu.vue'
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

const PENDING_ITEM = {
  group_id: 'flowgate.default.0350',
  branch: 'group/0350',
  status: 'waiting',
  default_action: 'merge_only',
}

function gitStatus() {
  return {
    enabled: true,
    base_branch: 'main',
    pending_count: 1,
    pending: [PENDING_ITEM],
  }
}

const RAW_SERVER_MESSAGE =
  'the merge is blocked by uncommitted new files in the base checkout; commit or remove them, then retry'

function mountMenu() {
  useProjectStore().setCurrentProject('flowgate')
  return mount(GitActionMenu, {
    global: {
      plugins: [i18n],
      stubs: { GitStatusPanel: true, teleport: true },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  localStorage.clear()
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockResolvedValue({ data: { ok: true, status: gitStatus() } })
})

describe('GitActionMenu × base_untracked_conflict (0350 T0004)', () => {
  it('opens the shared dialog instead of toasting the raw server message', async () => {
    postRequest.mockRejectedValue({
      response: {
        data: {
          error: {
            code: 'base_untracked_conflict',
            message: RAW_SERVER_MESSAGE,
            details: { files: ['clash.txt'] },
          },
        },
      },
    })
    const wrapper = mountMenu()
    await flushPromises()

    void (wrapper.vm as any).execute(PENDING_ITEM)
    await flushPromises()

    const dialog = wrapper.findComponent(GitUntrackedConflictDialog)
    expect(dialog.exists()).toBe(true)
    expect((dialog.vm as any).files).toEqual(['clash.txt'])
    expect((dialog.vm as any).open).toBe(true)
    expect(showToast).not.toHaveBeenCalledWith(RAW_SERVER_MESSAGE, expect.anything())
    wrapper.unmount()
  })

  it('retries [execute] exactly once after the operator commits from the dialog', async () => {
    let cleared = false
    postRequest.mockImplementation(async (url: string) => {
      if (url.includes('/git/finalize')) {
        if (!cleared) {
          const err = {
            response: {
              data: {
                error: { code: 'base_untracked_conflict', message: RAW_SERVER_MESSAGE, details: { files: ['clash.txt'] } },
              },
            },
          }
          return Promise.reject(err)
        }
        return { data: { ok: true, result: { status: 'merged', pushed: false, merge_commit: 'abc1234' } } }
      }
      if (url.includes('/git/base-commit')) {
        cleared = true
        return { data: { ok: true, result: { committed: true, files: ['clash.txt'], remaining: [], remaining_untracked: [] } } }
      }
      throw new Error('unexpected POST ' + url)
    })
    const wrapper = mountMenu()
    await flushPromises()

    const runPromise = (wrapper.vm as any).execute(PENDING_ITEM)
    await flushPromises()

    const dialog = wrapper.findComponent(GitUntrackedConflictDialog)
    await (dialog.vm as any).choose('commit')
    await runPromise
    await flushPromises()

    const finalizeCalls = postRequest.mock.calls.filter(([u]) => String(u).includes('/git/finalize'))
    expect(finalizeCalls).toHaveLength(2)
    const expectedToast = i18n.global.t('main.git_finalize.merged_toast', { commit: 'abc1234' })
    expect(showToast).toHaveBeenCalledWith(expectedToast, 'success')
    wrapper.unmount()
  })
})
