import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import TestDocumentBody from '@main/components/documents/TestDocumentBody.vue'
import TestSpecEditorDialog from '@main/components/documents/TestSpecEditorDialog.vue'
import TestResultEntryDialog from '@main/components/documents/TestResultEntryDialog.vue'

// flowgate.default.0549 T0008: TS = structured test specification, TSR = test report.
// The body picks the structured view only for an explicit contract-2 document; a legacy
// executable TS/TSR keeps its Markdown body (the slot), and every verdict shown is the
// server-computed one.

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest }))

const TS_ID = 'flowgate.default.0549.0010-TS'
const TSR_ID = 'flowgate.default.0549.0011-TSR'

const Markdown = defineComponent({ name: 'MarkdownStub', render: () => h('div', { class: 'md-stub' }, 'RAW MARKDOWN') })

const SPEC_CASES = [
  {
    case_id: 'TC-001', title: 'unauthorized update is refused', category: 'negative',
    requirement: 'R0001 §4.2 / AC-03', execution_mode: 'automated', required: true,
    precondition: 'plain user', input: 'PATCH /x', procedure: '1. call\n2. read', expected: 'HTTP 403',
    check_points: 'permission check', automation_ref: '',
  },
  {
    case_id: 'TC-002', title: 'screen shows the badge', category: 'normal',
    requirement: 'AC-1', execution_mode: 'manual', required: false,
    precondition: '', input: '', procedure: 'open', expected: 'badge visible',
    check_points: 'UI', automation_ref: '',
  },
]

function tab(id: string, typeCode: string) {
  return { id, type: 'md', typeCode, projectId: 'flowgate', path: `${id}.md` } as any
}

