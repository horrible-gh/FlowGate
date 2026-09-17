// flowgate.default.0578 T0006 §3 작업 5 — 승인 대기 화면의 서버 안내는 저장된 문장이
// 아니라 저장된 의미(message_code)에서 나온다. NR0003 F1 이 잰 문제는 "한국어가 들어
// 있다"가 아니라 "그 문장이 저장돼 있어서 화면 언어를 바꿔도 따라오지 않는다"였다.
// 그래서 이 시험은 한글 부재가 아니라 ko/en/ja 각각의 기대 문구를 단언한다(D0005 §5).
//
// 서버 절반(무엇이 어떤 code/params 로 저장되는지, locale 이 어떻게 보존되는지)은
// server/tests/test_review_turn_messages_0578.py 에 있다. 이 시험은 화면 절반이다.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() }, getRequest, postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

type Turn = {
  turn_id: string
  role: 'human' | 'ai'
  message: string
  provider_id: string | null
  status: string
  created_at: string
  message_code?: string
  message_params?: Record<string, unknown>
  source_locale?: string
  apply_errors?: Record<string, unknown>[]
}

let conversation: Turn[] = []

function coded(
  turn_id: string,
  message_code: string,
  message_params: Record<string, unknown>,
  extra: Partial<Turn> = {},
): Turn {
  return {
    turn_id, role: 'ai', message: '', provider_id: 'p1', status: 'failed',
    created_at: '2026-09-17T19:00:00+09:00', source_locale: 'ko',
    message_code, message_params, ...extra,
  }
}

function reviewPayload() {
  return {
    ok: true,
    result: {
      group_id: 'flowgate.default.0578', merge_id: 9,
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
      groupId: 'flowgate.default.0578', mergeId: 9, branch: 'group/0578', baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Haiku 4.5' }], selectedProvider: 'p1',
    },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  conversation = []
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/review-diff')) {
      return Promise.resolve({
        data: {
          ok: true,
          data: {
            group_id: 'flowgate.default.0578', merge_id: 9, path: 'README.md', status: 'M',
            old: { exists: true, binary: false, truncated: false, size: 2, content: 'a\n' },
            new: { exists: true, binary: false, truncated: false, size: 2, content: 'b\n' },
          },
        },
      })
    }
    if (url.includes('/review')) return Promise.resolve({ data: reviewPayload() })
    return Promise.reject(new Error('unexpected ' + url))
  })
})

afterEach(() => {
  i18n.global.locale.value = 'ko'
})

const notices = (wrapper: ReturnType<typeof mountDialog>) =>
  wrapper.findAll('[data-test="gmr-turn-notice"]').map((node) => node.text())
const contents = (wrapper: ReturnType<typeof mountDialog>) =>
  wrapper.findAll('[data-test="gmr-turn-content"]').map((node) => node.text())

