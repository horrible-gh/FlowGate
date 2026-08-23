// 0332 T#2 — 되돌리기 확인 창의 커밋 표시와 결과 화면 (D0005 §6.3·§6.4).
//
// 여기서 고정하는 것은 넷이다.
//   1. 누르기 전에 무엇이 취소되는지 단계 줄마다 보인다 — 커밋 해시 / 소스 변경 없음 /
//      이미 병합됨 / 이미 취소됨.
//   2. git 상태가 확인 단추를 잠그지 않는다. 미리보기를 못 받아도 되감기는 눌린다.
//   3. 하나라도 취소되지 않으면 창이 닫히지 않고 결과 화면으로 바뀐다.
//   4. 결과 화면은 사유만 말하지 않고 **다음에 할 일**까지 말한다 — 되돌릴 수 있는 것에만
//      [다시 시도]가, 병합된 것에만 [Git 상태 패널 열기]가 붙는다.
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import TimeMachineDialog from '@main/components/TimeMachineDialog.vue'

const STEPS = [
  { docId: 'flowgate.default.0332.0009-TR', seq: 9, typeCode: 'TR', title: '앞 레포트' },
  { docId: 'flowgate.default.0332.0010-T', seq: 10, typeCode: 'T', title: '지시' },
  { docId: 'flowgate.default.0332.0011-TR', seq: 11, typeCode: 'TR', title: '뒤 레포트' },
]

function preview(overrides: Record<string, unknown> = {}) {
  return {
    group_status: 'active',
    commits: [
      {
        seq: 9, doc_id: STEPS[0].docId, doc_code: '0009-TR', commit: 'a1b2c3d',
        subject: '0009-TR: 앞', status: 'live', cancel_commit: null,
      },
      {
        seq: 11, doc_id: STEPS[2].docId, doc_code: '0011-TR', commit: 'e4f5a6b',
        subject: '0011-TR: 뒤', status: 'live', cancel_commit: null,
      },
    ],
    ...overrides,
  }
}

function cancelResult(overrides: Record<string, unknown> = {}) {
  return {
    attempted: true, blocked_reason: null, canceled: [], skipped: [],
    stopped_reason: null, retryable: false, ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
})

// 창은 닫힌 채로 마운트한 뒤 열어야 한다 — 미리 고른 단계(preselectDocId)를 반영하는
// 것은 visible 이 false→true 로 바뀔 때 도는 watch 이고, 실제 화면도 그렇게 열린다.
async function mountDialog(props: Record<string, unknown> = {}) {
  const wrapper = mount(TimeMachineDialog, {
    props: { visible: false, steps: STEPS, ...props } as any,
    // teleport 를 스텁해야 마운트 트리 안에서 창을 찾을 수 있다.
    global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
  })
  await wrapper.setProps({ visible: true })
  await nextTick()
  return wrapper
}

