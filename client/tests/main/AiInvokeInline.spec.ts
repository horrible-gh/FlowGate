import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeInline from '@main/components/AiInvokeInline.vue'
import { INLINE_RESULT_WINDOW_MS, useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { RETENTION_MIRROR_KEY } from '@shared/aiFinishedCardRetention'

const inlineSource = readFileSync(join(process.cwd(), 'src/main/components/AiInvokeInline.vue'), 'utf8')

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

  it('uses an in-flow sticky card without the former overlay or pointer-blocking styles', () => {
    expect(inlineSource).toContain('class="ai-invoke-status-card"')
    expect(inlineSource).toContain('position: sticky;')
    expect(inlineSource).toContain('top: 0;')
    expect(inlineSource).toContain('z-index: 30;')
    expect(inlineSource).not.toContain('position: absolute;')
    expect(inlineSource).not.toContain('inset: 0;')
    expect(inlineSource).not.toContain('backdrop-filter')
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
    expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(false)

    store.trackStarted({
      run_id: 'run-next', group_id: 'flowgate.default.0258',
      doc_ref: 'flowgate.default.0258.0001-B', status: 'running',
    })
    await nextTick()
    expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(true)
    wrapper.unmount()
  })

  it('preserves the content scroll position when a run starts and renders zero-document diagnostics', async () => {
    const content = document.createElement('div')
    content.className = 'content-wrap'
    const host = document.createElement('div')
    content.appendChild(host)
    document.body.appendChild(content)
    const scrollTo = vi.fn()
    Object.defineProperty(content, 'scrollTo', { value: scrollTo, configurable: true })
    content.scrollTop = 180

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
    expect(scrollTo).not.toHaveBeenCalled()
    expect(content.scrollTop).toBe(180)

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

  it('shows the currently running provider name and elapsed time on the running card', async () => {
    const wrapper = mount(AiInvokeInline, {
      props: { groupId: 'flowgate.default.0398' },
      global: { plugins: [i18n] },
    })
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-provider', group_id: 'flowgate.default.0398',
      doc_ref: 'flowgate.default.0398.0001-B', status: 'running',
      provider_id: 'claude', provider_name: 'Claude',
    })
    await nextTick()

    const meta = wrapper.find('[data-test="ai-inline-running-meta"]')
    expect(meta.exists()).toBe(true)
    expect(meta.text()).toContain('Claude')
    expect(meta.text()).toMatch(/\d+:\d{2}/)
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
    expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(false)
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
    expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(false)
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
      expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(true)

      vi.advanceTimersByTime(INLINE_RESULT_WINDOW_MS + 1_000)
      await nextTick()
      expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(false)
      expect(store.runsByGroup['flowgate.default.0290']?.phase).toBe('finished')
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  // 0452 L0003 §2-6. "Never expires" is a request about the header monitor's list, not a
  // request to keep a result panel parked on top of the document — which is the symptom
  // 0290 NR0003 §5.3 removed. The banner therefore stays capped at 60s while the card it
  // is reading stays in the registry indefinitely.
  it('still closes after 60s when finished cards are set never to expire', async () => {
    vi.useFakeTimers()
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    try {
      const wrapper = mount(AiInvokeInline, {
        props: { groupId: 'flowgate.default.0452' },
        global: { plugins: [i18n] },
      })
      const store = useAiInvokeRunsStore()
      expect(store.retentionMinutes).toBe(-1)
      store.trackStarted({
        run_id: 'run-never', group_id: 'flowgate.default.0452',
        doc_ref: 'flowgate.default.0452.0001-R', status: 'running',
      })
      store.trackFinished({
        run_id: 'run-never', group_id: 'flowgate.default.0452', outcome: 'complete', docs_reached: 1,
      })
      await nextTick()
      expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(true)

      vi.advanceTimersByTime(INLINE_RESULT_WINDOW_MS + 1_000)
      await nextTick()
      expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(false)
      // The card itself is untouched — this surface expired its own view, nothing more.
      expect(store.runsByGroup['flowgate.default.0452']?.phase).toBe('finished')
      wrapper.unmount()
    } finally {
      localStorage.removeItem(RETENTION_MIRROR_KEY)
      vi.useRealTimers()
    }
  })
})

describe('AiInvokeInline document review loop cards (0417 T0013)', () => {
  it.each([
    ['ko', '검수', '지적 있음', '반려대응', '통과'],
    ['en', 'Review', 'Issues found', 'Rework', 'Passed'],
    ['ja', 'レビュー', '指摘あり', '差し戻し対応', '合格'],
  ] as const)('localizes prior review/rework/result rows in %s without raw backend identifiers', async (locale, review, issues, rework, passed) => {
    const previousLocale = i18n.global.locale.value
    i18n.global.locale.value = locale
    const groupId = 'flowgate.default.0417.history-' + locale
    setActivePinia(createPinia())
    const wrapper = mount(AiInvokeInline, {
      props: { groupId },
      global: { plugins: [i18n] },
    })
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'loop-history-' + locale, group_id: groupId, status: 'running',
      document_review_loop: {
        round_no: 2, current_stage: 'review',
        history: [
          { round_no: 1, stage: 'review', result: 'issues' },
          { round_no: 2, stage: 'rework', result: 'passed' },
        ],
      },
    })
    await nextTick()

    const rows = wrapper.findAll('[data-test="review-loop-card"] li').map(row => row.text())
    expect(rows[0]).toContain(review)
    expect(rows[0]).toContain(issues)
    expect(rows[1]).toContain(rework)
    expect(rows[1]).toContain(passed)
    expect(rows.join(' ')).not.toMatch(/\b(review|rework|issues|passed)\b/)
    wrapper.unmount()
    i18n.global.locale.value = previousLocale
  })

  it.each([
    'review_passed',
    'review_count_exhausted',
    'retry_exhausted',
    'total_timeout',
  ] as const)('renders accumulated rounds, %s, and stop detail without duplicate history', async (reason) => {
    const groupId = 'flowgate.default.0417.' + reason
    localStorage.setItem(RETENTION_MIRROR_KEY, '-1')
    setActivePinia(createPinia())
    const wrapper = mount(AiInvokeInline, {
      props: { groupId },
      global: { plugins: [i18n] },
    })
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'loop-' + reason, group_id: groupId, status: 'running',
      document_review_loop: {
        round_no: 2, current_stage: 'rework',
        history: [
          { round_no: 1, stage: 'review', result: 'issues' },
          { round_no: 2, stage: 'rework', result: 'complete' },
        ],
      },
    })
    store.trackFinished({
      run_id: 'loop-' + reason, group_id: groupId, status: 'finished', outcome: 'complete',
      document_review_loop: {
        round_no: 2, current_stage: 'stopped', stop_reason: reason,
        stop_detail: 'server detail',
        history: [{ round_no: 2, stage: 'rework', result: 'complete' }],
      },
    })
    await nextTick()

    const card = wrapper.find('[data-test="review-loop-card"]')
    expect(card.exists()).toBe(true)
    expect(card.findAll('li')).toHaveLength(2)
    expect(card.text()).toContain(i18n.global.t('main.ai_invoke_dialog.review_loop_stop_' + reason))
    expect(card.text()).toContain('server detail')
    wrapper.unmount()
  })
})

