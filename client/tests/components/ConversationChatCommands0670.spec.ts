import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

// flowgate.default.0670 T0004 — chat command execution UI and the FlowGate-computed
// per-run change summary (R0001 §3/§5/§8, NR0003 §8/§11/§12).
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

const DOC_ID = 'flowgate.default.0670.0009-CH'
const RUN_ID = 'aiv_run_1'

const DOMAIN = {
  send_action: ['copy_mention', 'invoke_ai', 'none'],
  context_mode: ['recent', 'all'],
  context_turns_presets: [5, 10, 15, 20, 30],
  context_turns_min: 1,
  context_turns_max: 200,
  source_access_mode: ['read_only', 'edit', 'edit_once'],
  command_policy: ['always_approve', 'user_approval', 'reject'],
}

function settings(over: Record<string, unknown> = {}) {
  const value = {
    send_action: 'none', context_mode: 'recent', context_turns: 20,
    source_access_mode: 'read_only', command_policy: 'user_approval', updated_at: null, ...over,
  }
  return { data: { ok: true, settings: value, is_default: true, defaults: value, domain: DOMAIN } }
}

function turn(seq: number, over: Record<string, unknown> = {}) {
  return {
    seq, speaker: 'user', participant_key: 'user:u1', display_name: 'u', locale: 'ko', body: `t${seq}`,
    based_on_seq: seq - 1, stale_since_seq: null, source_run_id: null,
    created_at: '2026-10-05T10:00:00+09:00', ...over,
  }
}

function page(rows: unknown[]) {
  return {
    data: {
      ok: true, doc_id: DOC_ID, head_seq: rows.length, next_after_seq: null, prev_before_seq: null,
      has_more: false, head: { intro: '', carried_over_from: null, total_turns: rows.length, head_seq: rows.length },
      turns: rows, participants: [], me: null,
    },
  }
}

function command(over: Record<string, unknown> = {}) {
  return {
    request_id: 'ccr_1', ai_run_id: RUN_ID, doc_id: DOC_ID, program: 'pytest',
    args: ['-q', 'server/tests/test_git_job_store.py'], command: 'pytest -q server/tests/test_git_job_store.py',
    cwd: '.', timeout_seconds: 300, category: 'test_build', high_impact: false, policy: 'user_approval',
    provider_name: 'Claude Opus 5.5', status: 'pending_approval', decision_source: null,
    exit_code: null, timed_out: false, stdout_tail: null, stderr_tail: null, error_code: null,
    created_at: '2026-10-05T10:01:00+09:00', ...over,
  }
}

const CHANGE = {
  run_id: RUN_ID, doc_id: DOC_ID, run_started_at: '2026-10-05T10:00:30+09:00',
  run_finished_at: '2026-10-05T10:03:47+09:00', files_changed: 2, insertions: 43, deletions: 8,
  files: [
    { path: 'server/modules/job_store.py', status: 'M', insertions: 41, deletions: 8 },
    { path: 'assets/logo.png', status: 'A', insertions: null, deletions: null },
  ],
}

let activity: { commands: unknown[]; changes: unknown[] } = { commands: [], changes: [] }
let rows: unknown[] = []

function serve() {
  getRequest.mockImplementation((url: unknown) => {
    const u = String(url)
    if (u.includes('ai-invoke/providers')) {
      return Promise.resolve({ data: { ok: true, project: 'flowgate', providers: [], default_provider_id: null } })
    }
    if (u.includes('/me/chat-settings')) return Promise.resolve(settings())
    if (u.includes('/runs/') && u.includes('/diff')) {
      return Promise.resolve({ data: { ok: true, data: {
        group_id: 'flowgate.default.0670', branch: null, commit: 'end', path: 'server/modules/job_store.py',
        status: 'M', old: { exists: true, binary: false, truncated: false, size: 4, content: 'old\n' },
        new: { exists: true, binary: false, truncated: false, size: 4, content: 'new\n' },
      } } })
    }
    if (u.includes('/chat-activity/')) return Promise.resolve({ data: { ok: true, doc_id: DOC_ID, ...activity } })
    if (u.includes('ai-invoke/active')) return Promise.resolve({ data: { active: false } })
    return Promise.resolve(page(rows))
  })
}

