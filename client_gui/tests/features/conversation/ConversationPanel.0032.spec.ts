import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import type { CommandRequestActivity, WorkChangeActivity } from '@/features/conversation/types'
import { createFakeSource, httpError, turn } from './fakeConversationSource'

// flowgate.v02.0032 — what the Conversation panel shows on the production contract
// (D0006 §6.1–§6.3): server command states and reasons, run change files with their stored
// diff, decision recovery and [새 채팅] during an AI run.
registerAppMessages(i18n)

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async () => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

const A = 'p.m.0001.0002-CH'

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

const cmd = (id: string, extra: Partial<CommandRequestActivity> = {}): CommandRequestActivity => ({
  kind: 'command',
  id,
  runId: 'run-1',
  createdAt: null,
  command: `npm ${id}`,
  state: 'pending_approval',
  anchor: { seq: 2, position: 'before', state: 'reply' },
  ...extra,
})

const change: WorkChangeActivity = {
  kind: 'change',
  id: 'run-1',
  runId: 'run-1',
  createdAt: null,
  files: [
    { path: 'src/a.ts', status: 'M', insertions: 1, deletions: 1 },
    { path: 'assets/logo.png', status: 'A', insertions: null, deletions: null },
  ],
  filesChanged: 2,
  insertions: 1,
  deletions: 1,
  anchor: { seq: 2, position: 'after', state: 'reply' },
}

async function mountPanel(init: Parameters<typeof createFakeSource>[0] = {}, props: Record<string, unknown> = {}) {
  const fake = createFakeSource({
    chats: [{ id: A, title: '빌드 확인', updatedAt: new Date().toISOString() }],
    turns: { [A]: [turn(1, 'user', '빌드 돌려 줘', '2026-10-06T08:02:00Z'), turn(2, 'ai', '돌렸습니다', '2026-10-06T08:03:00Z')] },
    ...init,
  })
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return fake
}

const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
const findAll = (sel: string) => wrapper!.findAll(`[data-test="${sel}"]`)
const card = (id: string) => findAll('tl-command').find((c) => c.text().includes(`npm ${id}`))!

describe('command card (D0006 §6.1)', () => {
  it('words each card from the server state and reason, and offers a decision only while pending', async () => {
    await mountPanel({
      activities: {
        [A]: [
          cmd('pending'),
          cmd('policy', { state: 'rejected', rejectedBy: 'policy' }),
          cmd('user', { state: 'rejected', rejectedBy: 'user' }),
          cmd('expired', { state: 'cancelled', errorCode: 'chat_command_approval_wait_expired' }),
          cmd('lost', { state: 'failed', errorCode: 'chat_command_process_owner_unavailable' }),
          cmd('exit', { state: 'failed', exitCode: 2 }),
          cmd('running', { state: 'running' }),
          cmd('timeout', { state: 'timed_out' }),
        ],
      },
    })
    expect(card('pending').find('[data-test="tl-command-state"]').text()).toBe('승인 대기')
    expect(card('pending').find('[data-test="tl-command-approve"]').exists()).toBe(true)
    expect(card('policy').find('[data-test="tl-command-reason"]').text()).toContain('명령 정책이 거절')
    expect(card('user').find('[data-test="tl-command-reason"]').text()).toContain('사용자가 거절')
    expect(card('expired').find('[data-test="tl-command-state"]').text()).toBe('취소됨')
    expect(card('expired').find('[data-test="tl-command-reason"]').text()).toContain('승인 대기 시간이 지나')
    expect(card('lost').find('[data-test="tl-command-reason"]').text()).toContain('프로세스를 잃었습니다')
    expect(card('exit').find('[data-test="tl-command-reason"]').text()).toBe('종료 코드 2')
    expect(card('running').find('[data-test="tl-command-state"]').text()).toBe('실행 중')
    expect(card('timeout').find('[data-test="tl-command-state"]').text()).toBe('시간 초과')
    // Exactly one card — the pending one — can be decided.
    expect(findAll('tl-command-approve')).toHaveLength(1)
  })

  it('hides the decision after a 403 and says why', async () => {
    const { source } = await mountPanel({ activities: { [A]: [cmd('k1')] } })
    source.decideCommand.mockRejectedValueOnce(httpError(403, 'no', 'chat_command_decision_forbidden'))
    await find('tl-command-approve').trigger('click')
    await flushPromises()
    expect(find('tl-command').attributes('data-state')).toBe('pending_approval')
    expect(find('tl-command-approve').exists()).toBe(false)
    expect(find('conv-notice').text()).toContain('결정할 권한이 없습니다')
  })

  it('shows the server state after a 409 with an "already handled" notice', async () => {
    const { source, data } = await mountPanel({ activities: { [A]: [cmd('k1')] } })
    source.decideCommand.mockImplementationOnce(async () => {
      data.activities[A] = [cmd('k1', { state: 'cancelled', errorCode: 'chat_command_run_finished' })]
      throw httpError(409, 'finished', 'chat_command_run_finished')
    })
    await find('tl-command-reject').trigger('click')
    await flushPromises()
    expect(find('tl-command').attributes('data-state')).toBe('cancelled')
    expect(find('tl-command-reason').text()).toContain('AI 호출이 끝나')
    expect(find('conv-notice').text()).toContain('이미 처리된')
  })
})

