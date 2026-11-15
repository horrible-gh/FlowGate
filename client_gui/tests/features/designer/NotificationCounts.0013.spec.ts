import { describe, expect, it, vi } from 'vitest'
import { countLabel, notificationCounts } from '@/features/designer/top/notifications/notificationCounts'
import { qaOpenItems } from '@/features/document/api/qa'
import { getRequest } from '@shared/api'
vi.mock('@shared/api', () => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
describe('complete project question snapshot and actionable counts', () => {
  it.each([0, 51, 120])('uses all %s questions, not feed document totals or history counts', async count => {
    const items = Array.from({ length: count }, (_, seq) => ({ doc_id: `p.m.${seq}.0001-T`, seq, title: 'Stored question', type_code: 'T' }))
    vi.mocked(getRequest).mockResolvedValue({ data: { items, total: 3, badge_count: 999 } } as never)
    const rows = await qaOpenItems('p')
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/q', { project_id: 'p' })
    expect(rows).toEqual(items)
    const counts = notificationCounts([], rows)
    expect(counts).toEqual({ runs: 0, questions: count, bell: count })
    expect(countLabel(counts.bell)).toBe(count === 0 ? '' : count > 99 ? '99+' : String(count))
  })
  it('adds live/paused runs only and omits unknown question counts', () => {
    const runs = [{ key: 'running' }, { key: 'paused' }] as never
    expect(notificationCounts(runs, null)).toEqual({ runs: 2, questions: null, bell: 2 })
    expect(countLabel(null)).toBe('')
    expect(countLabel(99)).toBe('99')
    expect(countLabel(100)).toBe('99+')
  })
  it('propagates a snapshot failure instead of fabricating zero', async () => {
    vi.mocked(getRequest).mockRejectedValue(new Error('offline'))
    await expect(qaOpenItems('p')).rejects.toThrow('offline')
  })
})

import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { useNotificationBadge } from '@/features/designer/top/notifications/useNotificationBadge'
vi.mock('@/features/project/projectEvents', () => ({ subscribeProjectEvents: () => () => {} }))
it('renders the bell from complete snapshot + project runs, hides zero and excludes AI history', async () => {
  const project = { groups: [{ flows: [{ id: 'p.m.1' }] }] } as any
  let count = 120, fail = false
  vi.mocked(getRequest).mockImplementation(async (url: any) => {
    if (url.endsWith('active-all')) return { data: { runs: [], paused: [] } } as any
    if (fail) throw new Error('offline')
    return { data: { items: Array.from({ length: count }, (_, seq) => ({ doc_id: `p.m.${seq}.0001-T`, seq, title: 'Question', type_code: 'T' })), badge_count: 999, total: 5 } } as any
  })
  const Harness = defineComponent({ setup() { const badge = useNotificationBadge(() => 'p', () => project); return () => h('button', badge.value ? [h('span', badge.value)] : []) } })
  let w = mount(Harness); await flushPromises(); expect(w.get('span').text()).toBe('99+'); w.unmount()
  count = 51; w = mount(Harness); await flushPromises(); expect(w.get('span').text()).toBe('51'); w.unmount()
  count = 0; w = mount(Harness); await flushPromises(); expect(w.find('span').exists()).toBe(false); w.unmount()
  fail = true; w = mount(Harness); await flushPromises(); expect(w.find('span').exists()).toBe(false); w.unmount()
})
