// 0332 T#2 — 되돌린 뒤 화면이 무엇을 하는가 (D0005 §6.4 / P0006 §3·§4).
//
// 다이얼로그 자체의 그림은 TimeMachineDialogCancel.0332.spec.ts 가 본다. 여기서 고정하는
// 것은 **부모의 판단**이다.
//   1. 전부 취소됐으면 창을 닫고, 알림이 개수를 말한다("되감았다"만으로는 소스가 어떻게
//      됐는지 말하지 않는다).
//   2. 하나라도 남았으면 창을 닫지 않고 결과를 창에 넘긴다.
//   3. [다시 시도]는 취소만 재실행하는 경로를 부른다 — 문서를 다시 되감지 않는다.
//   4. 창이 열릴 때 미리보기를 함께 받아 창에 넘긴다.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { mountMainPanel } from '../helpers/mountMainPanel'
import { useTabsStore } from '@main/stores/tabs'
import TimeMachineDialog from '@main/components/TimeMachineDialog.vue'

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

const SEQUENCE = [
  { type: 'TR', result_doc_id: TR_DOC, result_seq: 11, label: '뒤 레포트' },
]

const PREVIEW = {
  group_status: 'active',
  commits: [{
    seq: 11, doc_id: TR_DOC, doc_code: '0011-TR', commit: 'e4f5a6b',
    subject: '0011-TR: 뒤', status: 'live', cancel_commit: null,
  }],
}

// The strip resolves a clicked cell through the sequence; one TR cell is all this needs.
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
    return Promise.resolve({
      data: { ok: true, return_point: { exists: false }, tr_commit_preview: PREVIEW },
    })
  }
  return Promise.resolve({ data: { questions: [] } })
}

async function openDialog() {
  const wrapper = await mountMainPanel({
    tabs: [{ id: ROOT, title: 'R', path: '', type: 'md', typeCode: 'R' } as any],
    stubs: { DocWorkflow: true },
  })
  const strip = wrapper.findComponent({ name: 'DocWorkflow' })
  strip.vm.$emit('time-machine', { index: 0, code: 'TR' })
  await flushPromises()
  return wrapper
}

function dialogOf(wrapper: any) {
  return wrapper.findComponent(TimeMachineDialog)
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockImplementation(mockGet)
})

