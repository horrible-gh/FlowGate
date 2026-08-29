import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { watch } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NotificationCenter from '@main/components/NotificationCenter.vue'
import { useNotificationsStore } from '@main/stores/notifications'
import { useProjectStore } from '@main/stores/project'
import type { DashboardActivity } from '@main/stores/dashboard'; import { getRequest } from '@shared/api'
import { useQaOpenIntent } from '@main/composables/useQaOpenIntent'

// R0001 group 0135 / N0008 — 시안 3 (라이브 피드) actually applied to the notification center.
// These lock the VISIBLE mockup features the user asked for: filter tabs with live counts, per-row
// trust colours + AI verdict badges, and the "완료로 떴지만 issues" warning — so opening the bell
// no longer looks unchanged.

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
}))

const { openDashboardTarget, routerPush, currentRoute } = vi.hoisted(() => ({
  openDashboardTarget: vi.fn(),
  routerPush: vi.fn(),
  currentRoute: { value: { path: '/' } },
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush, currentRoute }),
}))
vi.mock('@main/composables/useDashboardNavigation', () => ({
  useDashboardNavigation: () => ({ openDashboardTarget }),
}))

function activity(over: Partial<DashboardActivity> & { event_id: number }): DashboardActivity {
  return {
    event_id: over.event_id,
    activity_type: 'document_created',
    occurred_at: '2026-07-02T00:05:00Z',
    actor: null,
    group: { group_id: 'flowgate.default.0135', title: 'Triage' },
    document: { doc_id: 'flowgate.default.0135.0010-TR', type_code: 'TR', title: 'Report', review: null },
    transition: null,
    navigation: { kind: 'document', doc_id: 'flowgate.default.0135.0010-TR' },
    ...over,
  }
}

async function mountOpen(items: DashboardActivity[]) {
  const project = useProjectStore()
  project.currentProjectId = 'flowgate'
  const store = useNotificationsStore()
  vi.spyOn(store, 'fetchFeed').mockResolvedValue()
  vi.spyOn(store, 'markSeen').mockResolvedValue()
  const wrapper = mount(NotificationCenter, { global: { plugins: [i18n] } })
  // Seed after mount so onMounted's refresh (stubbed) doesn't clobber it; keep watermark null so
  // isUnread stays true for the fresh/unread paths.
  store.items = items
  store.lastSeenAt = null
  await wrapper.find('.notif-bell').trigger('click')
  await flushPromises()
  return wrapper
}

