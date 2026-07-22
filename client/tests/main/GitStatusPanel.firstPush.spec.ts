// flowgate.default.0297 B0001 / NR0003 — the FIRST push had no entry point.
// Against a brand-new (empty) remote there is no refs/remotes/origin/{base}, so
// the server reports ahead/behind as null and the unpushed walk as unmeasured
// (commit_count 0). The push button — the only one in the whole client — was
// gated on that count, so it disappeared exactly when the first push was due.
// The panel now reads the bootstrap fields (measured / remote_branch_missing /
// local_commit_count) instead of inferring "in sync" from a zero.
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

// The exact payload project_git_status returns while origin/{base} is absent.
function bootstrapStatus(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    base_branch: 'main',
    base_path_state: 'checkout',
    ahead_count: null,
    behind_count: null,
    slots: [],
    pending: [],
    pending_count: 0,
    unpushed: {
      count: 0,
      commit_count: 0,
      merges: [],
      measured: false,
      remote_branch_missing: true,
      local_commit_count: 1,
      ...overrides,
    },
  }
}

function mountPanel() {
  return mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
}

function pushButton(wrapper: ReturnType<typeof mountPanel>) {
  return wrapper.findAll('.card-hd .btn-primary')
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('GitStatusPanel × first push against an empty remote', () => {
  it('offers the first push when the remote has no base branch but local commits exist', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, status: bootstrapStatus() } })
    postRequest.mockResolvedValue({ data: { ok: true, result: { pushed: true, branch: 'main' } } })

    const wrapper = mountPanel()
    await flushPromises()

    const btn = pushButton(wrapper)
    expect(btn).toHaveLength(1)
    expect(btn[0].text()).toContain('First push')
    // "fetch needed" would be a dead end here — there is nothing to fetch.
    expect(wrapper.find('.git-ab-meta').text()).toContain('first push')
    expect(wrapper.find('.git-unpushed-badge').text()).toContain('1')

    await btn[0].trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/projects/flowgate/git/push',
      { branch: 'main' },
    )

    wrapper.unmount()
  })

  it('stays hidden when unmeasured for any other reason', async () => {
    // git off / base checkout missing: unmeasured, but nothing to publish.
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        status: bootstrapStatus({ remote_branch_missing: false, local_commit_count: null }),
      },
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(pushButton(wrapper)).toHaveLength(0)
    expect(wrapper.find('.git-ab-meta').text()).toContain('fetch needed')
    wrapper.unmount()
  })

  it('stays hidden when the remote is empty and there is nothing committed yet', async () => {
    getRequest.mockResolvedValue({
      data: { ok: true, status: bootstrapStatus({ local_commit_count: 0 }) },
    })

    const wrapper = mountPanel()
    await flushPromises()

    expect(pushButton(wrapper)).toHaveLength(0)
    wrapper.unmount()
  })

  it('keeps the ordinary label once the remote branch exists', async () => {
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        status: {
          ...bootstrapStatus(),
          ahead_count: 2,
          behind_count: 0,
          unpushed: {
            count: 1,
            commit_count: 2,
            merges: [],
            measured: true,
            remote_branch_missing: false,
            local_commit_count: null,
          },
        },
      },
    })

    const wrapper = mountPanel()
    await flushPromises()

    const btn = pushButton(wrapper)
    expect(btn).toHaveLength(1)
    expect(btn[0].text()).toContain('Push all')
    wrapper.unmount()
  })
})