describe('TimeMachineDialog — 누르기 전 (D0005 §6.3)', () => {
  it('커밋이 있는 단계는 짧은 해시를, 없는 단계는 "소스 변경 없음"을 보인다', async () => {
    const wrapper = await mountDialog({ commitPreview: preview() })

    const cells = wrapper.findAll('.tmd-step-commit')
    expect(cells).toHaveLength(3)
    expect(cells[0].text()).toBe('a1b2c3d')
    // 원장에 줄이 없는 단계는 오류가 아니라 "그 승인은 소스를 안 바꿨다"는 뜻이다.
    expect(cells[1].text()).toBe('소스 변경 없음')
    expect(cells[2].text()).toBe('e4f5a6b')
  })

  it('이미 취소된 커밋은 "이미 취소됨"으로 구별된다', async () => {
    const wrapper = await mountDialog({
      commitPreview: preview({
        commits: [{
          seq: 9, doc_id: STEPS[0].docId, doc_code: '0009-TR', commit: 'a1b2c3d',
          subject: 's', status: 'canceled', cancel_commit: 'f7a1c02',
        }],
      }),
    })

    const cell = wrapper.findAll('.tmd-step-commit')[0]
    expect(cell.text()).toBe('이미 취소됨')
    expect(cell.classes()).toContain('tmd-step-commit--canceled')
  })

  it('이미 병합된 그룹은 모든 줄이 "이미 병합됨"이고 확인 단추는 그대로 살아 있다', async () => {
    const wrapper = await mountDialog({
      commitPreview: preview({ group_status: 'already_merged' }),
      preselectDocId: STEPS[0].docId,
    })

    expect(wrapper.findAll('.tmd-step-commit').map(c => c.text()))
      .toEqual(['이미 병합됨', '이미 병합됨', '이미 병합됨'])
    // 잠김 표식(D0005 §6.3)이 실제로 그려지는가 — 아이콘 이름이 등록부에 없으면
    // AppIcon 은 조용히 빈 svg 를 내므로 이름까지 못 박는다.
    expect(wrapper.findAll('.tmd-step-commit')[0].find('app-icon-stub').attributes('name'))
      .toBe('lock')
    expect(wrapper.find('.tmd-cancel-summary').text())
      .toBe('이미 병합되어 소스는 되돌아가지 않습니다.')
    // 되감기 자체는 유효하다 — git 상태로 단추를 잠그지 않는다(D0005 §6.3).
    const confirm = wrapper.findAll('.modal-ft button')[1]
    expect(confirm.attributes('disabled')).toBeUndefined()
  })

  it('미리보기를 못 받으면 "확인할 수 없음"이지만 되감기는 여전히 눌린다', async () => {
    const wrapper = await mountDialog({ commitPreview: null, preselectDocId: STEPS[0].docId })

    expect(wrapper.findAll('.tmd-step-commit').map(c => c.text()))
      .toEqual(['확인할 수 없음', '확인할 수 없음', '확인할 수 없음'])
    expect(wrapper.findAll('.modal-ft button')[1].attributes('disabled')).toBeUndefined()
  })

  it('선택한 단계 이상의 살아 있는 커밋만 요약이 센다', async () => {
    const wrapper = await mountDialog({ commitPreview: preview(), preselectDocId: STEPS[0].docId })

    // 9단계를 고르면 9·11 둘 다 취소 범위다.
    expect(wrapper.find('.tmd-cancel-summary').text())
      .toBe('확인하면 커밋 2개가 취소 커밋으로 되돌아갑니다.')
    // 취소 범위는 고른 한 칸이 아니라 그 이상 전부 — 줄이 함께 강조된다.
    expect(wrapper.findAll('.tmd-step--affected')).toHaveLength(3)

    await wrapper.findAll('.tmd-step')[2].trigger('click')

    expect(wrapper.find('.tmd-cancel-summary').text())
      .toBe('확인하면 커밋 1개가 취소 커밋으로 되돌아갑니다.')
    expect(wrapper.findAll('.tmd-step--affected')).toHaveLength(1)
  })

  it('취소할 커밋이 없으면 요약이 그렇게 말한다', async () => {
    const wrapper = await mountDialog({
      commitPreview: preview({ commits: [] }), preselectDocId: STEPS[1].docId,
    })

    expect(wrapper.find('.tmd-cancel-summary').text()).toBe('취소할 커밋이 없습니다.')
  })

  // 0332 TR0014 검토 — group_status가 no_worktree/git_inactive면 실제 취소도 아무 것도
  // 시도하지 않고 바로 blocked_reason으로 답한다(L0007 §3, git_service.cancel_group_status).
  // 원장에 살아 있는 행이 있다고 해서 미리보기가 해시를 보이면, 확인을 눌렀을 때 나오는
  // 결과 화면과 다른 말을 하게 된다.
  it('워크트리가 없는 그룹은 살아 있는 커밋이 있어도 "워크트리 없음"을 보인다', async () => {
    const wrapper = await mountDialog({
      commitPreview: preview({ group_status: 'no_worktree' }),
      preselectDocId: STEPS[0].docId,
    })

    expect(wrapper.findAll('.tmd-step-commit').map(c => c.text()))
      .toEqual(['워크트리 없음', '워크트리 없음', '워크트리 없음'])
    expect(wrapper.find('.tmd-cancel-summary').text())
      .toBe('이 그룹에는 워크트리가 없어 소스는 되돌아가지 않습니다.')
    // 되감기 자체는 유효하다 — git 상태로 단추를 잠그지 않는다.
    const confirm = wrapper.findAll('.modal-ft button')[1]
    expect(confirm.attributes('disabled')).toBeUndefined()
  })

  it('git 통합이 꺼진 그룹은 살아 있는 커밋이 있어도 "git 비활성"을 보인다', async () => {
    const wrapper = await mountDialog({
      commitPreview: preview({ group_status: 'git_inactive' }),
      preselectDocId: STEPS[0].docId,
    })

    expect(wrapper.findAll('.tmd-step-commit').map(c => c.text()))
      .toEqual(['git 비활성', 'git 비활성', 'git 비활성'])
    expect(wrapper.find('.tmd-cancel-summary').text())
      .toBe('이 프로젝트는 git 통합이 꺼져 있어 소스는 되돌아가지 않습니다.')
  })
})

