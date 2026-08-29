import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import type { StepState } from '@main/workflow/workflowViewState'

// group 0022: the [질의 응답] panel fetches GET /api/v1/q/{docId} on mount and POSTs on
// register/answer. Mock the api so tests control the payload and assert calls.
const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), head: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))
// 0457 T0009 §4: assert the recover-failure toast text directly instead of poking the
// shared toasts singleton, matching AttachmentCard.serverError.0060.spec.ts.
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import QaHistoryDialog from '@main/components/QaHistoryDialog.vue'
import { useQaOpenIntent } from '@main/composables/useQaOpenIntent'
import AppIcon from '@shared/AppIcon.vue'

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
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
    expect(vm.statusIcon).toBe('question')
    expect(badge(wrapper).classes()).toContain('not-decided')
    expect(badge(wrapper).findComponent(AppIcon).props('name')).toBe('question')
  })

  it('2. R + null reviewStatus + decided workflowSteps → review-pending', () => {
    const wrapper = mountPanel({ typeCode: 'R', reviewStatus: null, workflowSteps: ['NR', 'TR'] })
    const vm = wrapper.vm as { statusClass: string; statusLabel: string; statusIcon: string }
    expect(vm.statusClass).toBe('review-pending')
    expect(vm.statusLabel).toBe(i18n.global.t('main.doc_info_panel.status_pending'))
    expect(vm.statusIcon).toBe('hourglass-medium')
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

describe('DocInfoPanel orphan recovery (flowgate.default.0374 / 0457 T0009)', () => {
  type CandidateSlot = { item_seq: number; type: string; empty: boolean }

  function mountOrphan(orphan: boolean, candidateSlots: CandidateSlot[] = []) {
    return mount(DocInfoPanel, {
      props: {
        docId: 'flowgate.default.0374.9999-TR',
        typeCode: 'TR',
        reviewStatus: 'pending_review',
        rejectReason: null,
        orphan,
        candidateSlots,
        stepStates: [] as StepState[],
        nextStepIndex: null,
        collapsed: false,
      },
      global: { plugins: [i18n] },
    })
  }

  const MATCHING_SLOT: CandidateSlot = { item_seq: 4, type: 'TR', empty: true }

  it('shows the orphan warning only for an unattached document', () => {
    expect(mountOrphan(true).find('.dip-orphan-warning').exists()).toBe(true)
    expect(mountOrphan(false).find('.dip-orphan-warning').exists()).toBe(false)
  })

  it('reattaches to the matching empty candidate slot and emits a refresh signal', async () => {
    const wrapper = mountOrphan(true, [MATCHING_SLOT])
    const button = wrapper.find('.dip-orphan-warning button')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/documents/flowgate.default.0374.9999-TR/workflow/recover',
      { item_seq: 4 },
    )
    expect(wrapper.emitted('orphan-recovered')).toHaveLength(1)
  })

  it('disables the button and gives a reason (not a slot dump) when no candidate slot matches', async () => {
    // A candidate exists but its type does not match this TR document.
    const wrapper = mountOrphan(true, [{ item_seq: 4, type: 'NR', empty: true }])
    const button = wrapper.find('.dip-orphan-warning button')
    expect(button.attributes('disabled')).toBeDefined()

    await button.trigger('click')
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()

    expect(wrapper.find('.dip-orphan-warning p').text())
      .toBe(i18n.global.t('main.doc_info_panel.orphan_no_slot_desc'))
  })

  it.each([
    ['slot_type_not_recoverable', 'orphan_recover_error_slot_type_not_recoverable'],
    ['not_orphaned', 'orphan_recover_error_not_orphaned'],
    ['no_group_or_project', 'orphan_recover_error_no_group_or_project'],
    ['no_available_slot', 'orphan_recover_error_no_available_slot'],
    ['slot_occupied', 'orphan_recover_error_slot_occupied'],
    ['slot_type_mismatch', 'orphan_recover_error_slot_type_mismatch'],
    ['no_file_path', 'orphan_recover_error_no_file_path'],
  ])('maps error.code=%s to its Korean-safe toast copy, never the server detail text', async (code, key) => {
    postRequest.mockRejectedValueOnce({
      response: { data: { error: { code, message: 'English server prose that must not reach the screen' } } },
    })
    const wrapper = mountOrphan(true, [MATCHING_SLOT])
    await wrapper.find('.dip-orphan-warning button').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledTimes(1)
    const [message] = showToast.mock.calls[0]
    expect(message).toBe(i18n.global.t(`main.doc_info_panel.${key}`))
    expect(message).not.toContain('English server prose')
    expect(wrapper.emitted('orphan-recovered')).toBeUndefined()
  })

  it('falls back to the generic failure copy when error.code is missing entirely', async () => {
    postRequest.mockRejectedValueOnce({ response: { data: { detail: 'raw english detail' } } })
    const wrapper = mountOrphan(true, [MATCHING_SLOT])
    await wrapper.find('.dip-orphan-warning button').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledTimes(1)
    const [message] = showToast.mock.calls[0]
    expect(message).toBe(i18n.global.t('main.doc_info_panel.orphan_recover_failed'))
    expect(message).not.toContain('raw english detail')
  })

  // TS0011 TC-4 gap (rejection rev0): the case above only ever sends a body with NO
  // error.code key at all. ORPHAN_RECOVER_ERROR_KEYS[code] is also undefined for a
  // code the map simply does not list — a distinct branch (string but not a lookup
  // hit) that the missing-key input can never exercise. This sends a present-but-
  // unrecognized code so that branch actually runs.
  it('falls back to the generic failure copy for a present but unrecognized error.code', async () => {
    postRequest.mockRejectedValueOnce({
      response: { data: { error: { code: 'some_future_code_this_client_does_not_know', message: 'raw english server message' } } },
    })
    const wrapper = mountOrphan(true, [MATCHING_SLOT])
    await wrapper.find('.dip-orphan-warning button').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledTimes(1)
    const [message] = showToast.mock.calls[0]
    expect(message).toBe(i18n.global.t('main.doc_info_panel.orphan_recover_failed'))
    expect(message).not.toContain('raw english server message')
    expect(message).not.toContain('some_future_code_this_client_does_not_know')
  })
})

// 0457 TR0010 rev1 반려 §1: the block above's beforeEach pins locale to 'en', so it only
// ever asserts the English translation of these keys — it cannot catch a missing/wrong
// Korean string, and it never proves the server's English detail stays off screen when
// Korean is the active locale. This block mounts under 'ko' and compares against the
// LITERAL Korean copy copied out of shared/i18n/ko.ts (not i18n.global.t() re-derived
// under the same locale, which would trivially match a wrong ko.ts string against itself).
describe('DocInfoPanel orphan recovery — 한국어 안내 (0457 T0009 §3 완료 기준, TR0010 rev1)', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
  })

  type CandidateSlot = { item_seq: number; type: string; empty: boolean }
  function mountOrphanKo(candidateSlots: CandidateSlot[] = []) {
    return mount(DocInfoPanel, {
      props: {
        docId: 'flowgate.default.0374.9999-TR',
        typeCode: 'TR',
        reviewStatus: 'pending_review',
        rejectReason: null,
        orphan: true,
        candidateSlots,
        stepStates: [] as StepState[],
        nextStepIndex: null,
        collapsed: false,
      },
      global: { plugins: [i18n] },
    })
  }
  const MATCHING_SLOT: CandidateSlot = { item_seq: 4, type: 'TR', empty: true }

  it('후보 칸이 없으면 버튼이 비활성화되고, 왜 안 되는지와 다음 행동(반려·보관)을 한국어로 보여준다', async () => {
    const wrapper = mountOrphanKo([{ item_seq: 4, type: 'NR', empty: true }])
    const button = wrapper.find('.dip-orphan-warning button')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.find('.dip-orphan-warning p').text())
      .toBe('타입이 일치하는 빈 칸이 없어 다시 연결할 수 없습니다. 반려하여 보관하세요.')
  })

  it.each([
    ['slot_type_not_recoverable', '이 문서 종류는 워크플로 칸에 다시 연결할 수 없습니다.'],
    ['not_orphaned', '문서가 이미 다른 칸에 연결되어 있어 다시 연결할 수 없습니다.'],
    ['no_group_or_project', '문서에 그룹 정보가 없어 다시 연결할 수 없습니다.'],
    ['no_available_slot', '연결할 수 있는 빈 칸을 찾지 못했습니다.'],
    ['slot_occupied', '그 칸은 이미 다른 문서가 차지하고 있습니다.'],
    ['slot_type_mismatch', '그 칸은 다른 문서 종류를 기다리고 있어 연결할 수 없습니다.'],
    ['no_file_path', '문서 파일 경로가 없어 다시 연결할 수 없습니다.'],
  ])('error.code=%s는 한국어 문구로 뜨고, 서버 영어 원문은 화면에 나타나지 않는다', async (code, koreanText) => {
    postRequest.mockRejectedValueOnce({
      response: { data: { error: { code, message: 'English server prose that must not reach the screen' } } },
    })
    const wrapper = mountOrphanKo([MATCHING_SLOT])
    await wrapper.find('.dip-orphan-warning button').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledTimes(1)
    const [message] = showToast.mock.calls[0]
    expect(message).toBe(koreanText)
    expect(message).not.toContain('English server prose')
    expect(message).not.toContain('English')
  })

  it('error.code가 아예 없으면 서버 detail 없이 일반 실패 문구(한국어)로 대체한다', async () => {
    postRequest.mockRejectedValueOnce({ response: { data: { detail: 'raw english detail' } } })
    const wrapper = mountOrphanKo([MATCHING_SLOT])
    await wrapper.find('.dip-orphan-warning button').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledTimes(1)
    const [message] = showToast.mock.calls[0]
    expect(message).toBe('문서를 다시 연결하지 못했습니다. 잠시 후 다시 시도하거나 반려하여 보관하세요.')
    expect(message).not.toContain('raw english detail')
    expect(message).not.toContain('english')
  })

  // TS0011 TC-4 gap (rejection rev0): a present-but-unmapped error.code is a different
  // branch than a missing one (ORPHAN_RECOVER_ERROR_KEYS[code] undefined either way,
  // but only this input proves the lookup miss itself falls back correctly, not just
  // the absent-key short-circuit).
  it('알 수 없는(매핑에 없는) error.code도 서버 message 없이 일반 실패 문구(한국어)로 대체한다', async () => {
    postRequest.mockRejectedValueOnce({
      response: { data: { error: { code: 'some_future_code_this_client_does_not_know', message: 'raw english server message' } } },
    })
    const wrapper = mountOrphanKo([MATCHING_SLOT])
    await wrapper.find('.dip-orphan-warning button').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledTimes(1)
    const [message] = showToast.mock.calls[0]
    expect(message).toBe('문서를 다시 연결하지 못했습니다. 잠시 후 다시 시도하거나 반려하여 보관하세요.')
    expect(message).not.toContain('raw english server message')
    expect(message).not.toContain('some_future_code_this_client_does_not_know')
    expect(message).not.toContain('english')
  })
})

