import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import {
  buildQuickBody,
  escapeQuickTitle,
  parseQuickBody,
  quickBlock,
  type QuickMarkupItem,
} from '@/features/conversation/quickMessages/quickMarkup'
import type { ConversationTurn } from '@/features/conversation/types'

// flowgate.v02test.0072 card 4 (R rev 3, 시안 v7): a message sent with quick messages shows only the typed text, and
// under it the small titles in their kind colour; the AI still gets the quick message text (the markers are part of
// the body). Old messages and broken markers are shown as written.
registerAppMessages(i18n)

const clipboard = vi.hoisted(() => ({ copyToClipboard: vi.fn(async (_text: string, _host?: HTMLElement | null) => true) }))
vi.mock('@/core/interaction/clipboard', () => clipboard)

const STICKY: QuickMarkupItem = { kind: 'sticky', title: 'Dev2확인', message: '서버 정보를 확인해 주세요.\n둘째 줄' }
const ONCE: QuickMarkupItem = { kind: 'once', title: '반려정리', message: '반려 사유를 정리해 주세요.' }

/** What the server stores: NFC, CRLF -> LF, line ends and the whole body trimmed (normalize_body). */
const stored = (body: string) => body.normalize('NFC').replace(/\r\n?/g, '\n').split('\n').map((l) => l.trimEnd()).join('\n').trim()

describe('quick message markers (build / parse)', () => {
  it('round-trips the typed text and several quick messages in order', () => {
    const body = buildQuickBody('요약해 줘', [STICKY, ONCE])
    expect(body).toBe(
      '요약해 줘\n\n<!--fg-quick kind="sticky" title="Dev2확인"-->\n서버 정보를 확인해 주세요.\n둘째 줄\n<!--/fg-quick-->' +
      '\n\n<!--fg-quick kind="once" title="반려정리"-->\n반려 사유를 정리해 주세요.\n<!--/fg-quick-->',
    )
    expect(parseQuickBody(body)).toEqual({ text: '요약해 줘', quick: [STICKY, ONCE] })
    expect(parseQuickBody(stored(body))).toEqual({ text: '요약해 줘', quick: [STICKY, ONCE] })
    expect(parseQuickBody(body.replace(/\n/g, '\r\n'))).toEqual({ text: '요약해 줘', quick: [STICKY, ONCE] })
  })

  it('keeps the quick message text unchanged in the body (what the AI reads)', () => {
    const body = buildQuickBody('질문', [STICKY])
    expect(body).toContain(`\n${STICKY.message}\n`)
  })

  it('a send with quick messages only starts with the first block and parses to an empty text', () => {
    const body = buildQuickBody('   ', [ONCE])
    expect(body).toBe(quickBlock(ONCE))
    expect(parseQuickBody(body)).toEqual({ text: '', quick: [ONCE] })
  })

  it('escapes quotes, --> , & and line breaks in a title so it can never close the marker', () => {
    const odd: QuickMarkupItem = { kind: 'once', title: 'a"b-->c&<d>\ne', message: '문구' }
    expect(escapeQuickTitle(odd.title)).toBe('a&quot;b--&gt;c&amp;&lt;d&gt;&#10;e')
    const body = buildQuickBody('x', [odd])
    expect(body.split('-->').length).toBe(3) // only the open and the close marker end in -->
    expect(parseQuickBody(body)).toEqual({ text: 'x', quick: [odd] })
    expect(parseQuickBody(buildQuickBody('', [{ ...odd, title: '&amp;' }]))?.quick[0].title).toBe('&amp;')
  })

  it('a body without markers (old messages, card 2 messages) is not parsed', () => {
    expect(parseQuickBody('그냥 메시지')).toBeNull()
    expect(parseQuickBody('요약해 줘\n근거를 붙여 주세요.')).toBeNull()
    expect(parseQuickBody('')).toBeNull()
    expect(parseQuickBody(null)).toBeNull()
  })

  it('a broken or unpaired marker is not parsed (the body is shown as written)', () => {
    const good = buildQuickBody('요약', [STICKY, ONCE])
    expect(parseQuickBody(good.replace('<!--/fg-quick-->\n\n<!--fg-quick kind="once"', '\n<!--fg-quick kind="once"'))).toBeNull()
    expect(parseQuickBody(good.slice(0, good.lastIndexOf('<!--/fg-quick-->')))).toBeNull()
    expect(parseQuickBody(`${good}\n뒤에 쓴 글`)).toBeNull()
    expect(parseQuickBody(good.replace('kind="once"', 'kind="weird"'))).toBeNull()
    expect(parseQuickBody(good.replace('title="반려정리"', 'title="반려"정리"'))).toBeNull()
    expect(parseQuickBody(`<!--/fg-quick-->\n${quickBlock(ONCE)}`)).toBeNull()
    expect(parseQuickBody(`앞 <!--fg-quick kind="once" title="t"-->\n문구\n<!--/fg-quick-->`)).toBeNull()
    expect(parseQuickBody(quickBlock({ kind: 'once', title: 't', message: 'a\n<!--/fg-quick-->\nb' }))).toBeNull()
    expect(parseQuickBody('<!--fg-quick kind="once" title="t"-->\n\n<!--/fg-quick-->')).toBeNull()
  })
})

