// 0332 T0018 K11 — Git 상태 패널의 "소스 되살리기" 절반 (T0018 §3-6).
//
// 여기서 고정하는 것은 넷이다.
//   1. 되살린 커밋은 처음 승인이 남긴 커밋과 구분돼 보인다. 한 단계에 live 줄이 둘
//      생기는데 표식이 없으면 같은 줄이 두 번 있는 것으로 보인다.
//   2. 되살릴 것이 남았고 마지막 차단이 다시 눌러 볼 만한 것일 때만 단추가 나온다.
//   3. 충돌처럼 눌러도 같은 답이 나오는 상태에는 단추를 주지 않는다 — 사유만 남는다.
//   4. 단추는 되살리기 전용 경로를 부르고, 끝나면 패널을 다시 읽는다.
//
// 부재 단언에는 대조군을 붙였다. "단추가 없다"만 단언하면 선택자가 틀려도 초록이다
// (negative-ui-assertion-needs-a-positive-control).
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'

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

const GROUP = 'flowgate.default.0332'
const TR_DOC = 'flowgate.default.0332.0009-TR'

function statusWith(trCommits: unknown) {
  return {
    enabled: true,
    base_branch: 'main',
    base_path_state: 'checkout',
    ahead_count: 0,
    behind_count: 0,
    slots: [{
      group_id: GROUP,
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

const CANCELED_ROW = {
  doc_id: TR_DOC,
  doc_code: '0009-TR',
  state: 'canceled',
  commit: 'a1b2c3d',
  subject: '0009-TR: 커밋 포인트 생성',
  skipped_reason: null,
  cancel_commit: 'f7a1c02',
  restored: false,
}

const RESTORED_ROW = {
  ...CANCELED_ROW,
  state: 'live',
  commit: '9e0d4b7',
  cancel_commit: null,
  restored: true,
}

const FRESH_ROW = { ...RESTORED_ROW, commit: 'c0ffee1', restored: false }

function commits(rows: unknown[], extra: Record<string, unknown> = {}) {
  return {
    live: 0, canceled: 1, no_commit: 0, commits: rows, more: 0,
    reapply_pending: false, last_block: null,
    ...extra,
  }
}

function mountPanel() {
  return mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
}

async function openList(status: unknown) {
  getRequest.mockResolvedValue({ data: { ok: true, status } })
  const wrapper = mountPanel()
  await flushPromises()
  await wrapper.find('.git-trc-badge').trigger('click')
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
})

describe('GitStatusPanel × 소스 되살리기 (T0018 K11)', () => {
  it('되살린 커밋은 처음 승인이 남긴 커밋과 구분돼 보인다', async () => {
    const wrapper = await openList(statusWith(
      commits([RESTORED_ROW, FRESH_ROW, CANCELED_ROW], { live: 2 }),
    ))

    const rows = wrapper.findAll('.git-trc-row')
    expect(rows).toHaveLength(3)
    // 대조군: 되살린 줄에는 표식이 있고, 바로 옆 평범한 live 줄에는 없다.
    expect(rows[0].find('.git-trc-restored').exists()).toBe(true)
    expect(rows[0].text()).toContain(i18n.global.t('main.git_status.tr_commits.restored'))
    expect(rows[1].find('.git-trc-restored').exists()).toBe(false)
    // 취소된 줄에도 붙지 않는다 — 되살림은 live 인 줄의 이야기다.
    expect(rows[2].find('.git-trc-restored').exists()).toBe(false)
  })

  it('되살릴 것이 남고 마지막 차단이 재시도 가능하면 단추가 나온다', async () => {
    const wrapper = await openList(statusWith(commits([CANCELED_ROW], {
      reapply_pending: true,
      last_block: { reason: 'dirty_worktree', sub: 'dirty_worktree', at: null, retryable: true },
    })))

    const block = wrapper.find('.git-trc-reapply')
    expect(block.exists()).toBe(true)
    expect(block.find('.git-trc-reapply-btn').exists()).toBe(true)
    expect(block.text()).toContain(
      i18n.global.t('main.git_status.tr_commits.reapply_btn'),
    )
    // 왜 아직 안 돌아왔는지도 같은 자리에서 말한다.
    expect(block.text()).toContain(
      i18n.global.t('main.git_status.tr_commits.block_dirty_worktree'),
    )
  })

  it('되살릴 것이 없으면 단추가 없다 — 목록은 그대로 그려진다', async () => {
    const wrapper = await openList(statusWith(commits([CANCELED_ROW], {
      reapply_pending: false,
      last_block: { reason: 'dirty_worktree', sub: 'dirty_worktree', at: null, retryable: true },
    })))

    // 대조군: 줄 자체는 보인다. 선택자가 틀려서 초록인 것이 아니다.
    expect(wrapper.findAll('.git-trc-row')).toHaveLength(1)
    expect(wrapper.findAll('.git-trc-reapply')).toHaveLength(0)
  })

  it('충돌처럼 재시도해도 같은 답이 나오는 상태에는 단추를 주지 않는다', async () => {
    const wrapper = await openList(statusWith(commits([CANCELED_ROW], {
      reapply_pending: true,
      last_block: { reason: 'already_merged', sub: 'already_merged', at: null, retryable: false },
    })))

    expect(wrapper.findAll('.git-trc-row')).toHaveLength(1)      // 대조군
    expect(wrapper.findAll('.git-trc-reapply-btn')).toHaveLength(0)
  })

  it('차단 이력이 아예 없으면 단추를 먼저 들이밀지 않는다', async () => {
    // 되감기만 하고 아직 앞으로 오지 않은 상태. 되살릴 행은 있지만 사람이 아직
    // 되살려 달라고 한 적이 없다.
    const wrapper = await openList(statusWith(commits([CANCELED_ROW], {
      reapply_pending: true, last_block: null,
    })))

    expect(wrapper.findAll('.git-trc-row')).toHaveLength(1)      // 대조군
    expect(wrapper.findAll('.git-trc-reapply')).toHaveLength(0)
  })

  it('단추는 되살리기 전용 경로를 부르고, 끝나면 패널을 다시 읽는다', async () => {
    const wrapper = await openList(statusWith(commits([CANCELED_ROW], {
      reapply_pending: true,
      last_block: { reason: 'dirty_worktree', sub: 'dirty_worktree', at: null, retryable: true },
    })))
    const readsBefore = getRequest.mock.calls.length
    postRequest.mockResolvedValue({
      data: {
        ok: true,
        tr_commit_restore: {
          attempted: true, blocked_reason: null, stopped_reason: null, retryable: false,
          reapplied: [{ doc_id: TR_DOC, doc_code: '0009-TR', commit: 'a1b2c3d',
                        cancel_commit: 'f7a1c02', reapply_commit: '9e0d4b7' }],
          skipped: [],
        },
      },
    })

    await wrapper.find('.git-trc-reapply-btn').trigger('click')
    await flushPromises()

    const [url, body] = postRequest.mock.calls[0]
    expect(url).toBe(
      `/api/v1/documents/workflow/${encodeURIComponent(TR_DOC)}/return-point/reapply-commits`,
    )
    expect(body).toEqual({})
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_status.tr_commits.reapply_done', { n: 1 }),
      'success',
    )
    // 원장이 움직였으므로 목록과 배지를 다시 읽는다.
    expect(getRequest.mock.calls.length).toBeGreaterThan(readsBefore)
  })

  it('되살리기가 또 막히면 사유를 그대로 말한다 — 성공처럼 보이지 않는다', async () => {
    const wrapper = await openList(statusWith(commits([CANCELED_ROW], {
      reapply_pending: true,
      last_block: { reason: 'git_busy', sub: 'lock_timeout', at: null, retryable: true },
    })))
    postRequest.mockResolvedValue({
      data: {
        ok: true,
        tr_commit_restore: {
          attempted: false, blocked_reason: 'dirty_worktree', stopped_reason: null,
          retryable: true, reapplied: [], skipped: [],
        },
      },
    })

    await wrapper.find('.git-trc-reapply-btn').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_status.tr_commits.reapply_failed', {
        reason: i18n.global.t('main.git_status.tr_commits.block_dirty_worktree'),
      }),
      'danger',
    )
  })

  it('되살릴 소스가 없었으면 실패가 아니라 그렇게 말한다', async () => {
    const wrapper = await openList(statusWith(commits([CANCELED_ROW], {
      reapply_pending: true,
      last_block: { reason: 'git_busy', sub: 'lock_timeout', at: null, retryable: true },
    })))
    postRequest.mockResolvedValue({
      data: {
        ok: true,
        tr_commit_restore: {
          attempted: true, blocked_reason: null, stopped_reason: null, retryable: false,
          reapplied: [], skipped: [],
        },
      },
    })

    await wrapper.find('.git-trc-reapply-btn').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_status.tr_commits.reapply_none'),
      'warning',
    )
  })

  it('이 기능 이전 서버(새 키가 없는 응답)에서는 아무것도 늘지 않는다', async () => {
    const wrapper = await openList(statusWith({
      live: 1, canceled: 0, no_commit: 0, commits: [FRESH_ROW], more: 0,
    }))

    expect(wrapper.findAll('.git-trc-row')).toHaveLength(1)      // 대조군
    expect(wrapper.findAll('.git-trc-reapply')).toHaveLength(0)
    expect(wrapper.findAll('.git-trc-restored')).toHaveLength(0)
  })
})
