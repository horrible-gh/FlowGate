// 0332 TR0019 — Git 상태 패널의 "붙들린 충돌" 절반.
//
// 되돌리기/되살리기가 충돌하면 예전에는 워크트리를 되돌려 버리고 끝이었다. 지금은
// 충돌이 세션으로 남고, 병합 충돌이 쓰던 편집기·AI·중단이 그대로 이 세션을 연다.
// 화면에서 고정할 것은 다섯이다.
//
//   1. 붙들린 충돌은 **접기 밖**에 보인다. 커밋 목록을 펼치지 않아도 보여야 한다 —
//      이건 목록의 한 줄이 아니라 그룹이 멈춰 있는 이유다.
//   2. 방향(되돌리기/되살리기)을 구분해 말한다. 사람이 다음에 할 일이 다르다.
//   3. 커밋 단추는 **검토 대기(resolved)** 일 때만 나온다. 표식이 남아 있는 동안은
//      해결하러 들어가는 문과 중단만 있다.
//   4. 커밋과 중단은 각자의 경로를 부르고, 끝나면 패널을 다시 읽는다.
//   5. 해결 제출이 `resolved_pending_review` 로 돌아오면 "병합됐다"가 아니라
//      "확인하고 커밋하라"고 말한다 — 이게 이 기능의 안전장치다.
//
// 부재 단언에는 전부 대조군을 붙였다(negative-ui-assertion-needs-a-positive-control).
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
const MERGE_ID = 42

const CANCELED_ROW = {
  doc_id: TR_DOC,
  doc_code: '0009-TR',
  state: 'live',
  commit: 'a1b2c3d',
  subject: '0009-TR: 커밋 포인트 생성',
  skipped_reason: null,
  cancel_commit: null,
  restored: false,
}

function session(extra: Record<string, unknown> = {}) {
  return {
    merge_id: MERGE_ID,
    kind: 'tr_revert',
    doc_id: TR_DOC,
    doc_code: '0009-TR',
    subject: 'Revert "0009-TR: 커밋 포인트 생성"',
    files: ['server/app.py'],
    remaining: ['server/app.py'],
    review_state: 'open',
    ...extra,
  }
}

function statusWith(conflictSession: unknown) {
  return {
    enabled: true,
    base_branch: 'main',
    base_path_state: 'checkout',
    ahead_count: 0,
    behind_count: 0,
    slots: [{
      group_id: GROUP,
      branch: 'flowgate_default_0332',
      status: 'conflict',
      merge_id: null,
      tr_commits: {
        live: 1, canceled: 0, no_commit: 0, commits: [CANCELED_ROW], more: 0,
        reapply_pending: false, last_block: null,
        conflict_session: conflictSession,
      },
    }],
    pending: [],
    pending_count: 0,
    unpushed: { count: 0, commit_count: 0, merges: [], measured: true },
  }
}

async function mountWith(status: unknown) {
  getRequest.mockResolvedValue({ data: { ok: true, status } })
  const wrapper = mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
})

