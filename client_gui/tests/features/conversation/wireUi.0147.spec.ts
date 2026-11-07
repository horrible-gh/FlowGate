import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createHash } from 'node:crypto'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { createHttpConversationSource } from '@/features/conversation/conversationSource'
import type { ConversationSession } from '@/features/conversation/conversationSession'

const http = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn() }))
vi.mock('@shared/api', async (original) => ({ ...(await original<typeof import('@shared/api')>()), ...http }))
registerAppMessages(i18n)
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
let wrapper: VueWrapper | null = null
let getFailure = false
let raw: ReturnType<typeof fixture>

function fixture() {
  return {
    turns: [{ seq: 1, speaker: 'user', body: 'isolated input', source_run_id: null },
      { seq: 2, speaker: 'ai', body: 'isolated partial reply', source_run_id: 'partial' }],
    runs: [
      { run_id: 'partial', status: 'finished', doc_ref: A, group_id: 'p.m.0001', end_reason: 'timeout', outcome: 'none', exit_code: null },
      { run_id: 'empty', status: 'finished', doc_ref: A, group_id: 'p.m.0001', end_reason: 'all_providers_failed', outcome: 'none', exit_code: null,
        fallback_history: [{ detail: 'isolated provider failure', remote_final: { cause: 'workdir_missing', message: 'isolated path missing', workdir: '/isolated/job' } }] },
      { run_id: 'stop', status: 'finished', doc_ref: A, group_id: 'p.m.0001', end_reason: 'cancelled', outcome: 'none', exit_code: null },
    ],
    commands: [{ request_id: 'fixture-command', ai_run_id: 'partial', command: 'isolated fixture command', status: 'succeeded', exit_code: 0,
      anchor_seq: 2, anchor_position: 'before', anchor_state: 'reply' }],
  }
}
function locale(value: 'ko' | 'en' | 'ja') { (i18n.global.locale as unknown as { value: string }).value = value }
function installHttp() {
  http.getRequest.mockImplementation(async (url: string, query?: Record<string, number>) => {
    if (url === '/api/v1/documents') return { data: [{ doc_id: A, title: 'isolated chat' }, { doc_id: B, title: 'other chat' }] }
    if (url.includes('/conversation/turns')) {
      if (getFailure && query?.after_seq != null) throw new Error('isolated read unavailable')
      const turns = url.includes(A) ? raw.turns.filter((t) => query?.after_seq == null || t.seq > query.after_seq) : []
      return { data: { turns, head_seq: url.includes(A) ? 2 : 0, next_after_seq: null, prev_before_seq: null } }
    }
    if (url.includes('/me/chat-settings')) return { data: { settings: {}, source_access_scope: { project_id: 'p', group_id: 'p.m.0001' } } }
    if (url === '/api/v1/ai-invoke/providers') return { data: { providers: [{ id: 'fixture', name: 'isolated provider' }] } }
    if (url === '/api/v1/ai-invoke/active') {
      const active = raw.runs.find((run) => run.status === 'running')
      return { data: active ? { active: true, run_id: active.run_id, doc_ref: active.doc_ref, status: 'running' } : { active: false } }
    }
    if (url === '/api/v1/ai-invoke/runs') return { data: { items: raw.runs.map(({ run_id, doc_ref }) => ({ run_id, doc_ref })), total: raw.runs.length, has_more: false } }
    if (url.startsWith('/api/v1/ai-invoke/')) {
      const run = raw.runs.find((r) => url.endsWith('/' + r.run_id))
      return { data: run?.status === 'running' ? { run_id: run.run_id, status: 'running', action_scope: 'chat', group_id: run.group_id } : run }
    }
    if (url.startsWith('/api/v1/chat-activity/')) return { data: { commands: url.endsWith(A) ? raw.commands : [], changes: [] } }
    throw new Error(`Unexpected isolated GET: ${url}`)
  })
  http.postRequest.mockImplementation(async (url: string) => {
    if (url.endsWith('/conversation/read')) return { data: {} }
    throw new Error(`Unexpected mutating request: ${url}`)
  })
}
async function open() {
  wrapper = mount(ConversationPanel, { props: { projectId: 'p', groupId: 'p.m.0001', source: createHttpConversationSource() },
    attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises(); await flushPromises()
  return (wrapper.vm as unknown as { session: ConversationSession }).session
}
beforeEach(() => { raw = fixture(); getFailure = false; localStorage.clear(); locale('ko'); for (const fn of Object.values(http)) fn.mockReset(); installHttp() })
afterEach(() => { wrapper?.unmount(); wrapper = null; locale('ko'); vi.useRealTimers() })
const statuses = () => wrapper!.findAll('[data-test="tl-status"]').map((w) => w.text())
const digest = () => createHash('sha256').update(JSON.stringify(raw)).digest('hex')

describe('0147 isolated server wire -> real adapter -> session -> ConversationPanel/TimelineActivity', () => {
  it.each(['ko', 'en', 'ja'] as const)('renders body+timeout and all restored reasons in %s after F5', async (lang) => {
    locale(lang)
    const before = digest()
    const session = await open()
    expect(wrapper!.text()).toContain('isolated partial reply')
    const failed = String(i18n.global.t('gui.conversation.status.run_failed'))
    expect(statuses().some((text) => text.includes(failed) && text.includes('timeout'))).toBe(true)
    expect(statuses().some((text) => text.includes(String(i18n.global.t('gui.conversation.status.run_cancelled'))))).toBe(true)
    expect(statuses().some((text) => text.includes('/isolated/job') && text.includes('isolated path missing'))).toBe(true)
    expect(statuses().filter((text) => text.includes(String(i18n.global.t('gui.conversation.status.run_no_reply'))))).toHaveLength(1)
    expect(wrapper!.text()).not.toContain('gui.conversation.status.')
    expect(wrapper!.findAll('[data-test="tl-command"]')).toHaveLength(1)
    expect(session.state.runId).toBeNull()
    const first = statuses()
    wrapper!.unmount(); wrapper = null
    const fresh = await open() // a new session, no local statuses or active run survive
    expect(statuses()).toEqual(first)
    await fresh.selectChat(B); await fresh.selectChat(A); await flushPromises()
    expect(statuses()).toEqual(first)
    await fresh.catchUp()
    expect(statuses()).toEqual(first)
    expect(digest()).toBe(before) // raw seq/body/source_run_id + command id/anchor/result unchanged
    expect(http.postRequest.mock.calls.every((args: string[]) => args[0].endsWith('/conversation/read'))).toBe(true)
    expect(http.patchRequest).not.toHaveBeenCalled()
    // DOM evidence is emitted by the available self-check, without a real server connection.
    console.log('0147_DOM', lang, JSON.stringify({ statuses: first, messages: wrapper!.findAll('[data-test="tl-message"]').map((m) => ({ seq: m.attributes('data-seq'), speaker: m.attributes('data-speaker'), text: m.find('.tl-text').text() })), storedHash: before }))
  })

  it('F5 during a real-shaped running wire preserves a leading reply and follows completion', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    raw.runs = [{ ...raw.runs[0], status: 'running', end_reason: '', outcome: '', exit_code: null }]
    raw.commands = []
    const turnHash = () => createHash('sha256').update(JSON.stringify(raw.turns)).digest('hex')
    const before = turnHash()
    await open()
    expect(wrapper!.text()).toContain('isolated partial reply')
    wrapper!.unmount(); wrapper = null
    const fresh = await open()
    await vi.advanceTimersByTimeAsync(2500); await flushPromises()
    expect(fresh.state.runId).toBe('partial')
    expect(statuses()).toEqual([])
    raw.runs[0] = { ...raw.runs[0], status: 'finished', end_reason: 'exited', outcome: 'complete', exit_code: 0 }
    await vi.advanceTimersByTimeAsync(2500); await flushPromises()
    expect(fresh.state.runId).toBeNull()
    expect(statuses()).toEqual([])
    expect(wrapper!.text()).toContain('isolated partial reply')
    expect(turnHash()).toBe(before)
    expect(http.postRequest.mock.calls.every((args: string[]) => args[0].endsWith('/conversation/read'))).toBe(true)
    console.log('0147_live_wire_DOM', JSON.stringify({ statuses: statuses(), turnHash: before, running: fresh.running.value }))
  })

  it('normal stored exited/complete/0 reply has no false warning after a fresh mount', async () => {
    raw.runs = [{ ...raw.runs[0], run_id: 'normal', end_reason: 'exited', outcome: 'complete', exit_code: 0 }]
    raw.turns[1].source_run_id = 'normal'
    raw.commands = []
    await open()
    expect(statuses()).toEqual([])
    expect(wrapper!.text()).toContain('isolated partial reply')
    wrapper!.unmount(); wrapper = null
    await open()
    expect(statuses()).toEqual([])
  })

  it('renders read-unconfirmed and actual failure together, clears only uncertainty after recovery', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    raw.runs = [raw.runs[0]]; raw.commands = []
    getFailure = true
    const session = await open()
    await vi.advanceTimersByTimeAsync(5000); await flushPromises()
    expect(statuses().some((s) => s.includes(String(i18n.global.t('gui.conversation.status.run_query_unconfirmed'))))).toBe(true)
    expect(statuses().some((s) => s.includes('timeout'))).toBe(true)
    expect(statuses().some((s) => s.includes(String(i18n.global.t('gui.conversation.status.run_no_reply'))))).toBe(false)
    expect(session.running.value).toBe(false)
    getFailure = false
    await session.catchUp(); await flushPromises()
    expect(statuses()).toHaveLength(1)
    expect(statuses()[0]).toContain('timeout')
    expect(wrapper!.text()).toContain('isolated partial reply')
    console.log('0147_DOM_recovered', JSON.stringify(statuses()))
  })
})
