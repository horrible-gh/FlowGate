import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import UnapprovedHistory from '@main/components/UnapprovedHistory.vue'
import FinalApprovalBody from '@main/components/documents/FinalApprovalBody.vue'
import { resolveWorkflowViewState } from '@main/workflow/workflowViewState'

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), showToast: vi.fn() }))
vi.mock('@shared/api', () => ({ default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() }, getRequest, postRequest }))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))
const DOC = 'flowgate.default.0428.0008-TSR'
let preview: any
const wrappers: any[] = []
const decision = (type = 'TSR', extra = {}) => ({ decision_id: 'wcd_original', result_doc_id: DOC,
  document_revision: 7, actor_user_id: 'human-1', decided_at: '2026-10-12T12:00:00Z', type_code: type,
  valid: true, test_gate: { overall: 'FAIL', passed: false }, continuation_status: 'not_started', ...extra })
function bar(extra = {}) {
  const wrapper = mount(ReviewActionBar, { props: { docId: DOC, projectId: 'flowgate', groupId: 'flowgate.default.0428', docRef: DOC,
    mode: 'review', docType: 'TSR', reviewStatus: 'rejected', testGateBlocked: true, ...extra }, global: { plugins: [i18n] }, attachTo: document.body })
  wrappers.push(wrapper); return wrapper
}
beforeEach(() => {
  setActivePinia(createPinia()); i18n.global.locale.value = 'en'
  preview = { allowed: true, document_revision: 7, slot_id: 2, slot_generation: 4,
    history: [], test_gate: { applies: true, overall: 'FAIL', passed: false } }
  getRequest.mockImplementation(async (url: string) => ({ data: url.includes('unapproved-progress') ? structuredClone(preview) : { ok: true, state: { status: 'none', choices: [] } } }))
  postRequest.mockResolvedValue({ data: { slot_completed: true, doc_review_status: 'rejected', decision_id: 'wcd_original' } })
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.clearAllMocks(); document.body.innerHTML = '' })

