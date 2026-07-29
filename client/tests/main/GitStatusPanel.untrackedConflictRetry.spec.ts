/**
 * flowgate.default.0350 T0004 (NR0003 §1 발견 4 / §7 item 5-6): before this fix,
 * GitStatusPanel auto-selected the blocked files on a `base_untracked_conflict`
 * 409 but never parked the original finalize request, so resolving the
 * conflict (commit or the new delete) never resumed it — the operator had to
 * notice and press [실행] again by hand. It now mirrors the `base_dirty` park:
 * the original action/commit_message is retried once, automatically, as soon
 * as every blocked path is gone from the base checkout's untracked set.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function baseStatus() {
  return {
    enabled: true,
    base_branch: 'main',
    base_path_state: 'checkout',
    ahead_count: 0,
    behind_count: 0,
    slots: [],
    pending: [],
    pending_count: 0,
    base_untracked: { count: 1, files: ['clash.txt'] },
  }
}

function mountPanel() {
  return mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
}

const CONFLICT_ERROR = {
  response: {
    data: {
      error: {
        code: 'base_untracked_conflict',
        message: 'the merge is blocked by uncommitted new files in the base checkout',
        details: { files: ['clash.txt'] },
      },
    },
  },
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: { ok: true, status: baseStatus() } })
})

describe('GitStatusPanel × base_untracked_conflict auto-retry (0350 T0004)', () => {
  it('parks the finalize on 409, then resumes it once the selective commit clears the blocked file', async () => {
    let blocked = ['clash.txt']
    postRequest.mockImplementation(async (url: string, body: any) => {
      if (url.includes('/git/finalize')) {
        if (blocked.length) return Promise.reject(CONFLICT_ERROR)
        return { data: { ok: true, result: { status: 'merged', pushed: true, merge_commit: 'abc1234' } } }
      }
      if (url.includes('/git/base-commit')) {
        blocked = blocked.filter((f) => !(body.paths || []).includes(f))
        return { data: { ok: true, result: { committed: true, files: body.paths, remaining: [], remaining_untracked: blocked } } }
      }
      throw new Error('unexpected POST ' + url)
    })

    const wrapper = mountPanel()
    await flushPromises()

    await (wrapper.vm as any).runFinalize('flowgate.default.0350', { action: 'merge_only' })
    await flushPromises()

    // parked with the blocked file remembered, and the checkbox pre-selected
    expect((wrapper.vm as any).pendingFinalize).toMatchObject({
      groupId: 'flowgate.default.0350',
      payload: { action: 'merge_only' },
      blockedFiles: ['clash.txt'],
    })
    expect((wrapper.vm as any).untrackedPicked).toEqual(['clash.txt'])

    // operator commits the selected file (the existing selective-commit UI)
    await (wrapper.vm as any).doCommitUntracked()
    await flushPromises()

    const finalizeCalls = postRequest.mock.calls.filter(([u]) => String(u).includes('/git/finalize'))
    expect(finalizeCalls).toHaveLength(2)   // original attempt + the automatic retry
    expect((wrapper.vm as any).pendingFinalize).toBeNull()
    wrapper.unmount()
  })

  it('parks the finalize on 409, then resumes it once delete clears the blocked file', async () => {
    let blocked = ['clash.txt']
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    postRequest.mockImplementation(async (url: string, body: any) => {
      if (url.includes('/git/finalize')) {
        if (blocked.length) return Promise.reject(CONFLICT_ERROR)
        return { data: { ok: true, result: { status: 'merged', pushed: false, merge_commit: 'def5678' } } }
      }
      if (url.includes('/git/base-remove')) {
        blocked = blocked.filter((f) => !(body.files || []).includes(f))
        return { data: { ok: true, result: { results: [{ path: 'clash.txt', result: 'removed' }], remaining_untracked: blocked } } }
      }
      throw new Error('unexpected POST ' + url)
    })

    const wrapper = mountPanel()
    await flushPromises()

    await (wrapper.vm as any).runFinalize('flowgate.default.0350', { action: 'merge_only' })
    await flushPromises()
    expect((wrapper.vm as any).untrackedPicked).toEqual(['clash.txt'])

    await (wrapper.vm as any).doRemoveUntracked()
    await flushPromises()

    const removeCalls = postRequest.mock.calls.filter(([u]) => String(u).includes('/git/base-remove'))
    expect(removeCalls).toHaveLength(1)
    const finalizeCalls = postRequest.mock.calls.filter(([u]) => String(u).includes('/git/finalize'))
    expect(finalizeCalls).toHaveLength(2)
    expect((wrapper.vm as any).pendingFinalize).toBeNull()
    wrapper.unmount()
  })

  it('never opens the tracked base_dirty banner for an untracked-only park', async () => {
    postRequest.mockRejectedValue(CONFLICT_ERROR)
    const wrapper = mountPanel()
    await flushPromises()

    await (wrapper.vm as any).runFinalize('flowgate.default.0350', { action: 'merge_only' })
    await flushPromises()

    // showBaseDirtySection must stay false — that alert's copy/buttons assume a
    // base_dirty park, not an untracked-conflict one (0350 T0004 guard).
    expect((wrapper.vm as any).showBaseDirtySection).toBe(false)
    wrapper.unmount()
  })
})
