import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), extractApiErrorMessage: vi.fn(() => '') }))
vi.mock('@shared/api', () => http)
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { fetchRun } from '@/features/conversation/api'
import { dyingMessagePreview, hiddenDyingLines } from '@/features/conversation/dyingMessage'
import TimelineActivity from '@/features/conversation/TimelineActivity.vue'
import type { AiRunState, StatusActivity } from '@/features/conversation/types'
import { aiNotificationDetail, closeAiNotificationDetail } from '@/features/designer/top/notifications/aiNotificationDetailWindow'

// flowgate.v02test.0107 F107-14: a remote CLI run that ended without a reply turn shows its last message
// (다잉메세지) clamped to 3 screen lines under the no-reply badge, and [전체 보기] opens the existing detail window.
registerAppMessages(i18n)
const W = '작업 폴더/…'
let wrapper: VueWrapper | null = null
beforeEach(() => { i18n.global.locale.value = 'ko'; for (const fn of Object.values(http)) fn.mockReset() })
afterEach(() => { wrapper?.unmount(); wrapper = null; closeAiNotificationDetail(); vi.restoreAllMocks() })

const text = (message: string) => dyingMessagePreview(message, W)?.text

describe('F107-14 preview text', () => {
  it('is null for an empty message and for one of marks only (the badge stays alone)', () => {
    expect(dyingMessagePreview(null, W)).toBeNull()
    expect(dyingMessagePreview('  \n\n ', W)).toBeNull()
    expect(dyingMessagePreview('```\n```', W)).toBeNull()
    expect(dyingMessagePreview('---\n***', W)).toBeNull()
  })
  it('drops markdown marks and keeps every content line in order', () => {
    expect(dyingMessagePreview('# 제목\n\n- **첫** 항목 `code`\n- [링크](http://x.y/z)\n```\n넷\n```\n다섯', W)).toEqual({
      lines: ['제목', '첫 항목 code', '링크', '넷', '다섯'], text: '제목 첫 항목 code 링크 넷 다섯', lastLineCut: false, totalLines: 5,
    })
  })
  it('drops emphasis marks, never the underscores of a name or a product', () => {
    expect(text('_italic_ *emphasis* __bold__ ~~gone~~')).toBe('italic emphasis bold gone')
    expect(text('snake_case_name 2*3*4')).toBe('snake_case_name 2*3*4')
    expect(text('```js\nconst x = 1\n```')).toBe('const x = 1')
  })
  it('masks token-like values and local absolute paths to their end, never a URL or an API route', () => {
    expect(text('Authorization: Bearer abc.def-123\ntoken=xyz --3-1bh33G1jCshXZougbqUbAAAA\n/home/u/repo/a.ts C:\\Users\\me\\x https://h.io/data/x'))
      .toBe(`Authorization: Bearer •••• token=•••• •••• ${W} ${W} https://h.io/data/x`)
    expect(text('/run/user/1000/flowgate/session 끝')).toBe(`${W} 끝`)
    expect(text('C:\\Program Files\\Flowgate\\session.json 끝')).toBe(`${W} 끝`)
    expect(text('Saved C:/Program Files/Flowgate/session.json')).toBe(`Saved ${W}`)
    expect(text('Saved /config.json and /.env')).toBe(`Saved ${W} and ${W}`)
    expect(text('C:/Users/me/a.txt and/or b')).toBe(`${W} and/or b`)
    expect(text('"C:\\Program Files\\My App\\a b.txt" 끝')).toBe(`"${W}" 끝`)
    expect(text('\\\\server\\share\\x.txt file:///home/u/x ~/proj/a.ts and/or b')).toBe(`${W} ${W} ${W} and/or b`)
    expect(text('/home/u/server/api/x.py /tmp')).toBe(`${W} ${W}`)
    expect(text('GET /flowgate/api/v1/inbox /api/v1/document/x ko/ja/en /help')).toBe('GET /flowgate/api/v1/inbox /api/v1/document/x ko/ja/en /help')
  })
  it('masks before the character cap, so a cut never leaves part of a token or a path', () => {
    const token = 'a1'.repeat(30)
    const p = dyingMessagePreview(`${'x'.repeat(570)} ${token} tail`, W)!
    expect(p.text).not.toContain('a1a1')
    expect(p.text.endsWith('•••• tail')).toBe(true)
    const q = dyingMessagePreview(`${'y'.repeat(590)} C:\\Program Files\\Flowgate\\session.json`, W)!
    expect(q.text).not.toMatch(/Files|Flowgate|session/)
    const r = dyingMessagePreview(`${'z'.repeat(570)} C:/Program Files/Flowgate/session.json`, W)!
    expect(r.text).not.toMatch(/Files|Flowgate|session/)
  })
})