describe('GitStatusPanel × 붙들린 TR 충돌 (TR0019)', () => {
  it('충돌 블록은 커밋 목록을 펼치지 않아도 보인다', async () => {
    const wrapper = await mountWith(statusWith(session()))

    // 대조군: 목록 자체는 접혀 있다. 블록이 보이는 것은 접기와 무관하다는 뜻이다.
    expect(wrapper.findAll('.git-trc-list')).toHaveLength(0)
    expect(wrapper.findAll('.git-trc-row')).toHaveLength(0)
    expect(wrapper.find('.git-trc-conflict').exists()).toBe(true)
  })

  it('되돌리기와 되살리기를 구분해 말한다', async () => {
    const revert = await mountWith(statusWith(session()))
    expect(revert.find('.git-trc-conflict').text()).toContain(
      i18n.global.t('main.git_status.tr_commits.conflict_tr_revert', {
        code: '0009-TR', n: 1,
      }),
    )

    const reapply = await mountWith(statusWith(session({ kind: 'tr_reapply' })))
    const text = reapply.find('.git-trc-conflict').text()
    expect(text).toContain(
      i18n.global.t('main.git_status.tr_commits.conflict_tr_reapply', {
        code: '0009-TR', n: 1,
      }),
    )
    // 대조군: 같은 자리가 되돌리기 문구로 바뀌어 있지 않다.
    expect(text).not.toContain(
      i18n.global.t('main.git_status.tr_commits.conflict_tr_revert', {
        code: '0009-TR', n: 1,
      }),
    )
  })

  it('표식이 남아 있는 동안에는 커밋 단추를 주지 않는다', async () => {
    const wrapper = await mountWith(statusWith(session()))

    // 대조군: 같은 블록의 다른 두 단추는 있다 — 선택자가 틀려서 없는 것이 아니다.
    expect(wrapper.findAll('.git-trc-conflict-btn')).toHaveLength(1)
    expect(wrapper.findAll('.git-trc-conflict-abort-btn')).toHaveLength(1)
    expect(wrapper.findAll('.git-trc-conflict-commit-btn')).toHaveLength(0)
  })

  it('검토 대기가 되면 커밋 단추와 안내가 함께 나온다', async () => {
    const wrapper = await mountWith(statusWith(
      session({ review_state: 'resolved', remaining: [] }),
    ))

    expect(wrapper.findAll('.git-trc-conflict-commit-btn')).toHaveLength(1)
    expect(wrapper.find('.git-trc-conflict').text()).toContain(
      i18n.global.t('main.git_status.tr_commits.conflict_review_ready'),
    )
  })

  it('커밋 단추는 TR 전용 경로를 부르고, 끝나면 패널을 다시 읽는다', async () => {
    const wrapper = await mountWith(statusWith(
      session({ review_state: 'resolved', remaining: [] }),
    ))
    const readsBefore = getRequest.mock.calls.length
    postRequest.mockResolvedValue({
      data: { ok: true, result: { status: 'committed', commit: 'f00dcafe1234', kind: 'tr_revert' } },
    })

    await wrapper.find('.git-trc-conflict-commit-btn').trigger('click')
    await flushPromises()

    // T0011: mount already fired the once-per-project auto /git/cleanup call, so the
    // action under test is asserted by its own last call, not index 0.
    const lastCall = postRequest.mock.calls[postRequest.mock.calls.length - 1]
    expect(lastCall[0]).toBe(
      `/api/v1/groups/${GROUP}/git/merge/${MERGE_ID}/tr-commit`,
    )
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_status.tr_commits.conflict_committed_toast', {
        commit: 'f00dcaf',
      }),
      'success',
    )
    expect(getRequest.mock.calls.length).toBeGreaterThan(readsBefore)
  })

  it('중단은 같은 abort 경로를 부르고 경고로 말한다 — 성공처럼 보이지 않는다', async () => {
    const wrapper = await mountWith(statusWith(session()))
    postRequest.mockResolvedValue({ data: { ok: true, result: { status: 'aborted' } } })

    await wrapper.find('.git-trc-conflict-abort-btn').trigger('click')
    await flushPromises()

    expect(postRequest.mock.calls[postRequest.mock.calls.length - 1][0]).toBe(
      `/api/v1/groups/${GROUP}/git/merge/${MERGE_ID}/abort`,
    )
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_status.tr_commits.conflict_aborted_toast'),
      'warning',
    )
  })

  it('[충돌 해결]은 이 그룹이 pending 에 없어도 세션의 merge_id 로 편집기를 연다', async () => {
    // TR 충돌 그룹은 아직 마무리를 시작하지도 않았으므로 pending 목록에 없다. 예전
    // 코드는 pending 에서만 merge_id 를 찾았으므로 여기서 그룹 상세로 튕겼을 것이다.
    const wrapper = await mountWith(statusWith(session()))

    await wrapper.find('.git-trc-conflict-btn').trigger('click')
    await flushPromises()

    const opened = getRequest.mock.calls.map((c) => c[0])
    expect(opened).toContain(
      `/api/v1/groups/${GROUP}/git/merge/${MERGE_ID}/conflicts`,
    )
    expect(wrapper.emitted('open-group')).toBeUndefined()
  })

  it('해결 제출이 검토 대기로 돌아오면 "커밋됐다"고 말하지 않는다', async () => {
    const wrapper = await mountWith(statusWith(session()))
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        kind: 'tr_revert',
        files: [{ path: 'server/app.py', content: 'ok\n', conflict_count: 0 }],
        tr_conflict: { doc_code: '0009-TR', review_state: 'open' },
      },
    })
    await wrapper.find('.git-trc-conflict-btn').trigger('click')
    await flushPromises()
    postRequest.mockResolvedValue({
      data: { ok: true, result: { status: 'resolved_pending_review', merge_commit: null } },
    })

    wrapper.findComponent({ name: 'GitConflictResolverDialog' }).vm.$emit('submit')
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_status.tr_commits.conflict_resolved_toast'),
      'success',
    )
    // 대조군: 병합 완료 문구는 쓰이지 않았다.
    const said = showToast.mock.calls.map((c) => c[0])
    expect(said).not.toContain(
      i18n.global.t('main.git_finalize.merged_toast', { commit: '' }),
    )
  })

  it('붙들린 충돌이 없으면 블록도 단추도 없다', async () => {
    const wrapper = await mountWith(statusWith(null))

    // 대조군: 슬롯과 배지는 그려진다 — 패널이 통째로 비어서 초록인 것이 아니다.
    expect(wrapper.findAll('.git-status-slot').length).toBeGreaterThan(0)
    expect(wrapper.findAll('.git-trc-badge').length).toBeGreaterThan(0)
    expect(wrapper.findAll('.git-trc-conflict')).toHaveLength(0)
    expect(wrapper.findAll('.git-trc-conflict-commit-btn')).toHaveLength(0)
  })
})