// 0311 T0004 rev1: 질의 is a standalone section again (reject moved to the AI검수
// merge), but it keeps rev0's capped-card layout — cards in `.dip-qa-card`, headline
// actions in `.dip-qa-act` (`.dip-qa-fullview` / `.dip-qa-add`), the unanswered pill
// in `.dip-qa-count`, and the full-view dialog is QaHistoryDialog. TR0005 rev6 반려
// §3 ("질의는 빼라") split it back out of the merged dialog into its own again.
describe('DocInfoPanel notification Q&A open intent (0471 T0017)', () => {
  const props = {
    docId: 'flowgate.default.0471.0017-T',
    typeCode: 'T',
    reviewStatus: 'pending_review',
    rejectReason: null,
    stepStates: [] as StepState[],
    nextStepIndex: null,
    collapsed: true,
  }

  it('consumes the matching intent, expands the panel and Q&A section, and opens full view', async () => {
    const wrapper = mount(DocInfoPanel, { props, global: { plugins: [i18n] } })
    useQaOpenIntent().requestQaOpen(props.docId)
    await flushPromises()
    expect(wrapper.emitted('toggle')).toHaveLength(1)
    expect(wrapper.findComponent(QaHistoryDialog).props('visible')).toBe(true)
    expect(useQaOpenIntent().intent.value).toBeNull()
    // Both tests in this block target the same docId; an unmounted-but-still-reactive
    // watcher from this instance would otherwise race the next test's watcher for the
    // shared (module-singleton) intent and silently win the consumeQaOpen() call.
    wrapper.unmount()
  })

  it('does not consume another document intent and reopens on each fresh matching sequence', async () => {
    const wrapper = mount(DocInfoPanel, { props: { ...props, collapsed: false }, global: { plugins: [i18n] } })
    useQaOpenIntent().requestQaOpen('flowgate.default.0471.9999-T')
    await flushPromises()
    expect(wrapper.findComponent(QaHistoryDialog).props('visible')).toBe(false)
    expect(useQaOpenIntent().intent.value?.docId).toBe('flowgate.default.0471.9999-T')

    useQaOpenIntent().requestQaOpen(props.docId)
    await flushPromises()
    expect(wrapper.findComponent(QaHistoryDialog).props('visible')).toBe(true)
    await wrapper.findComponent(QaHistoryDialog).vm.$emit('update:visible', false)
    await flushPromises()
    useQaOpenIntent().requestQaOpen(props.docId)
    await flushPromises()
    expect(wrapper.findComponent(QaHistoryDialog).props('visible')).toBe(true)
    expect(useQaOpenIntent().intent.value).toBeNull()
    wrapper.unmount()
  })
})

