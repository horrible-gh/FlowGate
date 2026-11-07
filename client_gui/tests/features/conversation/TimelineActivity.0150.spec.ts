import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { toCommand } from '@/features/conversation/api'
import TimelineActivity from '@/features/conversation/TimelineActivity.vue'
import type { CommandRequestActivity } from '@/features/conversation/types'

// flowgate.v02.0150 — every command card has a detail button that opens, inside the card (one
// column), the full command, stdout / stderr, the exit code and the error, in any state.
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

const LONG = `npm run test -- ${'--reporter=verbose '.repeat(12)}--grep "a b"`

const cmd = (extra: Partial<CommandRequestActivity> = {}): CommandRequestActivity => ({
  kind: 'command',
  id: 'req-1',
  runId: 'run-1',
  createdAt: null,
  command: LONG,
  cwd: 'client_gui',
  state: 'succeeded',
  startedAt: '2026-11-07T11:00:00Z',
  exitCode: 0,
  durationMs: 1530,
  stdout: 'line 1\nline 2\n<b>not html</b>',
  stderr: '',
  stdoutTruncated: false,
  stderrTruncated: false,
  ...extra,
})

function mountCard(activity: CommandRequestActivity, props: Record<string, unknown> = {}) {
  wrapper = mount(TimelineActivity, { props: { activity, ...props }, global: { plugins: [i18n] } })
  return wrapper
}

const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)

async function openDetail() {
  await find('tl-command-detail-toggle').trigger('click')
}

describe('command card detail (0150)', () => {
  it('shows a detail button on every state, also read-only, and toggles the detail in place', async () => {
    const states: CommandRequestActivity['state'][] = [
      'pending_approval', 'approving', 'rejecting', 'approved', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled', 'rejected',
    ]
    for (const state of states) {
      mountCard(cmd({ state }), { readOnly: true })
      const toggle = find('tl-command-detail-toggle')
      expect(toggle.exists(), state).toBe(true)
      expect(toggle.attributes('disabled'), state).toBeUndefined()
      expect(toggle.attributes('aria-expanded')).toBe('false')
      expect(find('tl-command-detail').exists()).toBe(false)
      await openDetail()
      expect(find('tl-command-detail').exists()).toBe(true)
      expect(toggle.attributes('aria-expanded')).toBe('true')
      expect(toggle.text()).toBe('상세 닫기')
      await openDetail()
      expect(find('tl-command-detail').exists()).toBe(false)
      expect(toggle.text()).toBe('상세 보기')
      wrapper!.unmount()
      wrapper = null
    }
  })

  it('keeps approve / reject next to the detail button while pending', async () => {
    mountCard(cmd({ state: 'pending_approval', startedAt: null, exitCode: null, durationMs: null, stdout: '', stderr: '' }))
    expect(find('tl-command-approve').exists()).toBe(true)
    expect(find('tl-command-reject').exists()).toBe(true)
    await openDetail()
    await find('tl-command-approve').trigger('click')
    expect(wrapper!.emitted('decide')).toEqual([['approve']])
    expect(find('tl-command-detail-no-output').text()).toBe('아직 실행 전이라 결과가 없습니다.')
  })

  it('shows the full command, the directory, exit code 0, duration and both streams as text', async () => {
    mountCard(cmd())
    await openDetail()
    expect(find('tl-command-detail-command').text()).toBe(LONG)
    expect(find('tl-command-detail-cwd').text()).toBe('client_gui')
    expect(find('tl-command-detail-exit').text()).toBe('0')
    expect(find('tl-command-detail-duration').text()).toBe('1.53 s')
    const out = find('tl-command-detail-stdout')
    expect(out.find('pre').element.textContent).toBe('line 1\nline 2\n<b>not html</b>')
    expect(out.find('b').exists()).toBe(false)
    expect(find('tl-command-detail-stderr').text()).toContain('(출력 없음)')
    expect(find('tl-command-detail-error').exists()).toBe(false)
  })

  it('shows a failure with its exit code, stderr, the error wording with its code and the cut marks', async () => {
    mountCard(cmd({
      state: 'failed', exitCode: 2, stdout: 'partial', stderr: 'boom\nstack',
      stdoutTruncated: true, stderrTruncated: true, errorCode: 'chat_command_internal_error',
    }))
    await openDetail()
    expect(find('tl-command-detail-exit').text()).toBe('2')
    expect(find('tl-command-detail-stderr').find('pre').element.textContent).toBe('boom\nstack')
    expect(find('tl-command-detail-error').text()).toBe('실행 중 서버 오류가 발생했습니다. (chat_command_internal_error)')
    expect(find('tl-command-detail-stdout-truncated').exists()).toBe(true)
    expect(find('tl-command-detail-stderr-truncated').exists()).toBe(true)
  })

  it('names a timeout, a rejection and an unknown code; a command that never ran has no output', async () => {
    mountCard(cmd({ state: 'timed_out', timedOut: true, exitCode: null }))
    await openDetail()
    expect(find('tl-command-detail-exit').text()).toBe('없음')
    expect(find('tl-command-detail-error').text()).toContain('제한 시간을 넘어')
    wrapper!.unmount()

    mountCard(cmd({ state: 'rejected', rejectedBy: 'policy', startedAt: null, exitCode: null, durationMs: null, stdout: '', stderr: '' }))
    await openDetail()
    expect(find('tl-command-detail-error').text()).toContain('명령 정책이 거절')
    expect(find('tl-command-detail-no-output').text()).toBe('실행되지 않아 결과가 없습니다.')
    expect(find('tl-command-detail-stdout').exists()).toBe(false)
    wrapper!.unmount()

    mountCard(cmd({ state: 'failed', errorCode: 'chat_command_something_new', startedAt: null, stdout: '', stderr: 'refused by policy' }))
    await openDetail()
    expect(find('tl-command-detail-error').text()).toBe('chat_command_something_new')
    expect(find('tl-command-detail-stderr').text()).toContain('refused by policy')
  })

  it('tells a running command apart from an empty output and follows the server update', async () => {
    mountCard(cmd({ state: 'running', exitCode: null, durationMs: null, stdout: '', stderr: '' }))
    await openDetail()
    expect(find('tl-command-detail-no-output').text()).toBe('실행 중입니다. 끝나면 결과가 표시됩니다.')
    await wrapper!.setProps({ activity: cmd({ state: 'succeeded', stdout: 'done' }) })
    expect(find('tl-command-detail').exists()).toBe(true)
    expect(find('tl-command-detail-stdout').text()).toContain('done')
    expect(find('tl-command-detail-exit').text()).toBe('0')
  })

  it('keeps the server output fields in the adapter', () => {
    const c = toCommand({
      request_id: 'req-1', program: 'npm', args: ['test'], status: 'failed', exit_code: 1,
      started_at: '2026-11-07T11:00:00Z', stdout_tail: 'out', stderr_tail: 'err', stdout_truncated: true, stderr_truncated: false,
    })
    expect(c).toMatchObject({
      startedAt: '2026-11-07T11:00:00Z', stdout: 'out', stderr: 'err', stdoutTruncated: true, stderrTruncated: false, exitCode: 1,
    })
    const pending = toCommand({ request_id: 'req-2', program: 'npm', status: 'pending_approval' })
    expect(pending).toMatchObject({ startedAt: null, stdout: null, stderr: null, stdoutTruncated: false, stderrTruncated: false })
  })
})
