import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

// flowgate.default.0675 T0004 — command / change activity placed by the server-decided
// anchor (anchor_seq / anchor_position / anchor_state), tail-follow on activity SSE,
// finished unmatched commands kept, and paging/re-entry restoring the same position.
const { getRequest, postRequest, patchRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
  showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { get: (...a: unknown[]) => getRequest(...a) },
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
  patchRequest: (...a: unknown[]) => patchRequest(...a),
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

import ConversationView from '@main/components/ConversationView.vue'

const DOC_ID = 'flowgate.default.0675.0009-CH'
const RUN_ID = 'aiv_run_1'

type Row = Record<string, any>

function turn(seq: number, over: Row = {}): Row {
  return {
    seq, speaker: 'user', participant_key: 'user:u1', display_name: 'u', locale: 'ko', body: `t${seq}`,
    based_on_seq: seq - 1, stale_since_seq: null, source_run_id: null,
    created_at: `2026-10-06T10:${String(seq % 60).padStart(2, '0')}:00+09:00`, ...over,
  }
}

function aiTurn(seq: number, runId: string, over: Row = {}): Row {
  return turn(seq, { speaker: 'ai', participant_key: 'ai:p', body: `a${seq}`, source_run_id: runId, ...over })
}

function command(over: Row = {}): Row {
  return {
    request_id: 'ccr_1', ai_run_id: RUN_ID, doc_id: DOC_ID, program: 'git', args: ['status'],
    command: 'git status', cwd: '.', timeout_seconds: 60, category: 'read_only', high_impact: false,
    policy: 'always_approve', provider_name: 'Model', status: 'succeeded', decision_source: 'policy',
    exit_code: 0, timed_out: false, stdout_tail: '', stderr_tail: null, error_code: null,
    created_at: '2026-10-06T10:01:30+09:00',
    anchor_seq: 1, anchor_position: 'after', anchor_state: 'run_start', ...over,
  }
}

function change(over: Row = {}): Row {
  return {
    run_id: RUN_ID, doc_id: DOC_ID, run_started_at: '2026-10-06T10:01:00+09:00',
    run_finished_at: '2026-10-06T10:03:00+09:00', created_at: '2026-10-06T10:03:00+09:00',
    files_changed: 1, insertions: 2, deletions: 1,
    files: [{ path: 'a.py', status: 'M', insertions: 2, deletions: 1 }],
    start_tree: 's', end_tree: 'e',
    anchor_seq: 1, anchor_position: 'after', anchor_state: 'run_start', ...over,
  }
}

// ── a fake server: turn pages + the activity anchor window, as the backend applies it ──
let rows: Row[] = []
let firstPageFrom = 1
let activity: { commands: Row[]; changes: Row[] } = { commands: [], changes: [] }
const activityCalls: Row[] = []

function inWindow(row: Row, params: Row): boolean {
  const from = params.from_seq
  const to = params.to_seq
  if (from == null && to == null) return true
  if (row.anchor_seq == null) return params.unplaced !== false
  return (from == null || row.anchor_seq >= from) && (to == null || row.anchor_seq <= to)
}

function turnPage(params: Row): Row {
  let list: Row[]
  if (params.before_seq != null) {
    const older = rows.filter((r) => r.seq < params.before_seq)
    list = older.slice(Math.max(0, older.length - Number(params.limit ?? 30)))
  } else if (params.after_seq != null) {
    list = rows.filter((r) => r.seq > params.after_seq)
  } else {
    list = rows.filter((r) => r.seq >= firstPageFrom)
  }
  const head = rows.length ? rows[rows.length - 1].seq : 0
  const oldest = list.length ? list[0].seq : head + 1
  return {
    data: {
      ok: true, doc_id: DOC_ID, head_seq: head, next_after_seq: null,
      prev_before_seq: oldest > 1 ? oldest : null, has_more: false,
      head: { intro: '', carried_over_from: null, total_turns: rows.length, head_seq: head },
      turns: list, participants: [], me: null,
    },
  }
}

function serve() {
  getRequest.mockImplementation((url: unknown, params: Row = {}) => {
    const u = String(url)
    if (u.includes('ai-invoke/providers')) {
      return Promise.resolve({ data: { ok: true, project: 'flowgate', providers: [], default_provider_id: null } })
    }
    if (u.includes('/me/chat-settings')) {
      const value = { send_action: 'none', context_mode: 'recent', context_turns: 20, source_access_mode: 'read_only', command_policy: 'user_approval', updated_at: null }
      return Promise.resolve({ data: { ok: true, settings: value, is_default: true, defaults: value, domain: {} } })
    }
    if (u.includes('/chat-activity/')) {
      activityCalls.push({ ...(params ?? {}) })
      const p = params ?? {}
      return Promise.resolve({ data: {
        ok: true, doc_id: DOC_ID,
        commands: activity.commands.filter((r) => inWindow(r, p)),
        changes: activity.changes.filter((r) => inWindow(r, p)),
      } })
    }
    if (u.includes('ai-invoke/active')) return Promise.resolve({ data: { active: false } })
    return Promise.resolve(turnPage(params ?? {}))
  })
}

// jsdom has no layout: every row is 100px tall, the viewport 400px. scrollTop is
// settable so the component's own pin can be observed.
const ROW_PX = 100
function geometry(wrapper: VueWrapper, top: number | 'bottom') {
  const el = wrapper.find('.conv-scroll').element as HTMLElement
  const height = () => ROW_PX * el.querySelectorAll('.conv-row').length + ROW_PX
  let current = top === 'bottom' ? height() - 400 : top
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: height })
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 400 })
  Object.defineProperty(el, 'scrollTop', { configurable: true, get: () => current, set: (v: number) => { current = v } })
  return {
    get top() { return current },
    get bottom() { return height() },
  }
}