describe('F107-14 hidden line count (measured against the 3-line clip)', () => {
  const box = (row: number, right = 10) => ({ top: row * 16, bottom: row * 16 + 16, right, width: 10, height: 16 })
  /** 3 rows of 16px, 100px wide; the clamp's "…" is 8px wide. */
  const clip = { bottom: 48, right: 100, ellipsis: 8 }
  it('counts nothing when four short lines share one screen line', () => {
    expect(hiddenDyingLines(dyingMessagePreview('a\nb\nc\nd', W)!, [[box(0)], [box(0)], [box(0)], [box(0)]], clip)).toBe(0)
  })
  it('counts a single line folded past the clip', () => {
    expect(hiddenDyingLines(dyingMessagePreview('one long line', W)!, [[box(0), box(1), box(2), box(3)]], clip)).toBe(1)
  })
  it('counts the lines after the last one shown whole, and has no answer without a layout', () => {
    const p = dyingMessagePreview('a\nb\nc\nd\ne', W)!
    expect(hiddenDyingLines(p, [[box(0)], [box(1)], [box(2)], [box(3)], [box(4)]], clip)).toBe(2)
    expect(hiddenDyingLines(p, [[], [], [], [], []], { bottom: 0, right: 0, ellipsis: 0 })).toBeNull()
  })
})

describe('F107-14 hidden line count under the clamp\'s "…" at the end of the third row', () => {
  const box = (row: number, right: number) => ({ top: row * 16, bottom: row * 16 + 16, right, width: 10, height: 16 })
  const clip = { bottom: 48, right: 100, ellipsis: 8 }
  const p = () => dyingMessagePreview('a\nb\nc\nd\ne', W)!
  it('counts the line whose end the "…" covers', () => {
    expect(hiddenDyingLines(p(), [[box(0, 90)], [box(1, 90)], [box(2, 99)], [box(3, 20)], [box(4, 20)]], clip)).toBe(3)
    // c ends inside the "…" place while d starts on the same row and runs on past the clip
    expect(hiddenDyingLines(p(), [[box(0, 90)], [box(1, 90)], [box(2, 95)], [box(2, 100), box(3, 50)], [box(4, 20)]], clip)).toBe(3)
  })
  it('keeps a line that ends before the "…", and draws no "…" without overflow', () => {
    expect(hiddenDyingLines(p(), [[box(0, 90)], [box(1, 90)], [box(2, 60)], [box(3, 20)], [box(4, 20)]], clip)).toBe(2)
    expect(hiddenDyingLines(dyingMessagePreview('a\nb\nc', W)!, [[box(0, 90)], [box(1, 90)], [box(2, 100)]], clip)).toBe(0)
  })
})

describe('F107-14 fetchRun', () => {
  const finished = { run_id: 'r1', status: 'finished', end_reason: 'exited', outcome: 'none', exit_code: 0 }
  it('carries a remote CLI run\'s last message as sent', async () => {
    http.getRequest.mockResolvedValue({ data: { ...finished, last_message: '/home/u/x\nsecond', api_turns_used: null, remote_cli: true } })
    expect((await fetchRun('r1')).lastMessage).toBe('/home/u/x\nsecond')
  })
  it('never carries a server-local CLI run\'s, an API run\'s (already a chat turn) or a blank one', async () => {
    http.getRequest.mockResolvedValue({ data: { ...finished, last_message: 'local words', api_turns_used: null, remote_cli: false } })
    expect((await fetchRun('r1')).lastMessage).toBeUndefined()
    http.getRequest.mockResolvedValue({ data: { ...finished, last_message: 'no marker', api_turns_used: null } })
    expect((await fetchRun('r1')).lastMessage).toBeUndefined()
    http.getRequest.mockResolvedValue({ data: { ...finished, last_message: 'answer', api_turns_used: 2, remote_cli: false } })
    expect((await fetchRun('r1')).lastMessage).toBeUndefined()
    http.getRequest.mockResolvedValue({ data: { ...finished, last_message: '  ', remote_cli: true } })
    expect((await fetchRun('r1')).lastMessage).toBeUndefined()
  })
})