describe('run change card (D0006 §6.1)', () => {
  const diff = {
    runId: 'run-1',
    path: 'src/a.ts',
    status: 'M',
    old: { exists: true, binary: false, truncated: false, size: 8, content: 'keep\nold\n' },
    new: { exists: true, binary: false, truncated: false, size: 8, content: 'keep\nnew\n' },
  }

  it('lists the files with status and numbers, "no stats" for binary', async () => {
    await mountPanel({ activities: { [A]: [change] } })
    const rows = findAll('tl-change-file')
    expect(rows.map((r) => r.attributes('data-path'))).toEqual(['src/a.ts', 'assets/logo.png'])
    expect(['.tl-file-status', '.tl-file-path', '.tl-file-nums'].map((c) => rows[0].find(c).text())).toEqual(['M', 'src/a.ts', '+1 −1'])
    expect(rows[1].text()).toContain('통계 없음')
    expect(find('tl-change-count').text()).toBe('2 files')
  })

  it('opens the stored diff of a file under its row and closes it', async () => {
    const { source } = await mountPanel({ activities: { [A]: [change] }, diffs: { 'run-1\nsrc/a.ts': diff } })
    await findAll('tl-change-file')[0].trigger('click')
    await flushPromises()
    expect(source.fetchRunFileDiff).toHaveBeenCalledWith(A, 'run-1', 'src/a.ts')
    const lines = find('rc-diff-lines').findAll('.rc-diff-line').map((l) => `${l.attributes('data-kind')}:${l.text()}`)
    expect(lines).toEqual(['context:keep', 'del:-old', 'add:+new'])
    await find('rc-diff-close').trigger('click')
    expect(find('rc-diff').exists()).toBe(false)
  })

  it('says why a binary file has no line diff, and shows a failed read', async () => {
    await mountPanel({
      activities: { [A]: [change] },
      diffs: { 'run-1\nassets/logo.png': { ...diff, path: 'assets/logo.png', status: 'A', new: { ...diff.new, binary: true, content: null } } },
    })
    await findAll('tl-change-file')[1].trigger('click')
    await flushPromises()
    expect(find('rc-diff-unavailable').text()).toContain('바이너리')
    await findAll('tl-change-file')[0].trigger('click')
    await flushPromises()
    expect(find('rc-diff-error').exists()).toBe(true)
  })
})

describe('[새 채팅] (D0006 §6.1, §6.3)', () => {
  it('stays enabled and works while an AI run is in flight', async () => {
    const { source } = await mountPanel({ runs: { 'run-1': ['running'] } })
    await find('conv-invoke-ai').trigger('click')
    await flushPromises()
    expect(find('conv-send').classes()).toContain('is-stop')
    const button = find('conv-new-btn')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    await flushPromises()
    expect(source.createChat).toHaveBeenCalledWith({ projectId: 'p', groupId: 'p.m.0001' }, '새 대화')
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('새 대화')
  })

  it('keeps the open chat and shows a notice when the group is disposed', async () => {
    const { source } = await mountPanel()
    source.createChat.mockRejectedValueOnce(httpError(409, 'disposed', 'conversation_group_disposed'))
    await find('conv-new-btn').trigger('click')
    await flushPromises()
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('빌드 확인')
    expect(find('conv-notice').text()).toContain('폐기된 플로우')
  })
})
