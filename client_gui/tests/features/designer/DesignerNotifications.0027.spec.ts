import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerNotifications from '@/features/designer/top/notifications/DesignerNotifications.vue'
import NotificationAiDetailHost from '@/features/designer/top/notifications/NotificationAiDetailHost.vue'
import { qaOpenItems } from '@/features/document/api/qa'
import { normalizeActiveRuns } from '@/features/designer/top/notifications/notificationApi'
import type { ExplorerProject } from '@/features/designer/projectExplorer/projectExplorerTree'
const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), subscribe: vi.fn(), unsubscribe: vi.fn(), open: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest: mocks.get, postRequest: mocks.post }))
vi.mock('@/features/project/projectEvents', () => ({ subscribeProjectEvents: mocks.subscribe }))
vi.mock('@/features/documentWindow/documentWindows', () => ({ openDocumentWindow: mocks.open }))
vi.mock('@/features/document/api/qa', async (original) => ({
  ...await original<typeof import('@/features/document/api/qa')>(),
  qaOpenItems: vi.fn(async () => [{ doc_id: 'p.m.1.0001-T', seq: 1, title: 'Instruction', type_code: 'T' }]),
}))
// the merge items read has its own spec (notificationMerges.v02test0042); here it answers none
// without touching the transport the read counts below are about.
vi.mock('@/features/document/api/cardMerge', async (original) => ({
  ...await original<typeof import('@/features/document/api/cardMerge')>(),
  cardMergeItems: vi.fn(async () => []),
}))
vi.mock('@/features/designer/inspector/useInspectorResource', () => ({ useInspectorResource: vi.fn() }))
vi.mock('@/features/designer/inspector/features/qna/useQna', () => ({ useQna: vi.fn() }))
registerAppMessages(i18n)
const project: ExplorerProject = { id: 'project:p', name: 'Alpha', groups: [{ id: 'module:p:m', projectId: 'p', name: 'M', moduleName: 'm', flows: [
  { id: 'p.m.1', groupId: 'module:p:m', name: 'First Flow', number: '1' },
  { id: 'p.m.2', groupId: 'module:p:m', name: 'Paused Flow', number: '2' },
] }] }
const run = { run_id: 'run/1', group_id: 'p.m.1', doc_ref: 'p.m.1.0001-T', status: 'running', mode: 'continuous', provider: { name: 'Provider' }, docs_target: 4, docs_reached_so_far: 2 }
const active = { runs: [run, { ...run, run_id: 'foreign', group_id: 'other.m.1' }, { ...run, run_id: 'finished', status: 'finished' }], paused: [{ group_id: 'p.m.2', doc_ref: 'p.m.2.0001-T', mode: 'continuous', docs_target: 3, docs_reached: 1, stop_reason: 'Waiting for an answer' }] }
const feed = { ai_invoke_runs: { items: [{ run_id: 'done', doc_ref: 'p.m.1.0002-TR', doc_title: 'Report', succeeded: true, last_message_excerpt: 'Saved' }] }, open_questions: { items: [{ doc_id: 'p.m.1.0001-T', title: 'Instruction', type_code: 'T' }] } }
let wrapper: VueWrapper<any> | null = null
let anchor: HTMLButtonElement
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(qaOpenItems).mockResolvedValue([{ doc_id: 'p.m.1.0001-T', seq: 1, title: 'Instruction', type_code: 'T' }])
  i18n.global.locale.value = 'ko'
  anchor = document.createElement('button'); document.body.append(anchor)
  mocks.get.mockImplementation(async (url: string) => ({ data: url.endsWith('active-all') ? active : feed }))
  mocks.post.mockResolvedValue({ data: { ok: true } })
  mocks.subscribe.mockReturnValue(mocks.unsubscribe)
})
afterEach(() => { wrapper?.unmount(); wrapper = null; anchor.remove(); vi.useRealTimers(); i18n.global.locale.value = 'ko' })
async function open(props = {}) {
  wrapper = mount(DesignerNotifications, { props: { projectId: 'p', project, anchor, ...props }, attachTo: document.body, global: { plugins: [i18n], stubs: { NotificationQuestionDocument: { props: ['rows'], template: '<li data-test="notification-question"><span>T</span>{{ rows[0].title }}</li>' } } } })
  await flushPromises()
  return wrapper
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }

describe('Designer notifications', () => {
  it('shows all 120 snapshot rows and exact actionable tab counts independently of a 50-document feed', async () => {
    vi.mocked(qaOpenItems).mockResolvedValue(Array.from({ length: 120 }, (_, seq) => ({ doc_id: `p.m.${seq}.0001-T`, seq, title: 'Question', type_code: 'T' })))
    const w = await open()
    expect(w.get('#notification-tab-runs').text()).toBe('실행 2')
    expect(w.get('#notification-tab-questions').text()).toBe('질의응답 99+')
    expect(w.get('#notification-tab-ai').text()).toBe('AI')
    await w.get('#notification-tab-questions').trigger('click')
    expect(w.findAll('[data-test="notification-question"]')).toHaveLength(120)
  })
  it('hides unknown question counts and shows retry on snapshot failure', async () => {
    vi.mocked(qaOpenItems).mockRejectedValue(new Error('offline'))
    const w = await open()
    expect(w.get('#notification-tab-questions').text()).toBe('질의응답')
    await w.get('#notification-tab-questions').trigger('click')
    expect(w.find('[role="alert"]').exists()).toBe(true)
    vi.mocked(qaOpenItems).mockResolvedValue([])
    await w.findAll('.notification-toolbar button')[0]!.trigger('click'); await flushPromises()
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(w.get('#notification-tab-questions').text()).toBe('질의응답')
  })
  it('announces loading only initially and preserves rows during polling and event refresh', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    const initial = deferred<unknown>()
    mocks.get.mockReturnValue(initial.promise)
    const w = await open()
    expect(w.get('[role="status"]').text()).toContain('불러오는 중')
    await w.get('#notification-tab-ai').trigger('click')
    expect(w.text()).not.toContain('AI 알림이 없습니다.')
    initial.resolve({ data: feed })
    await flushPromises()
    expect(w.find('[role="status"]').exists()).toBe(false)
    expect(w.findAll('[data-test="notification-ai"]')).toHaveLength(1)
    for (const signal of ['poll', 'event']) {
      const pending = deferred<unknown>()
      mocks.get.mockReturnValue(pending.promise)
      if (signal === 'poll') await vi.advanceTimersByTimeAsync(5000)
      else mocks.subscribe.mock.calls[0]![1].event()
      await flushPromises()
      expect(w.find('[role="status"]').exists()).toBe(false)
      expect(w.get('[role="tabpanel"]').attributes('aria-busy')).toBe('true')
      expect(w.findAll('[data-test="notification-ai"]')).toHaveLength(1)
      pending.resolve({ data: feed })
      await flushPromises()
    }
  })
  it('only exposes aria-controls when the corresponding panel exists', async () => {
    const w = await open()
    for (const name of ['runs', 'ai', 'questions']) {
      await w.get('#notification-tab-' + name).trigger('click')
      for (const tab of w.findAll('[role="tab"]')) {
        const id = tab.attributes('aria-controls')
        if (tab.attributes('aria-selected') === 'true') {
          expect(id).toBe('notification-panel-' + name)
          expect(w.find('#' + id).exists()).toBe(true)
        } else expect(id).toBeUndefined()
      }
    }
  })
  it.each(['ai'] as const)('shows truncation for %s and clears it on a complete response', async (tab) => {
    const section = tab === 'ai' ? 'ai_invoke_runs' : 'open_questions'
    const items = Array.from({ length: 50 }, (_, n) => tab === 'ai'
      ? { run_id: 'done-' + n, doc_title: 'Report' }
      : { doc_id: 'doc-' + n, title: 'Instruction', type_code: 'T' })
    mocks.get.mockImplementation(async (url: string) => ({ data: url.endsWith('active-all') ? active : {
      ...feed, [section]: { items, total: 51, limit: 50, has_more: true },
    } }))
    const w = await open()
    await w.get('#notification-tab-' + tab).trigger('click')
    expect(w.get('[data-test="notification-truncated"]').text()).toContain('전체 51개 중 50개')
    mocks.get.mockImplementation(async (url: string) => ({ data: url.endsWith('active-all') ? active : feed }))
    mocks.subscribe.mock.calls[0]![1].event()
    await flushPromises()
    expect(w.find('[data-test="notification-truncated"]').exists()).toBe(false)
  })
  it.each(['ko', 'en', 'ja'] as const)('localizes timestamps, document types and unknown run values in %s', async (locale) => {
    i18n.global.locale.value = locale
    const finished = '2026-11-15T12:34:00Z'
    mocks.get.mockImplementation(async (url: string) => ({ data: url.endsWith('active-all')
      ? { runs: [{ ...run, status: 'future-phase', mode: 'future-mode' }], paused: [] }
      : { ...feed, ai_invoke_runs: { items: [{ ...feed.ai_invoke_runs.items[0], finished_at: finished }] } } }))
    const w = await open()
    expect(w.text()).not.toContain('future-phase')
    expect(w.text()).not.toContain('future-mode')
    expect(w.text()).toContain(i18n.global.t('gui.designer.top.notifications.phases.unknown'))
    expect(w.text()).toContain(i18n.global.t('gui.designer.top.notifications.modes.unknown'))
    await w.get('#notification-tab-ai').trigger('click')
    expect(w.get('time').attributes('datetime')).toBe(finished)
    expect(w.get('time').attributes('title')).toBe(finished)
    await w.get('#notification-tab-questions').trigger('click')
    expect(w.get('[data-test="notification-question"] span').text()).toBe('T')
  })


  it('distinguishes an unknown project tree from a successful empty list and recovers when loaded', async () => {
    const w = await open({ project: null })
    expect(w.text()).toContain('Flow 목록을 확인할 수 없습니다')
    expect(w.text()).not.toContain('진행 중이거나 대기 중인 실행이 없습니다.')
    expect(w.findAll('[data-test="notification-run"]')).toHaveLength(0)
    await w.setProps({ project })
    expect(w.findAll('[data-test="notification-run"]')).toHaveLength(2)
    expect(w.text()).not.toContain('Flow 목록을 확인할 수 없습니다')
  })
  it.each(['event', 'stop'] as const)('replays a %s signal received during an active read exactly once', async (signal) => {
    const w = await open()
    const pending = deferred<unknown>()
    mocks.get.mockImplementation((url: string) => url.endsWith('active-all') ? pending.promise : Promise.resolve({ data: feed }))
    const reload = mocks.subscribe.mock.calls[0]![1].event
    reload()
    await flushPromises()
    const count = mocks.get.mock.calls.length
    if (signal === 'stop') {
      await w.findAll('[data-test="notification-run"]')[0]!.findAll('button')[1]!.trigger('click')
      await flushPromises()
      expect(mocks.post).toHaveBeenCalledTimes(1)
    } else {
      reload(); reload()
    }
    expect(mocks.get).toHaveBeenCalledTimes(count)
    mocks.get.mockImplementation(async (url: string) => ({ data: url.endsWith('active-all') ? { runs: [], paused: [] } : feed }))
    pending.resolve({ data: active })
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledTimes(count + 2)
    expect(w.findAll('[data-test="notification-run"]')).toHaveLength(0)
    expect(w.text()).toContain('진행 중이거나 대기 중인 실행이 없습니다.')
  })
  it('does not replay a queued refresh after unmount', async () => {
    const pending = deferred<unknown>()
    mocks.get.mockReturnValue(pending.promise)
    const w = await open()
    mocks.subscribe.mock.calls[0]![1].event()
    w.unmount(); wrapper = null
    pending.resolve({ data: active }); await flushPromises()
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })
  it.each(['cancelling', 'pause_requested'] as const)('blocks stop for %s and open without a document ID', async (status) => {
    mocks.get.mockImplementation(async (url: string) => ({ data: url.endsWith('active-all') ? { runs: [{ ...run, status, doc_ref: null }], paused: [] } : feed }))
    const w = await open()
    const buttons = w.get('[data-test="notification-run"]').findAll('button')
    for (const button of buttons) {
      expect((button.element as HTMLButtonElement).disabled).toBe(true)
      await button.trigger('click')
    }
    expect(mocks.post).not.toHaveBeenCalled()
    expect(mocks.open).not.toHaveBeenCalled()
  })

  it('normalizes live and paused contracts without restoring finished cards; keeps chain progress', () => {
    const rows = normalizeActiveRuns({ runs: [{ ...run, chain_id: 'chain', chain_docs_target: 9, chain_docs_reached: 5 }], paused: [...active.paused, { group_id: 'p.m.1' }] })
    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatchObject({ target: 9, reached: 5, phase: 'running', provider: 'Provider' })
    expect(rows[1]).toMatchObject({ phase: 'paused', runId: null, reached: 1 })
    expect(normalizeActiveRuns({ runs: [{ ...run, status: 'finished' }, {}] })).toEqual([])
  })
  it('shows exactly three tabs, current-project runs, progress, paused state and working document links', async () => {
    const w = await open()
    expect(w.findAll('[role="tab"]').map((tab) => tab.text())).toEqual(['실행 2', 'AI', '질의응답 1'])
    expect(w.text()).toContain('현재 프로젝트 Alpha')
    expect(w.findAll('[data-test="notification-run"]')).toHaveLength(2)
    const rows = w.findAll('[data-test="notification-run"]')
    expect(rows[0]!.text()).toContain('2 / 4')
    expect(rows[0]!.text()).toContain('Provider')
    expect(rows[1]!.text()).toContain('일시정지')
    expect(rows[1]!.findAll('button')[1]!.attributes('disabled')).toBeDefined()
    await rows[1]!.findAll('button')[1]!.trigger('click')
    expect(mocks.post).not.toHaveBeenCalled()
    await rows[0]!.findAll('button')[0]!.trigger('click')
    expect(mocks.open).toHaveBeenCalledWith('p.m.1.0001-T')
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/projects/p/notifications', { limit: 50 })
  })
  it('sends the exact cancel request once while pending and waits for server status', async () => {
    const pending = deferred<unknown>(); mocks.post.mockReturnValue(pending.promise)
    const w = await open()
    const stop = w.findAll('[data-test="notification-run"]')[0]!.findAll('button')[1]!
    await stop.trigger('click'); await stop.trigger('click')
    expect(mocks.post).toHaveBeenCalledTimes(1)
    expect(mocks.post).toHaveBeenCalledWith('/api/v1/ai-invoke/run%2F1/cancel', {})
    expect(w.text()).toContain('실행 중')
    pending.resolve({ data: { ok: true } }); await flushPromises()
    expect(w.text()).toContain('실행 중')
    expect(stop.attributes('disabled')).toBeUndefined()
  })
  it('shows a cancel failure without removing the run', async () => {
    mocks.post.mockRejectedValue(new Error('403'))
    const w = await open(); await w.findAll('[data-test="notification-run"]')[0]!.findAll('button')[1]!.trigger('click'); await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('중지 요청에 실패')
    expect(w.findAll('[data-test="notification-run"]')).toHaveLength(2)
  })
  it('opens AI detail and preserves the basic question document link', async () => {
    const host = mount(NotificationAiDetailHost, { props: { projectId: 'p' }, global: { plugins: [i18n] } })
    const w = await open(); await w.get('#notification-tab-ai').trigger('click')
    expect(w.get('[data-test="notification-ai"]').text()).toContain('Saved')
    await w.get('[data-test="notification-ai"] button').trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledWith('/api/v1/ai-invoke/done')
    const detail = document.querySelector('[data-test="notification-ai-detail"]')!
    ;(detail.querySelector('button') as HTMLButtonElement).click()
    expect(mocks.open).toHaveBeenLastCalledWith('p.m.1.0002-TR')
    await w.get('#notification-tab-questions').trigger('click')
    expect(w.get('[data-test="notification-question"]').text()).toContain('Instruction')
    host.unmount()
  })
  it('0002-CH: pressing the AI detail window keeps the popover and the window; an outside press closes only the popover', async () => {
    const host = mount(NotificationAiDetailHost, { props: { projectId: 'p' }, global: { plugins: [i18n] } })
    const w = await open(); await w.get('#notification-tab-ai').trigger('click')
    await w.get('[data-test="notification-ai"] .ai-detail').trigger('click'); await flushPromises()
    const shell = () => document.querySelector('.ui-modeless-panel')
    for (const part of ['.ui-modeless-titlebar', '.ui-modeless-resize-handle', '[data-test="notification-ai-detail"]']) {
      shell()!.querySelector(part)!.dispatchEvent(new Event('pointerdown', { bubbles: true }))
      expect(w.emitted('close')).toBeUndefined()
    }
    // Escape in the window closes that window only (ModelessShell's own Escape), never the popover.
    shell()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flushPromises()
    expect(w.emitted('close')).toBeUndefined()
    expect(shell()).toBeNull()
    await w.get('[data-test="notification-ai"] .ai-detail').trigger('click'); await flushPromises()
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    expect(w.emitted('close')).toHaveLength(1)
    w.unmount(); wrapper = null; await flushPromises()
    expect(document.querySelector('[data-test="notification-ai-detail"]')).not.toBeNull()
    host.unmount()
    expect(document.querySelector('[data-test="notification-ai-detail"]')).toBeNull()
  })
  it('keeps the successful feed usable when the run endpoint fails', async () => {
    mocks.get.mockImplementation(async (url: string) => { if (url.endsWith('active-all')) throw new Error('403'); return { data: feed } })
    const w = await open(); expect(w.get('[role="alert"]').exists()).toBe(true)
    await w.get('#notification-tab-ai').trigger('click')
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(w.find('[data-test="notification-ai"]').exists()).toBe(true)
  })
  it('uses the complete Q snapshot even when the question feed is degraded', async () => {
    mocks.get.mockImplementation(async (url: string) => ({ data: url.endsWith('active-all') ? active : { ...feed, degraded_sections: ['open_questions'] } }))
    const w = await open(); await w.get('#notification-tab-questions').trigger('click')
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(w.find('[data-test="notification-question"]').exists()).toBe(true)
    await w.get('#notification-tab-ai').trigger('click')
    expect(w.find('[data-test="notification-ai"]').exists()).toBe(true)
  })
  it('discards the previous project response and stops subscriptions on unmount', async () => {
    const old = deferred<unknown>()
    mocks.get.mockImplementation((url: string) => url.includes('/projects/p/') ? old.promise : Promise.resolve({ data: url.endsWith('active-all') ? active : { ai_invoke_runs: { items: [] }, open_questions: { items: [] } } }))
    const w = await open()
    await w.setProps({ projectId: 'other', project: { ...project, id: 'project:other', name: 'Other', groups: [] } }); await flushPromises()
    old.resolve({ data: feed }); await flushPromises()
    expect(w.findAll('[data-test="notification-run"]')).toHaveLength(0)
    await w.get('#notification-tab-ai').trigger('click')
    expect(w.find('[data-test="notification-ai"]').exists()).toBe(false)
    expect(mocks.unsubscribe).toHaveBeenCalledTimes(1)
    w.unmount(); wrapper = null
    expect(mocks.unsubscribe).toHaveBeenCalledTimes(2)
  })
  it('requeries on project events and polls only while open', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    const w = await open(); const before = mocks.get.mock.calls.length
    mocks.subscribe.mock.calls[0]![1].event({ type: 'group_view_refresh' }); await flushPromises()
    expect(mocks.get.mock.calls.length).toBe(before + 2)
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(mocks.get.mock.calls.length).toBe(before + 4)
    w.unmount(); wrapper = null
    await vi.advanceTimersByTimeAsync(10000)
    expect(mocks.get.mock.calls.length).toBe(before + 4)
  })
  it('shows the empty state after a successful empty response', async () => {
    vi.mocked(qaOpenItems).mockResolvedValue([])
    mocks.get.mockImplementation(async (url: string) => ({ data: url.endsWith('active-all') ? { runs: [], paused: [] } : { ai_invoke_runs: { items: [] }, open_questions: { items: [] } } }))
    const w = await open()
    expect(w.text()).toContain('진행 중이거나 대기 중인 실행이 없습니다.')
    await w.get('#notification-tab-ai').trigger('click')
    expect(w.text()).toContain('AI 알림이 없습니다.')
    await w.get('#notification-tab-questions').trigger('click')
    expect(w.text()).toContain('답변 대기 질문이 없습니다.')
  })
  it('does not request global runs with no project', async () => {
    const w = await open({ projectId: null, project: null })
    expect(mocks.get).not.toHaveBeenCalled()
    expect(w.text()).toContain('프로젝트를 열어')
  })
  it('supports arrow tab navigation, Escape focus return, outside dismissal and anchor toggle', async () => {
    const w = await open()
    expect(document.activeElement?.id).toBe('notification-tab-runs')
    await w.get('#notification-tab-runs').trigger('keydown', { key: 'ArrowRight' })
    expect(w.get('#notification-tab-ai').attributes('aria-selected')).toBe('true')
    expect(document.activeElement?.id).toBe('notification-tab-ai')
    anchor.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    expect(w.emitted('close')).toBeUndefined()
    await w.get('#notification-tab-ai').trigger('keydown', { key: 'Escape' })
    expect(w.emitted('close')).toHaveLength(1)
    expect(document.activeElement).toBe(anchor)
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    expect(w.emitted('close')).toHaveLength(2)
  })
  it.each([['ko', '실행', '질의응답'], ['en', 'Runs', 'Q&A'], ['ja', '実行', '質疑応答']] as const)('provides %s tab labels', async (locale, runs, questions) => {
    i18n.global.locale.value = locale
    const w = await open()
    expect(w.findAll('[role="tab"]').map((node) => node.text())).toEqual([runs + ' 2', 'AI', questions + ' 1'])
  })
})