describe('0428 T#6 independent UI and audit', () => {
  it.each(['FAIL','BLOCKED','NOT_RUN'])('YES confirms once and preserves %s and rejected review', async overall => {
    preview.test_gate.overall = overall
    const w = bar(); await flushPromises()
    expect(w.get('[data-test="ab-approve"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="ab-proceed-unapproved"]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-test="unapproved-actual-verdict"]').text()).toContain(overall)
    await w.get('[data-test="ab-proceed-unapproved"]').trigger('click')
    const modal = w.findAllComponents({ name: 'ConfirmModal' }).find(m => m.props('title') === 'Proceed without approval')!
    expect(modal.props('message')).toBe('Proceed to the next step without approval. Continue?')
    expect(modal.props('confirmLabel')).toBe('YES'); expect(modal.props('cancelLabel')).toBe('NO')
    expect(modal.find('input').exists()).toBe(false)
    modal.vm.$emit('confirm'); await flushPromises()
    const [url, body] = postRequest.mock.calls[0]
    expect(url).toBe(`/api/v1/documents/${encodeURIComponent(DOC)}/proceed-unapproved`)
    expect(body).toEqual({ expected_revision: 7, expected_slot_id: 2, expected_slot_generation: 4, request_key: expect.any(String) })
    expect(w.emitted('approve')).toBeUndefined(); expect(w.emitted('reject')).toBeUndefined()
  })
  it('NO/close has no POST and no reason field; Korean TR2 warning is explicit', async () => {
    i18n.global.locale.value = 'ko'
    const w = bar({ docType: 'TR2', testGateBlocked: false }); await flushPromises()
    await w.get('[data-test="ab-proceed-unapproved"]').trigger('click')
    const modal = w.findAllComponents({ name: 'ConfirmModal' }).find(m => m.props('title') === '미승인 진행')!
    expect(modal.props('message')).toBe('승인하지 않고 다음 단계로 진행합니다. 계속하시겠습니까? 반영안은 소스에 적용되지 않습니다.')
    modal.vm.$emit('cancel'); modal.vm.$emit('update:visible', false); await flushPromises()
    expect(postRequest).not.toHaveBeenCalled()
  })
  it.each(['completion_permission_denied','workflow_not_head','group_disposed','completion_test_run_active','completion_ai_run_active','tr2_recovery_required','completion_source_locked'])('server denial %s disables YES and button', async reason => {
    preview.allowed = false; preview.reason = reason
    const w = bar(); await flushPromises()
    expect(w.get('[data-test="ab-proceed-unapproved"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="unapproved-disabled-reason"]').text()).not.toContain(reason)
    expect(postRequest).not.toHaveBeenCalled()
  })
  it('active group test locks the independently allowed button', async () => {
    const w = bar({ groupTestRunActive: true }); await flushPromises()
    expect(w.get('[data-test="ab-proceed-unapproved"]').attributes('disabled')).toBeDefined()
  })
  it('lost response retry keeps request key and busy blocks a second click', async () => {
    let reject!: (error: any) => void
    postRequest.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
    const w = bar(); await flushPromises()
    await w.get('[data-test="ab-proceed-unapproved"]').trigger('click')
    let modal = w.findAllComponents({ name: 'ConfirmModal' }).find(m => m.props('title') === 'Proceed without approval')!
    modal.vm.$emit('confirm'); await flushPromises()
    expect(w.get('[data-test="ab-proceed-unapproved"]').attributes('disabled')).toBeDefined()
    reject(new Error('response lost')); await flushPromises()
    await w.get('[data-test="ab-proceed-unapproved"]').trigger('click')
    modal.vm.$emit('confirm'); await flushPromises()
    expect(postRequest.mock.calls).toHaveLength(2)
    expect(postRequest.mock.calls[0][1].request_key).toBe(postRequest.mock.calls[1][1].request_key)
  })
  it('SSE and refresh reload head-independent history without approval emission', async () => {
    const w = bar(); await flushPromises()
    preview.history = [decision('TSR', { continuation_status: 'recovery_required' })]; preview.allowed = false
    window.dispatchEvent(new CustomEvent('fg:workflow_completion_changed', { detail: { doc_id: DOC } })); await flushPromises()
    expect(w.find('[data-test="ab-proceed-unapproved"]').exists()).toBe(false)
    expect(w.find('[data-test="unapproved-history"]').exists()).toBe(false)
    expect(w.find('[data-test="continuation-state"]').exists()).toBe(false)
    expect(w.emitted('approve')).toBeUndefined()
  })
  it('final screen shows original identity, failure/unapplied facts and historical decisions', async () => {
    preview.history = [decision(), decision('TR2', { decision_id:'wcd_tr2' }), decision('TSR', { decision_id:'wcd_old', valid:false })]
    const w = mount(FinalApprovalBody, { props: { groupId: 'flowgate.default.0428', completed:false }, global: { plugins:[i18n], stubs:{ FinalApprovalGitStatus:true } } }); wrappers.push(w)
    await flushPromises()
    expect(w.text()).toContain('Actual TSR verdict: FAIL'); expect(w.text()).toContain('was not applied to source')
    expect(w.text()).toContain('human-1'); expect(w.text()).toContain('wcd_original'); expect(w.text()).toContain('Historical decision')
    expect(w.find('input').exists()).toBe(false); expect(postRequest).not.toHaveBeenCalled()
  })
  it('the footer stays free of history cards with many unapproved decisions', async () => {
    preview.history = Array.from({ length: 30 }, (_, index) => decision('TR2', { decision_id: `wcd_${index}` }))
    const w = bar({ mode: 'next', docType: 'TR2' }); await flushPromises()
    expect(w.findComponent(UnapprovedHistory).exists()).toBe(false)
    expect(w.find('[data-test="unapproved-history"]').exists()).toBe(false)
    expect(w.find('[data-test="unapplied-summary"]').exists()).toBe(false)
  })
  it('original document link opens the real document tab', async () => {
    const w = mount(UnapprovedHistory, { props: { entries:[decision()] }, global:{ plugins:[i18n] } }); wrappers.push(w)
    getRequest.mockResolvedValueOnce({ data:{ doc_id:DOC, title:'Original', type_code:'TSR', file_path:'docs/original.md' } })
    await w.get('.source-link').trigger('click'); await flushPromises()
    const { useTabsStore } = await import('@main/stores/tabs')
    expect(useTabsStore().tabs.some(tab => tab.id === DOC)).toBe(true)
  })
  it('rejected but completed slot offers next and the exact repeated slot carries the badge', () => {
    const result = resolveWorkflowViewState({ tabTypeCode:'TSR', tabReviewStatus:'rejected', tabSlotCompleted:true,
      workflowSteps:['TSR','TSR','TR2'], headType:'TR2', headStatus:'pending', headDocId:null, headDocReviewStatus:null,
      headIndex:2, nextStepExists:true, slotCompletions:[{}, {completion_reason:'proceeded_unapproved'}, {}] })
    expect(result.mode).toBe('next'); expect(result.canNextAction).toBe(true)
    expect(result.stepStates[0].completionLabel).toBeUndefined()
    expect(result.stepStates[1].completionLabel).toBe('proceeded_unapproved')
  })
  it('unavailable server fails closed and names missing history', async () => {
    getRequest.mockRejectedValue(new Error('offline'))
    const w = bar(); await flushPromises()
    expect(w.get('[data-test="ab-proceed-unapproved"]').attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('Could not load progress history')
  })
})
