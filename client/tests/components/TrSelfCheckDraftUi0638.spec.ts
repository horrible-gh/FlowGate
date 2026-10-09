/**
 * flowgate.default.0638 T#2 — Self-check before the TR exists, on the screen.
 *
 * T#1 gave a TR(new) worker token its own Self-check runs (/api/v1/self-check/draft), linked to
 * the TR when the token registers it. These specs pin the UI side of that contract:
 *   - the instruction (T) screen shows the group's unregistered runs read/cancel-only (a console
 *     user owns no TR(new) token, so there is no Run), and hides itself while there are none;
 *   - a run linked to its TR leaves that list and shows in the TR's own panel, marked as run
 *     before registration, still read through the unchanged TR-bound route.
 */
import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, getRequest, postRequest }
})

import DocumentBodyRouter from '@main/components/documents/DocumentBodyRouter.vue'
import TrSelfCheckDraftPanel from '@main/components/documents/TrSelfCheckDraftPanel.vue'
import TrSelfCheckPanel from '@main/components/documents/TrSelfCheckPanel.vue'

const GROUP = 'flowgate.default.0638'
const DRAFT = '/api/v1/self-check/draft/runs'
const TR = `${GROUP}.0008-TR`

function run(overrides: Record<string, unknown> = {}) {
  return {
    self_check_run_id: 'scr_draft_1', group_id: GROUP, tr_doc_id: null, status: 'running',
    program: 'pytest', created_at: '2026-10-09T01:00:00', linked_at: null, exit_code: null,
    timed_out: false, cancel_requested: false, stdout_tail: 'collecting', stderr_tail: '', error_code: null,
    ...overrides,
  }
}

function emitUpdate(detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent('fg:self_check_run_updated', { detail }))
}

function draftPanel(wrapper: { find: (selector: string) => { exists: () => boolean } }) {
  return wrapper.find('[data-testid="tr-self-check-draft-panel"]').exists()
}