describe('MainPanel — 되돌리기 취소 결과 (D0005 §6.4)', () => {
  it('창이 열릴 때 커밋 미리보기를 함께 받아 넘긴다', async () => {
    const wrapper = await openDialog()

    const dialog = dialogOf(wrapper)
    expect(dialog.props('visible')).toBe(true)
    expect(dialog.props('commitPreview')).toEqual(PREVIEW)
    // 결과 화면은 아직 없다 — 누르기 전이다.
    expect(dialog.props('cancelResult')).toBeNull()
  })

  it('전부 취소되면 창을 닫고 알림이 취소 개수를 말한다', async () => {
    postRequest.mockResolvedValue({
      data: {
        ok: true, reopened: [TR_DOC],
        tr_commit_cancel: {
          attempted: true, blocked_reason: null, stopped_reason: null, retryable: false,
          canceled: [{ doc_id: TR_DOC, doc_code: '0011-TR', commit: 'e4f5a6b', cancel_commit: '0b3c9a1' }],
          skipped: [],
        },
      },
    })
    const wrapper = await openDialog()

    dialogOf(wrapper).vm.$emit('confirm', { docId: TR_DOC, seq: 11, typeCode: 'TR', title: '뒤' })
    await flushPromises()

    expect(dialogOf(wrapper).props('visible')).toBe(false)
    expect(showToast).toHaveBeenCalledWith('되감았습니다 — 커밋 1개를 취소했습니다.', 'success')
  })

  it('취소할 커밋이 없었으면 그렇게 말한다 — 실패처럼 보이지 않는다', async () => {
    postRequest.mockResolvedValue({
      data: {
        ok: true, reopened: [TR_DOC],
        tr_commit_cancel: {
          attempted: true, blocked_reason: null, stopped_reason: null, retryable: false,
          canceled: [], skipped: [],
        },
      },
    })
    const wrapper = await openDialog()

    dialogOf(wrapper).vm.$emit('confirm', { docId: TR_DOC, seq: 11, typeCode: 'TR', title: '뒤' })
    await flushPromises()

    expect(dialogOf(wrapper).props('visible')).toBe(false)
    expect(showToast).toHaveBeenCalledWith('되감았습니다 — 취소할 커밋이 없었습니다.', 'success')
  })

  it('하나라도 남으면 창을 닫지 않고 결과를 창에 넘긴다', async () => {
    const cancel = {
      attempted: false, blocked_reason: 'dirty_worktree', stopped_reason: null,
      retryable: true, canceled: [], skipped: [],
    }
    postRequest.mockResolvedValue({
      data: { ok: true, reopened: [TR_DOC], tr_commit_cancel: cancel },
    })
    const wrapper = await openDialog()

    dialogOf(wrapper).vm.$emit('confirm', { docId: TR_DOC, seq: 11, typeCode: 'TR', title: '뒤' })
    await flushPromises()

    const dialog = dialogOf(wrapper)
    expect(dialog.props('visible')).toBe(true)
    expect(dialog.props('cancelResult')).toEqual(cancel)
    // 되감기 자체는 성공이므로 위험 알림을 띄우지 않는다 — 결과 화면이 말한다.
    expect(showToast).not.toHaveBeenCalled()
  })

  it('[다시 시도]는 취소만 재실행하는 경로를 부르고 문서를 다시 되감지 않는다', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        ok: true, reopened: [TR_DOC],
        tr_commit_cancel: {
          attempted: false, blocked_reason: 'git_busy', stopped_reason: null,
          retryable: true, canceled: [], skipped: [],
        },
      },
    })
    const wrapper = await openDialog()
    dialogOf(wrapper).vm.$emit('confirm', { docId: TR_DOC, seq: 11, typeCode: 'TR', title: '뒤' })
    await flushPromises()

    postRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        tr_commit_cancel: {
          attempted: true, blocked_reason: null, stopped_reason: null, retryable: false,
          canceled: [{ doc_id: TR_DOC, doc_code: '0011-TR', commit: 'e4f5a6b', cancel_commit: '5f0e2d8' }],
          skipped: [],
        },
      },
    })
    dialogOf(wrapper).vm.$emit('retry-cancel')
    await flushPromises()

    const [url, body] = postRequest.mock.calls[1]
    expect(url).toBe(
      `/api/v1/documents/workflow/${encodeURIComponent(TR_DOC)}/return-point/cancel-commits`,
    )
    expect(body).toEqual({})
    // 재시도가 성공했으니 창은 닫히고 알림이 결과를 말한다.
    expect(dialogOf(wrapper).props('visible')).toBe(false)
    expect(showToast).toHaveBeenCalledWith('되감았습니다 — 커밋 1개를 취소했습니다.', 'success')
  })

  it('결과 화면을 닫으면 그때 되감긴 단계 문서가 열린다', async () => {
    postRequest.mockResolvedValue({
      data: {
        ok: true, reopened: [TR_DOC],
        tr_commit_cancel: {
          attempted: false, blocked_reason: 'already_merged', stopped_reason: null,
          retryable: false, canceled: [], skipped: [],
        },
      },
    })
    const wrapper = await openDialog()
    dialogOf(wrapper).vm.$emit('confirm', { docId: TR_DOC, seq: 11, typeCode: 'TR', title: '뒤' })
    await flushPromises()
    // 결과 화면이 떠 있는 동안에는 아직 열지 않는다 — 사람이 읽는 중이다.
    expect(useTabsStore().tabs.map(t => t.id)).not.toContain(TR_DOC)

    dialogOf(wrapper).vm.$emit('update:visible', false)
    await flushPromises()

    expect(dialogOf(wrapper).props('visible')).toBe(false)
    expect(useTabsStore().tabs.map(t => t.id)).toContain(TR_DOC)
  })

  // 0332 TR0014 검토 — [Git 상태 패널 열기]가 이 창을 닫지 않으면, 그 이벤트로 열리는
  // 관제소 모달이 이 창의 딤(z-index 1200) 아래 깔려 클릭을 못 받는다. 닫기가 먼저
  // 서야 하고, [닫기]와 같은 정리(되감긴 단계 문서 열기)도 그대로 따라와야 한다.
  it('[Git 상태 패널 열기]는 창을 먼저 닫고 관제소 이벤트를 쏜다', async () => {
    postRequest.mockResolvedValue({
      data: {
        ok: true, reopened: [TR_DOC],
        tr_commit_cancel: {
          attempted: false, blocked_reason: 'already_merged', stopped_reason: null,
          retryable: false, canceled: [], skipped: [],
        },
      },
    })
    const wrapper = await openDialog()
    dialogOf(wrapper).vm.$emit('confirm', { docId: TR_DOC, seq: 11, typeCode: 'TR', title: '뒤' })
    await flushPromises()
    expect(dialogOf(wrapper).props('visible')).toBe(true)

    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    dialogOf(wrapper).vm.$emit('open-git-panel')
    await flushPromises()

    // 관제소 모달과 이 창이 동시에 떠 있으면 안 된다 — 닫힘이 이벤트보다 먼저다.
    expect(dialogOf(wrapper).props('visible')).toBe(false)
    expect(useTabsStore().tabs.map(t => t.id)).toContain(TR_DOC)
    const dispatched = dispatchSpy.mock.calls.map(([e]) => (e as CustomEvent).type)
    expect(dispatched).toContain('fg:git_status_open')
    dispatchSpy.mockRestore()
  })

  it('서버가 취소 결과를 싣지 않으면 예전 문구 그대로 닫힌다', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, reopened: [TR_DOC] } })
    const wrapper = await openDialog()

    dialogOf(wrapper).vm.$emit('confirm', { docId: TR_DOC, seq: 11, typeCode: 'TR', title: '뒤' })
    await flushPromises()

    expect(dialogOf(wrapper).props('visible')).toBe(false)
    expect(showToast).toHaveBeenCalledWith('워크플로를 되돌렸습니다.', 'success')
  })
})