/** Promises AND the two animation frames scrollToBottom re-pins in. */
async function settle() {
  await flushPromises()
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null))))
  await flushPromises()
}

function mountView(docId = DOC_ID) {
  return mount(ConversationView, {
    props: { docId, projectId: 'flowgate' },
    global: { plugins: [i18n, createPinia()] },
    attachTo: document.body,
  })
}

/** What the scroller shows, top to bottom: turn bodies and activity cards. */
function layout(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.conv-scroll .conv-row').map((row) => {
    if (row.find('.ccmd').exists()) return `cmd:${row.find('.ccmd').attributes('data-request-id')}`
    if (row.find('.crc').exists()) return 'change'
    return row.find('.conv-body').text()
  })
}

function emit(name: string, detail: Row) {
  window.dispatchEvent(new CustomEvent(name, { detail }))
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  rows = []
  firstPageFrom = 1
  activity = { commands: [], changes: [] }
  activityCalls.length = 0
  getRequest.mockReset()
  postRequest.mockReset()
  patchRequest.mockReset()
  showToast.mockReset()
  serve()
})

describe('activity SSE tail-follow (§2-1)', () => {
  it('follows a new command card to the new bottom when the reader was at the bottom', async () => {
    rows = [turn(1), turn(2), turn(3), turn(4), turn(5)]
    const wrapper = mountView()
    await settle()
    const g = geometry(wrapper, 'bottom')
    activity = { commands: [command({ anchor_seq: 5, status: 'pending_approval' })], changes: [] }
    emit('fg:chat_command_updated', { doc_id: DOC_ID })
    await settle()
    expect(layout(wrapper)).toEqual(['t1', 't2', 't3', 't4', 't5', 'cmd:ccr_1'])
    expect(g.top).toBe(g.bottom)
    wrapper.unmount()
  })

  it('follows a new change summary the same way', async () => {
    rows = [turn(1), turn(2), turn(3), turn(4), aiTurn(5, RUN_ID)]
    const wrapper = mountView()
    await settle()
    const g = geometry(wrapper, 'bottom')
    activity = { commands: [], changes: [change({ anchor_seq: 5, anchor_state: 'reply' })] }
    emit('fg:chat_run_changes', { doc_id: DOC_ID })
    await settle()
    expect(layout(wrapper)).toEqual(['t1', 't2', 't3', 't4', 'a5', 'change'])
    expect(g.top).toBe(g.bottom)
    wrapper.unmount()
  })

  it('leaves a reader who scrolled up into history where they are', async () => {
    rows = [turn(1), turn(2), turn(3), turn(4), turn(5)]
    const wrapper = mountView()
    await settle()
    const g = geometry(wrapper, 20)
    activity = { commands: [command({ anchor_seq: 5, status: 'pending_approval' })], changes: [] }
    emit('fg:chat_command_updated', { doc_id: DOC_ID })
    await settle()
    expect(layout(wrapper)).toEqual(['t1', 't2', 't3', 't4', 't5', 'cmd:ccr_1'])
    expect(g.top).toBe(20)
    wrapper.unmount()
  })

  it('does not nudge a reader near the bottom when a re-read changes nothing', async () => {
    rows = [turn(1), turn(2), turn(3), turn(4), turn(5)]
    activity = { commands: [command({ anchor_seq: 5 })], changes: [] }
    const wrapper = mountView()
    await settle()
    const g = geometry(wrapper, 'bottom')
    const nearBottom = g.bottom - 400 - 50
    ;(wrapper.find('.conv-scroll').element as HTMLElement).scrollTop = nearBottom
    emit('fg:chat_command_updated', { doc_id: DOC_ID })
    await settle()
    expect(g.top).toBe(nearBottom)
    wrapper.unmount()
  })

  it('follows when the AI reply lands after the activity, without an extra jump', async () => {
    rows = [turn(1), turn(2), turn(3), turn(4)]
    activity = { commands: [command({ anchor_seq: 4, status: 'running' })], changes: [] }
    const wrapper = mountView()
    await settle()
    const g = geometry(wrapper, 'bottom')
    activity = {
      commands: [command({ anchor_seq: 5, anchor_position: 'before', anchor_state: 'reply' })],
      changes: [change({ anchor_seq: 5, anchor_state: 'reply' })],
    }
    emit('fg:conversation_turn', { doc_id: DOC_ID, head_seq: 5, turn: aiTurn(5, RUN_ID) })
    await settle()
    expect(layout(wrapper)).toEqual(['t1', 't2', 't3', 't4', 'cmd:ccr_1', 'a5', 'change'])
    expect(g.top).toBe(g.bottom)
    wrapper.unmount()
  })
})