function mountBody(id: string, typeCode: string, props: Record<string, unknown> = {}) {
  return mount(TestDocumentBody, {
    props: { tab: tab(id, typeCode), readOnly: false, canEdit: true, ...props },
    slots: { default: () => h(Markdown) },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('TestDocumentBody — contract boundary', () => {
  it('keeps the Markdown body for a legacy executable TS (no re-interpretation)', async () => {
    getRequest.mockResolvedValue({ data: { kind: 'TS', doc_id: TS_ID, contract_version: 1, can_start_spec: false } })
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(TS_ID)}/test-document`)
    expect(wrapper.find('[data-testid="test-spec-panel"]').exists()).toBe(false)
    const md = wrapper.find('[data-testid="test-doc-markdown"]')
    expect(md.exists()).toBe(true)
    expect(md.isVisible()).toBe(true)
    expect(wrapper.text()).toContain('RAW MARKDOWN')
    wrapper.unmount()
  })

  it('falls back to the Markdown body when the structured view cannot be loaded', async () => {
    getRequest.mockRejectedValue(new Error('network'))
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-doc-markdown"]').isVisible()).toBe(true)
    // 0684 T#3 (D#1 §6-3): the failure is said, with a retry — not a silent empty body.
    expect(wrapper.find('[data-testid="test-doc-load-failed"]').text()).toContain('Could not load the results.')
    getRequest.mockResolvedValue({ data: { kind: 'TS', doc_id: TS_ID, contract_version: 2, cases: [], errors: [] } })
    await wrapper.find('[data-testid="test-doc-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-doc-load-failed"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="test-spec-panel"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('shows the Case table outline until the view answers (0684 T#3)', async () => {
    let resolve: (value: unknown) => void = () => {}
    getRequest.mockReturnValue(new Promise((done) => { resolve = done }))
    const wrapper = mountBody(TS_ID, 'TS')
    await nextTick()
    expect(wrapper.find('[data-testid="test-doc-skeleton"]').exists()).toBe(true)
    expect(wrapper.findAll('.test-doc-skeleton-row')).toHaveLength(4)
    expect(wrapper.find('[data-testid="test-doc-markdown"]').isVisible()).toBe(false)
    resolve({ data: { kind: 'TS', doc_id: TS_ID, contract_version: 2, cases: [], errors: [] } })
    await flushPromises()
    expect(wrapper.find('[data-testid="test-doc-skeleton"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="test-spec-panel"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('offers "write specification" only on an empty, editable TS', async () => {
    getRequest.mockResolvedValue({ data: { kind: 'TS', doc_id: TS_ID, contract_version: 1, can_start_spec: true } })
    const editable = mountBody(TS_ID, 'TS')
    await flushPromises()
    expect(editable.find('[data-testid="test-spec-start"]').exists()).toBe(true)
    editable.unmount()

    const readOnly = mountBody(TS_ID, 'TS', { readOnly: true })
    await flushPromises()
    expect(readOnly.find('[data-testid="test-spec-start"]').exists()).toBe(false)
    expect(readOnly.find('[data-testid="test-doc-markdown"]').isVisible()).toBe(true)
    readOnly.unmount()
  })
})

describe('TestSpecPanel — structured specification', () => {
  function specView(extra: Record<string, unknown> = {}) {
    return {
      data: {
        kind: 'TS', doc_id: TS_ID, contract_version: 2, title: 'Permission spec', intro: '',
        cases: SPEC_CASES, errors: [], doc_review_status: 'approved',
        latest_result: {
          run_id: 'trun_1', contract_version: 2, overall: 'FAIL',
          cases: [
            { case_no: 'TC-001', case_status: 'FAIL', result_origin: 'payload' },
            { case_no: 'TC-002', case_status: 'NOT_RUN', result_origin: 'missing' },
          ],
        },
        ...extra,
      },
    }
  }

  it('renders every case with category / mode / required and the per-case verdict', async () => {
    getRequest.mockResolvedValue(specView())
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    const cards = wrapper.findAll('[data-testid="test-spec-case"]')
    expect(cards.map((c) => c.attributes('data-case-id'))).toEqual(['TC-001', 'TC-002'])
    expect(cards[0].text()).toContain('Negative')
    expect(cards[0].text()).toContain('Automated')
    expect(cards[0].text()).toContain('Required')
    expect(cards[0].text()).toContain('HTTP 403')
    expect(cards[0].find('[data-testid="test-spec-case-verdict"]').text()).toBe('FAIL')
    expect(cards[1].text()).toContain('Optional')
    expect(wrapper.find('[data-testid="test-spec-latest-overall"]').text()).toContain('FAIL')
    // Approved spec: results may be entered, the spec itself is no longer edited.
    expect(wrapper.find('[data-testid="test-spec-results-btn"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="test-spec-edit-btn"]').exists()).toBe(false)
    // The canonical Markdown stays one click away.
    await wrapper.find('[data-testid="test-doc-view-source"]').trigger('click')
    expect(wrapper.text()).toContain('RAW MARKDOWN')
    wrapper.unmount()
  })

  it('labels a JS/TS case runner_unsupported without a Run Case button (0682 T#2)', async () => {
    getRequest.mockResolvedValue(specView({
      test_basis: {
        basis_id: 'b'.repeat(64),
        source: { kind: 'source_bundle', exclusion_policy_version: 'source-bundle-v1', content_fingerprint: 'f'.repeat(64) },
        test_assets: { policy_version: 'test-asset-v2', manifest_hash: 'm'.repeat(64), asset_count: 1 },
        manifest: [{ path: 'client/tests/main/a.spec.ts', content_hash: 'c'.repeat(64), role: 'test', kind: 'runner_unsupported_test' }],
      },
      basis_valid: true,
      case_capabilities: { 'TC-001': 'runner_unsupported', 'TC-002': 'manual' },
    }))
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    const cards = wrapper.findAll('[data-testid="test-spec-case"]')
    expect(cards[0].find('[data-testid="test-spec-case-capability"]').attributes('data-capability')).toBe('runner_unsupported')
    expect(cards[0].find('[data-testid="test-spec-case-capability"]').text()).toBe('No automatic runner — NOT_RUN until a runner exists')
    expect(cards[0].find('[data-testid="test-spec-runner-unsupported"]').text()).toContain('No automatic runner')
    expect(cards[0].find('[data-testid="test-spec-run-case"]').exists()).toBe(false)
    expect(cards[1].find('[data-testid="test-spec-runner-unsupported"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('offers a Case run without a stored Basis and holds it while a run is live (0684 T#1/T#3)', async () => {
    getRequest.mockResolvedValue(specView({ case_capabilities: { 'TC-001': 'case_selectable', 'TC-002': 'manual' } }))
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-basis"]').exists()).toBe(false)
    // 0684 T#3 (D#1 §3-8): the whole-TS run is the action bar's [run again]; the panel has none.
    expect(wrapper.find('[data-testid="test-spec-run-all"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Run all automated Cases')
    const runCase = wrapper.find('[data-testid="test-spec-run-case"]')
    expect(runCase.attributes('disabled')).toBeUndefined()
    expect(runCase.text()).toBe('Run')
    postRequest.mockResolvedValue({ data: { run_id: 'trun_c' } })
    await runCase.trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${encodeURIComponent(TS_ID)}/test-spec/cases/TC-001/run`, {})
    wrapper.unmount()
    getRequest.mockResolvedValue(specView({
      case_capabilities: { 'TC-001': 'case_selectable', 'TC-002': 'manual' },
      active_run: { run_id: 'trun_9', status: 'running', phase: 'executing',
        selected_case_ids: ['TC-001'], reported_case_ids: [] },
      pending_case_ids: ['TC-001'],
    }))
    const live = mountBody(TS_ID, 'TS')
    await flushPromises()
    expect(live.find('[data-testid="test-spec-progress"]').text()).toContain('Running test (0/1)')
    expect(live.find('[data-testid="test-spec-run-case"]').attributes('disabled')).toBeDefined()
    const cards = live.findAll('[data-testid="test-spec-case"]')
    expect(cards[0].find('[data-testid="test-spec-case-pending"]').text()).toContain('Pending')
    expect(cards[0].find('[data-testid="test-spec-case-verdict"]').exists()).toBe(false)
    live.unmount()
  })

  it('says why a Case run was refused, in the user language (0684 T#3)', async () => {
    getRequest.mockResolvedValue(specView({ case_capabilities: { 'TC-001': 'case_selectable', 'TC-002': 'manual' } }))
    postRequest.mockRejectedValue({ response: { status: 409, data: { error: 'run_in_progress', run_id: 'trun_x' } } })
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    await wrapper.find('[data-testid="test-spec-run-case"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-spec-action-error"]').text()).toBe('A test run is already in progress.')
    wrapper.unmount()
  })

  it('offers result entry for the entered Cases only (0684 T#3)', async () => {
    getRequest.mockResolvedValue(specView({
      case_capabilities: { 'TC-001': 'case_selectable', 'TC-002': 'manual' },
      result_entry_case_ids: ['TC-002'],
    }))
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    await wrapper.find('[data-testid="test-spec-results-btn"]').trigger('click')
    await flushPromises()
    const rows = Array.from(document.body.querySelectorAll('[data-testid="tre-row"]'))
    expect(rows.map((row) => row.getAttribute('data-case-id'))).toEqual(['TC-002'])
    wrapper.unmount()

    getRequest.mockResolvedValue(specView({
      cases: [SPEC_CASES[0]],
      case_capabilities: { 'TC-001': 'case_selectable' },
      result_entry_case_ids: [],
    }))
    const serverOnly = mountBody(TS_ID, 'TS')
    await flushPromises()
    expect(serverOnly.find('[data-testid="test-spec-results-btn"]').exists()).toBe(false)
    serverOnly.unmount()

    // An automated Case with no server runner is not entered either (D#1 §3-8, CH S6).
    getRequest.mockResolvedValue(specView({
      case_capabilities: { 'TC-001': 'runner_unsupported', 'TC-002': 'manual' },
    }))
    const unsupported = mountBody(TS_ID, 'TS')
    await flushPromises()
    await unsupported.find('[data-testid="test-spec-results-btn"]').trigger('click')
    await flushPromises()
    const entryRows = Array.from(document.body.querySelectorAll('[data-testid="tre-row"]'))
    expect(entryRows.map((row) => row.getAttribute('data-case-id'))).toEqual(['TC-002'])
    unsupported.unmount()
  })

  it('shows the run basis in words and asks for a run after an asset edit (0684 T#3)', async () => {
    getRequest.mockResolvedValueOnce(specView({
      test_basis: {
        basis_id: 'b'.repeat(64),
        source: { kind: 'worktree_copy', content_fingerprint: 'f'.repeat(64) },
        test_assets: { manifest_hash: 'm'.repeat(64), asset_count: 1 },
        manifest: [{ path: 'tests/test_x.py', content_hash: 'c'.repeat(64), role: 'test' }],
      },
      basis_valid: false,
      basis_verdict: { state: 'stale', reasons: ['source_changed'] },
      basis_source: { fingerprint_prefix: 'ffffffffffff', git_revision: 'abcdef1234567890', source_dirty: true,
        measured_at: '2026-10-08T01:02:03' },
    }))
    getRequest.mockResolvedValueOnce({ data: { content: 'def test_x(): pass\n', content_hash: 'c'.repeat(64) } })
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    const basis = wrapper.find('[data-testid="test-basis"]')
    expect(basis.text()).toContain('Run basis')
    expect(basis.text()).toContain('Source fingerprint ffffffffffff')
    expect(basis.text()).toContain('git abcdef123456')
    expect(basis.text()).toContain('includes uncommitted changes')
    expect(basis.text()).toContain('Run at 2026-10-08T01:02:03')
    expect(wrapper.find('[data-testid="test-basis-verdict"]').text()).toBe('differs from the current source')
    expect(wrapper.find('[data-testid="test-basis-stale"]').text()).toContain('Reasons: source_changed')
    expect(wrapper.find('[data-testid="test-asset-manifest"]').text()).toContain('Test assets')
    expect(wrapper.text()).not.toMatch(/Approved test assets|Save with expected hash|Current basis is stale|Automated progress/)
    await wrapper.find('[data-testid="test-asset-manifest"] button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-asset-save"]').text()).toBe('Save (expected hash checked)')
    putRequest.mockResolvedValue({ data: { ok: true } })
    getRequest.mockResolvedValue(specView())
    await wrapper.find('[data-testid="test-asset-save"]').trigger('click')
    await flushPromises()
    expect(putRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${encodeURIComponent(TS_ID)}/test-spec/assets/${encodeURIComponent('tests/test_x.py')}`,
      { expected_hash: 'c'.repeat(64), content: 'def test_x(): pass\n' })
    expect(wrapper.find('[data-testid="test-asset-edited"]').text()).toContain('run again')
    wrapper.unmount()
  })

  it('replaces [enter results] with the reason once the test report is approved', async () => {
    getRequest.mockResolvedValue(specView({ tsr_doc_id: 'x.0011-TSR', tsr_review_status: 'approved' }))
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-spec-results-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="test-spec-report-locked"]').text()).toContain('reopen it')
    wrapper.unmount()
  })

  it('blocks result entry while the spec has validation errors and lists them', async () => {
    getRequest.mockResolvedValue(specView({
      doc_review_status: 'draft',
      errors: [{ code: 'missing_field', case_id: 'TC-001', field: 'expected', message: "TC-001: required field 'expected' missing." }],
    }))
    const wrapper = mountBody(TS_ID, 'TS')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-spec-errors"]').text()).toContain("required field 'expected' missing")
    expect(wrapper.find('[data-testid="test-spec-results-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="test-spec-edit-btn"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('TestReportPanel — TSR test report', () => {
  function reportView(overall: string, gatePassed: boolean) {
    return {
      data: {
        kind: 'TSR', doc_id: TSR_ID, contract_version: 2, target_ts: TS_ID,
        gate: { applies: true, passed: gatePassed, overall },
        report: {
          run_id: 'trun_2', contract_version: 2, overall, gate_passed: gatePassed,
          summary: {
            counts: { total: 3, pass: 1, fail: 1, blocked: 0, not_run: 1 },
            required_counts: { total: 2, pass: 1, fail: 1, blocked: 0, not_run: 0 },
            optional_counts: { total: 1, pass: 0, fail: 0, blocked: 0, not_run: 1 },
          },
          unmapped: [{ case_id: null, status: 'FAIL', source_name: 'suite::other', reason: 'missing_case_id' }],
          conflicts: [{ case_id: 'TC-003', result_count: 2, statuses: ['PASS', 'NOT_RUN'] }],
          cases: [
            { case_no: 'TC-001', case_title: 'a', case_status: 'PASS', required: true, execution_mode: 'automated',
              expect: 'HTTP 403', actual: 'HTTP 403', evidence: [{ kind: 'url', value: 'https://ci/1' }],
              source_identity: { git_revision: 'abc123' } },
            { case_no: 'TC-002', case_title: 'b', case_status: 'FAIL', required: true, execution_mode: 'manual',
              expect: 'badge visible', actual: 'no badge', defect_ref: 'BUG-7', evidence: [] },
            { case_no: 'TC-003', case_title: 'c', case_status: 'NOT_RUN', required: false, execution_mode: 'external',
              expect: 'x', actual: '', evidence: [] },
          ],
        },
      },
    }
  }

  it('shows the summary tiles, the server overall and the blocked gate for a FAIL report', async () => {
    getRequest.mockResolvedValue(reportView('FAIL', false))
    const wrapper = mountBody(TSR_ID, 'TSR')
    await flushPromises()
    expect(wrapper.find('[data-testid="tsr-tile-total"]').text()).toContain('3')
    expect(wrapper.find('[data-testid="tsr-tile-pass"]').text()).toContain('1')
    expect(wrapper.find('[data-testid="tsr-tile-fail"]').text()).toContain('1')
    expect(wrapper.find('[data-testid="tsr-tile-not_run"]').text()).toContain('1')
    const overall = wrapper.find('[data-testid="tsr-overall"]')
    expect(overall.text()).toContain('FAIL')
    expect(overall.text()).toContain('NOT passed')
    const rows = wrapper.findAll('[data-testid="tsr-row"]')
    expect(rows.map((r) => r.attributes('data-case-id'))).toEqual(['TC-001', 'TC-002', 'TC-003'])
    expect(rows[0].text()).toContain('git_revision: abc123')
    expect(rows[0].find('a').attributes('href')).toBe('https://ci/1')
    expect(rows[1].text()).toContain('no badge')
    expect(rows[1].text()).toContain('BUG-7')
    expect(rows[1].classes()).toContain('tsr-row-bad')
    expect(rows[2].classes()).not.toContain('tsr-row-bad') // optional case never marks the gate
    expect(wrapper.find('[data-testid="tsr-unmapped"]').text()).toContain('suite::other')
    expect(wrapper.find('[data-testid="tsr-conflicts"]').text()).toContain('TC-003')
    wrapper.unmount()
  })

  it('reports the passed gate for a PASS report', async () => {
    getRequest.mockResolvedValue(reportView('PASS', true))
    const wrapper = mountBody(TSR_ID, 'TSR')
    await flushPromises()
    expect(wrapper.find('[data-testid="tsr-overall"]').text()).toContain('Test gate passed')
    wrapper.unmount()
  })

  it('reads "pending" for Cases a live run has not reported and gives no verdict yet (0684 T#3)', async () => {
    const view = reportView('NOT_RUN', false)
    Object.assign(view.data, {
      active_run: { run_id: 'trun_live', status: 'running', phase: 'executing',
        selected_case_ids: ['TC-001', 'TC-002'], reported_case_ids: ['TC-001'] },
      pending_case_ids: ['TC-002'],
    })
    getRequest.mockResolvedValue(view)
    const wrapper = mountBody(TSR_ID, 'TSR')
    await flushPromises()
    expect(wrapper.find('[data-testid="tsr-progress"]').text()).toContain('Running test (1/2)')
    expect(wrapper.find('[data-testid="tsr-overall"]').exists()).toBe(false)
    const rows = wrapper.findAll('[data-testid="tsr-row"]')
    expect(rows[0].find('[data-testid="tsr-row-verdict"]').text()).toBe('PASS')
    expect(rows[1].find('[data-testid="tsr-row-pending"]').text()).toContain('Pending')
    expect(rows[1].classes()).not.toContain('tsr-row-bad')
    wrapper.unmount()
  })

  it('warns that a result no longer holds for the current source and runs again (0684 T#3)', async () => {
    const view = reportView('PASS', false)
    Object.assign(view.data, {
      ts_review_status: 'approved', doc_review_status: 'pending_review',
      basis_valid: false, basis_verdict: { state: 'stale', reasons: ['source_changed'] },
      test_basis: { basis_id: 'b'.repeat(64), source: {}, test_assets: { manifest_hash: 'm', asset_count: 0 }, manifest: [] },
      basis_source: { fingerprint_prefix: 'aaaaaaaaaaaa' },
      stale_previous_result: { run_id: 'trun_old', overall: 'PASS', cases: [] },
      run_history: [{ run_id: 'trun_old', run_kind: 'spec_execution', status: 'passed', overall: 'PASS', basis_id: 'b' }],
    })
    getRequest.mockResolvedValue(view)
    postRequest.mockResolvedValue({ data: { run_id: 'trun_new' } })
    const wrapper = mountBody(TSR_ID, 'TSR')
    await flushPromises()
    const band = wrapper.find('[data-testid="tsr-stale-band"]')
    expect(band.text()).toContain('The source changed after this run')
    expect(band.text()).toContain('Reasons: source_changed')
    expect(wrapper.find('[data-testid="tsr-basis"]').text()).toContain('Run basis')
    expect(wrapper.find('[data-testid="tsr-stale"]').text()).toContain('Previous result (differs from the current source)')
    expect(wrapper.find('[data-testid="tsr-run-history"]').text()).toContain('Run history')
    expect(wrapper.text()).not.toMatch(/Current Test Basis|STALE previous result|Current basis is stale/)
    await wrapper.find('[data-testid="tsr-rerun"]').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${encodeURIComponent(TS_ID)}/test-spec/runs`, {})
    wrapper.unmount()
  })

  it('keeps the Markdown body for a TSR assembled from a legacy run', async () => {

    getRequest.mockResolvedValue({ data: { kind: 'TSR', doc_id: TSR_ID, contract_version: 1 } })
    const wrapper = mountBody(TSR_ID, 'TSR')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-report-panel"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('RAW MARKDOWN')
    wrapper.unmount()
  })
})

