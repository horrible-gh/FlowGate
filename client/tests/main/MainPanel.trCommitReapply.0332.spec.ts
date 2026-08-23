// 0332 T0018 K11 — 앞으로 복원이 소스까지 데려왔는지 화면이 말하는가 (T0018 §3-6).
//
// 되감기 쪽 화면은 MainPanel.trCommitCancel.0332.spec.ts 가 본다. 여기서 고정하는 것은
// **앞으로 가는 방향**이고, 이 그룹이 반쪽으로 승인되면 안 되는 이유가 그대로 계약이다:
// 사람은 "되감았다 다시 왔으니 원래대로겠지"라고 믿는데, 화면이 소스에 대해 아무 말도
// 안 하면 그 믿음을 확인할 길이 없다.
//
//   1. 문서 문장은 그대로 두고 소스 문장을 뒤에 붙인다 — 문서는 어느 쪽이든 앞으로 왔다.
//   2. 되살릴 소스가 없었으면 실패처럼 보이지 않게 그렇게 말한다.
//   3. 막혔으면 사유를 말한다. 되살아났다고 말하지 않는다.
//   4. 복원 뒤에는 워크플로 표식과 Git 상태 패널을 함께 갱신한다 — 빠뜨리면 화면이
//      취소된 상태를 계속 보여 주고 "아무것도 안 변했다"로 읽힌다.
//   5. 새 키를 싣지 않는 서버(옛 빌드)에서는 예전 문구 그대로다.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { mountMainPanel } from '../helpers/mountMainPanel'
import ConfirmModal from '@main/components/ConfirmModal.vue'

const ROOT = 'flowgate.default.0332.0001-R'
const TR_DOC = 'flowgate.default.0332.0011-TR'

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({ issueToken: vi.fn(), copyMentToClipboard: vi.fn() }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

// 되감긴 TR 칸 하나. 앞으로 복원의 목적지다.
const SEQUENCE = [
  { type: 'TR', result_doc_id: TR_DOC, result_seq: 11, label: '뒤 레포트' },
]

const RETURN_POINT = {
  exists: true,
  front_seq: 11,
  front_label: '뒤 레포트',
  restorable_count: 1,
  current_min_seq: 11,
  destination_default: 11,
  destination_min: 11,
}

vi.mock('@main/workflow/workflowViewState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@main/workflow/workflowViewState')>()
  return {
    ...actual,
    resolveWorkflowViewState: () => ({
      mode: 'review' as const,
      canNextAction: false,
      currentStepCode: null,
      highlightStepCode: null,
      nextStepCode: null,
      nextStepActive: false,
      headDocLabel: null,
      headDocId: null,
      highlightDesignSeries: false,
      stepStates: [{ code: 'TR', visual: 'done', className: 'done', iconClass: 'check-circle' }],
      nextStepIndex: null,
    }),
  }
})

function mockGet(url: string) {
  if (url.includes('/sequence')) return Promise.resolve({ data: { sequence: SEQUENCE } })
  if (url.includes('/return-point')) {
    return Promise.resolve({ data: { ok: true, return_point: RETURN_POINT } })
  }
  return Promise.resolve({ data: { questions: [] } })
}

/** 칸을 눌러 확인창을 띄우고, 확인을 눌러 실제 복원을 돌린다. */
async function confirmForwardRestore() {
  const wrapper = await mountMainPanel({
    tabs: [{ id: ROOT, title: 'R', path: '', type: 'md', typeCode: 'R' } as any],
    stubs: { DocWorkflow: true },
  })
  const strip = wrapper.findComponent({ name: 'DocWorkflow' })
  strip.vm.$emit('return-to', { index: 0, code: 'TR' })
  await flushPromises()

  const modal = wrapper.findAllComponents(ConfirmModal)
    .find(m => m.props('visible') === true)
  expect(modal, '앞으로 복원 확인창이 떠 있어야 한다').toBeTruthy()
  modal!.vm.$emit('confirm')
  await flushPromises()
  return wrapper
}

function restoreResponse(trCommitRestore: unknown) {
  const data: Record<string, unknown> = {
    ok: true,
    restored: [TR_DOC],
    stopped_at: null,
    stopped_doc_id: null,
    reached_front: true,
    root_status: 'wf_done',
    return_point_cleared: true,
  }
  if (trCommitRestore !== undefined) data.tr_commit_restore = trCommitRestore
  return { data }
}

// 함수인 것이 중요하다 — 모듈이 읽힐 때 로케일은 아직 기본값이고, beforeEach 가
// 'ko' 로 바꾸는 것은 그 뒤다. 상수로 굳히면 영어 문장과 비교하게 된다.
const docSentence = () => i18n.global.t('main.time_machine.restore_done_full', { doc: '0011-TR' })

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockImplementation(mockGet)
})

