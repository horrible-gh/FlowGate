/**
 * flowgate.v02.0056 F2-2 — v0.2 card memos in the documents tab (O3 0009-TR §4-5; not the v0.1 M document), with
 * `@shared/api` mocked:
 * - the rules (cardMemoModel): the title 1..200 on one line, the body ≤ 20000, a change sends the changed fields
 *   only, [AI가 참조] OFF by default, every refusal sorted by error.code / the 422 field and worded in ko · en · ja;
 *   lengths in Unicode code points as the server counts them (emoji at and over the limits; a change checks only
 *   the fields it sends, so an ai_ref change of a stored memo is never refused before sending);
 * - the section: the list in written order with the AI 참조 chip, a new memo, view · edit · delete windows on
 *   ModelessShell, the list read again after every write (no server event), the [AI가 참조] hint;
 * - the refusals: 422 under its field with the server message, 404 memo_not_found (gone, read again), 409
 *   flow_disposed · card_memos_unavailable, 403 flow_access_denied;
 * - a read-only view lists without changing; a Node without a stored card draws nothing; the real registry puts
 *   the section between the card lines and the approval bar.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import CardMemoSection from '@/features/designer/inspector/features/cardMemo/CardMemoSection.vue'
import messages from '@/features/designer/inspector/features/cardMemo/messages'
import {
  CARD_MEMO_BODY_MAX,
  CARD_MEMO_FAILURE_KEYS,
  CARD_MEMO_TITLE_MAX,
  cardMemoBodyProblem,
  cardMemoChanges,
  cardMemoDraftOf,
  cardMemoFailure,
  cardMemoLength,
  cardMemoLockedReason,
  cardMemoTitleProblem,
} from '@/features/designer/inspector/features/cardMemo/cardMemoModel'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n, selectionOf } from './support/inspectorSupport'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), deleteRequest: vi.fn() }))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

const i18n = prepareInspectorI18n()

const FLOW = 'flowgate.v02.0056'
const PATH = `/api/v1/flows/${FLOW}/nodes/gn_t/memos`
/** A character beyond the BMP: one code point, two UTF-16 units. */
const EMOJI = '\u{1F600}'

const memo = (id: string, title: string, fields: Record<string, unknown> = {}) => ({
  memo_id: id,
  title,
  body: `${title} 본문`,
  ai_ref: false,
  created_by: 'u1',
  created_at: '2026-10-13T00:00:00Z',
  updated_at: '2026-10-13T00:00:00Z',
  updated_by: 'u1',
  ...fields,
})

let stored: Record<string, unknown>[] = []
let wrapper: VueWrapper<any> | null = null

const refusal = (status: number, code: string, message = '', params: Record<string, unknown> = {}) => ({
  isAxiosError: true,
  response: { status, data: { ok: false, error: { code, message, params } } },
})

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  for (const fn of Object.values(api)) fn.mockReset()
  stored = [memo('m1', '배경', { ai_ref: true }), memo('m2', '주의')]
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === PATH) return { data: { ok: true, flow_id: FLOW, node_key: 'gn_t', memos: stored } }
    throw new Error(`unexpected ${path}`)
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

function node(id = 'gn_t'): FlowGraphNode {
  return {
    id,
    kind: 'task',
    title: '작업 카드',
    state: 'draft',
    docType: 'T',
    x: 0,
    y: 0,
    definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: '작업 카드', assignee: null, rootBound: false },
    document: null,
    cardDocuments: [],
  }
}

function targetOf(viewMode = 'draft', id = 'gn_t') {
  const graph: FlowGraph = { flowId: FLOW, nodes: [node(id)], edges: [], ownership: 'graph', viewMode, draft: null }
  return toInspectorTarget(selectionOf(graph, id))!
}

