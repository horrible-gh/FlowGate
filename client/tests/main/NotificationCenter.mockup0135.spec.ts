import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NotificationCenter from '@main/components/NotificationCenter.vue'
import { useNotificationsStore } from '@main/stores/notifications'
import { useProjectStore } from '@main/stores/project'
import type { DashboardActivity } from '@main/stores/dashboard'

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

vi.mock('@main/composables/useDashboardNavigation', () => ({
  useDashboardNavigation: () => ({ openDashboardTarget: vi.fn() }),
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
  store.aiRuns = [{ run_id: 'run-1', success: true, outcome: 'complete', doc_ref: 'doc-ai', doc_title: 'AI doc', finished_at: '2026-07-02T00:06:00Z', result_line: 'done', provider_name: 'codex', stop_code: null, stop_reason: null }]
  store.aiRunsTotal = 1
  store.openQuestions = [{ doc_id: 'doc-q', doc_title: 'Question doc', type_code: 'Q' }]
  store.openQuestionsTotal = 1
  store.badgeCount = items.length + 1
  store.lastSeenAt = null
  await wrapper.find('.notif-bell').trigger('click')
  await flushPromises()
  return wrapper
}

describe('NotificationCenter 시안 3 mockup (group 0135)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'ko'
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

  it('renders and switches all three notification sections with section totals', async () => {
    const wrapper = await mountOpen([activity({ event_id: 1 })])

    const sectionTabs = wrapper.findAll('.notif-section-tab')
    expect(sectionTabs.map((tab) => tab.find('.notif-section-n').text())).toEqual(['1', '1', '1'])
    expect(wrapper.find('.notif-badge').text()).toBe('2')

    await sectionTabs[1].trigger('click')
    expect(wrapper.text()).toContain('doc-ai')
    expect(wrapper.text()).toContain('AI doc')
    expect(wrapper.find('.notif-tabs:not(.notif-section-tabs)').exists()).toBe(false)

    await wrapper.findAll('.notif-section-tab')[2].trigger('click')
    expect(wrapper.text()).toContain('doc-q')
    expect(wrapper.text()).toContain('Question doc')
  })

  it('renders a section-local degraded state without hiding the other sections', async () => {
    const wrapper = await mountOpen([activity({ event_id: 1 })])
    const store = useNotificationsStore()
    store.degradedSections = ['ai_runs']
    await wrapper.findAll('.notif-section-tab')[1].trigger('click')
    expect(wrapper.find('.notif-empty').text()).toContain('불러오지 못했습니다')

    await wrapper.findAll('.notif-section-tab')[2].trigger('click')
    expect(wrapper.text()).toContain('doc-q')
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