describe('MainPanel — 앞으로 복원이 소스까지 데려온다 (T0018 K11)', () => {
  it('되살린 개수를 문서 문장 뒤에 붙여 말한다', async () => {
    postRequest.mockResolvedValue(restoreResponse({
      attempted: true, blocked_reason: null, stopped_reason: null, retryable: false,
      reapplied: [
        { doc_id: TR_DOC, doc_code: '0011-TR', commit: 'e4f5a6b',
          cancel_commit: '0b3c9a1', reapply_commit: '7d2e5f0' },
      ],
      skipped: [],
    }))

    await confirmForwardRestore()

    const source = i18n.global.t('main.time_machine.restore_source_reapplied', { n: 1 })
    expect(showToast).toHaveBeenCalledWith(`${docSentence()} ${source}`, 'success')
  })

  it('되살릴 소스가 없었으면 실패처럼 보이지 않게 그렇게 말한다', async () => {
    postRequest.mockResolvedValue(restoreResponse({
      attempted: true, blocked_reason: null, stopped_reason: null, retryable: false,
      reapplied: [], skipped: [],
    }))

    await confirmForwardRestore()

    const source = i18n.global.t('main.time_machine.restore_source_none')
    expect(showToast).toHaveBeenCalledWith(`${docSentence()} ${source}`, 'success')
  })

  it('막혔으면 사유를 말한다 — 문서는 그대로 앞으로 왔다고 말하면서', async () => {
    postRequest.mockResolvedValue(restoreResponse({
      attempted: false, blocked_reason: 'dirty_worktree', stopped_reason: null,
      retryable: true, reapplied: [], skipped: [],
    }))

    await confirmForwardRestore()

    const source = i18n.global.t('main.time_machine.restore_source_blocked', {
      reason: i18n.global.t('main.time_machine.reason_dirty_worktree'),
    })
    // 문서 복원은 성공이다(D0005 K8) — 소스 문장만 사유를 싣는다.
    expect(showToast).toHaveBeenCalledWith(`${docSentence()} ${source}`, 'success')
  })

  it('충돌해 멈췄으면 되살아났다고 말하지 않는다', async () => {
    postRequest.mockResolvedValue(restoreResponse({
      attempted: true, blocked_reason: null, stopped_reason: 'conflict', retryable: false,
      reapplied: [],
      skipped: [{ doc_id: TR_DOC, doc_code: '0011-TR', commit: 'e4f5a6b', reason: 'conflict' }],
    }))

    await confirmForwardRestore()

    const [message] = showToast.mock.calls[0]
    expect(message).toContain(i18n.global.t('main.time_machine.restore_source_conflict'))
    // 대조군: 성공 문구가 섞여 들어가지 않는다.
    expect(message).not.toContain(
      i18n.global.t('main.time_machine.restore_source_reapplied', { n: 1 }),
    )
  })

  // TR0019 — 되살리기 충돌도 세션으로 남는다. 위 시험이 대조군(세션 없음 → 옛 문구)이다.
  it('되살리기 충돌을 세션으로 남겼으면 해결할 곳을 가리킨다', async () => {
    postRequest.mockResolvedValue(restoreResponse({
      attempted: true, blocked_reason: null, stopped_reason: 'conflict', retryable: false,
      reapplied: [],
      skipped: [{ doc_id: TR_DOC, doc_code: '0011-TR', commit: 'e4f5a6b', reason: 'conflict' }],
      conflict_session: { merge_id: 42, kind: 'tr_reapply', files: ['a.txt'], review_state: 'open' },
    }))

    await confirmForwardRestore()

    const [message] = showToast.mock.calls[0]
    expect(message).toContain(
      i18n.global.t('main.time_machine.restore_source_conflict_parked'),
    )
    expect(message).not.toContain(i18n.global.t('main.time_machine.restore_source_conflict'))
  })

  it('복원 뒤 Git 상태 패널과 워크플로 표식을 함께 갱신한다', async () => {
    postRequest.mockResolvedValue(restoreResponse({
      attempted: true, blocked_reason: null, stopped_reason: null, retryable: false,
      reapplied: [
        { doc_id: TR_DOC, doc_code: '0011-TR', commit: 'e4f5a6b',
          cancel_commit: '0b3c9a1', reapply_commit: '7d2e5f0' },
      ],
      skipped: [],
    }))
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    await confirmForwardRestore()

    // 표식이 취소→커밋으로 바뀌므로 시퀀스/반환점을 다시 읽고, 패널에도 신호를 보낸다.
    const dispatched = dispatchSpy.mock.calls.map(([e]) => (e as CustomEvent).type)
    expect(dispatched).toContain('fg:git_status_refresh')
    expect(getRequest.mock.calls.some(([url]) => String(url).includes('/return-point'))).toBe(true)
    dispatchSpy.mockRestore()
  })

  it('서버가 소스 결과를 싣지 않으면 예전 문구 그대로다', async () => {
    postRequest.mockResolvedValue(restoreResponse(undefined))

    await confirmForwardRestore()

    expect(showToast).toHaveBeenCalledWith(docSentence(), 'success')
  })
})