describe('DocumentBodyRouter — TS/TSR branch', () => {
  async function mountRouter(typeCode: string) {
    const { default: DocumentBodyRouter } = await import('@main/components/documents/DocumentBodyRouter.vue')
    return mount(DocumentBodyRouter, {
      props: {
        tab: tab(`${GROUP_PREFIX}-${typeCode}`, typeCode),
        readOnly: false, completed: false, canEdit: true, editDropdownOpen: false,
        textWrapEnabled: false, downloadAvailable: false, downloadBusy: false, uploadBusy: false,
        conversationReadOnly: false, conversationManualCopyText: null,
        conversationFullViewHost: null, conversationFullViewOn: false,
      },
      global: {
        plugins: [i18n],
        stubs: {
          GenericDocumentBody: { template: '<div class="generic-stub">GENERIC</div>' },
          WorkPlanEditor: true, FinalApprovalBody: true, DiscardBody: true,
          ConversationDocumentView: true, QuestionDocumentBody: true,
        },
      },
    })
  }
  const GROUP_PREFIX = 'flowgate.default.0549.0012'

  it('routes TS and TSR through TestDocumentBody and keeps the generic body inside it', async () => {
    getRequest.mockResolvedValue({ data: { kind: 'TS', doc_id: 'x', contract_version: 1 } })
    for (const typeCode of ['TS', 'TSR']) {
      const wrapper = await mountRouter(typeCode)
      await flushPromises()
      expect(wrapper.findComponent(TestDocumentBody).exists()).toBe(true)
      expect(wrapper.find('.generic-stub').exists()).toBe(true)
      wrapper.unmount()
    }
  })

  it('leaves every other type on the generic body without asking for a test document', async () => {
    const wrapper = await mountRouter('T')
    await flushPromises()
    expect(wrapper.findComponent(TestDocumentBody).exists()).toBe(false)
    expect(wrapper.find('.generic-stub').exists()).toBe(true)
    expect(getRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

function footerButton(id: string): HTMLButtonElement {
  const el = document.body.querySelector(`[data-dialog-action-id="${id}"]`) as HTMLButtonElement | null
  if (!el) throw new Error(`footer action ${id} not found`)
  return el
}

describe('TestSpecEditorDialog — structured editor saves through the canonical body', () => {
  it('PUTs the structured cases and reports every server validation error', async () => {
    putRequest.mockRejectedValueOnce({
      response: { data: { detail: { errors: [{ message: 'TC-001: duplicate Case ID' }, { message: 'At least one case must be required' }] } } },
    })
    const wrapper = mount(TestSpecEditorDialog, {
      props: { open: true, docId: TS_ID, title: 'Spec', intro: '', cases: SPEC_CASES },
      global: { plugins: [i18n] },
      attachTo: document.body,
    })
    await nextTick()
    footerButton('save').click()
    await flushPromises()
    expect(putRequest).toHaveBeenCalledTimes(1)
    const [path, payload] = putRequest.mock.calls[0]
    expect(path).toBe(`/api/v1/documents/${encodeURIComponent(TS_ID)}/test-spec`)
    expect(payload.title).toBe('Spec')
    expect(payload.cases.map((c: { case_id: string }) => c.case_id)).toEqual(['TC-001', 'TC-002'])
    expect(payload.cases[0]).not.toHaveProperty('key')
    expect(document.body.querySelector('[data-testid="tse-errors"]')?.textContent).toContain('duplicate Case ID')
    expect(wrapper.emitted('saved')).toBeUndefined()

    putRequest.mockResolvedValueOnce({ data: { ok: true } })
    footerButton('save').click()
    await flushPromises()
    expect(wrapper.emitted('saved')).toHaveLength(1)
    wrapper.unmount()
  })

  it('adds, reorders and removes cases', async () => {
    const wrapper = mount(TestSpecEditorDialog, {
      props: { open: true, docId: TS_ID, title: 'Spec', intro: '', cases: SPEC_CASES },
      global: { plugins: [i18n] },
      attachTo: document.body,
    })
    await nextTick()
    ;(document.body.querySelector('[data-testid="tse-add"]') as HTMLButtonElement).click()
    await nextTick()
    const ids = () => Array.from(document.body.querySelectorAll<HTMLInputElement>('[data-testid="tse-case-id"]')).map((i) => i.value)
    expect(ids()).toEqual(['TC-001', 'TC-002', 'TC-003'])
    ;(document.body.querySelectorAll<HTMLButtonElement>('[data-testid="tse-move-up"]')[1]).click()
    await nextTick()
    expect(ids()).toEqual(['TC-002', 'TC-001', 'TC-003'])
    ;(document.body.querySelectorAll<HTMLButtonElement>('[data-testid="tse-remove"]')[2]).click()
    await nextTick()
    expect(ids()).toEqual(['TC-002', 'TC-001'])
    wrapper.unmount()
  })
})

describe('TestResultEntryDialog — manual/external/JUnit results share one model', () => {
  it('submits only the cases given a verdict, with evidence items, and never an overall', async () => {
    postRequest.mockResolvedValue({ data: { overall: 'NOT_RUN' } })
    const wrapper = mount(TestResultEntryDialog, {
      props: {
        open: true, docId: TS_ID, cases: SPEC_CASES,
        latest: { cases: [{ case_no: 'TC-001', case_status: 'FAIL', result_origin: 'payload' }] } as any,
      },
      global: { plugins: [i18n] },
      attachTo: document.body,
    })
    await nextTick()
    const rows = document.body.querySelectorAll('[data-testid="tre-row"]')
    expect(rows[0].textContent).toContain('Previous: FAIL')
    const status = rows[0].querySelector('[data-testid="tre-status"]') as HTMLSelectElement
    status.value = 'PASS'
    status.dispatchEvent(new Event('change'))
    const actual = rows[0].querySelector('[data-testid="tre-actual"]') as HTMLTextAreaElement
    actual.value = 'HTTP 403'
    actual.dispatchEvent(new Event('input'))
    const evidence = rows[0].querySelector('[data-testid="tre-evidence"]') as HTMLTextAreaElement
    evidence.value = 'https://ci/run/9\nchecked by hand'
    evidence.dispatchEvent(new Event('input'))
    await nextTick()
    footerButton('submit').click()
    await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(1)
    const [path, payload] = postRequest.mock.calls[0]
    expect(path).toBe('/api/v1/documents/test-results')
    expect(payload.doc_id).toBe(TS_ID)
    expect(payload).not.toHaveProperty('overall')
    expect(payload.results).toEqual([
      {
        case_id: 'TC-001', status: 'PASS', actual: 'HTTP 403', defect_ref: '', execution_mode: 'automated',
        evidence: [{ kind: 'url', value: 'https://ci/run/9' }, { kind: 'manual_note', value: 'checked by hand' }],
      },
    ])
    expect(wrapper.emitted('submitted')?.[0]).toEqual(['NOT_RUN'])
    wrapper.unmount()
  })

  it('refuses an empty submission locally', async () => {
    const wrapper = mount(TestResultEntryDialog, {
      props: { open: true, docId: TS_ID, cases: SPEC_CASES, latest: null },
      global: { plugins: [i18n] },
      attachTo: document.body,
    })
    await nextTick()
    footerButton('submit').click()
    await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
    expect(document.body.querySelector('[data-testid="tre-error"]')?.textContent).toContain('No result was entered')
    wrapper.unmount()
  })
})