describe('finished unmatched commands (§2-2)', () => {
  it('keeps a finished command after its start turn when later turns arrive', async () => {
    rows = [turn(1)]
    activity = { commands: [command({ status: 'failed', exit_code: 1 })], changes: [] }
    const wrapper = mountView()
    await flushPromises()
    expect(layout(wrapper)).toEqual(['t1', 'cmd:ccr_1'])
    emit('fg:conversation_turn', { doc_id: DOC_ID, head_seq: 2, turn: turn(2, { created_at: '2026-10-06T11:00:00+09:00' }) })
    emit('fg:conversation_turn', { doc_id: DOC_ID, head_seq: 3, turn: aiTurn(3, 'other_run', { created_at: '2026-10-06T11:01:00+09:00' }) })
    await flushPromises()
    expect(layout(wrapper)).toEqual(['t1', 'cmd:ccr_1', 't2', 'a3'])
    wrapper.unmount()
  })

  it('no longer hides a finished command of a pre-anchor server behind a later turn', async () => {
    rows = [turn(1), turn(2, { created_at: '2026-10-06T11:00:00+09:00' })]
    const legacy = command({ status: 'rejected' })
    delete legacy.anchor_seq
    delete legacy.anchor_position
    delete legacy.anchor_state
    activity = { commands: [legacy], changes: [] }
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-live-activity .ccmd').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('activity before its reply turn (§4 client 5)', () => {
  it('moves the card from the start turn to ahead of the reply without duplicating it', async () => {
    rows = [turn(1)]
    activity = { commands: [command({ status: 'running' })], changes: [] }
    const wrapper = mountView()
    await flushPromises()
    expect(layout(wrapper)).toEqual(['t1', 'cmd:ccr_1'])
    // The server re-anchors on the reply before broadcasting it.
    activity = {
      commands: [command({ status: 'succeeded', anchor_seq: 2, anchor_position: 'before', anchor_state: 'reply' })],
      changes: [change({ anchor_seq: 2, anchor_position: 'after', anchor_state: 'reply' })],
    }
    emit('fg:conversation_turn', { doc_id: DOC_ID, head_seq: 2, turn: aiTurn(2, RUN_ID) })
    await flushPromises()
    expect(layout(wrapper)).toEqual(['t1', 'cmd:ccr_1', 'a2', 'change'])
    expect(wrapper.findAll('.ccmd')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps an anchor newer than the loaded turns visible at the tail until that turn lands', async () => {
    rows = [turn(1)]
    activity = { commands: [command({ anchor_seq: 2, anchor_position: 'before', anchor_state: 'reply' })], changes: [] }
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-live-activity .ccmd').exists()).toBe(true)
    emit('fg:conversation_turn', { doc_id: DOC_ID, head_seq: 2, turn: aiTurn(2, RUN_ID) })
    await flushPromises()
    expect(wrapper.find('.conv-live-activity').exists()).toBe(false)
    expect(layout(wrapper)).toEqual(['t1', 'cmd:ccr_1', 'a2'])
    wrapper.unmount()
  })
})

describe('paging (§2-4)', () => {
  function longConversation() {
    rows = []
    for (let seq = 1; seq <= 60; seq++) rows.push(seq === 6 ? aiTurn(6, RUN_ID) : turn(seq))
    firstPageFrom = 31
    activity = {
      commands: [command({ anchor_seq: 6, anchor_position: 'before', anchor_state: 'reply' })],
      changes: [change({ anchor_seq: 6, anchor_position: 'after', anchor_state: 'reply' })],
    }
  }

  it('does not float an older page\'s activity at the current tail', async () => {
    longConversation()
    const wrapper = mountView()
    await flushPromises()
    expect(activityCalls.at(-1)).toEqual({ from_seq: 31 })
    expect(wrapper.find('.ccmd').exists()).toBe(false)
    expect(wrapper.find('.crc').exists()).toBe(false)
    wrapper.unmount()
  })

  it('draws it around its anchor turn once that page is loaded, exactly once', async () => {
    longConversation()
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.conv-older').trigger('click')
    await flushPromises()
    // The page reaches turn 1, so the window opens at 0 (anchor-0 rows come with it).
    expect(activityCalls.at(-1)).toEqual({ from_seq: 0, to_seq: 30, unplaced: false })
    const seen = layout(wrapper)
    expect(seen.slice(4, 8)).toEqual(['t5', 'cmd:ccr_1', 'a6', 'change'])
    expect(seen.filter((x) => x.startsWith('cmd:'))).toHaveLength(1)
    // A later full refresh (SSE) keeps the older page's rows and adds no copy.
    emit('fg:chat_command_updated', { doc_id: DOC_ID })
    await flushPromises()
    expect(layout(wrapper)).toEqual(seen)
    wrapper.unmount()
  })
})

describe('re-entry and order (§2-3, §2-4)', () => {
  it('restores the same positions after a remount and a document round trip', async () => {
    rows = [turn(1), aiTurn(2, RUN_ID), turn(3), turn(4)]
    activity = {
      commands: [
        command({ anchor_seq: 2, anchor_position: 'before', anchor_state: 'reply' }),
        command({ request_id: 'ccr_9', ai_run_id: 'run_9', anchor_seq: 4, anchor_state: 'run_start', status: 'failed' }),
      ],
      changes: [change({ anchor_seq: 2, anchor_position: 'after', anchor_state: 'reply' })],
    }
    const expected = ['t1', 'cmd:ccr_1', 'a2', 'change', 't3', 't4', 'cmd:ccr_9']
    const first = mountView()
    await flushPromises()
    expect(layout(first)).toEqual(expected)
    first.unmount()

    const second = mountView()
    await flushPromises()
    expect(layout(second)).toEqual(expected)
    await second.setProps({ docId: 'flowgate.default.0675.0010-CH' })
    await flushPromises()
    await second.setProps({ docId: DOC_ID })
    await flushPromises()
    expect(layout(second)).toEqual(expected)
    second.unmount()
  })

  it('orders several commands on one anchor by time, then id, whatever order they arrive in', async () => {
    rows = [turn(1), aiTurn(2, RUN_ID)]
    const at = (request_id: string, created_at: string) =>
      command({ request_id, created_at, anchor_seq: 2, anchor_position: 'before', anchor_state: 'reply' })
    activity = {
      commands: [
        at('ccr_c', '2026-10-06T10:01:40+09:00'),
        at('ccr_b', '2026-10-06T10:01:20+09:00'),
        at('ccr_a', '2026-10-06T10:01:40+09:00'),
      ],
      changes: [],
    }
    const wrapper = mountView()
    await flushPromises()
    expect(layout(wrapper)).toEqual(['t1', 'cmd:ccr_b', 'cmd:ccr_a', 'cmd:ccr_c', 'a2'])
    activity.commands.reverse()
    emit('fg:chat_command_updated', { doc_id: DOC_ID })
    await flushPromises()
    expect(layout(wrapper)).toEqual(['t1', 'cmd:ccr_b', 'cmd:ccr_a', 'cmd:ccr_c', 'a2'])
    wrapper.unmount()
  })
})

describe('unplaced history (§2-5)', () => {
  it('keeps unresolved and ambiguous rows in a labelled block at the start, never at the tail', async () => {
    rows = [turn(1), aiTurn(2, 'other')]
    activity = {
      commands: [command({ request_id: 'ccr_old', ai_run_id: 'old', anchor_seq: null, anchor_position: null, anchor_state: 'unresolved' })],
      changes: [change({ run_id: 'old2', anchor_seq: null, anchor_position: null, anchor_state: 'ambiguous' })],
    }
    const wrapper = mountView()
    await flushPromises()
    const block = wrapper.find('.conv-unplaced')
    expect(block.exists()).toBe(true)
    expect(block.text()).toContain('대화 위치를 확정할 수 없는 이전 실행 기록')
    expect(block.findAll('.conv-unplaced-activity')).toHaveLength(2)
    expect(wrapper.find('.conv-live-activity').exists()).toBe(false)
    wrapper.unmount()
  })

  it('draws an anchor-0 row (a run started on an empty conversation) before the first turn', async () => {
    rows = [aiTurn(1, 'later')]
    activity = { commands: [command({ anchor_seq: 0 })], changes: [] }
    const wrapper = mountView()
    await flushPromises()
    expect(layout(wrapper)).toEqual(['cmd:ccr_1', 'a1'])
    wrapper.unmount()
  })

  it('brings an anchor-0 row in with the first page when the screen opened on a later page', async () => {
    rows = []
    for (let seq = 1; seq <= 60; seq++) rows.push(turn(seq))
    firstPageFrom = 46
    activity = {
      commands: [command({ request_id: 'ccr_0', ai_run_id: 'run_0', anchor_seq: 0, anchor_state: 'run_start', status: 'failed' })],
      changes: [change({ run_id: 'run_0', anchor_seq: 0, anchor_state: 'run_start' })],
    }
    const expectedStart = ['cmd:ccr_0', 'change', 't1', 't2']
    const wrapper = mountView()
    await flushPromises()
    expect(activityCalls.at(-1)).toEqual({ from_seq: 16 })
    expect(wrapper.find('.ccmd').exists()).toBe(false)
    expect(wrapper.find('.crc').exists()).toBe(false)
    // load() already pulled one older page (16..45); this one is the first page (1..15).
    await wrapper.find('.conv-older').trigger('click')
    await flushPromises()
    expect(activityCalls.at(-1)).toEqual({ from_seq: 0, to_seq: 15, unplaced: false })
    const seen = layout(wrapper)
    expect(seen.slice(0, 4)).toEqual(expectedStart)
    expect(seen.filter((x) => x === 'cmd:ccr_0')).toHaveLength(1)
    expect(seen.filter((x) => x === 'change')).toHaveLength(1)
    // A full refresh (SSE / reconnect) now reads everything and keeps exactly one copy.
    emit('fg:chat_command_updated', { doc_id: DOC_ID })
    await flushPromises()
    expect(layout(wrapper)).toEqual(seen)
    wrapper.unmount()

    // Re-entry opens on the late page again and pages down to turn 1 the same way.
    activityCalls.length = 0
    const again = mountView()
    await flushPromises()
    while (again.find('.conv-older').exists()) {
      await again.find('.conv-older').trigger('click')
      await flushPromises()
    }
    expect(activityCalls.some((c) => c.from_seq === 0)).toBe(true)
    expect(layout(again)).toEqual(seen)
    again.unmount()
  })
})
