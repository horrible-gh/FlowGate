import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import type { StepState } from '@main/workflow/workflowViewState'

// group 0022: the [질의 응답] panel fetches GET /api/v1/q/{docId} on mount and POSTs on
// register/answer. Mock the api so tests control the payload and assert calls.
const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), head: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

import DocInfoPanel from '@main/components/DocInfoPanel.vue'

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: { qa: { items: [] } } })
  postRequest.mockResolvedValue({ data: { ok: true } })
})

describe('DocInfoPanel status fallback', () => {
  const baseProps = {
    docId: 'test.doc',
    typeCode: 'R' as string | null,
    reviewStatus: null as string | null,
    rejectReason: null,
    stepStates: [] as StepState[],
    nextStepIndex: null as number | null,
    collapsed: false,
  }

  function mountPanel(overrides: Partial<typeof baseProps & { workflowSteps?: string[] | null }> = {}) {
    return mount(DocInfoPanel, {
      props: { ...baseProps, ...overrides },
      global: { plugins: [i18n] },
    })
  }

  function badge(wrapper: ReturnType<typeof mountPanel>) {
    return wrapper.find('.dip-status-badge')
  }

  it('1. R + null reviewStatus + null workflowSteps → not-decided', () => {
    const wrapper = mountPanel({ typeCode: 'R', reviewStatus: null, workflowSteps: null })
    const vm = wrapper.vm as { statusClass: string; statusLabel: string; statusIcon: string }
    expect(vm.statusClass).toBe('not-decided')
    expect(vm.statusLabel).toBe(i18n.global.t('main.doc_info_panel.status_not_decided'))
    expect(vm.statusIcon).toBe('fa-circle-question')
    expect(badge(wrapper).classes()).toContain('not-decided')
    expect(badge(wrapper).find('i').classes()).toContain('fa-circle-question')
  })

  it('2. R + null reviewStatus + decided workflowSteps → review-pending', () => {
    const wrapper = mountPanel({ typeCode: 'R', reviewStatus: null, workflowSteps: ['NR', 'TR'] })
    const vm = wrapper.vm as { statusClass: string; statusLabel: string; statusIcon: string }
    expect(vm.statusClass).toBe('review-pending')
    expect(vm.statusLabel).toBe(i18n.global.t('main.doc_info_panel.status_pending'))
    expect(vm.statusIcon).toBe('fa-hourglass-half')
    expect(badge(wrapper).classes()).toContain('review-pending')
  })

  it('3. NR + null reviewStatus + null workflowSteps → review-pending', () => {
    const wrapper = mountPanel({ typeCode: 'NR', reviewStatus: null, workflowSteps: null })
    const vm = wrapper.vm as { statusClass: string }
    expect(vm.statusClass).toBe('review-pending')
    expect(badge(wrapper).classes()).toContain('review-pending')
  })

  it('regression: pending_review enum → review-pending badge', () => {
    const wrapper = mountPanel({ typeCode: 'NR', reviewStatus: 'pending_review', workflowSteps: null })
    expect((wrapper.vm as { statusClass: string }).statusClass).toBe('review-pending')
    expect(badge(wrapper).classes()).toContain('review-pending')
  })

  it('clicking the current-step status badge emits next-action', async () => {
    const wrapper = mountPanel({
      typeCode: 'R', reviewStatus: 'wf_in_progress',
      stepStates: [{ code: 'DS', visual: 'highlight', className: 'current', iconClass: '' }] as StepState[],
      nextStepIndex: 0,
      workflowSteps: ['DS'],
    })
    // nextStep.visual must be 'current' for the badge to be clickable
    const wrapper2 = mountPanel({
      typeCode: 'R', reviewStatus: 'wf_in_progress',
      stepStates: [{ code: 'DS', visual: 'current', className: 'current', iconClass: '' }] as StepState[],
      nextStepIndex: 0,
      workflowSteps: ['DS'],
    })
    expect(wrapper2.find('.dip-status-badge').classes()).toContain('dip-badge-clickable')
    await wrapper2.find('.dip-status-badge').trigger('click')
    expect(wrapper2.emitted('next-action')).toBeTruthy()
    void wrapper
  })
})