describe('TimeMachineDialog — 되돌린 뒤 (D0005 §6.4)', () => {
  it('결과가 없으면 창은 여전히 고르는 화면이다', async () => {
    const wrapper = await mountDialog({ commitPreview: preview() })

    expect(wrapper.find('.tmd-result-head').exists()).toBe(false)
    expect(wrapper.find('.tmd-step-list').exists()).toBe(true)
  })

  it('충돌로 멈추면 결과 화면으로 바뀌고 줄마다 무슨 일이 있었는지 말한다', async () => {
    const wrapper = await mountDialog({
      cancelResult: cancelResult({
        canceled: [{
          doc_id: STEPS[2].docId, doc_code: '0011-TR', commit: 'e4f5a6b',
          cancel_commit: '0b3c9a1',
        }],
        skipped: [
          { doc_id: STEPS[0].docId, doc_code: '0009-TR', commit: 'a1b2c3d', reason: 'conflict' },
        ],
        stopped_reason: 'conflict',
      }),
    })

    // 머리글이 되감기는 확정됐다는 사실을 먼저 못 박는다.
    expect(wrapper.find('.tmd-result-head').text())
      .toBe('워크플로는 되감았습니다. 소스 커밋은 아래와 같습니다.')
    expect(wrapper.find('.tmd-step-list').exists()).toBe(false)
    const rows = wrapper.findAll('.tmd-result-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('0011-TR')
    expect(rows[0].text()).toContain('0b3c9a1')
    expect(rows[0].classes()).toContain('tmd-result-row--ok')
    expect(rows[1].text()).toContain('되돌림이 충돌해 여기서 멈췄습니다')
    // 충돌에는 자동 재시도가 없다 — 단추를 주지 않는다(L0007 §4.2).
    expect(wrapper.find('.tmd-retry-btn').exists()).toBe(false)
    expect(wrapper.find('.tmd-close-btn').exists()).toBe(true)
  })

  // TR0019 — 충돌을 세션으로 남긴 경우. 바로 위 시험이 대조군이다: 같은 stopped_reason
  // 인데 세션이 없으면 옛 문구가 그대로 나온다.
  it('충돌을 세션으로 남겼으면 손으로 정리하라고 하지 않고 해결할 곳을 가리킨다', async () => {
    const wrapper = await mountDialog({
      cancelResult: cancelResult({
        skipped: [
          { doc_id: STEPS[2].docId, doc_code: '0011-TR', commit: 'e4f5a6b', reason: 'conflict' },
        ],
        stopped_reason: 'conflict',
        conflict_session: { merge_id: 42 },
      }),
    })

    const row = wrapper.findAll('.tmd-result-row')[0].text()
    expect(row).toContain('충돌은 그대로 남겨 뒀습니다')
    expect(row).toContain('충돌 해결')
    // 남겨 뒀는데 "워크트리에서 정리하라"고 말하면 안 된다.
    expect(row).not.toContain('워크트리에서 정리하거나')
  })

  it('앞선 충돌로 시도되지 않은 줄도 사유를 그대로 말한다', async () => {
    const wrapper = await mountDialog({
      cancelResult: cancelResult({
        skipped: [
          { doc_id: STEPS[2].docId, doc_code: '0011-TR', commit: 'e4f5a6b', reason: 'conflict' },
          { doc_id: STEPS[0].docId, doc_code: '0009-TR', commit: 'a1b2c3d', reason: 'not_attempted' },
        ],
        stopped_reason: 'conflict',
      }),
    })

    expect(wrapper.findAll('.tmd-result-row')[1].text()).toContain('시도되지 않음')
  })

  it('정리되지 않은 변경은 [다시 시도]를 내고 누르면 재시도를 요청한다', async () => {
    const wrapper = await mountDialog({
      cancelResult: cancelResult({
        attempted: false, blocked_reason: 'dirty_worktree', retryable: true,
      }),
    })

    expect(wrapper.find('.tmd-result-row').text())
      .toContain('워크트리에 커밋되지 않은 변경이 있습니다')
    await wrapper.find('.tmd-retry-btn').trigger('click')

    expect(wrapper.emitted('retry-cancel')).toHaveLength(1)
    // 병합이 아니므로 Git 패널 단추는 없다.
    expect(wrapper.find('.tmd-open-git-btn').exists()).toBe(false)
  })

  it('재시도 중에는 단추가 잠긴다', async () => {
    const wrapper = await mountDialog({
      cancelResult: cancelResult({
        attempted: false, blocked_reason: 'git_busy', retryable: true,
      }),
      retrying: true,
    })

    expect(wrapper.find('.tmd-retry-btn').attributes('disabled')).toBeDefined()
  })

  it('이미 병합된 경우엔 [Git 상태 패널 열기]만 나온다', async () => {
    const wrapper = await mountDialog({
      cancelResult: cancelResult({
        attempted: false, blocked_reason: 'already_merged', retryable: false,
      }),
    })

    expect(wrapper.find('.tmd-result-row').text())
      .toContain('Git 상태 패널의 [병합 되돌리기]로만 취소할 수 있습니다')
    expect(wrapper.find('.tmd-retry-btn').exists()).toBe(false)
    await wrapper.find('.tmd-open-git-btn').trigger('click')

    expect(wrapper.emitted('open-git-panel')).toHaveLength(1)
  })

  it('[닫기]는 닫힘을 부모에게 알린다 — 부모가 되감긴 문서를 연다', async () => {
    const wrapper = await mountDialog({
      cancelResult: cancelResult({
        attempted: false, blocked_reason: 'no_worktree', retryable: false,
      }),
    })

    await wrapper.find('.tmd-close-btn').trigger('click')

    expect(wrapper.emitted('update:visible')?.[0]).toEqual([false])
  })
})
