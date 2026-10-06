import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { createFakeSource, turn } from './fakeConversationSource'

// flowgate.v02.0031 — the Conversation part mounted with an in-memory source (R0001 §12).
registerAppMessages(i18n)

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async () => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0004-CH'

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  clipboard.copyToClipboard.mockReset()
  clipboard.copyToClipboard.mockResolvedValue(true)
  localStorage.clear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

async function mountPanel(init: Parameters<typeof createFakeSource>[0] = {}, props: Record<string, unknown> = {}) {
  const fake = createFakeSource({
    chats: [
      { id: A, title: '인증 오류 조사 · 작업 대화', updatedAt: new Date().toISOString() },
      { id: B, title: 'Git 충돌 원인 확인', updatedAt: '2026-10-01T08:00:00Z' },
    ],
    turns: {
      [A]: [
        turn(1, 'user', '조사 결과 기준으로 수정하고 테스트는 돌리지 마.', '2026-10-06T08:02:00Z'),
        turn(2, 'ai', '요청한 범위만 수정했습니다.', '2026-10-06T08:03:00Z', { displayName: 'Claude Opus 5' }),
      ],
      [B]: [turn(1, 'user', 'git 상태 확인', '2026-10-01T08:00:00Z')],
    },
    activities: {
      [A]: [
        {
          kind: 'command',
          id: 'k1',
          runId: 'c1',
          createdAt: '2026-10-06T08:02:30Z',
          command: 'git diff -- client/src/main',
          state: 'pending_approval',
          anchor: { seq: 2, position: 'before', state: 'reply' },
        },
        {
          kind: 'change',
          id: 'c1',
          runId: 'c1',
          createdAt: '2026-10-06T08:04:00Z',
          files: [
            { path: 'client/src/main/components/ConversationView.vue', status: 'M', insertions: 30, deletions: 10 },
            { path: 'client/src/x.ts', status: 'A', insertions: 4, deletions: 1 },
          ],
          filesChanged: 2,
          insertions: 34,
          deletions: 11,
          anchor: { seq: 2, position: 'after', state: 'reply' },
        },
      ],
    },
    participants: [
      { key: 'u:test', kind: 'user', displayName: 'test', lastReadSeq: 2 },
      { key: 'ai:opus', kind: 'ai', displayName: 'Opus', lastReadSeq: 1 },
    ],
    ...init,
  })
  wrapper = mount(ConversationPanel, {
    props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return { ...fake, wrapper }
}

const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
const findAll = (sel: string) => wrapper!.findAll(`[data-test="${sel}"]`)

describe('ConversationPanel (flowgate.v02.0031)', () => {
  it('shows header, one ordered timeline, participants and composer — and no tool panel chrome', async () => {
    await mountPanel()
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('인증 오류 조사 · 작업 대화')
    const log = find('conv-log')
    const kinds = log.findAll('[data-test="tl-message"], [data-test="tl-command"], [data-test="tl-change"]').map((n) => n.attributes('data-test'))
    expect(kinds).toEqual(['tl-message', 'tl-command', 'tl-message', 'tl-change'])
    const ai = findAll('tl-message')[1]
    expect(ai.attributes('data-speaker')).toBe('ai')
    expect(ai.find('[data-test="tl-provider"]').text()).toBe('Claude Opus 5')
    expect(findAll('conv-participant').map((p) => p.text().replace(/\s+/g, ''))).toEqual(['●test#2', '◆Opus#1'])
    expect(find('conv-composer').exists()).toBe(true)
    // R0001 §10: no 팔레트 / 대화 / Git tab buttons and no Palette / Git parts inside.
    expect(wrapper!.text()).not.toMatch(/팔레트|Palette/)
    expect(wrapper!.findAll('[role="tab"]')).toHaveLength(0)
  })

  it('opens the chat list and switches the conversation', async () => {
    const { source } = await mountPanel()
    await find('conv-list-btn').trigger('click')
    const items = findAll('conv-list-item')
    expect(items.map((i) => i.find('.conv-list-name').text())).toEqual(['인증 오류 조사 · 작업 대화', 'Git 충돌 원인 확인'])
    expect(items[0].find('.conv-list-meta').text()).toBe('현재')
    expect(items[1].find('.conv-list-meta').text()).toBe('10/01')
    await items[1].trigger('click')
    await flushPromises()
    expect(find('conv-list-pop').exists()).toBe(false)
    expect(source.fetchTurns).toHaveBeenCalledWith(B, expect.anything())
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('Git 충돌 원인 확인')
    expect(findAll('tl-message').map((m) => m.find('.tl-text').text())).toEqual(['git 상태 확인'])
  })

  it('renames the current chat from the title input', async () => {
    const { source } = await mountPanel()
    const input = find('conv-title-input')
    ;(input.element as HTMLInputElement).focus()
    await input.setValue('새 제목')
    await input.trigger('keydown', { key: 'Enter' })
    await input.trigger('blur')
    await flushPromises()
    expect(source.renameChat).toHaveBeenCalledTimes(1)
    expect(source.renameChat).toHaveBeenCalledWith(A, '새 제목')
  })

  it('creates a new chat from the header', async () => {
    const { source } = await mountPanel()
    await find('conv-new-btn').trigger('click')
    await flushPromises()
    expect(source.createChat).toHaveBeenCalledWith({ projectId: 'p', groupId: 'p.m.0001' }, '새 대화')
    expect((find('conv-title-input').element as HTMLInputElement).value).toBe('새 대화')
    expect(find('conv-log-empty').exists()).toBe(true)
  })

  it('approves and rejects a command request in the timeline', async () => {
    const { source } = await mountPanel({
      activities: {
        [A]: [
          { kind: 'command', id: 'k1', createdAt: null, command: 'git diff', state: 'pending_approval', anchor: { seq: 2, position: 'before', state: 'reply' } },
          { kind: 'command', id: 'k2', createdAt: null, command: 'npm test', state: 'pending_approval', anchor: { seq: 2, position: 'before', state: 'reply' } },
        ],
      },
    })
    const [first, second] = findAll('tl-command')
    expect(first.find('[data-test="tl-command-state"]').text()).toBe('승인 대기')
    await first.find('[data-test="tl-command-approve"]').trigger('click')
    await second.find('[data-test="tl-command-reject"]').trigger('click')
    await flushPromises()
    expect(source.decideCommand).toHaveBeenCalledWith(A, 'k1', 'approve')
    expect(source.decideCommand).toHaveBeenCalledWith(A, 'k2', 'reject')
    const states = findAll('tl-command').map((c) => c.attributes('data-state'))
    expect(states).toEqual(['approved', 'rejected'])
    expect(findAll('tl-command-approve')).toHaveLength(0)
  })

  it('summarizes a work change and asks the container to open its detail', async () => {
    await mountPanel()
    const change = find('tl-change')
    expect(change.find('[data-test="tl-change-count"]').text()).toBe('2 files')
    expect(change.find('[data-test="tl-change-added"]').text()).toBe('+34')
    expect(change.find('[data-test="tl-change-removed"]').text()).toBe('−11')
    expect(change.text()).toContain('ConversationView.vue 외 1')
    await change.find('[data-test="tl-change-open"]').trigger('click')
    const emitted = wrapper!.emitted('open-change')
    expect(emitted).toHaveLength(1)
    expect((emitted![0][0] as { id: string }).id).toBe('c1')
  })

  it('sends a message, clears the draft and keeps earlier activities above it', async () => {
    const { source } = await mountPanel()
    const input = find('conv-input')
    await input.setValue('그대로 두고 다음 수정만 진행해.')
    expect(localStorage.getItem(`flowgate.user.guest.chat.drafts.${A}`)).toBe('그대로 두고 다음 수정만 진행해.')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    expect(source.postTurn).toHaveBeenCalledWith(A, expect.objectContaining({ body: '그대로 두고 다음 수정만 진행해.' }))
    expect((input.element as HTMLTextAreaElement).value).toBe('')
    expect(localStorage.getItem(`flowgate.user.guest.chat.drafts.${A}`)).toBeNull()
    const order = find('conv-log').findAll('[data-test="tl-message"], [data-test="tl-change"], [data-test="tl-command"]').map((n) => n.attributes('data-test'))
    expect(order.at(-1)).toBe('tl-message')
    expect(order.indexOf('tl-change')).toBeLessThan(order.length - 1)
  })

  it('keeps the draft and offers a retry when sending fails', async () => {
    const { source } = await mountPanel()
    source.postTurn.mockRejectedValue(Object.assign(new Error('nope'), { status: 422 }))
    const input = find('conv-input')
    await input.setValue('hello')
    await find('conv-composer').trigger('submit')
    await flushPromises()
    expect((input.element as HTMLTextAreaElement).value).toBe('hello')
    expect(find('tl-retry').exists()).toBe(true)
    expect(find('conv-notice').text()).toContain('메시지를 보내지 못했습니다')
  })

  it('selects the AI, calls it and turns the send button into STOP while it runs', async () => {
    const { source } = await mountPanel({ runs: { 'run-1': ['running'] } })
    const select = find('conv-provider-select')
    expect((select.element as HTMLSelectElement).value).toBe('opus')
    await select.setValue('sonnet')
    await find('conv-invoke-ai').trigger('click')
    await flushPromises()
    expect(source.startChatRun).toHaveBeenCalledWith(A, 'sonnet')
    const send = find('conv-send')
    expect(send.classes()).toContain('is-stop')
    expect(send.attributes('aria-label')).toBe('AI 호출 중지')
    await send.trigger('click')
    await flushPromises()
    expect(source.cancelRun).toHaveBeenCalledWith('run-1')
    expect(find('tl-status').text()).toContain('AI 호출을 시작했습니다')
  })

  it('switches the AI source mode (읽기 / 편집 / 1회 편집) through the server', async () => {
    const { source } = await mountPanel()
    const buttons = find('conv-source-mode').findAll('button')
    expect(buttons.map((b) => b.text())).toEqual(['읽기', '편집', '1회 편집'])
    expect(buttons[0].attributes('aria-pressed')).toBe('true')
    await buttons[2].trigger('click')
    await flushPromises()
    expect(source.saveChatSettings).toHaveBeenCalledWith({ source_access_mode: 'edit_once' })
    expect(find('conv-source-mode').findAll('button')[2].attributes('aria-pressed')).toBe('true')
  })

  it('saves chat settings from the inline settings panel', async () => {
    const { source } = await mountPanel()
    await find('conv-settings-btn').trigger('click')
    await find('conv-setting-send-action').setValue('copy_mention')
    await find('conv-setting-context').setValue('all')
    await find('conv-settings-save').trigger('click')
    await flushPromises()
    expect(source.saveChatSettings).toHaveBeenCalledWith({ send_action: 'copy_mention', context_mode: 'all' })
    expect(find('conv-settings').exists()).toBe(false)
  })

  it('copies the chat mention, and falls back to an inline manual copy', async () => {
    const { source } = await mountPanel()
    await find('conv-copy-mention').trigger('click')
    await flushPromises()
    expect(source.issueChatMention).toHaveBeenCalledWith(A)
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith(`@mention ${A}`)
    expect(find('conv-notice').text()).toContain('멘션을 복사했습니다')

    clipboard.copyToClipboard.mockResolvedValue(false)
    await find('conv-copy-mention').trigger('click')
    await flushPromises()
    const manual = find('conv-manual-copy')
    expect(manual.find('textarea').element.value).toBe(`@mention ${A}`)
    clipboard.copyToClipboard.mockResolvedValue(true)
    await find('conv-manual-copy-again').trigger('click')
    await flushPromises()
    expect(find('conv-manual-copy').exists()).toBe(false)
  })

  it('waits for a Flow and follows a Flow change', async () => {
    const { source, wrapper: w } = await mountPanel({}, { groupId: null })
    expect(source.listChats).not.toHaveBeenCalled()
    expect(find('conv-log-empty').text()).toBe('플로우를 열면 그 플로우의 대화가 여기에 표시됩니다.')
    expect(find('conv-new-btn').attributes('disabled')).toBeDefined()
    expect(find('conv-input').attributes('disabled')).toBeDefined()
    await w.setProps({ groupId: 'p.m.0001' })
    await flushPromises()
    expect(source.listChats).toHaveBeenCalledWith({ projectId: 'p', groupId: 'p.m.0001' })
    expect(findAll('tl-message')).toHaveLength(2)
  })

  it('reports the read position from viewport rects when the panel sits lower in the page', async () => {
    const { source } = await mountPanel()
    const log = find('conv-log').element as HTMLElement
    // The scroller starts 500px down the page and shows 300px. offsetTop is page-relative
    // here (the scroller is not the offsetParent), which is what used to defeat the check.
    log.getBoundingClientRect = () => ({ top: 500, bottom: 800, left: 0, right: 300, width: 300, height: 300, x: 0, y: 500, toJSON: () => ({}) })
    Object.defineProperty(log, 'clientHeight', { configurable: true, value: 300 })
    Object.defineProperty(log, 'scrollTop', { configurable: true, value: 0, writable: true })
    const bottoms: Record<string, number> = { '1': 640, '2': 900 }
    for (const node of Array.from(log.querySelectorAll<HTMLElement>('[data-seq]'))) {
      const bottom = bottoms[node.dataset.seq ?? ''] ?? 0
      node.getBoundingClientRect = () => ({ top: bottom - 40, bottom, left: 0, right: 300, width: 300, height: 40, x: 0, y: bottom - 40, toJSON: () => ({}) })
      Object.defineProperty(node, 'offsetTop', { configurable: true, value: bottom - 40 })
      Object.defineProperty(node, 'offsetHeight', { configurable: true, value: 40 })
    }
    source.markRead.mockClear()
    vi.useFakeTimers()
    try {
      await find('conv-log').trigger('scroll')
      vi.advanceTimersByTime(1100)
    } finally {
      vi.useRealTimers()
    }
    await flushPromises()
    // seq 1 ends at 640 (inside 500..800); seq 2 ends at 900 (below the fold).
    expect(source.markRead).toHaveBeenCalledWith(A, 1)
    expect(source.markRead).not.toHaveBeenCalledWith(A, 2)
  })

  it('read-only mode shows the conversation without write controls', async () => {
    await mountPanel({}, { readOnly: true })
    expect(find('conv-input').attributes('disabled')).toBeDefined()
    expect(find('conv-new-btn').attributes('disabled')).toBeDefined()
    expect(find('tl-command-approve').attributes('disabled')).toBeDefined()
  })
})
