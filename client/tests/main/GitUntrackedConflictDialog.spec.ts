/**
 * flowgate.default.0350 T0004 — unit coverage for the base_untracked_conflict
 * dialog itself: commit/delete both resolve 'proceed' once every blocked path
 * is gone, a partial clear keeps the dialog open on the leftover set (never
 * silently decides for the operator), and cancel resolves 'cancel' with no
 * request sent.
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitUntrackedConflictDialog from '@main/components/GitUntrackedConflictDialog.vue'

const { postRequest } = vi.hoisted(() => ({ postRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn(),
  postRequest,
}))

function mountDialog() {
  return mount(GitUntrackedConflictDialog, {
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  postRequest.mockReset()
})

describe('GitUntrackedConflictDialog', () => {
  it('resolves proceed once commit clears every blocked path', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { remaining_untracked: [] } } })
    const wrapper = mountDialog()

    const outcome = (wrapper.vm as any).resolve('flowgate', ['a.txt', 'b.txt'])
    await Promise.resolve()
    await (wrapper.vm as any).choose('commit')

    expect(await outcome).toBe('proceed')
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/base-commit',
      expect.objectContaining({ paths: ['a.txt', 'b.txt'] }),
    )
  })

  it('sends files for a group-scoped commit recovery', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { remaining_untracked: [] } } })
    const wrapper = mountDialog()

    const outcome = (wrapper.vm as any).resolve('flowgate.default.0483', ['a.txt'], 'group')
    await Promise.resolve()
    await (wrapper.vm as any).choose('commit')

    expect(await outcome).toBe('proceed')
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/groups/flowgate.default.0483/git/untracked-commit',
      expect.objectContaining({ files: ['a.txt'] }),
    )
    expect(postRequest.mock.calls[0][1]).not.toHaveProperty('paths')
  })

  it('resolves proceed once delete clears every blocked path', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { remaining_untracked: [] } } })
    const wrapper = mountDialog()

    const outcome = (wrapper.vm as any).resolve('flowgate', ['a.txt'])
    await Promise.resolve()
    await (wrapper.vm as any).choose('remove')

    expect(await outcome).toBe('proceed')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects/flowgate/git/base-remove', { files: ['a.txt'] })
  })

  it('keeps the dialog open on a partial clear instead of deciding for the operator', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { remaining_untracked: ['b.txt'] } } })
    const wrapper = mountDialog()

    const outcome = (wrapper.vm as any).resolve('flowgate', ['a.txt', 'b.txt'])
    await Promise.resolve()
    await (wrapper.vm as any).choose('commit')

    expect((wrapper.vm as any).open).toBe(true)
    expect((wrapper.vm as any).files).toEqual(['b.txt'])

    // now clears the rest
    postRequest.mockResolvedValue({ data: { ok: true, result: { remaining_untracked: [] } } })
    await (wrapper.vm as any).choose('remove')
    expect(await outcome).toBe('proceed')
  })

  it('recovers mixed group blockers with action-specific subsets', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, result: { remaining_untracked: [] } } })
    const wrapper = mountDialog()

    const outcome = (wrapper.vm as any).resolve(
      'flowgate.default.0483',
      ['untracked.txt', 'tracked.txt'],
      'group',
      { untrackedFiles: ['untracked.txt'], trackedFiles: ['tracked.txt'] },
    )
    await Promise.resolve()

    await (wrapper.vm as any).choose('commit')
    expect(postRequest).toHaveBeenNthCalledWith(
      1,
      '/api/v1/groups/flowgate.default.0483/git/untracked-commit',
      expect.objectContaining({ files: ['untracked.txt'] }),
    )
    expect((wrapper.vm as any).open).toBe(true)
    expect((wrapper.vm as any).files).toEqual(['tracked.txt'])

    await (wrapper.vm as any).choose('revert')
    expect(postRequest).toHaveBeenNthCalledWith(
      2,
      '/api/v1/groups/flowgate.default.0483/git/untracked-revert',
      { files: ['tracked.txt'] },
    )
    expect(await outcome).toBe('proceed')
  })

  it('resolves cancel without sending any request', async () => {
    const wrapper = mountDialog()
    const outcome = (wrapper.vm as any).resolve('flowgate', ['a.txt'])
    await Promise.resolve()
    ;(wrapper.vm as any).cancel()

    expect(await outcome).toBe('cancel')
    expect(postRequest).not.toHaveBeenCalled()
  })
})
