import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import { publishTestDocumentView } from '@main/components/documents/testSpecRun'

// flowgate.default.0684 T#3 (D#1 §3-8, §6-1): on a contract-2 TS the action bar's test slot is
// the run's state pill, [run again] through POST /test-spec/runs and, while a run is in
// flight, [cancel]. The legacy [run test] (/documents/test-run) stays a contract-1 control.

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

const TS_ID = 'flowgate.default.0684.0010-TS'

function mountBar(extra: Record<string, unknown> = {}) {
  return mount(ReviewActionBar, {
    props: {
      docId: TS_ID,
      projectId: 'flowgate',
      groupId: 'flowgate.default.0684',
      docRef: TS_ID,
      mode: 'next',
      docType: 'TS',
      nextStepCode: 'TSR',
      reviewStatus: 'approved',
      testRunStatus: null,
      groupTestRunActive: false,
      testContractVersion: 2,
      ...extra,
    },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
}

function view(extra: Record<string, unknown> = {}) {
  return {
    kind: 'TS', doc_id: TS_ID, contract_version: 2, doc_review_status: 'approved',
    tsr_review_status: 'draft', cases: [], errors: [], latest_result: null, active_run: null,
    ...extra,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockResolvedValue({
    data: { ok: true, state: { branch: null, status: 'none', default_action: null, choices: [] } },
  })
  postRequest.mockResolvedValue({ data: { run_id: 'trun_new' } })
})

afterEach(() => {
  publishTestDocumentView(TS_ID, null)
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('ReviewActionBar — contract-2 run state (0684 T#3)', () => {
  it('replaces the legacy [run test] with the state pill and [run again] on the spec run route', async () => {
    publishTestDocumentView(TS_ID, view({
      latest_result: { run_id: 'trun_r', overall: 'PASS', created_at: '2026-10-08T01:00:00' },
      effective_result: { summary: { overall: 'PASS' }, cases: [] },
      basis_valid: true,
      basis_verdict: { state: 'valid', reasons: [] },
    }) as any)
    const wrapper = mountBar()
    await flushPromises()
    expect(wrapper.find('[data-test="ab-spec-run"]').exists()).toBe(true)
    expect(wrapper.find('.ab-split-main').exists()).toBe(false)  // no legacy [run test]
    expect(wrapper.find('[data-test="ab-spec-run-pill"]').text()).toContain('Test finished — PASS')
    expect(wrapper.find('[data-test="ab-spec-cancel"]').exists()).toBe(false)
    const rerun = wrapper.find('[data-test="ab-spec-rerun"]')
    expect(rerun.attributes('disabled')).toBeUndefined()
    await rerun.trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${encodeURIComponent(TS_ID)}/test-spec/runs`, {})
    expect(postRequest.mock.calls.some(([url]) => String(url).includes('/documents/test-run'))).toBe(false)
    expect(wrapper.emitted('run-test')).toBeUndefined()
    expect(showToast).toHaveBeenCalledWith('Test run accepted.', 'success')
    wrapper.unmount()
  })

  it.each([
    ['queued', {}, 'Test queued'],
    ['preparing', {}, 'Preparing test'],
    ['executing', { selected_case_ids: ['TC-001', 'TC-002', 'TC-003'], reported_case_ids: ['TC-001'] }, 'Running test (1/3)'],
    ['finalizing', {}, 'Finalizing results'],
  ])('shows the %s phase, holds [run again] and offers [cancel]', async (phase, extra, label) => {
    publishTestDocumentView(TS_ID, view({
      active_run: { run_id: 'trun_live', status: 'running', phase, ...extra },
    }) as any)
    const wrapper = mountBar({ testRunStatus: 'running', groupTestRunActive: true })
    await flushPromises()
    expect(wrapper.find('[data-test="ab-spec-run-pill"]').text()).toContain(label)
    expect(wrapper.find('[data-test="ab-spec-rerun"]').attributes('disabled')).toBeDefined()
    const cancel = wrapper.find('[data-test="ab-spec-cancel"]')
    expect(cancel.attributes('disabled')).toBeUndefined()  // the busy bar never locks [cancel]
    await cancel.trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/test-run/trun_live/cancel', {})
    wrapper.unmount()
  })

  it.each([
    [{ last_execution: { run_id: 'trun_x', status: 'failed', error: 'basis_capture_failed',
      prepare_refused: { error: 'basis_capture_failed' }, created_at: '2026-10-08T02:00:00' } },
    'Preparation failed — the source could not be copied and measured'],
    [{ last_execution: { run_id: 'trun_x', status: 'cancelled', created_at: '2026-10-08T02:00:00' } }, 'Test stopped'],
    [{ latest_result: { run_id: 'r', overall: 'PASS' }, effective_result: { summary: { overall: 'PASS' }, cases: [] },
      basis_valid: false, basis_verdict: { state: 'stale', reasons: ['source_changed'] } },
    'Result differs from the current source'],
    [{ latest_result: { run_id: 'r', overall: 'FAIL' }, effective_result: { summary: { overall: 'FAIL' }, cases: [] },
      basis_valid: null, basis_verdict: { state: 'unchecked', reasons: [] } },
    'Result FAIL · current source not checked'],
    [{}, 'No test result'],
  ])('names the outcome a run left: %#', async (extra, label) => {
    publishTestDocumentView(TS_ID, view(extra) as any)
    const wrapper = mountBar()
    await flushPromises()
    expect(wrapper.find('[data-test="ab-spec-run-pill"]').text()).toContain(label)
    expect(wrapper.find('[data-test="ab-spec-rerun"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps [run again] off once the report is approved or while an AI run holds the group', async () => {
    publishTestDocumentView(TS_ID, view({ tsr_review_status: 'approved' }) as any)
    const approved = mountBar()
    await flushPromises()
    expect(approved.find('[data-test="ab-spec-rerun"]').attributes('disabled')).toBeDefined()
    approved.unmount()
  })

  it('keeps a refused run on screen until it is closed', async () => {
    publishTestDocumentView(TS_ID, view() as any)
    postRequest.mockRejectedValueOnce({ response: { status: 409, data: { error: 'failure_origin_pending' } } })
    const wrapper = mountBar()
    await flushPromises()
    await wrapper.find('[data-test="ab-spec-rerun"]').trigger('click')
    await flushPromises()
    expect(showToast).toHaveBeenCalledWith(
      'The failure origin has not been decided yet, so the test cannot run again.', 'danger', 0)
    wrapper.unmount()
  })

  it('leaves the contract-1 TS on the legacy [run test]', async () => {
    const wrapper = mountBar({ testContractVersion: 1 })
    await flushPromises()
    expect(wrapper.find('[data-test="ab-spec-run"]').exists()).toBe(false)
    expect(wrapper.find('.ab-split-main').text()).toContain('Run tests')
    expect(wrapper.find('[data-test="ab-spec-rerun"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('ReviewActionBar — approve in progress (0684 T#3)', () => {
  it('shows a spinner on [approve] until the server answers and keeps a refusal on screen', async () => {
    let reject: (reason: unknown) => void = () => {}
    postRequest.mockImplementation(() => new Promise((_resolve, rejectPromise) => { reject = rejectPromise }))
    getRequest.mockImplementation((url: string) => (String(url).includes('/detail')
      ? Promise.resolve({ data: { doc_review_status: 'pending_review' } })
      : Promise.resolve({ data: { ok: true, state: { branch: null, status: 'none', default_action: null, choices: [] } } })))
    const wrapper = mountBar({ mode: 'review', reviewStatus: 'pending_review', nextStepCode: undefined })
    await flushPromises()
    const pending = (wrapper.vm as any).doApprove()
    await flushPromises()
    const approve = wrapper.find('[data-test="ab-approve"]')
    expect(approve.attributes('aria-busy')).toBe('true')
    expect(approve.attributes('disabled')).toBeDefined()
    expect(approve.find('.app-icon--spin').exists()).toBe(true)
    reject({ response: { status: 409, data: { detail: 'missing test file: TC-001: tests/x.py' } } })
    await pending
    await flushPromises()
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('missing test file'), 'danger', 0)
    expect(wrapper.find('[data-test="ab-approve"]').attributes('aria-busy')).toBe('false')
    expect(wrapper.find('[data-test="ab-approve"] .app-icon--spin').exists()).toBe(false)
    wrapper.unmount()
  })
})
