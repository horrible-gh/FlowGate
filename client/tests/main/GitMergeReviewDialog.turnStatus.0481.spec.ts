// flowgate.default.0481 T0010 rev6 — 승인 대기 화면의 대화에서 "왜 이런 답이 왔는지"가
// 본문 안에만 숨어 있으면 안 된다. 2026-09-08 반려 3의 화면에는 버려진 답(stale_run) 두
// 개와 정상 답 두 개가 똑같은 모양으로 나란히 서 있었고, 사람은 그 차이를 볼 수 없었다.
//
// 서버 쪽 절반(대화 기록을 실행에 넘기고, 버려진 답의 본문을 살리고, 대화 턴이 해소를
// 제출하지 못하게 막는 것)은 server/tests/test_review_conversation_and_base_dirty_admission_0481.py
// 와 test_git_integration_0115.py 에 있다. 이 시험은 화면 절반이다.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() }, getRequest, postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

type Turn = { turn_id: string; role: 'human' | 'ai'; message: string; provider_id: string | null; status: string; created_at: string }

const conversation: Turn[] = [
  { turn_id: 't1', role: 'human', message: '이번엔 뭐가 문제였지?', provider_id: 'p1', status: 'accepted', created_at: '2026-09-08T19:22:27+09:00' },
  { turn_id: 't2', role: 'ai', message: '이 답을 만드는 동안 승인 대상이 새 후보로 바뀌었습니다. 아래 내용은 그 이전 후보를 보고 쓴 것입니다(stale_run).\n\nREADME.md 의 Features 절이 문제였습니다.', provider_id: 'p1', status: 'stale_run', created_at: '2026-09-08T19:23:21+09:00' },
  { turn_id: 't3', role: 'ai', message: '정상적으로 답했습니다.', provider_id: 'p1', status: 'accepted', created_at: '2026-09-08T19:24:17+09:00' },
]

function reviewPayload() {
  return {
    ok: true,
    result: {
      group_id: 'flowgate.default.0481', merge_id: 9,
      review_state: 'resolved_pending_review', review_fingerprint: 'fp-1', instruction_generation: 0,
      base_head: 'base1', merge_head: 'merge1', snapshot_tree: 'tree1',
      changes: [{ path: 'README.md', status: 'M', old_path: null }],
      conflict_origins: [], conversation, held_test_operations: [],
      pending_conversation: null, resolver_provider: 'Claude Haiku 4.5',
      auto_authority: false, reconciliation_kind: null, last_error: null,
      can_approve: true, can_reject: true, can_send: true,
    },
  }
}

function mountDialog() {
  return mount(GitMergeReviewDialog, {
    props: {
      groupId: 'flowgate.default.0481', mergeId: 9, branch: 'group/0481', baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Haiku 4.5' }], selectedProvider: 'p1',
    },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/review-diff')) {
      return Promise.resolve({ data: { ok: true, data: { group_id: 'flowgate.default.0481', merge_id: 9, path: 'README.md', status: 'M', old: { exists: true, binary: false, truncated: false, size: 2, content: 'a\n' }, new: { exists: true, binary: false, truncated: false, size: 2, content: 'b\n' } } } })
    }
    if (url.includes('/review')) return Promise.resolve({ data: reviewPayload() })
    return Promise.reject(new Error('unexpected ' + url))
  })
})

describe('0481 T0010 rev6 — 대화 턴의 결과가 화면에 보인다', () => {
  it('버려진 답에는 배지가 붙고, 그 답의 본문은 그대로 남아 있다', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const badges = wrapper.findAll('[data-test="gmr-turn-status"]')
    // 정상 답과 사람의 말에는 아무것도 붙지 않는다 — 버려진 답 하나에만 붙는다.
    expect(badges).toHaveLength(1)
    expect(badges[0].text()).toBe(i18n.global.t('main.git_review.turn_status_stale_run'))

    // 그리고 그 턴의 본문은 살아 있다: 사라진 답을 되살린 것이 이 반려의 핵심이다.
    const staleTurn = wrapper.findAll('.gmr-turn-ai')[0]
    expect(staleTurn.text()).toContain('README.md 의 Features 절이 문제였습니다.')
    expect(staleTurn.text()).toContain('승인 대상이 새 후보로 바뀌었습니다')
  })

  it('run_lost / failed 도 같은 자리에 이름을 남긴다', async () => {
    conversation.push(
      { turn_id: 't4', role: 'ai', message: '기록이 없습니다', provider_id: null, status: 'run_lost', created_at: '2026-09-08T19:25:00+09:00' },
      { turn_id: 't5', role: 'ai', message: '적용에 실패했습니다', provider_id: 'p1', status: 'failed', created_at: '2026-09-08T19:26:00+09:00' },
    )
    const wrapper = mountDialog()
    await flushPromises()
    const labels = wrapper.findAll('[data-test="gmr-turn-status"]').map((n) => n.text())
    expect(labels).toEqual([
      i18n.global.t('main.git_review.turn_status_stale_run'),
      i18n.global.t('main.git_review.turn_status_run_lost'),
      i18n.global.t('main.git_review.turn_status_failed'),
    ])
    conversation.splice(3)
  })
})