describe('DocInfoPanel 질의 응답 panel (group 0022 §3.1)', () => {
  const baseProps = {
    docId: 'p.none.0001.0001-D',
    typeCode: 'D' as string | null,
    reviewStatus: 'pending_review' as string | null,
    rejectReason: null,
    stepStates: [] as StepState[],
    nextStepIndex: null as number | null,
    collapsed: false,
  }

  function mountPanel() {
    return mount(DocInfoPanel, { props: { ...baseProps }, global: { plugins: [i18n] } })
  }

  it('renders the 질의 응답 section header and fetches the container', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith('/api/v1/q/p.none.0001.0001-D')
    expect(wrapper.text()).toContain(i18n.global.t('main.doc_info_panel.section_qa'))
  })

  it('shows empty state when there are no queries', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.dip-reject-empty').exists()).toBe(true)
  })

  it('renders question cards; answered card carries the answered-card accent (group 0126 / C안)', async () => {
    getRequest.mockResolvedValue({
      data: { qa: { items: [
        { id: 1, seq: 1, title: 'Palette', body: 'A or B?', asker_kind: 'human', answer_count: 1,
          answers: [{ body: 'B', author_kind: 'human' }] },
        { id: 2, seq: 2, title: 'Order', body: 'order?', asker_kind: 'ai', answer_count: 0, answers: [] },
      ] } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    const cards = wrapper.findAll('.dip-qa-card')
    expect(cards.length).toBe(2)
    expect(cards[0].classes()).toContain('answered-card') // has an answer
    expect(cards[1].classes()).not.toContain('answered-card') // still unanswered
    // headline unanswered-count badge reflects the single open query
    expect(wrapper.find('.dip-qa-count').text()).toContain('1')
  })

  it('card exposes only [답변], which opens the full-view dialog focused on that query', async () => {
    getRequest.mockResolvedValue({
      data: { qa: { items: [
        { id: 5, seq: 1, title: 'Q', body: 'b', asker_kind: 'human', answer_count: 0, answers: [] },
      ] } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    const dialog = wrapper.findComponent({ name: 'QaHistoryDialog' })
    expect(dialog.props('visible')).toBe(false)

    const buttons = wrapper.findAll('.dip-qa-card-actions .mini-action')
    expect(buttons.length).toBe(1)
    expect(buttons[0].text()).toContain(i18n.global.t('main.doc_info_panel.qa_answer'))

    await buttons[0].trigger('click')
    expect(dialog.props('visible')).toBe(true)
    expect(dialog.props('focusId')).toBe(5)
    expect(dialog.props('startAnswer')).toBe(true)
  })

  it('card [답변] opens the full-view dialog with the answer form requested', async () => {
    getRequest.mockResolvedValue({
      data: { qa: { items: [
        { id: 6, seq: 1, title: 'Q', body: 'b', asker_kind: 'human', answer_count: 0, answers: [] },
      ] } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    const dialog = wrapper.findComponent({ name: 'QaHistoryDialog' })
    await wrapper.find('.dip-qa-card-actions .mini-action.primary').trigger('click')
    expect(dialog.props('visible')).toBe(true)
    expect(dialog.props('focusId')).toBe(6)
    expect(dialog.props('startAnswer')).toBe(true)
  })

  it('headline keeps [전체보기], which opens the full-view dialog (no query focused)', async () => {
    getRequest.mockResolvedValue({
      data: { qa: { items: [
        { id: 7, seq: 1, title: 'Q', body: 'b', asker_kind: 'human', answer_count: 0, answers: [] },
      ] } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    const fullView = wrapper.find('.dip-qa-fullview')
    expect(fullView.exists()).toBe(true)
    expect(fullView.text()).toContain(i18n.global.t('main.doc_info_panel.qa_view_full'))

    const dialog = wrapper.findComponent({ name: 'QaHistoryDialog' })
    await fullView.trigger('click')
    expect(dialog.props('visible')).toBe(true)
    expect(dialog.props('focusId')).toBe(null)
    expect(dialog.props('startAnswer')).toBe(false)
  })

  it('[전체보기] and [+] share the matched headline button family (.dip-qa-act)', async () => {
    getRequest.mockResolvedValue({
      data: { qa: { items: [
        { id: 8, seq: 1, title: 'Q', body: 'b', asker_kind: 'human', answer_count: 0, answers: [] },
      ] } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.dip-qa-fullview').classes()).toContain('dip-qa-act')
    expect(wrapper.find('.dip-qa-add').classes()).toContain('dip-qa-act')
  })

  it('[전체보기] is hidden when there are no queries (nothing to view)', async () => {
    getRequest.mockResolvedValue({ data: { qa: { items: [] } } })
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.dip-qa-fullview').exists()).toBe(false)
    // the [+] add button is always available
    expect(wrapper.find('.dip-qa-add').exists()).toBe(true)
  })

  it('[+ 질의] toggles the new-question form', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.dip-qa-form').exists()).toBe(false)
    await wrapper.find('.dip-qa-add').trigger('click')
    expect(wrapper.find('.dip-qa-form').exists()).toBe(true)
  })

  it('submitting a new question POSTs to the questions endpoint and refetches', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('.dip-qa-add').trigger('click')
    await wrapper.find('.dip-qa-textarea').setValue('What color?')
    await wrapper.find('.dip-qa-form .btn-primary').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/q/p.none.0001.0001-D/questions',
      expect.objectContaining({ asker_kind: 'human' }),
    )
    // refetch after submit (initial + post-submit)
    expect(getRequest.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  // group 0126 / C안: answering moved out of the panel into the full-view dialog
  // (QaHistoryDialog), where the answer POST + AI-request behaviour is covered by
  // QaHistoryDialog.answer.spec.ts. The panel now only opens that dialog (above).
  it.skip('answering an item POSTs author_kind=human; AI request hits ai-request', async () => {
    getRequest.mockResolvedValue({
      data: { qa: { items: [
        { id: 9, seq: 1, title: 'Q', body: 'b', asker_kind: 'human', answer_count: 0, answers: [] },
      ] } },
    })
    const wrapper = mountPanel()
    await flushPromises()
    const actionBtns = wrapper.findAll('.dip-qa-actions .btn-outline')
    await actionBtns[1].trigger('click') // [AI에게 답변 요청]
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/q/p.none.0001.0001-D/items/9/answers/ai-request',
      {},
    )
  })

  // 0059 B0001 rework: a worker registering a Q on the doc on screen arrives via SSE
  // (qna_q_registered → fg:q_registered window event). The panel must refetch live so the
  // new Q surfaces WITHOUT F5. Without the listener it stayed at the mount-time fetch.
  //
  // Note: fg:q_registered is a global window event and earlier tests leave panels mounted,
  // so each live-refresh test uses a UNIQUE docId to isolate which panel reacts. We count
  // only GETs to this doc's own endpoint, not the shared mock's total call count.
  function mountWithDoc(docId: string) {
    return mount(DocInfoPanel, { props: { ...baseProps, docId }, global: { plugins: [i18n] } })
  }
  function getCallsFor(docId: string) {
    return getRequest.mock.calls.filter((c) => c[0] === `/api/v1/q/${docId}`).length
  }

  it('refetches the container on a matching fg:q_registered event (live, no F5)', async () => {
    const docId = 'q.live.match.0001-T'
    const wrapper = mountWithDoc(docId)
    await flushPromises()
    expect(getCallsFor(docId)).toBe(1) // mount fetch
    window.dispatchEvent(new CustomEvent('fg:q_registered', { detail: { doc_id: docId } }))
    await flushPromises()
    expect(getCallsFor(docId)).toBe(2) // live refetch
    wrapper.unmount()
  })

  it('ignores fg:q_registered for a different document', async () => {
    const docId = 'q.live.ignore.0001-T'
    const wrapper = mountWithDoc(docId)
    await flushPromises()
    expect(getCallsFor(docId)).toBe(1)
    window.dispatchEvent(new CustomEvent('fg:q_registered', { detail: { doc_id: 'some.other.doc-T' } }))
    await flushPromises()
    expect(getCallsFor(docId)).toBe(1) // unchanged — not this doc
    wrapper.unmount()
  })

  it('removes the listener on unmount (no refetch after teardown)', async () => {
    const docId = 'q.live.unmount.0001-T'
    const wrapper = mountWithDoc(docId)
    await flushPromises()
    expect(getCallsFor(docId)).toBe(1)
    wrapper.unmount()
    window.dispatchEvent(new CustomEvent('fg:q_registered', { detail: { doc_id: docId } }))
    await flushPromises()
    expect(getCallsFor(docId)).toBe(1) // listener gone — no extra fetch
  })
})
