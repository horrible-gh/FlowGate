import { config, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

// These tests exercise dropdown actions, not DOM ownership. Render teleports in place so
// their existing wrapper-scoped selectors remain focused on the behavioral contract.
const originalGlobalStubs = { ...config.global.stubs }

beforeEach(() => {
  config.global.stubs = { ...originalGlobalStubs, teleport: true }
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({
    data: { ok: true, state: { branch: null, status: 'none', default_action: null, choices: [] } },
  })
  postRequest.mockResolvedValue({ data: { document: { doc_review_status: 'approved' } } })
})

afterEach(() => {
  config.global.stubs = { ...originalGlobalStubs }
})

describe('ReviewActionBar', () => {
  const defaultProps = {
    docId: 'test.doc',
    projectId: 'test-project',
    groupId: 'test-group',
    docRef: 'test-ref',
    reviewStatus: null,
  }

  it('1. PM scenario — viewing past sibling: hides all action buttons and shows only the move-to-head button', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        headDocId: 'test.test2.0001.0005-D',
        viewedDocId: 'test.test2.0001.0004-DS',
        mode: 'review',
      },
      global: {
        plugins: [i18n],
      },
    })

    // The status and label area (which stays as-is) can show info:
    expect(wrapper.text()).toContain('0005-D')

    // It should have the move button:
    const navBtn = wrapper.find('button.btn-primary')
    expect(navBtn.exists()).toBe(true)
    expect(navBtn.text()).toContain(i18n.global.t('main.review_action_bar.btn_go_to_head', { doc: '0005-D' }))

    // Standard action buttons such as approve/reject must not be visible:
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('2. Viewing head doc itself: renders standard action area normally (approve/reject shown)', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        headDocId: 'test.test2.0001.0005-D',
        viewedDocId: 'test.test2.0001.0005-D',
        mode: 'review',
      },
      global: {
        plugins: [i18n],
      },
    })

    // It should NOT show the navigation button:
    expect(wrapper.text()).not.toContain(i18n.global.t('main.review_action_bar.btn_go_to_head', { doc: '0005-D' }))

    // It should show standard review buttons:
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
  })

  it('3. No head (done state): showHeadLabel is false -> existing action area unchanged', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        headDocId: null,
        viewedDocId: 'test.doc',
        mode: 'review',
      },
      global: {
        plugins: [i18n],
      },
    })

    expect(wrapper.text()).not.toContain('로 이동')
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
  })

  it('4. Click nav button: emits open-head-doc with correct payload', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        headDocId: 'test.test2.0001.0005-D',
        headDocTitle: 'My Head Doc Title',
        headDocLabel: 'D',
        viewedDocId: 'test.test2.0001.0004-DS',
        mode: 'review',
      },
      global: {
        plugins: [i18n],
      },
    })

    const navBtn = wrapper.find('button.btn-primary')
    expect(navBtn.exists()).toBe(true)

    await navBtn.trigger('click')

    expect(wrapper.emitted('open-head-doc')).toBeTruthy()
    expect(wrapper.emitted('open-head-doc')?.[0]).toEqual([
      {
        docId: 'test.test2.0001.0005-D',
        title: 'My Head Doc Title',
        typeCode: 'D',
      },
    ])
  })

  it('7. headDocShort computed logic: extracts last segment cleanly', () => {
    // We can verify through VM instance computed property:
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        headDocId: 'test.test2.0001.0005-D',
        viewedDocId: 'test.test2.0001.0004-DS',
        mode: 'review',
      },
      global: {
        plugins: [i18n],
      },
    })

    expect((wrapper.vm as any).headDocShort).toBe('0005-D')
  })

  it('5. R decided + headStatus pending: mode=next renders [다음 단계] button', () => {
    // After T832 revert, R tab uses its own typeCode='R' and reviewStatus='wf_in_progress'.
    // actionBarPolicy.ts R-branch: wfDecided=true, headStatus=null → mode='next'.
    // ReviewActionBar must render the next-step button when mode='next'.
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.test2.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        nextStepLabel: 'DS',
      },
      global: {
        plugins: [i18n],
      },
    })

    // The [Next step] button (btn_next_step) must be rendered:
    const nextBtn = wrapper.find('.sfb-actions button.btn-primary')
    expect(nextBtn.exists()).toBe(true)
    expect(nextBtn.text()).toContain('DS')

    // approve/reject must NOT be present:
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('6. R decided + head doc in_progress on R tab: mode=next renders [다음 단계] not approve/reject', () => {
    // Simulate the R tab scenario after fix: R tab passes its own typeCode/reviewStatus.
    // R is the workflow root, so headDocId is null on the R tab itself.
    // Even when D (head doc) is in_progress, the R tab has headStatus=null (→ isHeadPending=true),
    // so actionBarPolicy resolves mode='next'. This confirms the approve/reject buttons
    // (a D-tab concern) do not appear on the R tab.
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.test2.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        nextStepLabel: 'DS',
        headDocId: null,
      },
      global: {
        plugins: [i18n],
      },
    })

    // The [Next step] button is rendered:
    const nextBtn = wrapper.find('.sfb-actions button.btn-primary')
    expect(nextBtn.exists()).toBe(true)
    expect(nextBtn.text()).toContain('DS')

    // No approve/reject buttons:
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  // ── Regression points (T834 D031 §9) ────────────────────────────────────────

  it('R1. R decided + head=pending → [다음 단계] button (enabled)', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'DS',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('DS')
    expect(btn.attributes('disabled')).toBeUndefined()
  })

  it('R2. R decided + head=in_progress → main button opens dropdown; no Proceed item is offered here (0366 T0007)', async () => {
    // 0366 T0007 removed the [다음 단계 진행] (Proceed to Next Step) dropdown item from the
    // action bar entirely — it is no longer a canNextAction-gated dropdown item, since the
    // sole remaining entry to NextActionModal is the workflow strip's current-step cell
    // (DocWorkflow.vue), which already carries its own canNextAction gate.
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        canNextAction: false,
        nextStepLabel: 'D',
      },
      global: { plugins: [i18n] },
    })
    const mainBtn = wrapper.find('.ab-dd-toggle')
    expect(mainBtn.exists()).toBe(true)
    expect(mainBtn.text()).toContain('D')
    // Main button is always enabled — it only opens the dropdown.
    expect(mainBtn.attributes('disabled')).toBeUndefined()

    await mainBtn.trigger('click')
    // Clicking the main button opens the dropdown rather than emitting next-action.
    expect(wrapper.find('.ab-split-dd').exists()).toBe(true)
    expect(wrapper.emitted('next-action')).toBeFalsy()

    const items = wrapper.findAll('.ab-split-dd .ab-split-item')
    expect(items.some(i => i.text().includes('Proceed to Next Step'))).toBe(false)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('R2-1. next mode: main button click opens the dropdown and does NOT emit next-action (0366 T0007)', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'DS',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.ab-split-dd').exists()).toBe(false)
    await wrapper.find('.ab-dd-toggle').trigger('click')
    expect(wrapper.find('.ab-split-dd').exists()).toBe(true)
    expect(wrapper.emitted('next-action')).toBeFalsy()

    // No dropdown item drives next-action any more — that action is reachable only
    // via the workflow strip's current-step cell (DocWorkflow.vue @next-action).
    const items = wrapper.findAll('.ab-split-dd .ab-split-item')
    expect(items.some(i => i.text().includes('Proceed to Next Step'))).toBe(false)
  })

  it('R2-2. next mode dropdown exposes a direct "Copy Mention" item that emits copy-next-mention (R0001 ③-b)', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'T',
        nextStepCode: 'T',
      },
      global: { plugins: [i18n] },
    })
    await wrapper.find('.ab-dd-toggle').trigger('click')
    const items = wrapper.findAll('.ab-split-dd .ab-split-item')
    // Order per reviewer: 승인 문서 생성 → 빈 문서 생성 → 멘트 복사 → AI 호출.
    // 0366 T0007 dropped [다음 단계 진행] entirely. The final item retains the
    // continuous-work behavior under the [Invoke AI] label.
    expect(items.map(i => i.text())).toEqual([
      'Create Approved Doc',
      'Create Empty Doc',
      'Copy Mention',
      'Invoke AI',
    ])
    const invokeItem = items.find(i => i.text().includes('Invoke AI'))!
    await invokeItem.trigger('click')
    expect(wrapper.emitted('continuous-work')).toHaveLength(1)
    await wrapper.find('.ab-dd-toggle').trigger('click')
    const copyItem = wrapper.findAll('.ab-split-dd .ab-split-item').find(i => i.text().includes('Copy Mention'))!
    await copyItem.trigger('click')
    expect(wrapper.emitted('copy-next-mention')).toHaveLength(1)
    // Selecting the item closes the dropdown.
    expect(wrapper.find('.ab-split-dd').exists()).toBe(false)
  })

  it('R2-3. next step TS → no "Create Approved Doc" item (group 0121 R0001: TS is token-issued)', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_in_progress',
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'TS',
        nextStepCode: 'TS',
      },
      global: { plugins: [i18n] },
    })
    await wrapper.find('.ab-dd-toggle').trigger('click')
    const labels = wrapper.findAll('.ab-split-dd .ab-split-item').map(i => i.text())
    // create-approved is gone for TS; the normal token path (copy mention) remains.
    expect(labels).not.toContain('Create Approved Doc')
    expect(labels).toContain('Copy Mention')
    expect(labels).not.toContain('Proceed to Next Step')
  })

  it('R3. R undecided → [워크플로 결정] button', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0001-R',
        docType: 'R',
        reviewStatus: 'decide_workflow',
        mode: 'workflow',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
    expect(wrapper.text()).not.toContain('다음 단계')
  })

  it('R3-1. workflow dropdown exposes manual, mention, and command actions', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0001-R',
        projectId: 'test',
        groupId: 'test.p.0001',
        docRef: 'test.p.0001.0001-R',
        docType: 'R',
        reviewStatus: null,
        mode: 'workflow',
      },
      global: { plugins: [i18n] },
    })

    await wrapper.find('.ab-dd-toggle').trigger('click')
    const items = wrapper.findAll('.ab-split-dd .ab-split-item')

    // The old standalone AI entry is removed; continuous-work keeps its behavior under [Invoke AI].
    expect(items.map(item => item.text())).toEqual([
      'Copy Mention',
      'Run Command',
      'Manual Decision',
      'Invoke AI',
    ])
    await items[2].trigger('click')
    expect(wrapper.emitted('decide-workflow')).toHaveLength(1)
  })

  it('R3-2. workflow mention and command actions emit the active document payload', async () => {
    const props = {
      ...defaultProps,
      docId: 'test.p.0001.0001-R',
      projectId: 'test',
      groupId: 'test.p.0001',
      docRef: 'test.p.0001.0001-R',
      docType: 'R',
      reviewStatus: null,
      mode: 'workflow' as const,
    }
    const payload = {
      docId: props.docId,
      projectId: props.projectId,
      groupId: props.groupId,
      docRef: props.docRef,
    }
    const wrapper = mount(ReviewActionBar, {
      props,
      global: { plugins: [i18n] },
    })

    await wrapper.find('.ab-dd-toggle').trigger('click')
    await wrapper.findAll('.ab-split-item')[0].trigger('click')
    expect(wrapper.emitted('copy-workflow-mention')?.[0]).toEqual([payload])

    await wrapper.find('.ab-dd-toggle').trigger('click')
    await wrapper.findAll('.ab-split-item')[1].trigger('click')
    expect(wrapper.emitted('invoke-workflow-command')?.[0]).toEqual([payload])
  })

  it('R4. R wf_done → info mode, empty action area', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0001-R',
        docType: 'R',
        reviewStatus: 'wf_done',
        mode: 'info',
      },
      global: { plugins: [i18n] },
    })
    const actions = wrapper.find('.sfb-actions')
    expect(actions.exists()).toBe(true)
    // No action buttons
    expect(wrapper.find('.sfb-actions button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('R5. non-R pending_review → Approve/Reject buttons shown', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0003-D',
        docType: 'D',
        reviewStatus: 'pending_review',
        mode: 'review',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.text()).toContain('Approve')
    expect(wrapper.text()).toContain('Reject')
  })

  it('R5-1. uses AI invoke as the review split-button default when a provider exists', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0003-D',
        docType: 'D',
        reviewStatus: 'pending_review',
        mode: 'review',
        hasAiProvider: true,
      },
      global: { plugins: [i18n] },
    })

    const mainButton = wrapper.find('.ab-split-main')
    expect(mainButton.text()).toContain('Request Review')

    await mainButton.trigger('click')
    expect(wrapper.emitted('invoke-review-ai')).toHaveLength(1)
    expect(wrapper.emitted('open-mention-dialog')).toBeFalsy()
  })

  it('R5-2. preserves mention copy as the review split-button default without a provider', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0003-D',
        docType: 'D',
        reviewStatus: 'pending_review',
        mode: 'review',
        hasAiProvider: false,
      },
      global: { plugins: [i18n] },
    })

    const mainButton = wrapper.find('.ab-split-main')
    expect(mainButton.text()).toContain('Request Review')

    await mainButton.trigger('click')
    expect(wrapper.emitted('open-mention-dialog')).toHaveLength(1)
    expect(wrapper.emitted('invoke-review-ai')).toBeFalsy()
  })

  it('R6. non-R approved + next step → [다음 단계] enabled', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0003-D',
        docType: 'D',
        reviewStatus: 'approved',
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'T',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('T')
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  // ── R0001 (0078): final-approval (AC) carve-out ─────────────────────────────

  it('R6-AC1. next step = AC → single [Final Approval] button, no dropdown/list', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0005-D',
        docType: 'D',
        reviewStatus: 'approved',
        mode: 'next',
        canNextAction: true,
        nextStepLabel: 'AC',
        nextStepCode: 'AC',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('Final Approval')
    // No drop-up toggle / dropdown (the AC carve-out replaces them entirely).
    expect(wrapper.find('.ab-dd-toggle').exists()).toBe(false)
    expect(wrapper.find('.ab-split-dd').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('R6-AC2. clicking the [Final Approval] button emits next-action', async () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0005-D',
        docType: 'D',
        reviewStatus: 'approved',
        mode: 'next',
        canNextAction: true,
        nextStepCode: 'AC',
      },
      global: { plugins: [i18n] },
    })
    await wrapper.find('.sfb-actions button.btn-primary').trigger('click')
    expect(wrapper.emitted('next-action')).toHaveLength(1)
  })

  it('R6-AC3. AC button respects canNextAction gate (disabled when false)', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0005-D',
        docType: 'D',
        reviewStatus: 'approved',
        mode: 'next',
        canNextAction: false,
        nextStepCode: 'AC',
      },
      global: { plugins: [i18n] },
    })
    const btn = wrapper.find('.sfb-actions button.btn-primary')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('R7. non-R rejected → rework toolbar (mode=rejected)', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0003-D',
        docType: 'D',
        reviewStatus: 'rejected',
        mode: 'rejected',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.sfb-actions--rework').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('R8. Q tab → Q guidance hint shown', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.q.0001-Q',
        docType: 'Q',
        reviewStatus: null,
        mode: 'q',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.sfb-hint').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })

  it('R9. isViewingPastDoc → [head doc 로 이동] button only (no normal actions)', () => {
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'test.p.0001.0003-D',
        docType: 'D',
        reviewStatus: 'pending_review',
        mode: 'review',
        headDocId: 'test.p.0001.0005-D',
        viewedDocId: 'test.p.0001.0003-D',
      },
      global: { plugins: [i18n] },
    })
    // isViewingPastDoc = headDocId !== viewedDocId
    const navBtn = wrapper.find('.sfb-actions button.btn-primary')
    expect(navBtn.exists()).toBe(true)
    expect(navBtn.text()).toContain(i18n.global.t('main.review_action_bar.btn_go_to_head', { doc: '0005-D' }))
    expect(wrapper.text()).not.toContain('Approve')
    expect(wrapper.text()).not.toContain('Reject')
  })
  it('AC approval with a git conflict opens the Git status panel event path', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        git: { ok: true, result: { status: 'conflict', conflict_files: ['client/a.ts'] } },
      },
    })
    const events: any[] = []
    const onOpen = (e: Event) => events.push((e as CustomEvent).detail)
    window.addEventListener('fg:git_status_open', onOpen)
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'flowgate.default.0170.0005-AC',
        projectId: 'flowgate',
        groupId: 'flowgate.default.0170',
        docRef: 'flowgate.default.0170.0005-AC',
        docType: 'AC',
        reviewStatus: 'pending_review',
        mode: 'review',
      },
      global: { plugins: [i18n] },
    })

    try {
      await (wrapper.vm as any).doApprove()
      await flushPromises()
      expect(events).toEqual([
        { project: 'flowgate', group_id: 'flowgate.default.0170', status: 'conflict' },
      ])
      expect(wrapper.emitted('approve')?.[0]).toEqual(['approved'])
    } finally {
      window.removeEventListener('fg:git_status_open', onOpen)
      wrapper.unmount()
    }
  })

  it('AC approval with terminal git result refreshes the Git button without opening the panel', async () => {
    postRequest.mockResolvedValueOnce({
      data: {
        document: { doc_review_status: 'approved' },
        git: { ok: true, result: { status: 'merged', merge_commit: 'abc123' } },
      },
    })
    const refreshEvents: any[] = []
    const openEvents: any[] = []
    const onRefresh = (e: Event) => refreshEvents.push((e as CustomEvent).detail)
    const onOpen = (e: Event) => openEvents.push((e as CustomEvent).detail)
    window.addEventListener('fg:git_status_refresh', onRefresh)
    window.addEventListener('fg:git_status_open', onOpen)
    const wrapper = mount(ReviewActionBar, {
      props: {
        ...defaultProps,
        docId: 'flowgate.default.0170.0005-AC',
        projectId: 'flowgate',
        groupId: 'flowgate.default.0170',
        docRef: 'flowgate.default.0170.0005-AC',
        docType: 'AC',
        reviewStatus: 'pending_review',
        mode: 'review',
      },
      global: { plugins: [i18n] },
    })

    try {
      await (wrapper.vm as any).doApprove()
      await flushPromises()
      expect(refreshEvents).toEqual([
        { project: 'flowgate', group_id: 'flowgate.default.0170', status: 'merged' },
      ])
      expect(openEvents).toEqual([])
    } finally {
      window.removeEventListener('fg:git_status_refresh', onRefresh)
      window.removeEventListener('fg:git_status_open', onOpen)
      wrapper.unmount()
    }
  })

  // The bar used to publish its measured height so the floating run miniplayer could park
  // above it (TR0007 rev3). The miniplayer moved into the app header (0269 NR0011), so
  // nothing floats over this bar any more and the bar must not leave a stray custom
  // property behind on the document element.
  it('no longer publishes a bottom-offset custom property', () => {
    const wrapper = mount(ReviewActionBar, {
      props: { ...defaultProps, mode: 'review' },
      global: { plugins: [i18n] },
    })

    try {
      expect(
        document.documentElement.style.getPropertyValue('--fg-actionbar-h'),
      ).toBe('')
    } finally {
      wrapper.unmount()
    }
  })

  // ── B0001/N0002/NR0003: group-wide AI-run lock ──────────────────────────────
  describe('AI-run lock (group busy)', () => {
    function markGroupRunning(groupId: string) {
      const store = useAiInvokeRunsStore()
      store.trackStarted({ run_id: 'run-1', group_id: groupId, status: 'running' })
    }

    it('L1. review mode busy: approve/reject/review-request split are all disabled and the AI Running pill shows', () => {
      markGroupRunning('test-group')
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0001.0003-D',
          docType: 'D',
          reviewStatus: 'pending_review',
          mode: 'review',
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).toContain('AI Running')
      const buttons = wrapper.findAll('.sfb-actions button')
      expect(buttons.length).toBeGreaterThan(0)
      for (const btn of buttons) {
        expect(btn.attributes('disabled')).toBeDefined()
      }
    })

    it('L2. review mode not busy: no AI Running pill and buttons stay enabled', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0001.0003-D',
          docType: 'D',
          reviewStatus: 'pending_review',
          mode: 'review',
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).not.toContain('AI Running')
      const approveBtn = wrapper.findAll('.sfb-actions button').find(b => b.text().includes('Approve'))!
      expect(approveBtn.attributes('disabled')).toBeUndefined()
    })

    it('L3. review-request dropdown items are disabled when busy', async () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0001.0003-D',
          docType: 'D',
          reviewStatus: 'pending_review',
          mode: 'review',
        },
        global: { plugins: [i18n] },
      })
      // Open the dropdown while NOT busy (the caret itself is disabled once busy).
      await wrapper.find('.ab-split-caret').trigger('click')
      markGroupRunning('test-group')
      await wrapper.vm.$nextTick()
      const items = wrapper.findAll('.ab-split-dd .ab-split-item')
      expect(items.length).toBeGreaterThan(0)
      for (const item of items) {
        expect(item.attributes('disabled')).toBeDefined()
      }
    })

    it('L4. workflow mode busy: the decide-workflow toggle is disabled', () => {
      markGroupRunning('test.p.0001')
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0001.0001-R',
          projectId: 'test',
          groupId: 'test.p.0001',
          docRef: 'test.p.0001.0001-R',
          docType: 'R',
          reviewStatus: null,
          mode: 'workflow',
        },
        global: { plugins: [i18n] },
      })
      expect(wrapper.find('.ab-dd-toggle').attributes('disabled')).toBeDefined()
    })

    it('L5. next mode (general) busy: the next-step toggle is disabled', () => {
      markGroupRunning('test.p.0001')
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0001.0001-R',
          groupId: 'test.p.0001',
          docType: 'R',
          reviewStatus: 'wf_in_progress',
          mode: 'next',
          canNextAction: true,
          nextStepLabel: 'DS',
        },
        global: { plugins: [i18n] },
      })
      expect(wrapper.find('.ab-dd-toggle').attributes('disabled')).toBeDefined()
    })

    it('L6. next mode (AC final approval) busy: single button disabled', () => {
      markGroupRunning('test.p.0001')
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0001.0005-D',
          groupId: 'test.p.0001',
          docType: 'D',
          reviewStatus: 'approved',
          mode: 'next',
          canNextAction: true,
          nextStepCode: 'AC',
        },
        global: { plugins: [i18n] },
      })
      const btn = wrapper.find('.sfb-actions button.btn-primary')
      expect(btn.attributes('disabled')).toBeDefined()
    })

    it('L7. next mode (test report pending) busy: run split buttons disabled', () => {
      markGroupRunning('test.p.0001')
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0001.0004-TS',
          groupId: 'test.p.0001',
          docType: 'TS',
          reviewStatus: 'approved',
          mode: 'next',
          canNextAction: true,
          nextStepCode: 'TSR',
          testRunStatus: null,
        },
        global: { plugins: [i18n] },
      })
      expect(wrapper.find('.ab-split-main').attributes('disabled')).toBeDefined()
      expect(wrapper.find('.ab-split-caret').attributes('disabled')).toBeDefined()
    })

    it('L8. rejected mode busy: rework tools and mark-revised are disabled', () => {
      markGroupRunning('test-group')
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0001.0003-D',
          docType: 'D',
          reviewStatus: 'rejected',
          mode: 'rejected',
        },
        global: { plugins: [i18n] },
      })
      const buttons = wrapper.findAll('.sfb-actions--rework button')
      expect(buttons.length).toBeGreaterThan(0)
      for (const btn of buttons) {
        expect(btn.attributes('disabled')).toBeDefined()
      }
    })

    it('L9. isViewingPastDoc [go to head] button stays enabled even when the group is busy', () => {
      markGroupRunning('test-group')
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          headDocId: 'test.test2.0001.0005-D',
          viewedDocId: 'test.test2.0001.0004-DS',
          mode: 'review',
        },
        global: { plugins: [i18n] },
      })
      const navBtn = wrapper.find('button.btn-primary')
      expect(navBtn.exists()).toBe(true)
      // The lock never touches the one button the spec explicitly exempts —
      // it just navigates and changes no state.
      expect(navBtn.attributes('disabled')).toBeUndefined()
    })
  })

  // ── 0441 TR0005 rework (rejection: "테스트 중일때는 액션바를 전부 비활성화 해놔야
  // 할거아냐"): TestRunStrip's 0163/0169 re-run relaxation lets a run be bound and
  // running/cancelling while the TS document itself is still pending_review/revised
  // (mode='review') or rejected (mode='rejected') — i.e. this document's OWN test run,
  // not an AI-invoke run for the group. Before this fix, isGroupBusy was the only lock
  // consulted in those modes, so approve/reject/review-request/rework stayed clickable
  // for the run's entire duration. No group-wide AI-invoke run is started in any of
  // these tests — the lock must fire from testRunStatus alone.
  describe('Test-run lock (this document busy)', () => {
    it('L10. review mode busy from a running test on THIS doc: approve/reject/review-request split are all disabled and the Test Running pill shows', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0441.0004-TS',
          docType: 'TS',
          reviewStatus: 'pending_review',
          mode: 'review',
          testRunStatus: 'running',
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).toContain('Test Running')
      expect(wrapper.text()).not.toContain('AI Running')
      const buttons = wrapper.findAll('.sfb-actions button')
      expect(buttons.length).toBeGreaterThan(0)
      for (const btn of buttons) {
        expect(btn.attributes('disabled')).toBeDefined()
      }
    })

    it('L11. review mode not busy when testRunStatus is null: no Test Running pill and buttons stay enabled (positive control for L10)', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0441.0004-TS',
          docType: 'TS',
          reviewStatus: 'pending_review',
          mode: 'review',
          testRunStatus: null,
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).not.toContain('Test Running')
      const approveBtn = wrapper.findAll('.sfb-actions button').find(b => b.text().includes('Approve'))!
      expect(approveBtn.attributes('disabled')).toBeUndefined()
    })

    it.each(['running', 'cancelling'])('L12. rejected mode busy from a %s test on THIS doc: rework tools and mark-revised are disabled', (status) => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0441.0004-TS',
          docType: 'TS',
          reviewStatus: 'rejected',
          mode: 'rejected',
          testRunStatus: status,
        },
        global: { plugins: [i18n] },
      })
      expect(wrapper.text()).toContain('Test Running')
      const buttons = wrapper.findAll('.sfb-actions--rework button')
      expect(buttons.length).toBeGreaterThan(0)
      for (const btn of buttons) {
        expect(btn.attributes('disabled')).toBeDefined()
      }
    })

    it.each(['failed', 'passed', 'cancelled', null])('L13. review mode NOT busy once the bound run reaches a terminal status (%s): buttons stay enabled', (status) => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0441.0004-TS',
          docType: 'TS',
          reviewStatus: 'pending_review',
          mode: 'review',
          testRunStatus: status,
        },
        global: { plugins: [i18n] },
      })
      expect(wrapper.text()).not.toContain('Test Running')
      const approveBtn = wrapper.findAll('.sfb-actions button').find(b => b.text().includes('Approve'))!
      expect(approveBtn.attributes('disabled')).toBeUndefined()
    })
  })

  // Rejection rev2: "테스트 중일때는 '그룹 내 다른 문서의' 액션바'도' 전부 비활성화 해놓아야 할거아냐".
  // rev1 keyed the lock on props.testRunStatus, which is the ACTIVE tab's own run embed and is
  // therefore null on every sibling document — switching tabs mid-run handed back a fully live
  // action bar. The lock is now keyed on groupTestRunActive, the group-scoped flag the server
  // ships on the document detail of EVERY document of the group. No AI-invoke group lock is set
  // anywhere in this block, so a failure here cannot be the isGroupBusy path passing by accident.
  describe('Test-run lock (another document in the group busy)', () => {
    const siblingProps = {
      ...defaultProps,
      docId: 'test.p.0441.0005-TR',
      groupId: 'test.p.0441',
      docType: 'TR',
    }

    it('L14. review mode on a SIBLING doc: its own testRunStatus is null, yet the group run disables every action and shows the pill', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...siblingProps,
          reviewStatus: 'pending_review',
          mode: 'review',
          testRunStatus: null,
          groupTestRunActive: true,
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).toContain('Test Running')
      expect(wrapper.text()).not.toContain('AI Running')
      const buttons = wrapper.findAll('.sfb-actions button')
      expect(buttons.length).toBeGreaterThan(0)
      for (const btn of buttons) {
        expect(btn.attributes('disabled')).toBeDefined()
      }
    })

    it('L15. positive control for L14: the identical sibling mount with no group run keeps its buttons live', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...siblingProps,
          reviewStatus: 'pending_review',
          mode: 'review',
          testRunStatus: null,
          groupTestRunActive: false,
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).not.toContain('Test Running')
      const approveBtn = wrapper.findAll('.sfb-actions button').find(b => b.text().includes('Approve'))!
      expect(approveBtn.attributes('disabled')).toBeUndefined()
    })

    it('L16. rejected mode on a SIBLING doc: the rework tools and mark-revised go inert for the group run', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...siblingProps,
          docType: 'D',
          reviewStatus: 'rejected',
          mode: 'rejected',
          testRunStatus: null,
          groupTestRunActive: true,
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).toContain('Test Running')
      const buttons = wrapper.findAll('.sfb-actions--rework button')
      expect(buttons.length).toBeGreaterThan(0)
      for (const btn of buttons) {
        expect(btn.attributes('disabled')).toBeDefined()
      }
    })

    it('L17. next mode on the R root: the [next step] button, its caret and every drop-up item are disabled by the group run', async () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0441.0001-R',
          groupId: 'test.p.0441',
          docType: 'R',
          reviewStatus: 'wf_in_progress',
          mode: 'next',
          canNextAction: true,
          nextStepCode: 'D',
          nextStepLabel: 'D',
          groupTestRunActive: true,
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).toContain('Test Running')
      const toggle = wrapper.find('.ab-dd-toggle')
      expect(toggle.exists()).toBe(true)
      expect(toggle.attributes('disabled')).toBeDefined()
      // The caret is disabled, so open the menu the only way left in a unit test and assert
      // the items behind it are inert too — a disabled trigger in front of live items is
      // still a live path once anything re-enables the trigger.
      await toggle.trigger('click')
      const buttons = wrapper.findAll('.sfb-actions button')
      expect(buttons.length).toBeGreaterThan(0)
      for (const btn of buttons) {
        expect(btn.attributes('disabled')).toBeDefined()
      }
      expect(wrapper.emitted('create-empty')).toBeUndefined()
    })

    it('L18. a TS tab whose OWN run is in flight still locks (rev1 behaviour preserved when the group flag is absent)', () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...defaultProps,
          docId: 'test.p.0441.0004-TS',
          groupId: 'test.p.0441',
          docType: 'TS',
          reviewStatus: 'pending_review',
          mode: 'review',
          testRunStatus: 'running',
          groupTestRunActive: false,
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.text()).toContain('Test Running')
      const buttons = wrapper.findAll('.sfb-actions button')
      expect(buttons.length).toBeGreaterThan(0)
      for (const btn of buttons) {
        expect(btn.attributes('disabled')).toBeDefined()
      }
    })
  })

  // ── 0441 T0004 item 4 (B0001 / NR0003 §4) ────────────────────────────────────────────
  // The workflow head is a pending TSR slot. The head is a property of the GROUP, so every
  // open tab of that group receives nextStepCode='TSR'. Before this fix only the TS tab was
  // special-cased, and every other tab rendered the generic [next step] drop-up whose
  // [Create Empty Doc] really did create a manual TSR — B0001's "a test report shows up on
  // other documents" and "the action bar differs from the TS document's".
  //
  // NR0003 §4 measured the gap this closes: across the whole client suite there was no
  // ReviewActionBar mount with nextStepCode='TSR' and a docType other than 'TS'.
  describe('0441: pending TSR head renders per tab type', () => {
    const tsrHeadProps = {
      ...defaultProps,
      docId: 'test.p.0441.0001-R',
      groupId: 'test.p.0441',
      mode: 'next' as const,
      canNextAction: true,
      nextStepCode: 'TSR',
      nextStepLabel: 'Test Report',
    }

    // A positive control for the negative assertions below: with an ordinary next step the
    // very same mount DOES produce the drop-up and all three manual items. Without it the
    // "no create-empty" expectations would also pass against a component rendering nothing.
    it('control: an ordinary (non server-assembled) next step still offers the manual items', async () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...tsrHeadProps,
          docType: 'R',
          reviewStatus: 'wf_in_progress',
          nextStepCode: 'D',
          nextStepLabel: 'D',
        },
        global: { plugins: [i18n] },
      })
      expect(wrapper.find('.ab-dd-wrap').exists()).toBe(true)
      await wrapper.find('.ab-dd-toggle').trigger('click')
      const labels = wrapper.findAll('.ab-split-dd .ab-split-item').map(i => i.text())
      expect(labels.some(l => l.includes('Create Empty Doc'))).toBe(true)
      expect(labels.some(l => l.includes('Copy Mention'))).toBe(true)
      expect(labels.some(l => l.includes('Invoke AI'))).toBe(true)
      expect(wrapper.find('[data-test="ab-server-assembled-hint"]').exists()).toBe(false)
    })

    it.each([
      ['R', 'wf_in_progress'],
      ['B', 'wf_in_progress'],
      ['D', 'approved'],
    ])('%s tab on a TSR head with no run in flight: the next-step control is present and ENABLED, no manual create/copy/invoke, and no run-it-on-TS explanation is exposed', async (docType, reviewStatus) => {
      const wrapper = mount(ReviewActionBar, {
        props: { ...tsrHeadProps, docType, reviewStatus },
        global: { plugins: [i18n] },
      })

      // 0441 TR0005 rev6 (rejection: "아예 없애버리면 어떻게해"): a bare hint with zero
      // buttons reads as "the feature vanished". Exactly one button stays visible here.
      const buttons = wrapper.findAll('.sfb-actions button')
      expect(buttons).toHaveLength(1)
      expect(wrapper.find('.ab-split-wrap').exists()).toBe(false)

      // 0441 TR0005 rev9 (rejection: "테스트 실행중 아닌데 왜 버튼 비활성화 되냐?"):
      // rev6..rev8 hard-coded `disabled` here, so the control was dead even with nothing
      // running. Idle now means pressable, and pressing it navigates to the TS document.
      expect(buttons[0].attributes('disabled')).toBeUndefined()
      await buttons[0].trigger('click')
      expect(wrapper.emitted('open-test-scenario')).toHaveLength(1)
      expect(wrapper.emitted('open-test-scenario')![0][0]).toMatchObject({
        groupId: 'test.p.0441',
        docId: 'test.p.0441.0001-R',
      })
      // Navigation only — none of the three manual-authoring events may ride along.
      expect(wrapper.emitted('create-empty')).toBeUndefined()
      expect(wrapper.emitted('copy-next-mention')).toBeUndefined()
      expect(wrapper.emitted('continuous-work')).toBeUndefined()

      const text = wrapper.text()
      expect(text).not.toContain('Create Empty Doc')
      expect(text).not.toContain('Copy Mention')
      expect(text).not.toContain('Invoke AI')

      // 0441 TR0005 rev8: the rejected explanation is absent both as standing text and as
      // hover text; only the next-step control remains.
      expect(wrapper.find('[data-test="ab-server-assembled-hint"]').exists()).toBe(false)
      expect(buttons[0].attributes('title')).toBeUndefined()
    })

    // The other half of the same contract, and the one rejections 1-5 were about: while the
    // group has a run in flight the very same control IS disabled (and still not removed).
    it.each([
      ['R', 'wf_in_progress'],
      ['B', 'wf_in_progress'],
      ['D', 'approved'],
    ])('%s tab on a TSR head while the group is running a test: the same control is visible but disabled', async (docType, reviewStatus) => {
      const wrapper = mount(ReviewActionBar, {
        props: { ...tsrHeadProps, docType, reviewStatus, groupTestRunActive: true },
        global: { plugins: [i18n] },
      })

      const buttons = wrapper.findAll('.sfb-actions button')
      expect(buttons).toHaveLength(1)
      expect(buttons[0].attributes('disabled')).toBeDefined()
      await buttons[0].trigger('click')
      expect(wrapper.emitted('open-test-scenario')).toBeUndefined()
    })

    it.each([
      ['R', 'wf_in_progress'],
      ['B', 'wf_in_progress'],
      ['D', 'approved'],
    ])('%s tab on a TSR head emits neither create-empty nor copy-next-mention', (docType, reviewStatus) => {
      const wrapper = mount(ReviewActionBar, {
        props: { ...tsrHeadProps, docType, reviewStatus },
        global: { plugins: [i18n] },
      })
      // Asserted on the emit contract itself, not just on an absent selector, because these
      // are the events MainPanel turns into server requests.
      expect(wrapper.emitted('create-empty')).toBeUndefined()
      expect(wrapper.emitted('copy-next-mention')).toBeUndefined()
      expect(wrapper.emitted('continuous-work')).toBeUndefined()
    })

    it('TS tab on a TSR head keeps the idle [Run tests] split button and its allowed menu', async () => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...tsrHeadProps,
          docId: 'test.p.0441.0004-TS',
          docType: 'TS',
          reviewStatus: 'approved',
          testRunStatus: null,
        },
        global: { plugins: [i18n] },
      })

      expect(wrapper.find('[data-test="ab-server-assembled-hint"]').exists()).toBe(false)
      const main = wrapper.find('.ab-split-main')
      expect(main.exists()).toBe(true)
      expect(main.text()).toContain('Run tests')
      expect(main.classes()).not.toContain('ab-split-main--status')

      await wrapper.find('.ab-split-caret').trigger('click')
      const labels = wrapper.findAll('.ab-split-dd .ab-split-item').map(i => i.text())
      expect(labels.some(l => l.includes('Copy Mention'))).toBe(true)
      expect(labels.some(l => l.includes('Invoke AI'))).toBe(true)
      // The manual escape hatch the TS branch has always withheld stays withheld.
      expect(labels.some(l => l.includes('Create Empty Doc'))).toBe(false)

      await main.trigger('click')
      expect(wrapper.emitted('run-test')).toHaveLength(1)
      expect(wrapper.emitted('create-empty')).toBeUndefined()
    })

    it.each(['running', 'cancelling'])('TS tab on a TSR head shows the %s status indicator, not a menu', (status) => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...tsrHeadProps,
          docId: 'test.p.0441.0004-TS',
          docType: 'TS',
          reviewStatus: 'approved',
          testRunStatus: status,
        },
        global: { plugins: [i18n] },
      })
      const main = wrapper.find('.ab-split-main')
      expect(main.exists()).toBe(true)
      expect(main.classes()).toContain('ab-split-main--status')
      expect(main.attributes('disabled')).toBeDefined()
      expect(wrapper.find('.ab-dd-wrap').exists()).toBe(false)
      expect(wrapper.text()).not.toContain('Create Empty Doc')
    })

    // The same hole reached from the TS tab: a RED run leaves the head on TSR with a
    // terminal status, which used to drop out of the TS branch (it only matched
    // null/running/cancelling) and into the generic manual-creation drop-up.
    it.each(['failed', 'passed', 'cancelled'])('TS tab on a TSR head after a %s run offers a re-run, never the manual menu', (status) => {
      const wrapper = mount(ReviewActionBar, {
        props: {
          ...tsrHeadProps,
          docId: 'test.p.0441.0004-TS',
          docType: 'TS',
          reviewStatus: 'approved',
          testRunStatus: status,
        },
        global: { plugins: [i18n] },
      })
      expect(wrapper.find('.ab-dd-wrap').exists()).toBe(false)
      expect(wrapper.text()).not.toContain('Create Empty Doc')
      const main = wrapper.find('.ab-split-main')
      expect(main.exists()).toBe(true)
      expect(main.text()).toContain('Run tests')
    })
  })
})