function mountView() {
  return mount(ConversationView, {
    props: { docId: DOC_ID, projectId: 'flowgate' },
    global: { plugins: [i18n, createPinia()] },
    attachTo: document.body,
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  activity = { commands: [], changes: [] }
  rows = []
  getRequest.mockReset()
  postRequest.mockReset()
  patchRequest.mockReset().mockImplementation((_p: unknown, body: unknown) =>
    Promise.resolve(settings((body ?? {}) as Record<string, unknown>)))
  showToast.mockReset()
  serve()
})

describe('chat command request card', () => {
  it('shows the full command, cwd and timeout with [실행]/[거부] while the run waits', async () => {
    rows = [turn(1, { body: '테스트 돌려줘' })]
    activity = { commands: [command()], changes: [] }
    const wrapper = mountView()
    await flushPromises()
    const card = wrapper.find('.conv-live-activity .ccmd')
    expect(card.exists()).toBe(true)
    expect(card.find('.ccmd-cmd').text()).toBe('pytest -q server/tests/test_git_job_store.py')
    expect(card.text()).toContain('명령 실행 요청')
    expect(card.text()).toContain('<worktree>')
    expect(card.text()).toContain('300s')
    expect(card.text()).toContain('Claude Opus 5.5')
    const buttons = card.findAll('.ccmd-actions button')
    expect(buttons.map((b) => b.text())).toEqual(['실행', '거부'])
    wrapper.unmount()
  })

  it('posts the decision and renders the server-confirmed state', async () => {
    rows = [turn(1)]
    activity = { commands: [command()], changes: [] }
    postRequest.mockResolvedValue({ data: { ok: true, request: command({ status: 'running', decision_source: 'user' }) } })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.ccmd-btn--run').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith('/api/v1/chat-commands/ccr_1/decision', { decision: 'approve' })
    expect(wrapper.find('.ccmd').classes()).toContain('ccmd--running')
    expect(wrapper.find('.ccmd-btn--run').exists()).toBe(false)
    wrapper.unmount()
  })

  it('attaches finished commands to the AI turn of the same run with exit code and output', async () => {
    rows = [turn(1), turn(2, { speaker: 'ai', participant_key: 'ai:p', body: '테스트 통과', source_run_id: RUN_ID })]
    activity = {
      commands: [command({ status: 'succeeded', exit_code: 0, duration_ms: 4800, stdout_tail: '28 passed in 4.32s' })],
      changes: [],
    }
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-live-activity').exists()).toBe(false)
    const card = wrapper.find('.conv-row--ai .ccmd')
    expect(card.text()).toContain('명령 실행 결과')
    expect(card.text()).toContain('exit code 0')
    expect(card.text()).toContain('4.8s')
    expect(card.find('.ccmd-out').text()).toBe('28 passed in 4.32s')
    wrapper.unmount()
  })

  it('keeps commands as their own rows ahead of the reply instead of inside the reply bubble', async () => {
    rows = [turn(1), turn(2, { speaker: 'ai', participant_key: 'ai:p', body: '테스트 통과', source_run_id: RUN_ID })]
    activity = {
      commands: [
        command({ status: 'succeeded', exit_code: 0 }),
        command({ request_id: 'ccr_2', command: 'git status', status: 'succeeded', exit_code: 0 }),
      ],
      changes: [CHANGE],
    }
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-bubble .ccmd').exists()).toBe(false)
    expect(wrapper.find('.conv-bubble .crc').exists()).toBe(false)
    const rowKinds = wrapper.findAll('.conv-scroll > .conv-row').map((row) => {
      if (row.find('.ccmd').exists()) return `cmd:${row.find('.ccmd').attributes('data-request-id')}`
      if (row.find('.crc').exists()) return 'change'
      return row.find('.conv-body').text()
    })
    expect(rowKinds).toEqual(['t1', 'cmd:ccr_1', 'cmd:ccr_2', '테스트 통과', 'change'])
    wrapper.unmount()
  })

  it('leaves a live command row in place and appends the reply below it when the turn lands', async () => {
    rows = [turn(1)]
    activity = { commands: [command({ status: 'running' })], changes: [] }
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.conv-live-activity .ccmd').exists()).toBe(true)
    expect(wrapper.find('.conv-live-activity .conv-bubble').exists()).toBe(false)
    expect(wrapper.find('.conv-live-activity .conv-meta').exists()).toBe(false)
    wrapper.unmount()
  })

  it('says when the policy refused the command', async () => {
    rows = [turn(1), turn(2, { speaker: 'ai', participant_key: 'ai:p', source_run_id: RUN_ID })]
    activity = { commands: [command({ status: 'rejected', decision_source: 'policy', policy: 'reject' })], changes: [] }
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.ccmd').text()).toContain('명령 실행 설정이 [거부]라서 실행하지 않았습니다.')
    wrapper.unmount()
  })
})

describe('run change summary', () => {
  it('renders FlowGate numbers under the AI turn, with unknown binary counts', async () => {
    rows = [turn(1), turn(2, { speaker: 'ai', participant_key: 'ai:p', body: '4 files +999 -999 바꿨어요', source_run_id: RUN_ID })]
    activity = { commands: [], changes: [CHANGE] }
    const wrapper = mountView()
    await flushPromises()
    const card = wrapper.find('.conv-row--ai .crc')
    expect(card.exists()).toBe(true)
    expect(card.find('.crc-summary').text()).toContain('2 files')
    expect(card.find('.crc-summary').text()).toContain('+43')
    expect(card.find('.crc-summary').text()).toContain('−8')
    const files = card.findAll('.crc-file')
    expect(files.map((f) => f.find('.crc-path').text())).toEqual(['server/modules/job_store.py', 'assets/logo.png'])
    expect(files[1].text()).toContain('?')
    expect(card.text()).not.toContain('FlowGate 자동 계산')
    expect(card.find('.crc-badge').exists()).toBe(false)
    wrapper.unmount()
  })

  it('draws nothing for a run without a stored change summary', async () => {
    rows = [turn(1), turn(2, { speaker: 'ai', participant_key: 'ai:p', source_run_id: RUN_ID })]
    activity = { commands: [], changes: [{ ...CHANGE, files_changed: 0, files: [] }] }
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.crc').exists()).toBe(false)
    expect(wrapper.find('.ccmd').exists()).toBe(false)
    wrapper.unmount()
  })

  it('opens the existing change viewer on the clicked file and reads the run diff', async () => {
    rows = [turn(1), turn(2, { speaker: 'ai', participant_key: 'ai:p', source_run_id: RUN_ID })]
    activity = { commands: [], changes: [CHANGE] }
    const wrapper = mountView()
    await flushPromises()
    await wrapper.findAll('.crc-file')[0].trigger('click')
    await flushPromises()
    const diffCall = getRequest.mock.calls.find((c) => String(c[0]).includes('/diff'))
    expect(diffCall?.[0]).toBe(`/api/v1/chat-activity/${DOC_ID}/runs/${RUN_ID}/diff`)
    expect(diffCall?.[1]).toEqual({ path: 'server/modules/job_store.py' })
    expect(document.body.textContent).toContain('작업 변경 상세')
    wrapper.unmount()
  })
})

describe('chat settings popover', () => {
  it('adds [명령 실행] with the default selected and no explanatory hint', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.conv-gear-btn').trigger('click')
    const panel = wrapper.find('.conv-settings')
    expect(panel.classes()).toContain('conv-settings--popover')
    expect(panel.text()).toContain('명령 실행')
    expect((wrapper.find('input[type="radio"][value="user_approval"]').element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.find('.conv-settings-hint').exists()).toBe(false)
    wrapper.unmount()
  })

  it('sends command_policy only when it was changed', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('.conv-gear-btn').trigger('click')
    await wrapper.find('input[type="radio"][value="always_approve"]').setValue()
    await wrapper.find('.conv-settings-save').trigger('click')
    await flushPromises()
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/me/chat-settings', {
      send_action: 'none', context_mode: 'recent', context_turns: 20, command_policy: 'always_approve',
    })
    wrapper.unmount()
  })
})