async function mountSection(viewMode = 'draft', id = 'gn_t') {
  wrapper = mount(CardMemoSection, { attachTo: document.body, props: { target: targetOf(viewMode, id) }, global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

const q = (id: string, root: ParentNode = document.body) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const qa = (id: string) => [...document.body.querySelectorAll<HTMLElement>(`[data-test="${id}"]`)]
const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? null
async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}
async function type(id: string, value: string) {
  const field = q(id) as HTMLInputElement | HTMLTextAreaElement | null
  if (!field) throw new Error(`no ${id}`)
  field.value = value
  field.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}
const memoWindows = () => qa('card-memo-window')

describe('the card memo rules (cardMemoModel, flowgate.v02.0056 F2-2)', () => {
  it('the title: 1..200 characters on one line (trimmed); the body ≤ 20000', () => {
    expect([CARD_MEMO_TITLE_MAX, CARD_MEMO_BODY_MAX]).toEqual([200, 20000])
    expect(cardMemoTitleProblem('  ')).toBe('required')
    expect(cardMemoTitleProblem(' 배경 ')).toBeNull()
    expect(cardMemoTitleProblem('a'.repeat(200))).toBeNull()
    expect(cardMemoTitleProblem('a'.repeat(201))).toBe('too_long')
    expect(cardMemoTitleProblem('두\n줄')).toBe('invalid_text')
    expect(cardMemoTitleProblem('탭\t')).toBeNull()
    expect(cardMemoTitleProblem('탭\t문자')).toBe('invalid_text')
    expect(cardMemoBodyProblem('a'.repeat(20000))).toBeNull()
    expect(cardMemoBodyProblem('a'.repeat(20001))).toBe('too_long')
  })

  it('lengths are Unicode code points as the server counts them (Python len), not UTF-16 units', () => {
    expect(cardMemoLength(`${EMOJI}a`)).toBe(2)
    expect(cardMemoLength('한글')).toBe(2)
    expect(cardMemoLength('\uD800')).toBe(1)
    expect(cardMemoTitleProblem(EMOJI.repeat(101))).toBeNull()
    expect(cardMemoTitleProblem(EMOJI.repeat(200))).toBeNull()
    expect(cardMemoTitleProblem(EMOJI.repeat(201))).toBe('too_long')
    expect(cardMemoBodyProblem(EMOJI.repeat(20000))).toBeNull()
    expect(cardMemoBodyProblem(EMOJI.repeat(20001))).toBe('too_long')
  })

  it('a new memo starts with [AI가 참조] OFF; a change sends the changed fields only', () => {
    expect(cardMemoDraftOf(null)).toEqual({ title: '', body: '', aiRef: false })
    const shown = { memoId: 'm1', title: '배경', body: '본문', aiRef: false }
    expect(cardMemoChanges(shown, { title: '배경 ', body: '본문', aiRef: false })).toBeNull()
    expect(cardMemoChanges(shown, { title: '배경', body: '본문', aiRef: true })).toEqual({ aiRef: true })
    expect(cardMemoChanges(shown, { title: ' 새 제목 ', body: '새 본문', aiRef: false })).toEqual({ title: '새 제목', body: '새 본문' })
  })

  it('refusals by code and 422 field; the server message is kept when it says more', () => {
    const error = (fields: Record<string, unknown>) => ({ code: null, status: null, network: false, message: '', field: null, reason: null, ...fields })
    expect(cardMemoFailure(error({ code: 'invalid_request', status: 422, field: 'title', reason: 'too_long', message: '200자 초과' }))).toEqual({
      field: 'title',
      key: 'field.title.too_long',
      message: '200자 초과',
      reread: false,
    })
    expect(cardMemoFailure(error({ code: 'invalid_request', status: 422, field: 'ai_ref', reason: 'not_a_bool' }))).toMatchObject({ field: 'ai_ref', key: 'field.ai_ref.not_a_bool' })
    expect(cardMemoFailure(error({ code: 'invalid_request', status: 422, field: 'x', reason: 'unknown_field' }))).toMatchObject({ field: null, key: 'invalid' })
    expect(cardMemoFailure(error({ code: 'memo_not_found', status: 404 }))).toMatchObject({ key: 'memo_not_found', reread: true })
    expect(cardMemoFailure(error({ code: 'node_not_found', status: 404 }))).toMatchObject({ key: 'not_found', reread: true })
    expect(cardMemoFailure(error({ code: 'flow_disposed', status: 409, message: 'flow_disposed' }))).toEqual({ field: null, key: 'flow_disposed', message: '', reread: false })
    expect(cardMemoFailure(error({ code: 'card_memos_unavailable', status: 409 }))).toMatchObject({ key: 'unavailable' })
    expect(cardMemoFailure(error({ code: 'flow_access_denied', status: 403 }))).toMatchObject({ key: 'forbidden' })
    expect(cardMemoFailure(error({ network: true }))).toMatchObject({ key: 'network' })
    expect(cardMemoLockedReason({ readOnly: 'revision' })).toBe('locked.revision')
    expect(cardMemoLockedReason({ readOnly: null })).toBeNull()
  })

  it.each(['ko', 'en', 'ja'] as const)('every failure key and the hint have %s words', (locale) => {
    for (const key of CARD_MEMO_FAILURE_KEYS) expect(i18n.global.te(`gui.inspector.cardMemo.failure.${key}`, locale), key).toBe(true)
    expect(messages[locale].ai_ref_hint).toMatch(/8000/)
  })
})

describe('the card memo section (flowgate.v02.0056 F2-2)', () => {
  it('lists the memos in written order with the AI 참조 chip; [+] and the hint', async () => {
    await mountSection()
    expect(api.getRequest).toHaveBeenCalledWith(PATH)
    expect(qa('card-memo-item').map((item) => [item.getAttribute('data-memo'), text(q('card-memo-open', item)), !!q('card-memo-ai-ref', item)])).toEqual([
      ['m1', '배경', true],
      ['m2', '주의', false],
    ])
    expect(text(q('card-memo-count'))).toBe('2')
    expect((q('card-memo-add') as HTMLButtonElement).disabled).toBe(false)
  })

  it('a new memo: [AI가 참조] OFF by default, a missing title is refused before sending; [저장] posts and reads the list again', async () => {
    await mountSection()
    await click(q('card-memo-add'))
    expect(memoWindows().map((win) => win.getAttribute('data-mode'))).toEqual(['edit'])
    expect(q('card-memo-ai-ref-toggle')!.getAttribute('aria-checked')).toBe('false')
    expect(text(q('card-memo-ai-ref-hint'))).toContain('8000자')
    await click(q('card-memo-save'))
    expect(api.postRequest).not.toHaveBeenCalled()
    expect(text(q('card-memo-title-error'))).toBe('제목을 입력하세요.')
    await type('card-memo-title-input', ' 결정 사항 ')
    expect(q('card-memo-title-error')).toBeNull()
    await type('card-memo-body-input', '세부 내용')
    api.postRequest.mockImplementation(async () => {
      const created = memo('m3', '결정 사항', { body: '세부 내용' })
      stored = [...stored, created]
      return { data: { ok: true, memo: created } }
    })
    const reads = api.getRequest.mock.calls.length
    await click(q('card-memo-save'))
    expect(api.postRequest).toHaveBeenCalledWith(PATH, { title: '결정 사항', body: '세부 내용', ai_ref: false })
    expect(api.getRequest.mock.calls.length).toBe(reads + 1)
    expect(memoWindows().map((win) => [win.getAttribute('data-memo'), win.getAttribute('data-mode')])).toEqual([['m3', 'view']])
    expect(qa('card-memo-item').map((item) => item.getAttribute('data-memo'))).toEqual(['m1', 'm2', 'm3'])
  })

  it('view → edit: turning [AI가 참조] on sends ai_ref only', async () => {
    await mountSection()
    await click(q('card-memo-open', qa('card-memo-item')[1]))
    expect(text(q('card-memo-view-title'))).toBe('주의')
    expect(text(q('card-memo-view-ai-ref'))).toBe('AI 참조 안 함')
    await click(q('card-memo-edit'))
    api.patchRequest.mockImplementation(async () => {
      stored = [stored[0], { ...stored[1], ai_ref: true }]
      return { data: { ok: true, memo: stored[1] } }
    })
    await click(q('card-memo-ai-ref-toggle'))
    await click(q('card-memo-save'))
    expect(api.patchRequest).toHaveBeenCalledWith(`${PATH}/m2`, { ai_ref: true })
    expect(text(q('card-memo-view-ai-ref'))).toBe('AI 참조')
    expect(qa('card-memo-item').map((item) => !!q('card-memo-ai-ref', item))).toEqual([true, true])
  })

  it('a title and a body of emoji at the server limits are sent; the counter counts code points; one more is refused', async () => {
    await mountSection()
    await click(q('card-memo-add'))
    await type('card-memo-title-input', EMOJI.repeat(201))
    expect(text(q('card-memo-title-error'))).toBe('제목은 200자까지 쓸 수 있습니다.')
    await type('card-memo-body-input', EMOJI.repeat(20001))
    expect(text(q('card-memo-body-length'))).toBe('20001/20000자')
    expect(q('card-memo-body-length')!.classList.contains('is-over')).toBe(true)
    await click(q('card-memo-save'))
    expect(api.postRequest).not.toHaveBeenCalled()
    await type('card-memo-title-input', EMOJI.repeat(101))
    await type('card-memo-body-input', EMOJI.repeat(20000))
    expect(text(q('card-memo-body-length'))).toBe('20000/20000자')
    expect(q('card-memo-body-length')!.classList.contains('is-over')).toBe(false)
    expect(q('card-memo-title-error')).toBeNull()
    expect(q('card-memo-body-error')).toBeNull()
    api.postRequest.mockImplementation(async () => {
      const created = memo('m3', EMOJI.repeat(101), { body: EMOJI.repeat(20000) })
      stored = [...stored, created]
      return { data: { ok: true, memo: created } }
    })
    await click(q('card-memo-save'))
    expect(api.postRequest).toHaveBeenCalledWith(PATH, { title: EMOJI.repeat(101), body: EMOJI.repeat(20000), ai_ref: false })
  })

  it('a stored memo with a long emoji title and body: turning [AI가 참조] on sends ai_ref only, nothing is refused', async () => {
    stored = [memo('m1', EMOJI.repeat(200), { body: EMOJI.repeat(20000) }), memo('m2', '주의')]
    await mountSection()
    await click(q('card-memo-open', qa('card-memo-item')[0]))
    await click(q('card-memo-edit'))
    expect(text(q('card-memo-body-length'))).toBe('20000/20000자')
    api.patchRequest.mockImplementation(async () => {
      stored = [{ ...stored[0], ai_ref: true }, stored[1]]
      return { data: { ok: true, memo: stored[0] } }
    })
    await click(q('card-memo-ai-ref-toggle'))
    await click(q('card-memo-save'))
    expect(q('card-memo-title-error')).toBeNull()
    expect(q('card-memo-body-error')).toBeNull()
    expect(api.patchRequest).toHaveBeenCalledWith(`${PATH}/m1`, { ai_ref: true })
    expect(text(q('card-memo-view-ai-ref'))).toBe('AI 참조')
  })

  it('a change checks only the fields it sends: a stored body the editor would refuse does not stop an ai_ref change', async () => {
    stored = [memo('m1', '배경', { body: 'a'.repeat(20001) }), memo('m2', '주의')]
    await mountSection()
    await click(q('card-memo-open', qa('card-memo-item')[0]))
    await click(q('card-memo-edit'))
    api.patchRequest.mockImplementation(async () => {
      stored = [{ ...stored[0], ai_ref: true }, stored[1]]
      return { data: { ok: true, memo: stored[0] } }
    })
    await click(q('card-memo-ai-ref-toggle'))
    await click(q('card-memo-save'))
    expect(api.patchRequest).toHaveBeenCalledWith(`${PATH}/m1`, { ai_ref: true })
    expect(memoWindows()[0].getAttribute('data-mode')).toBe('view')
  })

  it('nothing changed: no request, back to the view', async () => {
    await mountSection()
    await click(q('card-memo-open', qa('card-memo-item')[0]))
    await click(q('card-memo-edit'))
    await click(q('card-memo-save'))
    expect(api.patchRequest).not.toHaveBeenCalled()
    expect(memoWindows()[0].getAttribute('data-mode')).toBe('view')
  })

  it('delete asks first, then DELETE closes the window and the list follows', async () => {
    await mountSection()
    await click(q('card-memo-open', qa('card-memo-item')[0]))
    await click(q('card-memo-delete'))
    expect(text(q('card-memo-confirm'))).toContain('이 메모를 삭제할까요?')
    expect(api.deleteRequest).not.toHaveBeenCalled()
    api.deleteRequest.mockImplementation(async () => {
      stored = stored.slice(1)
      return { data: { ok: true, memo_id: 'm1', deleted: true } }
    })
    await click(q('card-memo-confirm-delete'))
    expect(api.deleteRequest).toHaveBeenCalledWith(`${PATH}/m1`)
    expect(memoWindows()).toEqual([])
    expect(qa('card-memo-item').map((item) => item.getAttribute('data-memo'))).toEqual(['m2'])
  })

  it('422 under its field with the server message', async () => {
    await mountSection()
    await click(q('card-memo-add'))
    await type('card-memo-title-input', '제목')
    api.postRequest.mockRejectedValue(refusal(422, 'invalid_request', '본문이 깁니다', { errors: [{ field: 'body', reason: 'too_long', max: 20000 }] }))
    await click(q('card-memo-save'))
    expect(text(q('card-memo-body-error'))).toBe('본문은 20000자까지 쓸 수 있습니다. — 본문이 깁니다')
  })

  it('404 memo_not_found on a change: the memo is gone, the list is read again', async () => {
    await mountSection()
    await click(q('card-memo-open', qa('card-memo-item')[1]))
    await click(q('card-memo-edit'))
    await type('card-memo-title-input', '바뀐 제목')
    api.patchRequest.mockImplementation(async () => {
      stored = stored.slice(0, 1)
      throw refusal(404, 'memo_not_found', '', { memo_id: 'm2' })
    })
    const reads = api.getRequest.mock.calls.length
    await click(q('card-memo-save'))
    expect(api.getRequest.mock.calls.length).toBe(reads + 1)
    expect(text(q('card-memo-failure'))).toBe('메모가 이미 없습니다.')
    expect(qa('card-memo-item').map((item) => item.getAttribute('data-memo'))).toEqual(['m1'])
  })

  it.each([
    [409, 'flow_disposed', '폐기된 플로우라 메모를 바꿀 수 없습니다.'],
    [409, 'card_memos_unavailable', '서버가 카드 메모를 지원하지 않습니다.'],
    [403, 'flow_access_denied', '메모를 다룰 권한이 없습니다.'],
  ])('%s %s: told in the window with the server message', async (status, code, words) => {
    await mountSection()
    await click(q('card-memo-add'))
    await type('card-memo-title-input', '제목')
    api.postRequest.mockRejectedValue(refusal(status, code, '서버 안내'))
    await click(q('card-memo-save'))
    expect(text(q('card-memo-failure'))).toBe(`${words} — 서버 안내`)
    expect(memoWindows()[0].getAttribute('data-mode')).toBe('edit')
  })

  it('a failed list read says why and retries', async () => {
    api.getRequest.mockRejectedValueOnce(refusal(403, 'flow_access_denied'))
    await mountSection()
    expect(text(q('card-memo-read-error'))).toContain('메모를 다룰 권한이 없습니다.')
    await click(q('card-memo-retry'))
    expect(qa('card-memo-item')).toHaveLength(2)
  })

  it('a read-only view lists the memos but changes nothing', async () => {
    await mountSection('revision')
    expect(qa('card-memo-item')).toHaveLength(2)
    expect((q('card-memo-add') as HTMLButtonElement).disabled).toBe(true)
    expect(q('card-memo-add')!.getAttribute('title')).toBe('확정본 보기에서는 메모를 바꿀 수 없습니다')
    await click(q('card-memo-open', qa('card-memo-item')[0]))
    expect((q('card-memo-edit') as HTMLButtonElement).disabled).toBe(true)
    expect((q('card-memo-delete') as HTMLButtonElement).disabled).toBe(true)
  })

  it('a card not saved yet draws nothing and reads nothing', async () => {
    await mountSection('draft', 'tmp_new')
    expect(q('card-memo')).toBeNull()
    expect(api.getRequest).not.toHaveBeenCalled()
  })
})