// Mirrors @shared/api getRequest: a GET whose (path, params) twin is still in flight shares that
// pending response instead of reaching the server. Each list read that does reach the server is
// held until the test answers it through the returned array, in send order.
function coalescingLists() {
  const sent: Array<(runs: unknown[]) => void> = []
  const inflight = new Map<string, Promise<unknown>>()
  getRequest.mockImplementation((path: string, params: Record<string, unknown> = {}) => {
    if (path.endsWith('/settings')) return Promise.resolve({ data: { tr_self_check_enabled: true } })
    const key = `${path}?${JSON.stringify(params)}`
    const shared = inflight.get(key)
    if (shared) return shared
    const request = new Promise((resolve) => { sent.push((runs) => resolve({ data: { ok: true, runs } })) })
      .finally(() => inflight.delete(key))
    inflight.set(key, request)
    return request
  })
  return sent
}

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('TrSelfCheckDraftPanel (TR not registered yet)', () => {
  it('lists the group draft runs through the user route with group_id and offers no Run', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, runs: [run()] } })
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(DRAFT, { group_id: GROUP })
    expect(getRequest.mock.calls.every(([path]) => !String(path).includes('/documents/'))).toBe(true)
    const panel = wrapper.get('[data-testid="tr-self-check-draft-panel"]')
    expect(panel.text()).toContain('TR 등록 전')
    expect(panel.text()).toContain('collecting')
    expect(wrapper.findAll('button').map((b) => b.text())).toEqual(['Cancel'])
    expect(wrapper.find('input').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders nothing while the group has no unregistered run', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, runs: [] } })
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()
    expect(draftPanel(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it('cancels the active draft run through the draft route', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, runs: [run()] } })
    postRequest.mockResolvedValue({ data: { ok: true, ...run({ cancel_requested: true }) } })
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()

    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${DRAFT}/scr_draft_1/cancel`, {})
    wrapper.unmount()
  })

  it('shows the server error code when cancel is refused', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, runs: [run()] } })
    postRequest.mockRejectedValue({ response: { data: { error: { code: 'forbidden' } } } })
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('forbidden')
    wrapper.unmount()
  })

  it('reads one run through the draft read route when selected', async () => {
    getRequest.mockImplementation(async (path: string) => path === DRAFT
      ? { data: { ok: true, runs: [run(), run({ self_check_run_id: 'scr_draft_0', status: 'failed', exit_code: 1 })] } }
      : { data: { ok: true, ...run({ self_check_run_id: 'scr_draft_0', status: 'failed', exit_code: 1, stdout_tail: 'E assert' }) } })
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()

    await wrapper.get('select').setValue('scr_draft_0')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(`${DRAFT}/scr_draft_0`)
    expect(wrapper.text()).toContain('E assert')
    wrapper.unmount()
  })

  it('appears on a new draft run event and disappears once the run is linked to its TR', async () => {
    getRequest.mockResolvedValueOnce({ data: { ok: true, runs: [] } })
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()
    expect(draftPanel(wrapper)).toBe(false)

    getRequest.mockResolvedValueOnce({ data: { ok: true, runs: [run()] } })
    emitUpdate({ group_id: GROUP, tr_doc_id: null, draft: true, self_check_run_id: 'scr_draft_1', status: 'running' })
    await flushPromises()
    expect(draftPanel(wrapper)).toBe(true)

    // Registration links the run: the server emits it with the new TR id and draft=false.
    getRequest.mockResolvedValueOnce({ data: { ok: true, runs: [] } })
    emitUpdate({ group_id: GROUP, tr_doc_id: TR, draft: false, self_check_run_id: 'scr_draft_1', status: 'running' })
    await flushPromises()
    expect(draftPanel(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it("ignores another group's events", async () => {
    getRequest.mockResolvedValue({ data: { ok: true, runs: [] } })
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()
    const calls = getRequest.mock.calls.length
    emitUpdate({ group_id: 'flowgate.default.9999', tr_doc_id: null, draft: true })
    await flushPromises()
    expect(getRequest.mock.calls.length).toBe(calls)
    wrapper.unmount()
  })

  it('re-reads once after the in-flight first load when a run is created meanwhile', async () => {
    const sent = coalescingLists()
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()
    expect(sent).toHaveLength(1)

    // The run is created (two events) while the first list GET is still pending.
    emitUpdate({ group_id: GROUP, tr_doc_id: null, draft: true, self_check_run_id: 'scr_draft_1', status: 'pending' })
    emitUpdate({ group_id: GROUP, tr_doc_id: null, draft: true, self_check_run_id: 'scr_draft_1', status: 'running' })
    await flushPromises()
    expect(sent).toHaveLength(1)

    sent[0]([]) // read by the server before the run existed
    await flushPromises()
    expect(draftPanel(wrapper)).toBe(false)
    expect(sent).toHaveLength(2) // one follow-up read for both events

    sent[1]([run()])
    await flushPromises()
    expect(draftPanel(wrapper)).toBe(true)
    expect(sent).toHaveLength(2)
    wrapper.unmount()
  })

  it('re-reads once after the in-flight first load when the run is linked to its TR meanwhile', async () => {
    const sent = coalescingLists()
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()

    emitUpdate({ group_id: GROUP, tr_doc_id: TR, draft: false, self_check_run_id: 'scr_draft_1', status: 'completed' })
    await flushPromises()
    expect(sent).toHaveLength(1)

    // Read before linking: a finished run, so there is no polling to correct it later.
    sent[0]([run({ status: 'completed', exit_code: 0 })])
    await flushPromises()
    expect(sent).toHaveLength(2)

    sent[1]([])
    await flushPromises()
    expect(draftPanel(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it('polls while a run is active and stops once it finished', async () => {
    vi.useFakeTimers()
    getRequest.mockResolvedValueOnce({ data: { ok: true, runs: [run()] } })
    const wrapper = mount(TrSelfCheckDraftPanel, { props: { groupId: GROUP } })
    await flushPromises()

    getRequest.mockResolvedValueOnce({ data: { ok: true, runs: [run({ status: 'completed', exit_code: 0 })] } })
    await vi.advanceTimersByTimeAsync(1600)
    await flushPromises()
    expect(wrapper.text()).toContain('Exit code: 0')
    const calls = getRequest.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(getRequest.mock.calls.length).toBe(calls)
    wrapper.unmount()
  })
})

describe('TrSelfCheckPanel after registration', () => {
  const tab = { id: TR, title: 'TR', path: 'x.md', type: 'md' as const, typeCode: 'TR', projectId: 'flowgate' }

  function serve(runs: unknown[]) {
    getRequest.mockImplementation(async (path: string) => path.endsWith('/settings')
      ? { data: { tr_self_check_enabled: true } }
      : { data: { ok: true, runs } })
  }

  it('reads the linked run through the unchanged TR-bound route and marks it as run before registration', async () => {
    serve([run({ tr_doc_id: TR, status: 'completed', exit_code: 0, linked_at: '2026-10-09T01:05:00' })])
    const wrapper = mount(TrSelfCheckPanel, { props: { tab } })
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(`/api/v1/documents/${TR}/self-check/runs`)
    expect(getRequest.mock.calls.every(([path]) => !String(path).includes('/self-check/draft'))).toBe(true)
    expect(wrapper.get('[data-testid="tr-self-check-linked"]').text()).toContain('2026-10-09T01:05:00')
    expect(wrapper.get('option').text()).toContain('TR 등록 전')
    wrapper.unmount()
  })

  it('shows no pre-registration mark on a run started on the TR itself', async () => {
    serve([run({ tr_doc_id: TR, status: 'completed', exit_code: 0 })])
    const wrapper = mount(TrSelfCheckPanel, { props: { tab } })
    await flushPromises()
    expect(wrapper.find('[data-testid="tr-self-check-linked"]').exists()).toBe(false)
    expect(wrapper.get('option').text()).not.toContain('TR 등록 전')
    wrapper.unmount()
  })

  it('still starts a run on the TR-bound route (edit path unchanged)', async () => {
    serve([])
    postRequest.mockResolvedValue({ data: { ok: true, ...run({ tr_doc_id: TR, status: 'pending' }) } })
    const wrapper = mount(TrSelfCheckPanel, { props: { tab } })
    await flushPromises()

    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/documents/${TR}/self-check/runs`, expect.objectContaining({ program: 'pytest' }))
    wrapper.unmount()
  })

  it('refreshes when its run is linked (event carries the TR id)', async () => {
    serve([])
    const wrapper = mount(TrSelfCheckPanel, { props: { tab } })
    await flushPromises()
    serve([run({ tr_doc_id: TR, linked_at: '2026-10-09T01:05:00' })])
    emitUpdate({ group_id: GROUP, tr_doc_id: TR, draft: false, self_check_run_id: 'scr_draft_1' })
    await flushPromises()
    expect(wrapper.find('[data-testid="tr-self-check-linked"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('re-reads after the in-flight first load when its run is linked meanwhile', async () => {
    const sent = coalescingLists()
    const wrapper = mount(TrSelfCheckPanel, { props: { tab } })
    await flushPromises()
    expect(sent).toHaveLength(1)

    emitUpdate({ group_id: GROUP, tr_doc_id: TR, draft: false, self_check_run_id: 'scr_draft_1', status: 'completed' })
    await flushPromises()
    expect(sent).toHaveLength(1)

    sent[0]([]) // read before linking
    await flushPromises()
    expect(sent).toHaveLength(2)

    sent[1]([run({ tr_doc_id: TR, status: 'completed', exit_code: 0, linked_at: '2026-10-09T01:05:00' })])
    await flushPromises()
    expect(wrapper.find('[data-testid="tr-self-check-linked"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('DocumentBodyRouter wiring', () => {
  const common = {
    readOnly: false, completed: false, canEdit: true, editDropdownOpen: false, textWrapEnabled: false,
    downloadAvailable: false, downloadBusy: false, uploadBusy: false, conversationReadOnly: false,
    conversationManualCopyText: null, conversationFullViewHost: null, conversationFullViewOn: false,
  }

  function route(typeCode: string, id: string) {
    return shallowMount(DocumentBodyRouter, { props: { ...common, tab: { id, title: id, path: 'x.md', type: 'md', typeCode } } })
  }

  it('gives an instruction (T) screen the group draft panel', () => {
    const wrapper = route('T', `${GROUP}.0007-T`)
    expect(wrapper.findComponent(TrSelfCheckDraftPanel).props('groupId')).toBe(GROUP)
    expect(wrapper.findComponent(TrSelfCheckPanel).exists()).toBe(false)
  })

  it('keeps the TR screen on the TR-bound panel only', () => {
    const wrapper = route('TR', TR)
    expect(wrapper.findComponent(TrSelfCheckPanel).exists()).toBe(true)
    expect(wrapper.findComponent(TrSelfCheckDraftPanel).exists()).toBe(false)
  })

  it('adds no Self-check panel to other document types', () => {
    for (const code of ['B', 'NR', 'TS']) {
      const wrapper = route(code, `${GROUP}.0001-${code}`)
      expect(wrapper.findComponent(TrSelfCheckPanel).exists()).toBe(false)
      expect(wrapper.findComponent(TrSelfCheckDraftPanel).exists()).toBe(false)
    }
  })
})