describe('0578 T0006 — 대화 turn 안내는 저장된 code 에서 현재 언어로 그려진다', () => {
  // 세 언어의 기대 문구. "한글이 없다"가 아니라 "그 언어의 이 문장이 나온다"를 본다.
  const EXPECTED: Record<string, { runLost: string; staleReason: string; cancelled: string }> = {
    ko: {
      runLost: '이 지시를 맡은 실행의 기록이 남아 있지 않아',
      staleReason: '승인 대상이 새 후보로 바뀌었습니다',
      cancelled: '사용자가 이 실행을 중지했습니다',
    },
    en: {
      runLost: 'No record is left of the run that took this instruction',
      staleReason: 'the approval target moved to a new candidate',
      cancelled: 'The user stopped this run (cancelled).',
    },
    ja: {
      runLost: 'この指示を担当した実行の記録が残っておらず',
      staleReason: '承認対象が新しい候補に変わりました',
      cancelled: 'ユーザーがこの実行を停止しました',
    },
  }

  it.each(['ko', 'en', 'ja'])('%s — 같은 저장 payload 가 그 언어의 문구로 나온다', async (locale) => {
    conversation = [
      coded('a1', 'review_run_lost', {}, { status: 'run_lost' }),
      coded('a2', 'review_cancelled', {}, { status: 'cancelled' }),
      coded('a3', 'review_stale_run',
        { changed: ['candidate_refrozen'], plan_discarded: false },
        { status: 'stale_run', message: 'README 의 Features 절이 문제였습니다.' }),
    ]
    i18n.global.locale.value = locale as 'ko' | 'en' | 'ja'

    const wrapper = mountDialog()
    await flushPromises()

    const text = notices(wrapper).join('\n')
    expect(text).toContain(EXPECTED[locale].runLost)
    expect(text).toContain(EXPECTED[locale].cancelled)
    expect(text).toContain(EXPECTED[locale].staleReason)
    // 다른 언어의 문장이 섞여 나오지 않는다.
    for (const other of ['ko', 'en', 'ja'].filter((l) => l !== locale)) {
      expect(text).not.toContain(EXPECTED[other].runLost)
    }
    // AI 가 쓴 원문은 번역 대상이 아니다 — 어느 언어에서도 그대로다.
    expect(contents(wrapper)).toContain('README 의 Features 절이 문제였습니다.')

    wrapper.unmount()
  })

  it('화면 언어를 바꾸면 이미 불러온 turn 도 다시 그려진다', async () => {
    // 마운트 시점에 문자열로 캐싱했다면 이 단언이 깨진다 — 저장된 문장으로 되돌아가는
    // 것을 막는 것이 이 작업의 목적이다.
    conversation = [coded('a1', 'review_run_lost', {}, { status: 'run_lost' })]
    const wrapper = mountDialog()
    await flushPromises()
    expect(notices(wrapper)[0]).toContain(EXPECTED.ko.runLost)

    i18n.global.locale.value = 'ja'
    await flushPromises()

    expect(notices(wrapper)[0]).toContain(EXPECTED.ja.runLost)
    expect(notices(wrapper)[0]).not.toContain(EXPECTED.ko.runLost)

    wrapper.unmount()
  })

  it('code 가 없는 기존 turn 은 원문 그대로 나오고 안내 블록을 만들지 않는다', async () => {
    const legacy = '이 답을 만드는 동안 승인 대상이 새 후보로 바뀌었습니다. (stale_run)\n\nREADME 를 고쳤습니다.'
    conversation = [
      { turn_id: 'h1', role: 'human', message: '이번엔 뭐가 문제였지?', provider_id: 'p1', status: 'accepted', created_at: '2026-09-01T00:00:00+09:00' },
      { turn_id: 'old', role: 'ai', message: legacy, provider_id: 'p1', status: 'stale_run', created_at: '2026-09-01T00:01:00+09:00' },
      coded('new', 'review_stale_run',
        { changed: ['approval_settled'], review_state: 'completed', plan_discarded: true },
        { status: 'stale_run', message: '새 답입니다.' }),
    ]
    i18n.global.locale.value = 'en'

    const wrapper = mountDialog()
    await flushPromises()

    // legacy 는 원문 블록에만, 안내 블록은 새 turn 하나뿐.
    expect(contents(wrapper)).toEqual([
      '이번엔 뭐가 문제였지?', legacy, '새 답입니다.',
    ])
    expect(notices(wrapper)).toHaveLength(1)
    expect(notices(wrapper)[0]).toContain('the approval wait ended (now completed)')
    expect(notices(wrapper)[0]).toContain('The write plan submitted with it was not applied.')

    wrapper.unmount()
  })

  it('미등록 code 와 필수 params 누락은 일반 안내로 떨어지고 raw message 를 올리지 않는다', async () => {
    conversation = [
      // 이 화면이 모르는 code — 더 새로운 서버.
      coded('f1', 'review_something_new_0999', { x: 1 }, { status: 'cancelled' }),
      // 등록된 code 지만 필수 보간값이 없다.
      coded('f2', 'review_apply_held_only', {}, { status: 'accepted' }),
      // stale 인데 사유가 비었다. message 에는 AI 원문이 따로 있다.
      coded('f3', 'review_stale_run', { changed: [] },
        { status: 'stale_run', message: 'AI 가 쓴 답입니다.' }),
    ]
    i18n.global.locale.value = 'en'

    const wrapper = mountDialog()
    await flushPromises()

    // `status` is a stable identifier, not copy: the generic sentence shows the current
    // locale's display word for it, not the raw status string (review finding).
    expect(notices(wrapper)).toEqual([
      "This turn ended in the 'cancelled' state.",
      "This turn ended in the 'completed' state.",
      "This turn ended in the 'answer to a previous candidate' state.",
    ])
    // 원문은 잃지 않지만, 안내 자리로 올라오지도 않는다.
    expect(contents(wrapper)).toEqual(['AI 가 쓴 답입니다.'])

    wrapper.unmount()
  })

  it('경로는 원문 그대로 보간되고, 없을 때는 번역된 표현을 쓴다', async () => {
    conversation = [
      coded('p1', 'review_apply_re_review',
        { paths: ['client/shared/i18n/ko.ts', 'server/app.py'], path_count: 2, held_count: 0 },
        { status: 'accepted' }),
      coded('p2', 'review_apply_re_review', { paths: [], path_count: 0, held_count: 0 },
        { status: 'accepted' }),
    ]

    const wrapper = mountDialog()
    await flushPromises()

    expect(notices(wrapper)[0]).toContain('client/shared/i18n/ko.ts, server/app.py')
    expect(notices(wrapper)[1]).toContain('(없음)')

    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(notices(wrapper)[1]).toContain('(none)')
    // 경로는 언어를 따라가지 않는다 — 원문 데이터다.
    expect(notices(wrapper)[0]).toContain('client/shared/i18n/ko.ts, server/app.py')

    wrapper.unmount()
  })

  it('적용 실패 원인은 본문이 아니라 접힌 진단 영역에만 나온다', async () => {
    conversation = [
      coded('e1', 'review_apply_failed', { error_count: 2, held_count: 0 }, {
        status: 'failed',
        apply_errors: [
          { path: 'server/app.py', validator: 'python-compile', line: 12, message: 'SyntaxError: invalid syntax' },
          { code: 'git_busy', message: "fatal: pathspec 'x' did not match any files" },
        ],
      }),
    ]

    const wrapper = mountDialog()
    await flushPromises()

    // 본문에는 실패 사실과 건수만.
    expect(notices(wrapper)[0]).toBe('수정 적용에 실패했습니다(원인 2건).')
    expect(notices(wrapper)[0]).not.toContain('SyntaxError')
    expect(notices(wrapper)[0]).not.toContain('fatal:')

    const diagnostics = wrapper.find('[data-test="gmr-apply-errors"]')
    expect(diagnostics.exists()).toBe(true)
    // 기본으로 펼치지 않는다.
    expect(diagnostics.attributes('open')).toBeUndefined()
    expect(diagnostics.text()).toContain('SyntaxError: invalid syntax')
    expect(diagnostics.text()).toContain("fatal: pathspec 'x' did not match any files")
    expect(diagnostics.text()).toContain('server/app.py python-compile:12')

    wrapper.unmount()
  })

  it('적용 원인이 없는 turn 에는 진단 영역 자체가 없다', async () => {
    conversation = [
      coded('c1', 'review_apply_rollback_verification_failed', {}, { status: 'failed' }),
    ]

    const wrapper = mountDialog()
    await flushPromises()

    expect(wrapper.find('[data-test="gmr-apply-errors"]').exists()).toBe(false)
    expect(notices(wrapper)[0]).toContain('수정 적용 실패 후 상태 복구 확인에도 실패했습니다')

    wrapper.unmount()
  })

  // ── review finding — schema validation was incomplete ───────────────────────────

  it('review_apply_re_review 가 {} 또는 null paths/count 불일치이면 일반 안내로 떨어진다', async () => {
    // 반려에서 재현한 두 예: `{}`와 `{paths: null, path_count: 1, held_count: 0}`가
    // "빈 경로로 적용됨"을 뜻하는 문장을 냈다 — 이제는 둘 다 일반 안내여야 한다.
    conversation = [
      coded('r1', 'review_apply_re_review', {}, { status: 'accepted' }),
      coded('r2', 'review_apply_re_review', { paths: null, path_count: 1, held_count: 0 },
        { status: 'accepted' }),
      coded('r3', 'review_apply_re_review', { paths: ['a.py'], path_count: 2, held_count: 0 },
        { status: 'accepted' }),
    ]
    i18n.global.locale.value = 'en'

    const wrapper = mountDialog()
    await flushPromises()

    const expected = "This turn ended in the 'completed' state."
    expect(notices(wrapper)).toEqual([expected, expected, expected])
    for (const notice of notices(wrapper)) {
      expect(notice).not.toContain('Changed files')
      expect(notice).not.toContain('(none)')
    }

    wrapper.unmount()
  })

  it('음수·소수 count 는 일반 안내로 떨어진다', async () => {
    conversation = [
      coded('n1', 'review_apply_held_only', { held_count: -1 }, { status: 'accepted' }),
      coded('n2', 'review_apply_failed', { error_count: 1.5 }, { status: 'failed' }),
      coded('n3', 'review_apply_failed', { error_count: 1, held_count: -1 }, { status: 'failed' }),
    ]
    i18n.global.locale.value = 'en'

    const wrapper = mountDialog()
    await flushPromises()

    expect(notices(wrapper)[0]).toBe("This turn ended in the 'completed' state.")
    expect(notices(wrapper)[1]).toBe("This turn ended in the 'failed' state.")
    expect(notices(wrapper)[2]).toBe("This turn ended in the 'failed' state.")

    wrapper.unmount()
  })

  it('plan_discarded 가 boolean 이 아니면 일반 안내로 떨어진다', async () => {
    conversation = [
      coded('pd1', 'review_stale_run',
        { changed: ['candidate_refrozen'], plan_discarded: 'yes' },
        { status: 'stale_run' }),
    ]
    i18n.global.locale.value = 'en'

    const wrapper = mountDialog()
    await flushPromises()

    expect(notices(wrapper)[0]).toBe("This turn ended in the 'answer to a previous candidate' state.")

    wrapper.unmount()
  })

  // ── review finding — status/review_state are identifiers, not copy ──────────────

  it('일반 안내의 status 단어는 화면 언어별로 다르다(ko/en/ja)', async () => {
    conversation = [coded('s1', 'review_something_new_0999', {}, { status: 'failed' })]

    const wrapper = mountDialog()
    await flushPromises()
    expect(notices(wrapper)[0]).toContain('실패')
    expect(notices(wrapper)[0]).not.toContain('failed')

    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(notices(wrapper)[0]).toContain('failed')

    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(notices(wrapper)[0]).toContain('失敗')
    expect(notices(wrapper)[0]).not.toContain('failed')

    wrapper.unmount()
  })

  it('승인 정산 사유의 review_state 단어도 화면 언어별로 다르다(ko/ja)', async () => {
    conversation = [
      coded('rs1', 'review_stale_run',
        { changed: ['approval_settled'], review_state: 'completed' },
        { status: 'stale_run' }),
    ]

    const wrapper = mountDialog()
    await flushPromises()
    expect(notices(wrapper)[0]).toContain('현재 완료')
    expect(notices(wrapper)[0]).not.toContain('completed')

    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(notices(wrapper)[0]).toContain('現在 完了')
    expect(notices(wrapper)[0]).not.toContain('completed')

    wrapper.unmount()
  })
})
