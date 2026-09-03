// flowgate.default.0212 T0009 — the header Git status panel must open the
// SHARED resolver dialog (GitConflictResolverDialog, 0207 시안 A) instead of
// the removed compact in-house overlay.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'

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

const CONFLICT_CONTENT = [
  'head',
  '<<<<<<< HEAD',
  'ours line',
  '=======',
  'theirs line',
  '>>>>>>> main',
  'tail',
].join('\n')

function gitStatus() {
  return {
    enabled: true,
    base_branch: 'main',
    base_path_state: 'ready',
    ahead_count: 0,
    behind_count: 0,
    slots: [],
    pending: [
      {
        group_id: 'flowgate.default.0212',
        branch: 'group/0212',
        status: 'conflict',
        default_action: 'merge',
        merge_id: 7,
      },
    ],
    pending_count: 1,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('GitStatusPanel × shared resolver dialog', () => {
  it('opens GitConflictResolverDialog with parsed files when [resolve] is clicked', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/status')) {
        return Promise.resolve({ data: { ok: true, status: gitStatus() } })
      }
      if (url.includes('/git/merge/7/conflicts')) {
        return Promise.resolve({
          data: {
            ok: true,
            files: [{ path: 'shared.txt', content: CONFLICT_CONTENT, conflict_count: 1 }],
          },
        })
      }
      return Promise.reject(new Error('unexpected ' + url))
    })

    const wrapper = mount(GitStatusPanel, {
      props: { projectId: 'flowgate' },
      global: {
        plugins: [i18n],
        stubs: { AppIcon: true, GitConflictResolverDialog: true },
      },
    })
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)

    await wrapper.find('.btn-danger-ol').trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    expect(dialog.exists()).toBe(true)
    expect(dialog.props('loadStatus')).toBe('ready')
    expect(dialog.props('branch')).toBe('group/0212')
    expect(dialog.props('baseBranch')).toBe('main')
    const files = dialog.props('files') as Array<{ path: string; mode: string }>
    expect(files).toHaveLength(1)
    expect(files[0].path).toBe('shared.txt')
    expect(files[0].mode).toBe('chunk')

    // closing drops the dialog and its state.
    dialog.vm.$emit('close')
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)

    wrapper.unmount()
  })

  it('submits resolved content through the same resolve endpoint on dialog submit', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/git/status')) {
        return Promise.resolve({ data: { ok: true, status: gitStatus() } })
      }
      if (url.includes('/git/merge/7/conflicts')) {
        return Promise.resolve({
          data: {
            ok: true,
            files: [{ path: 'shared.txt', content: CONFLICT_CONTENT, conflict_count: 1 }],
          },
        })
      }
      return Promise.reject(new Error('unexpected ' + url))
    })
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'merged', merge_commit: 'abc' } } })

    const wrapper = mount(GitStatusPanel, {
      props: { projectId: 'flowgate' },
      global: {
        plugins: [i18n],
        stubs: { AppIcon: true, GitConflictResolverDialog: true },
      },
    })
    await flushPromises()
    await wrapper.find('.btn-danger-ol').trigger('click')
    await flushPromises()

    const dialog = wrapper.findComponent(GitConflictResolverDialog)
    const files = dialog.props('files') as any[]

    // marker guard: an unresolved submit must never reach the backend.
    // (T0011: mount already fired the once-per-project auto /git/cleanup call, so the guard
    // is checked against the resolve endpoint specifically, not "postRequest never called".)
    const resolveCalls = () => postRequest.mock.calls.filter(([url]) => String(url).includes('/git/merge/7/resolve'))
    dialog.vm.$emit('submit')
    await flushPromises()
    expect(resolveCalls()).toHaveLength(0)

    // resolve the single chunk (choose ours), then submit passes the guard.
    const chunk = files[0].segments.find((s: any) => s.kind === 'chunk')
    chunk.choice = 'ours'
    chunk.resolution = [...chunk.ours]
    dialog.vm.$emit('submit')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/groups/flowgate.default.0212/git/merge/7/resolve',
      expect.objectContaining({
        complete: true,
        files: [expect.objectContaining({ path: 'shared.txt' })],
      }),
    )
    const posted = resolveCalls()[0][1] as { files: Array<{ content: string }> }
    expect(posted.files[0].content).not.toContain('<<<<<<<')
    expect(posted.files[0].content).toContain('ours line')

    wrapper.unmount()
  })
})