const userTurn = (body: string): ConversationTurn => ({ seq: 7, speaker: 'user', body, createdAt: '2026-11-22T09:00:00Z', displayName: null })

let wrappers: VueWrapper[] = []
function mountMessage(turn: ConversationTurn, props: Record<string, unknown> = {}) {
  const wrapper = mount(TimelineMessage, {
    props: { turn, pending: false, failed: false, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  wrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  clipboard.copyToClipboard.mockReset()
  clipboard.copyToClipboard.mockResolvedValue(true)
})

afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
})

describe('the sent bubble', () => {
  it('shows only the typed text, then the titles in row order in their kind colour, with the text on hover', () => {
    const w = mountMessage(userTurn(stored(buildQuickBody('뮤ㅇ', [STICKY, ONCE]))))
    expect(w.get('[data-test="tl-user-text"]').text()).toBe('뮤ㅇ')
    expect(w.text()).not.toContain('서버 정보를 확인해 주세요.')
    expect(w.text()).not.toContain('fg-quick')
    const titles = w.findAll('[data-test="tl-quick-title"]')
    expect(titles.map((t) => t.text())).toEqual(['Dev2확인', '반려정리'])
    expect(titles.map((t) => t.attributes('data-kind'))).toEqual(['sticky', 'once'])
    expect(titles[0].classes()).toContain('is-sticky')
    expect(titles[1].classes()).toContain('is-once')
    expect(titles[0].attributes('title')).toBe(STICKY.message)
    expect(titles[1].attributes('title')).toBe(ONCE.message)
  })

  it('a send with quick messages only shows the titles only', () => {
    const w = mountMessage(userTurn(buildQuickBody('', [ONCE])))
    expect(w.find('[data-test="tl-user-text"]').exists()).toBe(false)
    expect(w.findAll('[data-test="tl-quick-title"]').map((t) => t.text())).toEqual(['반려정리'])
  })

  it('the pending bubble is shown the same way', () => {
    const w = mountMessage(userTurn(buildQuickBody('질문', [ONCE])), { pending: true })
    expect(w.get('[data-test="tl-user-text"]').text()).toBe('질문')
    expect(w.findAll('[data-test="tl-quick-title"]')).toHaveLength(1)
  })

  it('an old message and a broken marker are shown as written', () => {
    const old = mountMessage(userTurn('요약해 줘\n근거를 붙여 주세요.'))
    expect(old.find('[data-test="tl-quick-msg"]').exists()).toBe(false)
    expect(old.get('.tl-text').text()).toBe('요약해 줘\n근거를 붙여 주세요.')
    const brokenBody = buildQuickBody('질문', [ONCE]).replace('<!--/fg-quick-->', '')
    const broken = mountMessage(userTurn(brokenBody))
    expect(broken.find('[data-test="tl-quick-msg"]').exists()).toBe(false)
    expect(broken.get('.tl-text').text()).toBe(brokenBody.trim())
  })

  it('⧉ copy uses the body as written, markers included', async () => {
    const body = buildQuickBody('질문', [STICKY])
    const w = mountMessage(userTurn(body))
    await w.get('[data-test="tl-copy"]').trigger('click')
    expect(clipboard.copyToClipboard).toHaveBeenCalledWith(body, expect.anything())
  })

  it('an AI message is not touched (Markdown as before)', () => {
    const w = mountMessage({ ...userTurn(buildQuickBody('x', [ONCE])), speaker: 'ai', displayName: 'Claude' })
    expect(w.find('[data-test="tl-quick-msg"]').exists()).toBe(false)
    expect(w.find('[data-test="tl-md"]').exists()).toBe(true)
  })
})