describe('F107-14 box under the no-reply badge', () => {
  const run = (lastMessage?: string): AiRunState => ({ runId: 'r1', status: 'finished', endReason: 'exited', outcome: 'none', exitCode: 0,
    docRef: 'p.m.0001.0002-CH', providerName: 'Claude (AG)', finishedAt: '2026-11-28T00:00:00Z', ...(lastMessage ? { lastMessage } : {}) })
  const line = (code: string, lastMessage?: string): StatusActivity => ({
    kind: 'status', id: `local-status-run:c:r1:result`, chatId: 'p.m.0001.0002-CH', runId: 'r1', runResult: run(lastMessage),
    createdAt: null, tone: 'warning', code, text: null,
  })
  const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)
  /**
   * a fake layout: the paragraph clips at 3 rows of 16px and is 200px wide; `rowsOf(i)` = the rows preview
   * line i is drawn on, each box ending at `rightOf(i)` (the "…" falls back to 1em = 11px under jsdom).
   */
  function layout(rowsOf: (index: number) => number[], rightOf: (index: number) => number = () => 40): void {
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(function (this: HTMLElement) {
      if (!this.classList.contains('tl-dying-line')) return [] as unknown as DOMRectList
      const index = Array.from(this.parentElement!.querySelectorAll('.tl-dying-line')).indexOf(this)
      return rowsOf(index).map((row) => ({ top: row * 16, bottom: row * 16 + 16, right: rightOf(index), width: 40, height: 16 })) as unknown as DOMRectList
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const text = this.classList.contains('tl-dying-text')
      const bottom = text ? 48 : 0
      const right = text ? 200 : 0
      return { x: 0, y: 0, top: 0, left: 0, right, width: right, height: bottom, bottom, toJSON: () => ({}) } as DOMRect
    })
  }
  const mountLine = async (code: string, lastMessage?: string) => {
    wrapper = mount(TimelineActivity, { props: { activity: line(code, lastMessage) }, global: { plugins: [i18n] } })
    await nextTick()
    await nextTick()
  }

  it('shows the head, the clamped text, the measured count and [전체 보기], which opens the existing detail window', async () => {
    layout((i) => [i])
    await mountLine('run_no_reply', 'a\nb\nc\nd\ne')
    expect(find('tl-status').exists()).toBe(true)
    expect(find('tl-dying').text()).toContain('마지막 메세지 · 대화에 등록되지 않음')
    expect(find('tl-dying-text').text()).toBe('a b c d e')
    expect(find('tl-dying-more').text()).toBe('… 외 2줄')
    await find('tl-dying-open').trigger('click')
    expect(aiNotificationDetail.value).toMatchObject({ run_id: 'r1', doc_ref: 'p.m.0001.0002-CH', provider_name: 'Claude (AG)' })
  })
  it('shows no count when four short lines fit on one screen line', async () => {
    layout(() => [0])
    await mountLine('run_no_reply', 'a\nb\nc\nd')
    expect(find('tl-dying-more').exists()).toBe(false)
  })
  it('counts one long line folded past the third screen line', async () => {
    layout(() => [0, 1, 2, 3])
    await mountLine('run_no_reply', '긴 한 줄')
    expect(find('tl-dying-more').text()).toBe('… 외 1줄')
  })
  it('counts the third line when the "…" covers its end', async () => {
    layout((i) => [i], (i) => (i === 2 ? 198 : 40))
    await mountLine('run_no_reply', 'a\nb\nc\nd')
    expect(find('tl-dying-more').text()).toBe('… 외 2줄')
  })
  it('keeps the button for a short message, without a count', async () => {
    await mountLine('run_no_reply', '한 줄')
    expect(find('tl-dying-more').exists()).toBe(false)
    expect(find('tl-dying-open').exists()).toBe(true)
  })
  it('leaves the badge alone without a message (empty, marks only, or not a remote CLI run) and on other lines', async () => {
    await mountLine('run_no_reply')
    expect(find('tl-status').exists()).toBe(true)
    expect(find('tl-dying').exists()).toBe(false)
    wrapper!.unmount()
    await mountLine('run_no_reply', '```\n```')
    expect(find('tl-dying').exists()).toBe(false)
    wrapper!.unmount()
    await mountLine('run_failed', 'x')
    expect(find('tl-dying').exists()).toBe(false)
  })
  it.each(['ja', 'en'])('has the %s wording', (locale) => {
    for (const key of ['dying_title', 'dying_more', 'dying_open', 'dying_work_folder']) {
      expect(i18n.global.te(`gui.conversation.status.${key}`, locale)).toBe(true)
    }
  })
})
