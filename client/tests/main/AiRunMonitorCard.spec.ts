import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiRunMonitorCard from '@main/components/AiRunMonitorCard.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { useToast } from '@main/components/common/useToast'
import { mountMainPanel } from '../helpers/mountMainPanel'

const { getRequest, postRequest, deleteRequest } = vi.hoisted(() => (
  { getRequest: vi.fn(), postRequest: vi.fn(), deleteRequest: vi.fn() }
))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
  patchRequest: vi.fn(),
  deleteRequest,
}))

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

function mountCard() {
  return mount(AiRunMonitorCard, { global: { plugins: [i18n] } })
}

describe('AiRunMonitorCard (dashboard)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    deleteRequest.mockReset()
    useToast().toasts.value = []
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) return { data: { ok: true, runs: [], paused: [] } }
      if (url.includes('/documents/detail')) {
        const docId = decodeURIComponent(url.split('doc_id=')[1] ?? '')
        return { data: { doc_id: docId, title: '대상 문서', type_code: 'R', file_path: 'a.md' } }
      }
      return { data: {} }
    })
  })

  it('renders on the dashboard with an empty state when nothing is running', async () => {
    const wrapper = mountCard()
    await flushPromises()
    expect(wrapper.find('[data-test="ai-run-monitor-card"]').exists()).toBe(true)
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_title'))
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_empty'))
    expect(wrapper.findAll('.airm-row')).toHaveLength(0)
    wrapper.unmount()
  })

  it('lists running, paused and awaiting-Q runs', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-a', group_id: 'flowgate.default.4001',
      doc_ref: 'flowgate.default.4001.0001-R', mode: 'continuous', docs_target: 4,
    })
    store.trackFinished({
      run_id: 'run-b', group_id: 'flowgate.default.4002',
      doc_ref: 'flowgate.default.4002.0001-R', end_reason: 'user_paused',
    })
    store.trackStarted({
      run_id: 'run-c', group_id: 'flowgate.default.4003',
      doc_ref: 'flowgate.default.4003.0001-R', mode: 'single',
    })
    store.trackQuestionRegistered('flowgate.default.4003.0002-Q')
    await flushPromises()

    expect(wrapper.findAll('.airm-row')).toHaveLength(3)
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_state_running'))
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_state_paused'))
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_state_awaiting'))
    expect(wrapper.text()).not.toContain(t('main.ai_miniplayer.dash_empty'))
    wrapper.unmount()
  })

  it('shows chain progress instead of the shrinking hop target', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    const groupId = 'flowgate.default.0357'
    store.trackStarted({
      run_id: 'run-hop-2', group_id: groupId,
      doc_ref: `${groupId}.0001-B`, mode: 'continuous',
      docs_target: 4, chain_id: 'run-hop-1',
      chain_docs_target: 5, chain_docs_reached: 1,
    })
    await flushPromises()

    expect(wrapper.find('.airm-row-meta').text()).toContain(
      t('main.ai_miniplayer.progress', { reached: 1, target: 5 }),
    )
    wrapper.unmount()
  })

  it('opens the waiting Q — not the run document — from an awaiting row', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-q', group_id: 'flowgate.default.4004',
      doc_ref: 'flowgate.default.4004.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.4004.0007-Q')
    await flushPromises()

    await wrapper.find('.airm-row-main').trigger('click')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(
      expect.stringContaining('flowgate.default.4004.0007-Q'),
    )
    // 0290: opening acknowledges a FINISHED card only — a live run awaiting an answer
    // must stay on the dashboard after its Q is opened.
    expect(store.runsByGroup['flowgate.default.4004']).toBeDefined()
    wrapper.unmount()
  })

  it('removes a finished row when it is opened, and offers an explicit remove button', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-f', group_id: 'flowgate.default.4005',
      doc_ref: 'flowgate.default.4005.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-f', group_id: 'flowgate.default.4005',
      doc_ref: 'flowgate.default.4005.0001-R', outcome: 'complete',
    })
    store.trackStarted({
      run_id: 'run-g', group_id: 'flowgate.default.4006',
      doc_ref: 'flowgate.default.4006.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-g', group_id: 'flowgate.default.4006',
      doc_ref: 'flowgate.default.4006.0001-R', outcome: 'complete',
    })
    await flushPromises()

    // The remove button only appears on finished rows, and it is a sibling of the row
    // button — never nested inside it.
    expect(wrapper.findAll('[data-test="ai-run-monitor-remove"]')).toHaveLength(2)
    // Address the row by its document, not by list position — two runs can finish in the
    // same millisecond, so the finished band's order is not something to assert here.
    const rowFor = (docRef: string) => wrapper.findAll('.airm-row')
      .find(row => row.text().includes(docRef))!
    await rowFor('flowgate.default.4005.0001-R')
      .find('[data-test="ai-run-monitor-remove"]').trigger('click')
    expect(store.runsByGroup['flowgate.default.4005']).toBeUndefined()

    await rowFor('flowgate.default.4006.0001-R').find('.airm-row-main').trigger('click')
    await flushPromises()
    expect(store.runsByGroup['flowgate.default.4006']).toBeUndefined()
    wrapper.unmount()
  })

  // 0500 T0004 §7 (rev3). This dashboard list IS the "목록" of the report, and the X was
  // gated on isFinishedCard() alone -- so the non-resumable system-stop card the whole
  // group is about had NO remove control here at all. That is why the rev2 rejection was
  // "난 카드가 계속 뜨고있는데?": the one place the user meets the ghost card offered
  // nothing to press, and opening the row (which acknowledges finished cards) calls
  // dismiss(), which refuses every paused card.
  describe('non-resumable system-stop card (0500 T0004 §7)', () => {
    const GROUP = 'flowgate.default.0481'

    async function bootstrapGhostCard(overrides: Record<string, unknown> = {}) {
      getRequest.mockImplementation(async (url: string) => {
        if (url.includes('active-all')) {
          return {
            data: {
              ok: true,
              runs: [],
              paused: [{
                group_id: GROUP,
                doc_ref: `${GROUP}.0004-NR`,
                paused_at: '2026-09-01T10:00:00+09:00',
                stop_kind: 'system',
                stop_code: 'group_lease_denied',
                stop_run_id: 'aiv_lease_denied',
                resume_available: false,
                ...overrides,
              }],
            },
          }
        }
        return { data: {} }
      })
      const wrapper = mountCard()
      const store = useAiInvokeRunsStore()
      await store.bootstrap()
      await flushPromises()
      return { wrapper, store }
    }

    it('offers the remove button and releases the durable row on a confirmed click', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      deleteRequest.mockResolvedValue({ data: { ok: true, released: true, already_released: false } })
      const { wrapper, store } = await bootstrapGhostCard()
      expect(store.runsByGroup[GROUP]).toMatchObject({
        phase: 'paused', stopKind: 'system', stopCode: 'group_lease_denied', resumeAvailable: false,
      })

      const removeBtn = wrapper.find('[data-test="ai-run-monitor-remove"]')
      expect(removeBtn.exists()).toBe(true)

      await removeBtn.trigger('click')
      await flushPromises()

      expect(deleteRequest).toHaveBeenCalledWith(`/api/v1/ai-invoke/paused/${GROUP}`)
      expect(store.runsByGroup[GROUP]).toBeUndefined()
      expect(useToast().toasts.value.at(-1)).toMatchObject({
        message: t('main.ai_miniplayer.release_paused_success'),
        type: 'success',
      })
      wrapper.unmount()
      vi.restoreAllMocks()
    })

    it('keeps the card and explains why when the server refuses the release', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      deleteRequest.mockRejectedValue({ response: { status: 409, data: { code: 'group_lease_active' } } })
      const { wrapper, store } = await bootstrapGhostCard()

      await wrapper.find('[data-test="ai-run-monitor-remove"]').trigger('click')
      await flushPromises()

      expect(store.runsByGroup[GROUP]?.phase).toBe('paused')
      expect(useToast().toasts.value.at(-1)).toMatchObject({
        message: t('main.ai_miniplayer.error_release_paused_lease_conflict'),
        type: 'danger',
      })
      wrapper.unmount()
      vi.restoreAllMocks()
    })

    // §10: a resumable system stop keeps the paused lifecycle -- no remove control, and
    // certainly no DELETE. §9's user pause is covered by the store's own removeCard tests.
    it('offers no remove button on a resumable system stop', async () => {
      const { wrapper } = await bootstrapGhostCard({
        stop_code: 'no_output_exhausted', resume_available: true,
      })

      expect(wrapper.find('[data-test="ai-run-monitor-remove"]').exists()).toBe(false)
      expect(deleteRequest).not.toHaveBeenCalled()
      wrapper.unmount()
    })
  })

  it('keeps live runs above finished ones', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    // Group id order alone would put the finished run first — state has to win.
    store.trackStarted({
      run_id: 'run-old', group_id: 'flowgate.default.4001',
      doc_ref: 'flowgate.default.4001.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-old', group_id: 'flowgate.default.4001',
      doc_ref: 'flowgate.default.4001.0001-R', outcome: 'complete',
    })
    store.trackStarted({
      run_id: 'run-live', group_id: 'flowgate.default.4009',
      doc_ref: 'flowgate.default.4009.0001-R', mode: 'single',
    })
    await flushPromises()

    const docs = wrapper.findAll('.airm-row-doc').map(el => el.text())
    expect(docs[0]).toBe('flowgate.default.4009.0001-R')
    wrapper.unmount()
  })

  // The card must actually be wired into the dashboard overview, not merely exist:
  // a component nobody mounts is exactly the "안 보인다" failure this fixes.
  //
  // 0394 T0016 (NR0003 §6.2-라): this case used to read MainPanel.vue as text — an import
  // line, then the tag somewhere after the string `class="overview-panel"`. Both halves
  // are text placement, not behaviour: renaming the panel's class, or leaving the tag in a
  // branch that never renders, keeps the substrings in place and the case green. Mount the
  // panel in the state where the dashboard IS the screen (no document open) and look for
  // the component in the rendered tree instead.
  it('is mounted by the dashboard overview panel', async () => {
    const wrapper = await mountMainPanel({ tabs: [] })

    expect(wrapper.find('.overview-panel').exists()).toBe(true)
    expect(wrapper.findComponent(AiRunMonitorCard).exists()).toBe(true)
    wrapper.unmount()
  })
})
