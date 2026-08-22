/**
 * 0449 T0004 item 6.3 — on an AC head the client uses /workflow/final-approval, full stop.
 *
 * NR0003 re-ran the incident's own server shape (0444's sequence: item_seq 1,2,3,10~18, every
 * row realised) and found `/workflow/advance` answers 409 `sequence_exhausted` there — with or
 * without a lingering return point. That 409 is the correct contract, because advance is not
 * the endpoint that progresses an AC head: `POST /workflow/final-approval` is, and it refuses
 * unless the head really is AC (documents.py). The client branch that keeps those two apart is
 * `ReviewActionBar.isNextFinalApproval` → `MainPanel.onProceedNextStep` → `onOpenFinalApproval`.
 *
 * This pins that branch: on an AC head the only call that leaves the client is
 * final-approval — never /workflow/advance, never /token/issue.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import { mountMainPanel } from '../helpers/mountMainPanel'

const { getRequest, postRequest, issueToken } = vi.hoisted(() => ({
  getRequest: vi.fn().mockResolvedValue({ data: { questions: [] } }),
  postRequest: vi.fn(),
  issueToken: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
}))

vi.mock('../composables/useShortcuts', () => ({
  useShortcuts: () => ({ register: vi.fn(), unregister: vi.fn() }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({
    issueToken,
    copyMentToClipboard: vi.fn(),
    advanceWithWorkPlanScope: vi.fn(),
    requestReview: vi.fn(),
    requestWorkflowDecision: vi.fn(),
    requestSequenceEdit: vi.fn(),
    composeMention: vi.fn(),
    issuing: { value: false },
  }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

// The next step is AC and no AC document exists yet — the incident's own shape.
vi.mock('@main/workflow/workflowViewState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@main/workflow/workflowViewState')>()
  return {
    ...actual,
    resolveWorkflowViewState: () => ({
      mode: 'review' as const,
      canNextAction: true,
      currentStepCode: 'TR',
      highlightStepCode: 'AC',
      nextStepCode: 'AC',
      nextStepActive: true,
      headDocLabel: null,
      headDocId: null,
      highlightDesignSeries: false,
      stepStates: [],
      nextStepIndex: null,
    }),
  }
})

const TAB_ID = 'p.default.0449.0001-B'

const urls = () => postRequest.mock.calls.map((c) => String(c[0]))

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockClear()
  postRequest.mockReset()
  issueToken.mockReset()
})

describe('AC head — final approval is the only endpoint the client calls', () => {
  it('proceeding on an AC head posts final-approval and nothing else', async () => {
    postRequest.mockResolvedValue({ data: { doc_id: 'p.default.0449.0005-AC' } })
    const wrapper = await mountMainPanel({
      tabs: [{ id: TAB_ID, title: 'B doc', path: '', type: 'md', typeCode: 'B' }],
    })

    await (wrapper.vm as any).onProceedNextStep(TAB_ID)
    await wrapper.vm.$nextTick()

    expect(urls()).toEqual(['/api/v1/documents/workflow/final-approval'])
    expect(urls().some((u) => u.includes('/workflow/advance'))).toBe(false)
    expect(urls().some((u) => u.includes('/token/issue'))).toBe(false)
    // issueToken() is the function that would have reached both of those.
    expect(issueToken).not.toHaveBeenCalled()
  })

  it('the final-approval request names the document, and carries no token payload', async () => {
    postRequest.mockResolvedValue({ data: { doc_id: 'p.default.0449.0005-AC' } })
    const wrapper = await mountMainPanel({
      tabs: [{ id: TAB_ID, title: 'B doc', path: '', type: 'md', typeCode: 'B' }],
    })

    await (wrapper.vm as any).onProceedNextStep(TAB_ID)

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/documents/workflow/final-approval',
      { doc_id: TAB_ID },
    )
  })

  it('a refused final-approval does NOT fall back to advance or token issue either', async () => {
    postRequest.mockRejectedValue(
      Object.assign(new Error('409'), { response: { status: 409, data: { detail: 'Final approval is not the current step' } } }),
    )
    const wrapper = await mountMainPanel({
      tabs: [{ id: TAB_ID, title: 'B doc', path: '', type: 'md', typeCode: 'B' }],
    })

    await (wrapper.vm as any).onProceedNextStep(TAB_ID)
    await wrapper.vm.$nextTick()

    expect(urls()).toEqual(['/api/v1/documents/workflow/final-approval'])
    expect(issueToken).not.toHaveBeenCalled()
  })
})

describe('ReviewActionBar — the AC branch is what routes there', () => {
  function mountBar(nextStepCode: string | null) {
    return mount(ReviewActionBar, {
      props: {
        // 'next' is the mode the action bar is in when a next step is offered — the
        // AC carve-out lives inside that branch.
        mode: 'next',
        docId: TAB_ID,
        projectId: 'p',
        groupId: 'p.default.0449',
        docRef: TAB_ID,
        reviewStatus: 'approved',
        docType: 'B',
        canNextAction: true,
        nextStepCode,
      },
      global: { plugins: [i18n] },
    })
  }

  const finalApprovalButton = (wrapper: ReturnType<typeof mountBar>) =>
    wrapper.find('[data-test="review-action-final-approval"]')

  it('collapses to the single final-approval action when the next step is AC', async () => {
    const wrapper = mountBar('AC')
    expect((wrapper.vm as any).isNextFinalApproval).toBe(true)
    expect(finalApprovalButton(wrapper).exists()).toBe(true)

    // Emitting next-action is what MainPanel turns into onOpenFinalApproval.
    await finalApprovalButton(wrapper).trigger('click')
    expect(wrapper.emitted('next-action')).toBeTruthy()
  })

  it('POSITIVE CONTROL — a non-AC next step does not take the final-approval branch', () => {
    const tr = mountBar('TR')
    expect((tr.vm as any).isNextFinalApproval).toBe(false)
    expect(finalApprovalButton(tr).exists()).toBe(false)
    const none = mountBar(null)
    expect((none.vm as any).isNextFinalApproval).toBe(false)
    expect(finalApprovalButton(none).exists()).toBe(false)
  })
})
