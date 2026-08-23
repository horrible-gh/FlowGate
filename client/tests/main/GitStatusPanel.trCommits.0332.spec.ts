// 0332 T#1 — Git 상태 패널의 "이 그룹의 커밋" (D0005 §6.2).
//
// 한 그룹의 커밋이 이제 여럿이라 슬롯 행 하나로는 말할 수 없다. 여기서 고정하는 것은:
//   1. 개수 배지는 접힌 상태에서도 늘 보이고, 목록만 접힌다.
//   2. 커밋이 한 줄도 없는 그룹에는 배지 자체가 없다(패널은 이 기능 전과 같다).
//   3. "소스 변경 없음"과 "커밋하지 못했습니다"를 한 문구로 뭉치지 않는다(K3).
//   4. 상한을 넘은 줄은 "N개 더"로 말한다 — 조용히 잘리지 않는다.
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

function statusWith(trCommits: unknown) {
  return {
    enabled: true,
    base_branch: 'main',
    base_path_state: 'checkout',
    ahead_count: 0,
    behind_count: 0,
    slots: [{
      group_id: 'flowgate.default.0332',
      branch: 'flowgate_default_0332',
      status: 'none',
      merge_id: null,
      tr_commits: trCommits,
    }],
    pending: [],
    pending_count: 0,
    unpushed: { count: 0, commit_count: 0, merges: [], measured: true },
  }
}

const LIVE_ROW = {
  doc_id: 'flowgate.default.0332.0009-TR',
  doc_code: '0009-TR',
  state: 'live',
  commit: 'a1b2c3d',
  subject: '0009-TR: 커밋 포인트 생성',
  skipped_reason: null,
  cancel_commit: null,
}

function mountPanel() {
  return mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('GitStatusPanel × 이 그룹의 커밋', () => {
  it('커밋이 한 줄도 없으면 배지도 목록도 없다', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, status: statusWith(
      { live: 0, canceled: 0, no_commit: 0, commits: [], more: 0 },
    ) } })

    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.findAll('.git-trc-badge')).toHaveLength(0)
    expect(wrapper.findAll('.git-trc-list')).toHaveLength(0)
    // 슬롯 행 자체는 그대로다.
    expect(wrapper.findAll('.git-status-slot')).toHaveLength(1)
  })

  it('개수 배지는 접힌 상태에서도 보이고, 누르면 목록이 펼쳐진다', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, status: statusWith(
      { live: 1, canceled: 1, no_commit: 0, commits: [LIVE_ROW, {
        ...LIVE_ROW, doc_id: 'x', doc_code: '0006-TR', state: 'canceled', cancel_commit: 'f7a1c02',
      }], more: 0 },
    ) } })

    const wrapper = mountPanel()
    await flushPromises()

    const badge = wrapper.find('.git-trc-badge')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toContain('1')
    expect(wrapper.findAll('.git-trc-list')).toHaveLength(0)   // 기본 접힘

    await badge.trigger('click')

    const rows = wrapper.findAll('.git-trc-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('0009-TR')
    expect(rows[0].text()).toContain('a1b2c3d')
    expect(rows[1].classes()).toContain('is-canceled')
    expect(rows[1].text()).toContain('f7a1c02')
  })

  it('소스를 안 바꾼 승인과 커밋하지 못한 승인을 다르게 말한다', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, status: statusWith(
      { live: 0, canceled: 0, no_commit: 2, commits: [
        { ...LIVE_ROW, state: 'no_commit', commit: null, subject: null, skipped_reason: 'no_changes' },
        { ...LIVE_ROW, doc_id: 'y', doc_code: '0011-TR', state: 'no_commit', commit: null,
          subject: null, skipped_reason: 'git_busy' },
      ], more: 0 },
    ) } })

    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('.git-trc-badge').trigger('click')

    const rows = wrapper.findAll('.git-trc-row')
    expect(rows[0].classes()).toContain('is-quiet')
    expect(rows[0].text()).toContain(i18n.global.t('main.git_status.tr_commits.no_source_change'))
    // 커밋을 시도조차 못 한 줄은 사유까지 말한다 — 사람이 손볼 거리다.
    expect(rows[1].classes()).toContain('is-warn')
    expect(rows[1].text()).toContain(i18n.global.t('main.git_status.tr_commits.reason_git_busy'))
  })

  it('접힌 나머지는 "N개 더"로 말한다 — 조용히 잘리지 않는다', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, status: statusWith(
      { live: 24, canceled: 0, no_commit: 0, commits: [LIVE_ROW], more: 4 },
    ) } })

    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('.git-trc-badge').trigger('click')

    expect(wrapper.find('.git-trc-more').text()).toContain('4')
  })
})
