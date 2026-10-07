/**
 * flowgate.default.0549 T0008 — the existing test controls around a specification TS/TSR.
 *
 * Measured on the real app (isolated server + built bundle): before this change a
 * specification TS showed the legacy [re-run] (server execution → 422) and a FAIL TSR showed
 * an armed [approve] (server refuses with 409). These pin the corrected controls.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import TestFailStrip from '@main/components/TestFailStrip.vue'
import TestRunStrip from '@main/components/TestRunStrip.vue'

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
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: { ok: true, state: { branch: null, status: 'none', default_action: null, choices: [] } } })
})

const TSR = 'flowgate.default.0549.0011-TSR'

function mountBar(testGateBlocked: boolean) {
  return mount(ReviewActionBar, {
    props: {
      docId: TSR, projectId: 'flowgate', groupId: 'flowgate.default.0549', docRef: TSR,
      docType: 'TSR', reviewStatus: 'pending_review', mode: 'review' as const, testGateBlocked,
    },
    global: { plugins: [i18n] },
  })
}

describe('ReviewActionBar — TSR test gate', () => {
  it('keeps [approve] visible but disabled, with the reason, while the gate is not passed', async () => {
    const wrapper = mountBar(true)
    await flushPromises()
    const approve = wrapper.findAll('button.btn-success').find((b) => b.text().includes('Approve'))
    expect(approve?.exists()).toBe(true)
    expect(approve?.attributes('disabled')).toBeDefined()
    expect(approve?.attributes('title')).toContain('Test gate not passed')
    expect(wrapper.find('[data-testid="ab-test-gate-hint"]').text()).toContain('cannot be approved')
    wrapper.unmount()
  })

  it('arms [approve] again once the gate passed', async () => {
    const wrapper = mountBar(false)
    await flushPromises()
    const approve = wrapper.findAll('button.btn-success').find((b) => b.text().includes('Approve'))
    expect(approve?.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[data-testid="ab-test-gate-hint"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('TestRunStrip — no server [run] for a specification TS', () => {
  function mountStrip(props: Record<string, unknown>) {
    return mount(TestRunStrip, {
      props: {
        typeCode: 'TS', reviewStatus: 'approved', testRun: null, groupDisposed: false,
        docLoaded: true, docId: 'flowgate.default.0549.0010-TS', ...props,
      },
      global: { plugins: [i18n] },
    })
  }

  it('offers only the delegation hand-offs on a contract-2 TS', () => {
    const wrapper = mountStrip({ testContractVersion: 2 })
    expect(wrapper.find('.run-strip-btn--run').exists()).toBe(false)
    expect(wrapper.findAll('.run-strip-btn')).toHaveLength(2) // copy mention + invoke AI
    // 0684 T#3: the server runs the automated Cases; the hand-off is for manual/external ones.
    expect(wrapper.text()).toContain('the server runs the automated Cases')
    wrapper.unmount()
  })

  it('reads the contract from the latest result record too', () => {
    const wrapper = mountStrip({ testRun: { run_id: 'r', status: 'passed', contract_version: 2, overall: 'PASS' } })
    expect(wrapper.find('.run-strip-btn--run').exists()).toBe(false)
    expect(wrapper.text()).toContain('Test result PASS')
    wrapper.unmount()
  })

  it('keeps the legacy executable TS exactly as before', () => {
    const wrapper = mountStrip({ testContractVersion: 1 })
    expect(wrapper.find('.run-strip-btn--run').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('TestFailStrip — specification result', () => {
  const specRun = {
    run_id: 'trun_1', status: 'failed', contract_version: 2, overall: 'FAIL', case_total: 3, case_failed: 1,
    summary: { required_counts: { total: 2, pass: 1, fail: 1, blocked: 0, not_run: 0 } },
    cases: [
      { case_no: 'TC-001', case_title: 'forbidden', case_status: 'FAIL', required: true, expect: 'HTTP 403', actual: 'HTTP 200', result: 'fail' },
      { case_no: 'TC-002', case_title: 'badge', case_status: 'PASS', required: true, result: 'pass' },
      { case_no: 'TC-003', case_title: 'external', case_status: 'NOT_RUN', required: false, result: null },
    ],
  }

  it('summarises the required-case verdict and never offers the server re-run', async () => {
    const wrapper = mount(TestFailStrip, {
      props: { testRun: specRun as any, docId: 'flowgate.default.0549.0010-TS' },
      global: { plugins: [i18n] },
    })
    expect(wrapper.text()).toContain('Test result FAIL (required cases)')
    expect(wrapper.find('.fail-strip-btn--rerun').exists()).toBe(false)
    await wrapper.find('.fail-strip-bar').trigger('click')
    const listed = wrapper.findAll('.fail-case-name').map((n) => n.text())
    expect(listed).toEqual(['forbidden'])            // required and not PASS only
    expect(wrapper.find('.fail-case-result').text()).toBe('FAIL')
    expect(wrapper.text()).toContain('HTTP 200')
    wrapper.unmount()
  })

  it('leaves the legacy failure strip untouched', () => {
    const wrapper = mount(TestFailStrip, {
      props: {
        testRun: { run_id: 'trun_0', status: 'failed', case_total: 2, case_failed: 1,
          cases: [{ case_no: 'TC-1', result: 'fail', exit_code: 1 }] } as any,
        docId: 'flowgate.default.0549.0010-TS',
      },
      global: { plugins: [i18n] },
    })
    expect(wrapper.find('.fail-strip-btn--rerun').exists()).toBe(true)
    wrapper.unmount()
  })
})
