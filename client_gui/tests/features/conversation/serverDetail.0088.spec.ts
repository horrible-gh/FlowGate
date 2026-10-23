import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { createFakeSource } from './fakeConversationSource'

// flowgate.v02.0088 F12 (rework of TR0007): the Conversation adapter keeps a diagnostic the response
// body carries even when the thrown Error repeats it, and a check the app makes itself (an answer
// without its required field, an id that is not a document id) carries no English words — the
// notice shows its localized line only.
registerAppMessages(i18n)

const api = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@shared/api')>()), ...api }))
const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async () => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

const conversationApi = await import('@/features/conversation/api')
const { ConversationRequestError } = conversationApi
const { ConversationPanel } = await import('@/features/conversation')
const { createConversationSession } = await import('@/features/conversation/conversationSession')

const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const ARCHIVED = 'The group is archived; restore it before creating a chat.'

const setLocale = (locale: 'ko' | 'ja' | 'en') => {
  ;(i18n.global.locale as unknown as { value: string }).value = locale
}

/** An axios-like refusal whose Error message repeats the body message, as a server may send it. */
const repeated = (status: number, code: string, message: string) =>
  Object.assign(new Error(message), { isAxiosError: true, response: { status, data: { ok: false, error: { code, message } } } })

let wrapper: VueWrapper | null = null

beforeEach(() => {
  setLocale('ko')
  for (const fn of Object.values(api)) fn.mockReset()
  clipboard.copyToClipboard.mockReset()
  clipboard.copyToClipboard.mockResolvedValue(true)
  localStorage.clear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setLocale('ko')
})

type RequestError = InstanceType<typeof ConversationRequestError>
const failure = (promise: Promise<unknown>) => promise.then(() => null, (e: unknown) => e as RequestError)

describe('a server diagnostic is told apart by where it came from', () => {
  it('keeps the body message when Error.message repeats it (message != code)', async () => {
    api.postRequest.mockRejectedValueOnce(repeated(409, 'group_archived', ARCHIVED))
    const error = await failure(conversationApi.createChat(SCOPE, '새 채팅'))
    expect(error).toBeInstanceOf(ConversationRequestError)
    expect(error!.message).toBe(ARCHIVED)
    expect(error!.status).toBe(409)
    expect(error!.code).toBe('group_archived')
  })

  it('still drops the transport line, a code repeated as the message and a network failure', async () => {
    api.postRequest.mockRejectedValueOnce(
      Object.assign(new Error('Request failed with status code 502'), { isAxiosError: true, response: { status: 502, data: {} } }),
    )
    expect((await failure(conversationApi.createChat(SCOPE, 'x')))!.message).toBe('')
    api.postRequest.mockRejectedValueOnce(repeated(409, 'group_archived', 'group_archived'))
    expect((await failure(conversationApi.createChat(SCOPE, 'x')))!.message).toBe('')
    api.postRequest.mockRejectedValueOnce(Object.assign(new Error('Network Error'), { isAxiosError: true, request: {} }))
    const network = await failure(conversationApi.createChat(SCOPE, 'x'))
    expect(network!.message).toBe('')
    expect(network!.status).toBeNull()
  })
})

describe('a check the app makes itself carries no words', () => {
  it('an answer without doc_id / request.request_id / mention, and an id that is not a document id', async () => {
    api.postRequest.mockResolvedValue({ data: {} })
    for (const call of [
      () => conversationApi.createChat(SCOPE, '새 채팅'),
      () => conversationApi.decideCommand(A, 'k1', 'approve'),
      () => conversationApi.issueChatMention(A),
    ]) {
      const error = await failure(call())
      expect(error).toBeInstanceOf(ConversationRequestError)
      expect(error).toMatchObject({ message: '', status: null, code: null })
    }
    api.postRequest.mockClear()
    const notAnId = await failure(conversationApi.issueChatMention('not-a-document-id'))
    expect(notAnId).toBeInstanceOf(ConversationRequestError)
    expect(notAnId).toMatchObject({ message: '', status: null, code: null })
    expect(api.postRequest).not.toHaveBeenCalled()
  })

  it('the session notices of create / decision carry no detail', async () => {
    const fake = createFakeSource({
      chats: [{ id: A, title: '빌드 확인', updatedAt: '2026-10-06T08:00:00Z' }],
      activities: { [A]: [{ kind: 'command', id: 'k1', createdAt: null, command: 'x', state: 'pending_approval' }] },
    })
    fake.source.createChat = conversationApi.createChat
    fake.source.decideCommand = conversationApi.decideCommand
    const session = createConversationSession({ source: fake.source, sleep: async () => undefined })
    await session.setScope(SCOPE)
    await flushPromises()
    api.postRequest.mockResolvedValue({ data: {} })
    expect(await session.createChat('새 채팅')).toBe(false)
    expect(session.state.notice).toMatchObject({ code: 'create_failed', detail: '' })
    await session.decideCommand('k1', 'approve')
    expect(session.state.notice).toMatchObject({ code: 'command_decision_failed', detail: '' })
    session.dispose()
  })
})

describe('the notice line (ko / ja / en)', () => {
  async function mountPanel() {
    const fake = createFakeSource({ chats: [{ id: A, title: '빌드 확인', updatedAt: new Date().toISOString() }] })
    fake.source.issueChatMention = conversationApi.issueChatMention
    wrapper = mount(ConversationPanel, {
      props: { projectId: 'p', groupId: 'p.m.0001', source: fake.source },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await flushPromises()
  }

  async function copyMention() {
    await wrapper!.find('[data-test="conv-more-btn"]').trigger('click')
    await wrapper!.find('[data-test="conv-copy-mention"]').trigger('click')
    await flushPromises()
  }

  const notice = () => wrapper!.find('[data-test="conv-notice"]').text()

  it.each([
    ['ko', '멘션을 만들지 못했습니다'],
    ['ja', 'メンションを作成できませんでした'],
    ['en', 'Could not create the mention'],
  ] as const)('an answer without its mention shows the localized notice only (%s)', async (locale, label) => {
    setLocale(locale)
    await mountPanel()
    api.postRequest.mockResolvedValue({ data: {} })
    await copyMention()
    expect(notice()).toContain(label)
    expect(notice()).not.toContain('(')
    expect(notice()).not.toMatch(/server returned/i)
  })

  it('an open notice follows a language switch, and a server diagnostic stays as it came', async () => {
    await mountPanel()
    api.postRequest.mockResolvedValue({ data: {} })
    await copyMention()
    expect(notice()).toContain('멘션을 만들지 못했습니다')
    setLocale('ja')
    await flushPromises()
    expect(notice()).toContain('メンションを作成できませんでした')
    expect(notice()).not.toContain('(')

    api.postRequest.mockReset()
    api.postRequest.mockRejectedValueOnce(repeated(409, 'group_archived', ARCHIVED))
    await copyMention()
    expect(notice()).toContain(`メンションを作成できませんでした (${ARCHIVED})`)
  })
})
