import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import Tr2DocumentBody from '@main/components/documents/Tr2DocumentBody.vue'

// flowgate.default.0660 T0004 §3 (RC3): while a Time Machine commit cancel is pending the
// group is held and the cancel retry is the only way out. The rewind dialog's [다시 시도] is
// gone once that dialog closes or the page reloads, so the proposal offers the same retry
// (`/return-point/cancel-commits`) for as long as the server says `revert_pending`.

const { getRequest, postRequest, putRequest, deleteRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), deleteRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest, deleteRequest }))

const docId = 'flowgate.default.0660.0003-TR2'
const tab = { id: docId, title: 'TR2', type: 'md' as const, typeCode: 'TR2', path: 'document.json', projectId: 'flowgate' }
const retryUrl = `/api/v1/documents/workflow/${encodeURIComponent(docId)}/return-point/cancel-commits`
const EDIT = { id: 'e1', kind: 'edit', file: 'src/a.txt', anchor_old: 'a', replacement_new: 'b', confidence: 'high', rationale: 'r' }

function view(reason: string | null) {
  return {
    document: { doc_id: docId, revision_no: 1, doc_review_status: 'pending_review', editable: reason === null },
    mutation: { allowed: reason === null, reason },
    readiness: { ready: false, code: reason ? 'tr2_revert_pending' : 'tr2_history_revision_required', loc: null, reason: null, edits: [] },
    gate_admission: { fingerprint: 'fp', candidate_count: 0, all_candidate: false, commands: [] },
    body: {
      tr2_version: 1, source_t2_doc_id: 'flowgate.default.0660.0002-T2', baseline_fingerprint: 'sha256:base',
      edit_spec: { termination: 'ready_to_apply', edits: [EDIT], deferred: [], gate: { commands: [], apply: false } },
    },
    derived: { files: [], live_precheck: { drift: false, baseline_fingerprint: 'sha256:base', live_fingerprint: 'sha256:base' } },
    approval: { latest_attempt: null, attempts: [], retry: { allowed: false, reason: 'no_attempt' } },
    history: { source_history_state: 'aligned', ledger: [] },
  }
}

let current: ReturnType<typeof view>
const mounted: Array<ReturnType<typeof mount>> = []
function mountBody(readOnly = false) {
  const wrapper = mount(Tr2DocumentBody, { props: { tab, readOnly }, global: { plugins: [i18n] }, attachTo: document.body })
  mounted.push(wrapper)
  return wrapper
}
const t = i18n.global.t

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  current = view('revert_pending')
  for (const fn of [getRequest, postRequest, putRequest, deleteRequest]) fn.mockReset()
  getRequest.mockImplementation(() => Promise.resolve({ data: current }))
})
afterEach(() => { mounted.forEach((w) => w.unmount()); mounted.length = 0; i18n.global.locale.value = 'en' })

describe('Tr2DocumentBody — commit cancel retry while revert_pending (RC3)', () => {
  it('offers the retry only while the server says revert_pending', async () => {
    const pending = mountBody(); await flushPromises()
    expect(pending.find('[data-testid="tr2-lock"]').text()).toBe(t('main.tr2_body.lock.revert_pending'))
    expect(pending.find('[data-testid="tr2-revert-retry"]').text()).toBe(t('main.tr2_body.revert_retry.action'))
    current = view(null)
    const editable = mountBody(); await flushPromises()
    expect(editable.find('[data-testid="tr2-revert-retry"]').exists()).toBe(false)
    current = view('revert_pending')
    const aiRunning = mountBody(true); await flushPromises()
    expect(aiRunning.find('[data-testid="tr2-revert-retry"]').exists()).toBe(false)
  })

  it('a successful retry reports it and re-reads the server state', async () => {
    postRequest.mockImplementation(async (url: string) => {
      expect(url).toBe(retryUrl)
      current = view(null)
      return { data: { ok: true, tr_commit_cancel: { canceled: [{ doc_id: docId, commit: 'abc1234' }], blocked_reason: null } } }
    })
    const wrapper = mountBody(); await flushPromises()
    await wrapper.find('[data-testid="tr2-revert-retry"]').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(retryUrl, {})
    expect(wrapper.find('.tr2-notice').text()).toBe(t('main.tr2_body.revert_retry.done'))
    expect(wrapper.find('[data-testid="tr2-revert-retry"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="tr2-lock"]').exists()).toBe(false)
  })

  it('a blocked retry names why and keeps the retry available', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, tr_commit_cancel: { canceled: [], blocked_reason: 'git_busy', retryable: true } } })
    const wrapper = mountBody(); await flushPromises()
    await wrapper.find('[data-testid="tr2-revert-retry"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.tr2-error').text()).toBe(t('main.tr2_body.revert_retry.blocked', {
      reason: t('main.time_machine.reason_git_busy'),
    }))
    expect(wrapper.find('[data-testid="tr2-revert-retry"]').exists()).toBe(true)
  })
})