describe('NotificationCenter 시안 3 mockup (group 0135)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
    vi.mocked(getRequest).mockReset()
    openDashboardTarget.mockReset()
    routerPush.mockReset()
    routerPush.mockResolvedValue(undefined)
    currentRoute.value.path = '/'
  })

  it('renders the LIVE indicator and filter tabs with live counts', async () => {
    const items = [
      activity({ event_id: 1, document: { doc_id: 'a', type_code: 'TR', title: 'A', review: { status: 'open', verdict: 'issues', finding_count: 2 } } }),
      activity({ event_id: 2, document: { doc_id: 'b', type_code: 'NR', title: 'B', review: { status: 'open', verdict: 'pass', finding_count: 0 } } }),
      activity({ event_id: 3, document: { doc_id: 'c', type_code: 'T', title: 'C', review: null } }),
    ]
    const wrapper = await mountOpen(items)

    expect(wrapper.find('.notif-live').exists()).toBe(true)
    const tabCounts = wrapper.findAll('.notif-tab .notif-tab-n').map((n) => n.text())
    // 전체 3 / 확인 필요 1 (only the `issues` row) / 미확인 3 (no watermark → all unread)
    expect(tabCounts).toEqual(['3', '1', '3'])
  })

  it('paints trust colours + AI badge and warns on a completed-but-issues row', async () => {
    const items = [
      activity({ event_id: 1, document: { doc_id: 'a', type_code: 'TR', title: 'A', review: { status: 'open', verdict: 'issues', finding_count: 2 } } }),
    ]
    const wrapper = await mountOpen(items)

    const row = wrapper.find('.notif-item')
    expect(row.classes()).toContain('notif-item--danger')
    expect(wrapper.find('.notif-ai-badge--danger').text()).toBe('issues 2')
    expect(wrapper.find('.notif-warn').exists()).toBe(true)
  })

  it('filters to attention-only rows when the 확인 필요 tab is selected', async () => {
    const items = [
      activity({ event_id: 1, document: { doc_id: 'a', type_code: 'TR', title: 'A', review: { status: 'open', verdict: 'issues', finding_count: 2 } } }),
      activity({ event_id: 2, document: { doc_id: 'b', type_code: 'NR', title: 'B', review: { status: 'open', verdict: 'pass', finding_count: 0 } } }),
    ]
    const wrapper = await mountOpen(items)

    // Click the second tab (확인 필요).
    await wrapper.findAll('.notif-tab')[1].trigger('click')
    const rows = wrapper.findAll('.notif-item')
    expect(rows).toHaveLength(1)
    expect(rows[0].classes()).toContain('notif-item--danger')
  })

  it('shows General, AI, and Q&A section tabs in order with General selected', async () => {
    const wrapper = await mountOpen([activity({ event_id: 1 })])

    const sectionTabs = wrapper.findAll('.notif-section-tab')
    expect(sectionTabs.map((tab) => tab.text())).toEqual(['일반', 'AI 0', '질의응답 0'])
    expect(sectionTabs.map((tab) => tab.attributes('role'))).toEqual(['tab', 'tab', 'tab'])
    expect(sectionTabs.map((tab) => tab.attributes('aria-selected'))).toEqual(['true', 'false', 'false'])
    expect(wrapper.find('.notif-tabs').exists()).toBe(true)
  })

  it('keeps the existing filters and feed inside General while section switches only replace the body', async () => {
    const items = [
      activity({ event_id: 1, document: { doc_id: 'a', type_code: 'TR', title: 'A', review: { status: 'open', verdict: 'issues', finding_count: 2 } } }),
      activity({ event_id: 2, document: { doc_id: 'b', type_code: 'NR', title: 'B', review: { status: 'open', verdict: 'pass', finding_count: 0 } } }),
    ]
    const wrapper = await mountOpen(items)
    const store = useNotificationsStore()
    const fetchFeed = vi.mocked(store.fetchFeed)
    const callsBeforeSwitch = fetchFeed.mock.calls.length

    const generalFilters = wrapper.findAll('.notif-tab')
    expect(generalFilters.map((tab) => tab.text())).toEqual(['전체 2', '확인 필요 1', '미확인 2'])
    await generalFilters[1].trigger('click')
    expect(wrapper.findAll('.notif-item')).toHaveLength(1)

    await wrapper.findAll('.notif-section-tab')[1].trigger('click')
    expect(wrapper.find('.notif-tabs').exists()).toBe(false)
    expect(wrapper.find('.notif-item').exists()).toBe(false)
    expect(wrapper.find('.notif-mark-read').exists()).toBe(false)
    expect(wrapper.find('.notif-ai-section .notif-empty').text()).toContain('완료된 AI 호출')

    await wrapper.findAll('.notif-section-tab')[2].trigger('click')
    expect(wrapper.find('.notif-qa-section .notif-empty').text()).toContain('대기 중인 질의응답')

    await wrapper.findAll('.notif-section-tab')[0].trigger('click')
    expect(wrapper.find('.notif-tabs').exists()).toBe(true)
    expect(wrapper.findAll('.notif-item')).toHaveLength(1)
    expect(wrapper.find('.notif-mark-read').exists()).toBe(true)
    expect(fetchFeed).toHaveBeenCalledTimes(callsBeforeSwitch)
  })

  it('renders Q&A document rows and records navigation intent before opening the document', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    store.qaItems = [{ doc_id: 'flowgate.default.0135.0017-T', title: 'Task title', type_code: 'T' }]
    store.qaTotal = 3
    await wrapper.findAll('.notif-section-tab')[2].trigger('click')

    expect(wrapper.findAll('.notif-section-tab')[2].text()).toBe('질의응답 3')
    expect(wrapper.find('.notif-qa-row').text()).toContain('flowgate.default.0135.0017-T — Task title')
    currentRoute.value.path = '/requirements/create'

    // Record the exact order of "intent recorded" vs "panel closed" with sync watchers on
    // the underlying refs — both mutations happen synchronously back-to-back in
    // openQaDocument(), so any assertion that waits for a DOM/microtask flush would pass
    // even if the two statements were swapped. flush: 'sync' fires the moment each ref
    // is written, before either can be reordered by scheduling.
    const order: string[] = []
    const { intent } = useQaOpenIntent()
    const stopIntentWatch = watch(() => intent.value?.docId, (docId) => {
      if (docId) order.push('intent-recorded')
    }, { flush: 'sync' })
    const stopOpenWatch = watch(() => wrapper.vm.open, (isOpen) => {
      if (!isOpen) order.push('panel-closed')
    }, { flush: 'sync' })

    await wrapper.find('.notif-qa-open').trigger('click')
    stopIntentWatch()
    stopOpenWatch()

    expect(order).toEqual(['intent-recorded', 'panel-closed'])
    expect(routerPush).toHaveBeenCalledWith('/')
    expect(routerPush.mock.invocationCallOrder[0]).toBeLessThan(openDashboardTarget.mock.invocationCallOrder[0])
    expect(openDashboardTarget).toHaveBeenCalledWith({
      kind: 'document', doc_id: 'flowgate.default.0135.0017-T',
    })
    expect(useQaOpenIntent().intent.value?.docId).toBe('flowgate.default.0135.0017-T')
    expect(wrapper.find('.notif-panel').exists()).toBe(false)
  })

  it('renders title-less Q&A rows and distinguishes loading, feed error, degraded, and genuine empty states', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    store.qaItems = [{ doc_id: 'flowgate.default.0135.0018-TR', title: '   ', type_code: 'TR' }]
    store.qaTotal = 1
    await wrapper.findAll('.notif-section-tab')[2].trigger('click')
    const rowText = wrapper.find('.notif-qa-row').text()
    expect(rowText).toContain('flowgate.default.0135.0018-TR')
    expect(rowText).not.toContain('—')
    expect(rowText).not.toContain('null')

    store.qaItems = []
    store.loading = true
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.notif-loading').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('대기 중인 질의응답이 없습니다')

    store.loading = false
    store.error = 'offline'
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('알림을 불러오지 못했습니다')
    expect(wrapper.find('.notif-empty button').exists()).toBe(true)

    store.error = null
    store.degradedSections = ['open_questions']
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('질의응답 목록을 불러오지 못했습니다')
    expect(wrapper.find('.notif-empty button').exists()).toBe(true)

    store.degradedSections = []
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('대기 중인 질의응답이 없습니다')
  })

  it('assigns a fresh intent sequence when the same Q&A document is opened again', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    store.qaItems = [{ doc_id: 'same-doc', title: 'Same', type_code: 'T' }]
    store.qaTotal = 1
    await wrapper.findAll('.notif-section-tab')[2].trigger('click')
    await wrapper.find('.notif-qa-open').trigger('click')
    const first = useQaOpenIntent().intent.value!.sequence

    await wrapper.find('.notif-bell').trigger('click')
    await wrapper.findAll('.notif-section-tab')[2].trigger('click')
    await wrapper.find('.notif-qa-open').trigger('click')
    expect(useQaOpenIntent().intent.value!.sequence).toBeGreaterThan(first)
    expect(openDashboardTarget).toHaveBeenCalledTimes(2)
  })

  it('resets the default section and filter when the panel is reopened', async () => {
    const wrapper = await mountOpen([activity({ event_id: 1 })])
    await wrapper.findAll('.notif-tab')[2].trigger('click')
    await wrapper.findAll('.notif-section-tab')[1].trigger('click')
    await wrapper.find('.notif-bell').trigger('click')
    await wrapper.find('.notif-bell').trigger('click')

    expect(wrapper.findAll('.notif-section-tab')[0].attributes('aria-selected')).toBe('true')
    expect(wrapper.findAll('.notif-tab')[0].attributes('aria-selected')).toBe('true')
  })

  it('opens one AI row and shows the complete multiline message', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    store.aiItems = [{ run_id: 'run-1', doc_ref: 'flowgate.default.0135.0010-TR', doc_title: 'Report', doc_type_code: 'TR', succeeded: true, outcome: 'complete', docs_reached: 1, docs_target: 1, end_reason: 'completed', stop_code: null, provider_name: 'Codex', finished_at: '2026-07-02T00:05:00Z', last_message_excerpt: 'summary only' }]
    vi.mocked(getRequest).mockResolvedValueOnce({ data: { run_id: 'run-1', doc_ref: 'flowgate.default.0135.0010-TR', doc_title: 'Report', succeeded: true, outcome: 'complete', end_reason: 'completed', stop_code: null, stop_reason: null, provider_name: 'Codex', finished_at: '2026-07-02T00:05:00Z', last_message: 'FULL LINE 1\n  indented line 2\nFULL LINE 3' } } as any)
    await wrapper.findAll('.notif-section-tab')[1].trigger('click')
    expect(wrapper.findAll('.notif-ai-row')).toHaveLength(1)
    await wrapper.find('.notif-ai-detail-btn').trigger('click')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/run-1')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.find('.notif-dialog-message').text()).toBe('FULL LINE 1\n  indented line 2\nFULL LINE 3')
    expect(wrapper.find('.notif-panel').exists()).toBe(true)
  })

  it('renders success and failure rows without separators for omitted fragments', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    store.aiItems = [
      { run_id: 'ok', doc_ref: null, doc_title: null, doc_type_code: null, succeeded: true, outcome: 'complete', docs_reached: null, docs_target: null, end_reason: null, stop_code: null, provider_name: null, finished_at: '2026-07-02T00:05:00Z', last_message_excerpt: null },
      { run_id: 'bad', doc_ref: 'doc-bad', doc_title: null, doc_type_code: null, succeeded: false, outcome: 'failed', docs_reached: null, docs_target: null, end_reason: 'failed', stop_code: null, provider_name: null, finished_at: '2026-07-02T00:04:00Z', last_message_excerpt: null },
    ]
    await wrapper.findAll('.notif-section-tab')[1].trigger('click')

    const rows = wrapper.findAll('.notif-ai-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].classes()).toContain('notif-ai-row--success')
    expect(rows[1].classes()).toContain('notif-ai-row--failure')
    expect(rows[0].find('.notif-msg').exists()).toBe(false)
    expect(rows[0].text()).not.toContain('·')
  })

  it('distinguishes loading, feed error, degraded, and genuine empty AI states', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    await wrapper.findAll('.notif-section-tab')[1].trigger('click')

    store.loading = true
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.notif-loading').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('완료된 AI 호출이 없습니다')

    store.loading = false
    store.error = 'feed unavailable'
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('feed unavailable')
    expect(wrapper.find('.notif-empty button').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('완료된 AI 호출이 없습니다')

    store.error = null
    store.degradedSections = ['ai_runs']
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('AI 호출 목록을 불러오지 못했습니다')
    expect(wrapper.find('.notif-empty button').exists()).toBe(true)

    store.degradedSections = []
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('완료된 AI 호출이 없습니다')
  })

  it('shows detail loading, error, stop reason, and no-message fallback in order', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    store.aiItems = [{ run_id: 'run-1', doc_ref: null, doc_title: null, doc_type_code: null, succeeded: false, outcome: 'failed', docs_reached: null, docs_target: null, end_reason: 'failed', stop_code: null, provider_name: null, finished_at: '2026-07-02T00:05:00Z', last_message_excerpt: null }]
    await wrapper.findAll('.notif-section-tab')[1].trigger('click')

    let rejectDetail!: (error: unknown) => void
    vi.mocked(getRequest).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectDetail = reject }))
    await wrapper.find('.notif-ai-detail-btn').trigger('click')
    expect(wrapper.find('.notif-dialog-message').text()).toContain('불러오는 중')
    rejectDetail(new Error('offline'))
    await flushPromises()
    expect(wrapper.find('.notif-dialog-message').text()).toContain('불러오지 못했습니다')

    await wrapper.find('.notif-dialog-actions button').trigger('click')
    vi.mocked(getRequest).mockResolvedValueOnce({ data: { run_id: 'run-1', doc_ref: null, doc_title: null, succeeded: false, outcome: 'failed', end_reason: 'failed', stop_code: null, stop_reason: 'provider stopped', provider_name: null, finished_at: null, last_message: null } } as any)
    await wrapper.find('.notif-ai-detail-btn').trigger('click'); await flushPromises()
    expect(wrapper.find('.notif-dialog-message').text()).toBe('provider stopped')

    await wrapper.find('.notif-dialog-actions button').trigger('click')
    vi.mocked(getRequest).mockResolvedValueOnce({ data: { run_id: 'run-1', doc_ref: null, doc_title: null, succeeded: false, outcome: 'failed', end_reason: 'failed', stop_code: null, stop_reason: null, provider_name: null, finished_at: null, last_message: null } } as any)
    await wrapper.find('.notif-ai-detail-btn').trigger('click'); await flushPromises()
    expect(wrapper.find('.notif-dialog-message').text()).toContain('남은 메시지가 없습니다')
  })

  it('discards a late detail response after a faster row selection', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    store.aiItems = ['slow', 'fast'].map((run_id) => ({ run_id, doc_ref: null, doc_title: null, doc_type_code: null, succeeded: true, outcome: 'complete', docs_reached: 1, docs_target: 1, end_reason: 'completed', stop_code: null, provider_name: null, finished_at: '2026-07-02T00:05:00Z', last_message_excerpt: null }))
    await wrapper.findAll('.notif-section-tab')[1].trigger('click')
    let resolveSlow!: (value: unknown) => void
    vi.mocked(getRequest)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSlow = resolve }))
      .mockResolvedValueOnce({ data: { run_id: 'fast', doc_ref: null, doc_title: null, succeeded: true, outcome: 'complete', end_reason: 'completed', stop_code: null, stop_reason: null, provider_name: null, finished_at: null, last_message: 'FAST' } } as any)

    await wrapper.findAll('.notif-ai-detail-btn')[0].trigger('click')
    await wrapper.findAll('.notif-ai-detail-btn')[1].trigger('click')
    await flushPromises()
    resolveSlow({ data: { run_id: 'slow', last_message: 'STALE' } })
    await flushPromises()
    expect(wrapper.find('.notif-dialog-message').text()).toBe('FAST')
  })

  it('closes detail by the footer button and Escape while keeping the notification panel', async () => {
    const wrapper = await mountOpen([])
    const store = useNotificationsStore()
    store.aiItems = [{ run_id: 'run-1', doc_ref: null, doc_title: null, doc_type_code: null, succeeded: true, outcome: 'complete', docs_reached: 1, docs_target: 1, end_reason: 'completed', stop_code: null, provider_name: null, finished_at: '2026-07-02T00:05:00Z', last_message_excerpt: null }]
    vi.mocked(getRequest).mockResolvedValue({ data: { run_id: 'run-1', doc_ref: null, doc_title: null, succeeded: true, outcome: 'complete', end_reason: 'completed', stop_code: null, stop_reason: null, provider_name: null, finished_at: null, last_message: 'done' } } as any)
    await wrapper.findAll('.notif-section-tab')[1].trigger('click')

    await wrapper.find('.notif-ai-detail-btn').trigger('click'); await flushPromises()
    await wrapper.find('.notif-dialog-actions button').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.find('.notif-panel').exists()).toBe(true)

    await wrapper.find('.notif-ai-detail-btn').trigger('click'); await flushPromises()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.find('.notif-panel').exists()).toBe(true)
  })

  it('gives the terminal continuous-completed row its distinct emerald signal', async () => {
    const items = [
      activity({ event_id: 1, activity_type: 'continuous_work_completed', document: { doc_id: 'a', type_code: 'TR', title: 'A', review: null } }),
    ]
    const wrapper = await mountOpen(items)
    const dot = wrapper.find('.notif-dot')
    // Emerald from useActivityFormat (no review verdict → falls back to the activity colour).
    expect(dot.attributes('style')).toContain('rgb(5, 150, 105)')
    expect(wrapper.find('.notif-msg').text()).toBe('연속작업(무인)이 완료되었습니다.')
  })
})