describe('DocInfoPanel 질의 panel (group 0022 §3.1, 0311 T0004 rev1)', () => {
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

  it('renders the 질의 section header and fetches the qa container', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith('/api/v1/q/p.none.0001.0001-D')
    expect(wrapper.text()).toContain(i18n.global.t('main.doc_info_panel.section_qa'))
    // and it is NOT the merged AI검수·반려 section
    expect(wrapper.text()).toContain(i18n.global.t('main.doc_info_panel.section_review_reject'))
  })

  it('shows empty state when there are no queries', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const qaSection = wrapper.findAll('.dip-section').find((s) => s.find('.dip-qa-headline').exists())!
    expect(qaSection.find('.dip-reject-empty').exists()).toBe(true)
  })

  it('renders question cards; answered card carries the answered accent (group 0126 / C안)', async () => {
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
    // newest-first by seq: id 2 (unanswered) leads, id 1 (answered) follows
    // rev3: the card is the panel's existing amber .dip-qa-card again — an answered one
    // dims via .answered-card and STILL carries its [답변] mini-action, as it always did.
    expect(cards[0].classes()).not.toContain('answered-card')
    expect(cards[0].findAll('.dip-qa-card-actions .mini-action').length).toBe(1)
    expect(cards[1].classes()).toContain('answered-card') // has an answer
    expect(cards[1].findAll('.dip-qa-card-actions .mini-action').length).toBe(1)
    // headline unanswered-count badge reflects the single open query
    expect(wrapper.find('.dip-qa-count').text()).toContain('1')
  })

  it('an unanswered card exposes only [답변], which opens the full-view dialog focused on that query', async () => {
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
  // (now QaHistoryDialog, 0311 T0004), so this asserts the panel hands the
  // dialog working actions rather than clicking panel buttons that no longer exist.
  //
  // 0248 B0001 / NR0003 "테스트 공백": this was left as it.skip against the pre-0126 DOM,
  // so the ONE test that would have exercised the real ai-request POST never ran — the
  // endpoint being a no-op went unnoticed. Re-activated against the current structure.
  it('hands the dialog an AI request action that POSTs ai-request and tracks the run', async () => {
    getRequest.mockResolvedValue({
      data: { qa: { items: [
        { id: 9, seq: 1, title: 'Q', body: 'b', asker_kind: 'human', answer_count: 0, answers: [] },
      ] } },
    })
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'run-7', status: 'running' } })
    const wrapper = mountPanel()
    await flushPromises()

    const dialog = wrapper.findComponent(QaHistoryDialog)
    expect(dialog.props('aiRunItemId')).toBeNull()

    await (dialog.props('requestAiAnswer') as (id: number) => Promise<boolean>)(9)
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/q/p.none.0001.0001-D/items/9/answers/ai-request',
      {},
    )
    // The panel must reflect the live run, so the dialog can show it (0248 B0001).
    expect(dialog.props('aiRunItemId')).toBe(9)
  })

  // 0059 B0001 rework: a worker registering a Q on the doc on screen arrives via SSE
  // (qna_q_registered → fg:qa_refresh window event). The panel must refetch live so the
  // new Q surfaces WITHOUT F5. Without the listener it stayed at the mount-time fetch.
  //
  // Note: fg:qa_refresh is a global window event and earlier tests leave panels mounted,
  // so each live-refresh test uses a UNIQUE docId to isolate which panel reacts. We count
  // only GETs to this doc's own endpoint, not the shared mock's total call count.
  function mountWithDoc(docId: string) {
    return mount(DocInfoPanel, { props: { ...baseProps, docId }, global: { plugins: [i18n] } })
  }
  function getCallsFor(docId: string) {
    return getRequest.mock.calls.filter((c) => c[0] === `/api/v1/q/${docId}`).length
  }

  it('refetches the container on a matching fg:qa_refresh event (live, no F5)', async () => {
    const docId = 'q.live.match.0001-T'
    const wrapper = mountWithDoc(docId)
    await flushPromises()
    expect(getCallsFor(docId)).toBe(1) // mount fetch
    window.dispatchEvent(new CustomEvent('fg:qa_refresh', { detail: { doc_id: docId } }))
    await flushPromises()
    expect(getCallsFor(docId)).toBe(2) // live refetch
    wrapper.unmount()
  })

  it('ignores fg:qa_refresh for a different document', async () => {
    const docId = 'q.live.ignore.0001-T'
    const wrapper = mountWithDoc(docId)
    await flushPromises()
    expect(getCallsFor(docId)).toBe(1)
    window.dispatchEvent(new CustomEvent('fg:qa_refresh', { detail: { doc_id: 'some.other.doc-T' } }))
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
    window.dispatchEvent(new CustomEvent('fg:qa_refresh', { detail: { doc_id: docId } }))
    await flushPromises()
    expect(getCallsFor(docId)).toBe(1) // listener gone — no extra fetch
  })
})
