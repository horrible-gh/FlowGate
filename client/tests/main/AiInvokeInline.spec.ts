import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeInline from '@main/components/AiInvokeInline.vue'
import { INLINE_RESULT_WINDOW_MS, useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

describe('AiInvokeInline', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    getRequest.mockResolvedValue({ data: { active: false } })
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('hides only a run whose docRef matches suppressDocRef', async () => {
    const wrapper = mount(AiInvokeInline, {
      props: {
        groupId: 'flowgate.default.0258',
        suppressDocRef: 'flowgate.default.0258.0009-CH',
      },
      global: { plugins: [i18n] },
    })
    const store = useAiInvokeRunsStore()

    store.trackStarted({
      run_id: 'run-chat', group_id: 'flowgate.default.0258',
      doc_ref: 'flowgate.default.0258.0009-CH', status: 'running',
    })
    await nextTick()
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)

    store.trackStarted({
      run_id: 'run-next', group_id: 'flowgate.default.0258',
      doc_ref: 'flowgate.default.0258.0001-B', status: 'running',
    })
    await nextTick()
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)
    wrapper.unmount()
  })

  it('resets only the content scroller when a run starts and renders zero-document diagnostics', async () => {
    const content = document.createElement('div')
    content.className = 'content-wrap'
    const host = document.createElement('div')
    content.appendChild(host)
    document.body.appendChild(content)
    const scrollTo = vi.fn()
    Object.defineProperty(content, 'scrollTo', { value: scrollTo, configurable: true })

    const wrapper = mount(AiInvokeInline, {
      attachTo: host,
      props: { groupId: 'flowgate.default.0231' },
      global: { plugins: [i18n] },
    })
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-0231', group_id: 'flowgate.default.0231',
      doc_ref: 'flowgate.default.0231.0001-B', status: 'running',
    })
    await nextTick()
    await nextTick()
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })

    store.trackFinished({
      run_id: 'run-0231', group_id: 'flowgate.default.0231', outcome: 'none', docs_reached: 0,
      register_errors: [{ status: 403, reason: 'context_binding', turn: 2 }],
      tool_call_misses: 2, turn_limit_exhausted: true, oracle_mismatch: false,
      end_reason: 'exited',
    })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('HTTP 403')
    expect(text).toContain('context_binding')
    expect(text).toContain(i18n.global.t('main.ai_invoke_dialog.turn_limit_exhausted'))
    wrapper.unmount()
  })

  it('renders pause_requested without failure wording and closes only the inline surface', async () => {
    const groupId = 'flowgate.default.0384'
    const wrapper = mount(AiInvokeInline, {
      props: { groupId },
      global: { plugins: [i18n] },
    })
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-pause', group_id: groupId,
      doc_ref: `${groupId}.0001-B`, mode: 'continuous', status: 'running',
    })
    postRequest.mockResolvedValueOnce({
      data: { ok: true, run_id: 'run-pause', status: 'pause_requested' },
    })
    await store.pause(groupId)
    await nextTick()

    expect(wrapper.find('[data-test="ai-inline-pause-state"]').text()).toBe(
      i18n.global.t('main.ai_miniplayer.pause_scheduled'),
    )
    expect(wrapper.text()).not.toContain(i18n.global.t('main.ai_invoke_dialog.outcome_none'))
    expect(wrapper.text()).not.toContain(i18n.global.t('main.ai_invoke_dialog.outcome_none_scoped'))

    await wrapper.find('[data-test="ai-inline-close"]').trigger('click')
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)
    expect(store.runsByGroup[groupId]?.phase).toBe('pause_requested')
    wrapper.unmount()
  })

  it('renders paused without failure wording and preserves the resumable card on close', async () => {
    const groupId = 'flowgate.default.0385'
    const wrapper = mount(AiInvokeInline, {
      props: { groupId },
      global: { plugins: [i18n] },
    })
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-paused', group_id: groupId,
      doc_ref: `${groupId}.0001-B`, mode: 'continuous',
    })
    store.trackFinished({
      run_id: 'run-paused', group_id: groupId, end_reason: 'user_paused',
    })
    await nextTick()

    expect(wrapper.find('[data-test="ai-inline-pause-state"]').text()).toBe(
      i18n.global.t('main.ai_miniplayer.state_paused'),
    )
    expect(wrapper.text()).not.toContain(i18n.global.t('main.ai_invoke_dialog.outcome_none'))
    expect(wrapper.text()).not.toContain(i18n.global.t('main.ai_invoke_dialog.outcome_none_scoped'))

    await wrapper.find('[data-test="ai-inline-close"]').trigger('click')
    expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)
    expect(store.runsByGroup[groupId]?.phase).toBe('paused')
    wrapper.unmount()
  })

  // 0290 NR0003 §5.3: the header monitor keeps a finished card for 30 minutes, but this
  // banner sits on the document — it gets its own, much shorter, view of the same entry.
  // The registry keeps the card either way; only this surface stops showing it.
  it('stops showing a finished run after its own short window, without dismissing it', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = mount(AiInvokeInline, {
        props: { groupId: 'flowgate.default.0290' },
        global: { plugins: [i18n] },
      })
      const store = useAiInvokeRunsStore()
      store.trackStarted({
        run_id: 'run-w', group_id: 'flowgate.default.0290',
        doc_ref: 'flowgate.default.0290.0001-R', status: 'running',
      })
      store.trackFinished({
        run_id: 'run-w', group_id: 'flowgate.default.0290', outcome: 'complete', docs_reached: 1,
      })
      await nextTick()
      expect(wrapper.find('.aiv-inline-layer').exists()).toBe(true)

      vi.advanceTimersByTime(INLINE_RESULT_WINDOW_MS + 1_000)
      await nextTick()
      expect(wrapper.find('.aiv-inline-layer').exists()).toBe(false)
      expect(store.runsByGroup['flowgate.default.0290']?.phase).toBe('finished')
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})