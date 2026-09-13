/**
 * flowgate.default.0548 T0004 — a TR approval that could not commit because the
 * group has no git (project git off / no worktree) must not show a Git-failure
 * warning when the TR itself declared no source changes. The server now decides
 * this (tr_commit.quiet) instead of ReviewActionBar re-deriving it from
 * skipped_reason strings — see server/modules/flow_gate/services/tr_commit_service.py.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'

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

const GIT_STATE = {
  data: { ok: true, state: { branch: null, status: 'none', default_action: null, choices: [] } },
}

// A group that DID work: the server answers the AC preview with a real gate.
const GIT_STATE_WITH_WORK = {
  data: {
    ok: true,
    state: {
      branch: 'test_p_0001', status: 'awaiting_choice', default_action: 'merge',
      choices: ['merge', 'wait'], preview: true,
    },
  },
}

function mountBar(overrides: Record<string, unknown> = {}) {
  return mount(ReviewActionBar, {
    props: {
      docId: 'test.p.0001.0009-TR',
      projectId: 'test-project',
      groupId: 'test.p.0001',
      docRef: 'test.p.0001.0009-TR',
      docType: 'TR',
      reviewStatus: 'pending_review',
      mode: 'review' as const,
      ...overrides,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockResolvedValue(GIT_STATE)
})

describe('ReviewActionBar × tr_commit.quiet (0548 T0004)', () => {
  it('a no-work group with git_inactive stays quiet — no toast at all', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        tr_commit: {
          committed: false, commit: null, subject: null,
          skipped_reason: 'git_inactive', quiet: true,
          excluded_artifact_count: 0, excluded_artifacts: [],
          reported_diff: { unreported: [], missing: [] },
        },
      },
    })
    const wrapper = mountBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(showToast).not.toHaveBeenCalled()
    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    wrapper.unmount()
  })

  it('no_changes stays quiet — no success toast either', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        tr_commit: {
          committed: false, commit: null, subject: null,
          skipped_reason: 'no_changes', quiet: true,
          excluded_artifact_count: 0, excluded_artifacts: [],
          reported_diff: { unreported: [], missing: [] },
        },
      },
    })
    const wrapper = mountBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(showToast).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('a real-work group with git_inactive still warns (quiet=false)', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        tr_commit: {
          committed: false, commit: null, subject: null,
          skipped_reason: 'git_inactive', quiet: false,
          excluded_artifact_count: 0, excluded_artifacts: [],
          reported_diff: { unreported: [], missing: [] },
        },
      },
    })
    const wrapper = mountBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    const expected = i18n.global.t('main.review_action_bar.tr_commit_failed_toast', {
      reason: i18n.global.t('main.git_status.tr_commits.reason_git_inactive'),
    })
    expect(showToast).toHaveBeenCalledWith(expected, 'warning')
    wrapper.unmount()
  })

  it('a real commit success still shows the success toast', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        tr_commit: {
          committed: true, commit: 'a1b2c3d', subject: 'chore: approve 0009-TR',
          skipped_reason: null, quiet: false,
          excluded_artifact_count: 0, excluded_artifacts: [],
          reported_diff: { unreported: [], missing: [] },
        },
      },
    })
    const wrapper = mountBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    const expected = i18n.global.t('main.review_action_bar.tr_commit_toast', { commit: 'a1b2c3d' })
    expect(showToast).toHaveBeenCalledWith(expected, 'success')
    wrapper.unmount()
  })
})

/**
 * The rest of the reviewer's rejection of revision 4:
 *
 *   머지할게 없는데 … 경고 토스트가 그대로 뜬다 / 깃 다이얼로그도 그대로 뜬다 /
 *   머지 다이얼로그가 그대로 뜬다
 *
 * All three come off the AC final-approval path, not the TR commit path: the
 * approval dialog offered a merge/push choice to a group with no work, that stale
 * choice rode along into an approval whose finalize could only fail, and the
 * failure became a warning toast plus an auto-opened Git panel.
 */
describe('ReviewActionBar × AC finalize on a group with nothing to merge (0548 T0004)', () => {
  function acBar() {
    return mountBar({
      docId: 'test.p.0001.0010-AC',
      docRef: 'test.p.0001.0010-AC',
      docType: 'AC',
    })
  }

  it('offers no merge/push choice block when the server says there is nothing to merge', async () => {
    const wrapper = acBar()
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(
      '/api/v1/groups/test.p.0001/git/finalize?context=approval',
    )
    expect((wrapper.vm as any).showGitFinalizeBlock).toBe(false)
    wrapper.unmount()
  })

  it('still offers the choice block when the group really did work', async () => {
    getRequest.mockResolvedValue(GIT_STATE_WITH_WORK)
    const wrapper = acBar()
    await flushPromises()

    expect((wrapper.vm as any).showGitFinalizeBlock).toBe(true)
    wrapper.unmount()
  })

  it('a quiet git no-op shows no toast and never opens the Git panel', async () => {
    // The exact race the reviewer hit: the preview still offered a choice, but by
    // the time the ride-along ran, the approval's own no-work auto-discard had
    // already taken the slot down. The server now reports that quietly.
    getRequest.mockResolvedValue(GIT_STATE_WITH_WORK)
    const events: string[] = []
    const listener = (e: Event) => events.push(e.type)
    window.addEventListener('fg:git_status_open', listener)
    window.addEventListener('fg:git_status_refresh', listener)
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        git: {
          ok: false, quiet: true,
          error: { code: 'invalid_state', message: "Git integration is not active for group 'test.p.0001'" },
        },
      },
    })
    const wrapper = acBar()
    await flushPromises()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(showToast).not.toHaveBeenCalled()
    expect(events).toEqual(['fg:git_status_refresh'])
    window.removeEventListener('fg:git_status_open', listener)
    window.removeEventListener('fg:git_status_refresh', listener)
    wrapper.unmount()
  })

  it('a real git failure still warns and still opens the Git panel', async () => {
    const events: string[] = []
    const listener = (e: Event) => events.push(e.type)
    window.addEventListener('fg:git_status_open', listener)
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        git: { ok: false, error: { code: 'git_busy', message: '다른 git 작업이 진행 중입니다' } },
      },
    })
    const wrapper = acBar()
    await flushPromises()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith('다른 git 작업이 진행 중입니다', 'warning')
    expect(events).toEqual(['fg:git_status_open'])
    window.removeEventListener('fg:git_status_open', listener)
    wrapper.unmount()
  })
})
