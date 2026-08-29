import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useNotificationsStore, type NotificationFeed } from '@main/stores/notifications'
import type { DashboardActivity } from '@main/stores/dashboard'

// 🔔 notification center store (R0001 group 0045 / NR0003 A안 + D안).
const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({ getRequest, postRequest }))

function activity(eventId: number, occurredAt: string): DashboardActivity {
  return {
    event_id: eventId,
    activity_type: 'document_created',
    occurred_at: occurredAt,
    actor: null,
    group: null,
    document: { doc_id: `doc-${eventId}`, type_code: 'D', title: `Doc ${eventId}` },
    transition: null,
    navigation: { kind: 'document', doc_id: `doc-${eventId}` },
  }
}

function feed(unread: number, lastSeen: string | null, items: DashboardActivity[]): NotificationFeed {
  return {
    ok: true,
    project_id: 'flowgate',
    generated_at: '2026-06-12T01:00:00Z',
    last_seen_at: lastSeen,
    unread_count: unread,
    badge_count: unread,
    recent_activities: { limit: 50, total: items.length, has_more: false, items },
    ai_invoke_runs: { limit: 50, total: 0, has_more: false, items: [] },
    open_questions: { limit: 50, total: 0, has_more: false, items: [] },
    degraded_sections: [],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('notifications store', () => {
  it('loads the feed with unread count and watermark', async () => {
    const store = useNotificationsStore()
    getRequest.mockResolvedValueOnce({
      data: feed(2, '2026-06-12T00:00:00Z', [
        activity(2, '2026-06-12T00:02:00Z'),
        activity(1, '2026-06-12T00:01:00Z'),
      ]),
    })

    await store.fetchFeed('flowgate')

    expect(store.items).toHaveLength(2)
    expect(store.unreadCount).toBe(2)
    expect(store.lastSeenAt).toBe('2026-06-12T00:00:00Z')
    expect(store.loadedProjectId).toBe('flowgate')
  })

  it('atomically adopts Q&A items, total, badge, and preserves Q&A count after seen', async () => {
    const store = useNotificationsStore()
    const value = feed(2, null, [])
    value.badge_count = 4
    value.open_questions = {
      limit: 50, total: 2, has_more: false,
      items: [{ doc_id: 'doc-1', title: 'Document', type_code: 'T' }],
    }
    value.degraded_sections = ['open_questions']
    getRequest.mockResolvedValueOnce({ data: value })

    await store.fetchFeed('flowgate')
    expect(store.qaItems).toEqual(value.open_questions.items)
    expect(store.qaTotal).toBe(2)
    expect(store.unreadCount).toBe(4)
    expect(store.degradedSections).toEqual(['open_questions'])

    postRequest.mockResolvedValueOnce({ data: { ok: true, last_seen_at: '2026-06-12T02:00:00Z' } })
    await store.markSeen('flowgate')
    expect(store.unreadCount).toBe(2)

    store.reset()
    expect(store.qaItems).toEqual([])
    expect(store.qaTotal).toBe(0)
  })

  it('atomically adopts and resets AI items and degraded sections', async () => {
    const store = useNotificationsStore()
    const value = feed(0, null, [])
    value.ai_invoke_runs.items = [{
      run_id: 'run-1', doc_ref: 'doc-1', doc_title: 'Document', doc_type_code: 'T',
      succeeded: true, outcome: 'complete', docs_reached: 1, docs_target: 1,
      end_reason: 'completed', stop_code: null, provider_name: 'Codex',
      finished_at: '2026-06-12T00:05:00Z', last_message_excerpt: 'done',
    }]
    value.ai_invoke_runs.total = 1
    value.degraded_sections = ['ai_runs']
    getRequest.mockResolvedValueOnce({ data: value })

    await store.fetchFeed('flowgate')
    expect(store.aiItems).toHaveLength(1)
    expect(store.degradedSections).toEqual(['ai_runs'])

    store.reset()
    expect(store.items).toEqual([])
    expect(store.aiItems).toEqual([])
    expect(store.degradedSections).toEqual([])
  })

  it('marks read optimistically and advances the watermark', async () => {
    const store = useNotificationsStore()
    getRequest.mockResolvedValueOnce({
      data: feed(3, null, [activity(1, '2026-06-12T00:01:00Z')]),
    })
    await store.fetchFeed('flowgate')
    expect(store.unreadCount).toBe(3)

    postRequest.mockResolvedValueOnce({ data: { ok: true, last_seen_at: '2026-06-12T02:00:00Z' } })
    await store.markSeen('flowgate')

    expect(store.unreadCount).toBe(0)
    expect(store.lastSeenAt).toBe('2026-06-12T02:00:00Z')
  })

  it('treats every item as unread when no watermark exists', async () => {
    const store = useNotificationsStore()
    getRequest.mockResolvedValueOnce({ data: feed(1, null, [activity(1, '2026-06-12T00:01:00Z')]) })
    await store.fetchFeed('flowgate')
    expect(store.isUnread(activity(9, '2020-01-01T00:00:00Z'))).toBe(true)
  })

  it('highlights only items newer than the watermark', async () => {
    const store = useNotificationsStore()
    getRequest.mockResolvedValueOnce({ data: feed(0, '2026-06-12T00:01:30Z', []) })
    await store.fetchFeed('flowgate')
    expect(store.isUnread(activity(1, '2026-06-12T00:02:00Z'))).toBe(true)
    expect(store.isUnread(activity(2, '2026-06-12T00:01:00Z'))).toBe(false)
  })

  it('ignores a superseded fetch response (project switch race)', async () => {
    const store = useNotificationsStore()
    const staleFeed = feed(1, null, [activity(1, '2026-06-12T00:01:00Z')])
    staleFeed.open_questions = {
      limit: 50, total: 9, has_more: false,
      items: [{ doc_id: 'stale-doc', title: 'Stale', type_code: 'T' }],
    }
    const winningFeed = feed(5, null, [activity(7, '2026-06-12T00:09:00Z')])
    winningFeed.open_questions = {
      limit: 50, total: 2, has_more: false,
      items: [{ doc_id: 'fresh-doc', title: 'Fresh', type_code: 'P' }],
    }
    let resolveFirst: (v: unknown) => void = () => {}
    getRequest
      .mockImplementationOnce(() => new Promise((r) => { resolveFirst = r }))
      .mockResolvedValueOnce({ data: winningFeed })

    const first = store.fetchFeed('alpha')
    const second = store.fetchFeed('beta')
    resolveFirst({ data: staleFeed })
    await Promise.all([first, second])

    // The later (beta) response wins; the stale alpha response — including its Q&A page — is discarded.
    expect(store.loadedProjectId).toBe('beta')
    expect(store.unreadCount).toBe(5)
    expect(store.aiItems).toEqual([])
    expect(store.degradedSections).toEqual([])
    expect(store.qaTotal).toBe(2)
    expect(store.qaItems).toEqual([{ doc_id: 'fresh-doc', title: 'Fresh', type_code: 'P' }])
  })
})
