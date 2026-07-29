/**
 * flowgate.default.0350 T0004 (NR0003 §7 item 7 / §1 발견 3): before this fix,
 * a `base_untracked_conflict` git post-step failure on approve fell through to
 * ReviewActionBar's generic branch and rendered the server's raw English string
 * verbatim ("the merge is blocked by uncommitted new files in the base
 * checkout; commit or remove them, then retry"). It now gets the same
 * treatment as `base_dirty`: a localized toast naming the blocked paths.
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

const RAW_SERVER_MESSAGE =
  'the merge is blocked by uncommitted new files in the base checkout; commit or remove them, then retry'

function mountBar() {
  return mount(ReviewActionBar, {
    props: {
      docId: 'test.p.0001.0003-AC',
      projectId: 'test-project',
      groupId: 'test.p.0001',
      docRef: 'test.p.0001.0003-AC',
      docType: 'AC',
      reviewStatus: 'pending_review',
      mode: 'review' as const,
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

describe('ReviewActionBar × base_untracked_conflict (0350 T0004)', () => {
  it('shows the localized blocked-file guidance, never the raw server string', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        git: {
          ok: false,
          error: {
            code: 'base_untracked_conflict',
            message: RAW_SERVER_MESSAGE,
            details: { files: ['clash.txt'] },
          },
        },
      },
    })
    const wrapper = mountBar()

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    const expected = i18n.global.t('main.git_status.base_untracked_conflict_toast', {
      files: 'clash.txt',
    })
    expect(showToast).toHaveBeenCalledWith(expected, 'warning')
    expect(showToast).not.toHaveBeenCalledWith(RAW_SERVER_MESSAGE, expect.anything())
    // approval itself still stood — only the git post-step failed.
    expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    wrapper.unmount()
  })

  it('opens the header Git panel (same hand-off as base_dirty) so the operator can resolve it', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        git: {
          ok: false,
          error: { code: 'base_untracked_conflict', message: RAW_SERVER_MESSAGE, details: { files: ['clash.txt'] } },
        },
      },
    })
    const wrapper = mountBar()
    const openSpy = vi.fn()
    window.addEventListener('fg:git_status_open', openSpy)

    await (wrapper.vm as any).doApprove()
    await flushPromises()

    expect(openSpy).toHaveBeenCalledTimes(1)
    window.removeEventListener('fg:git_status_open', openSpy)
    wrapper.unmount()
  })
})
