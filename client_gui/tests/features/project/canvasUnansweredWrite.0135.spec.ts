/**
 * flowgate.v02.0135 T#4 — a card that disappeared without being deleted. Measured on stg: a new Flow's seed card
 * save (PUT /graph/draft) answered after ~2.5 min (the card document write gate walked every document row). The
 * GUI gave up at 30 s, read the Flow again as stored (not committed yet: no card) and so dropped the card from the
 * screen; minutes later the server stored it and announced it with this tab's `origin_client_id`, which this tab
 * dropped as "already applied" — the card stayed invisible until a reload.
 *
 * A write that got no answer is now kept by what it stores (a draft save by the draft version it makes, a layout
 * save by the cards it moves) until the own change event of exactly that write arrives; other own events (a title,
 * a layout while a draft is still open, an older draft version) are dropped as before and settle nothing.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const putRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: vi.fn(),
  putRequest: (...args: unknown[]) => putRequest(...args),
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CLIENT_ID, saveFlowCanvasLayout, unansweredCanvasWrites } from '@/features/project/flowCanvasApi'
import { changedFlowOf } from '@/features/project/flowCanvasEvents'
import { saveFlowGraphDraft } from '@/features/project/flowGraphApi'

let flowNo = 0
let F = ''
const event = (flowId: string, payload: Record<string, unknown>, origin: string | null = CANVAS_CLIENT_ID) =>
  JSON.stringify({ type: 'graph_canvas_changed', payload: { flow_id: flowId, origin_client_id: origin, draft_version: null, node_keys: [], ...payload } })
const ownDraft = (flowId: string, version: number) => event(flowId, { reason: 'draft', draft_version: version })
const ownLayout = (flowId: string, keys: string[]) => event(flowId, { reason: 'layout', node_keys: keys })
const ownTitle = (flowId: string, key: string) => event(flowId, { reason: 'title', node_keys: [key] })
const timeout = () => Object.assign(new Error('timeout of 30000ms exceeded'), { isAxiosError: true, code: 'ECONNABORTED' })
const refused = (status: number) => Object.assign(new Error('refused'), {
  isAxiosError: true, response: { status, data: { error: { code: 'draft_version_conflict', message: 'conflict' } } },
})
const draft = (version: number) => ({ draftVersion: version, nodes: [{ nodeKey: null, clientKey: 'tmp_1', nodeType: 'document', docType: 'R', paletteItemId: null, label: '요구사항', assignee: null }], edges: [] })
const layout = (keys: string[]) => ({ positions: keys.map((nodeKey, i) => ({ nodeKey, x: 100 * i, y: 50 })), viewport: null })

async function lostDraft(flowId: string, base: number) {
  putRequest.mockRejectedValueOnce(timeout())
  await expect(saveFlowGraphDraft(flowId, draft(base) as never)).rejects.toThrow()
}
async function lostLayout(flowId: string, keys: string[]) {
  putRequest.mockRejectedValueOnce(timeout())
  await expect(saveFlowCanvasLayout(flowId, layout(keys))).rejects.toThrow()
}

beforeEach(() => {
  putRequest.mockReset()
  flowNo += 1
  F = `f.unanswered.${flowNo}` // every test its own Flow: the record is module state
})

describe('own change events after a Canvas write without an answer', () => {
  it('an own event is dropped as before when every write was answered; another tab is always read', () => {
    expect(changedFlowOf(ownDraft(F, 1))).toBeNull()
    expect(changedFlowOf(event(F, { reason: 'draft', draft_version: 1 }, 'other-tab'))).toBe(F)
  })

  it('a timed-out seed draft save: exactly its own commit event is read again, once; other Flows unaffected', async () => {
    await lostDraft(F, 0)
    expect(unansweredCanvasWrites(F)).toEqual([{ kind: 'draft', draftVersion: 1 }])
    expect(changedFlowOf(ownDraft('f.other', 1))).toBeNull()
    expect(changedFlowOf(ownDraft(F, 1))).toBe(F)
    expect(changedFlowOf(ownDraft(F, 1))).toBeNull()
    expect(unansweredCanvasWrites(F)).toEqual([])
  })

  it('an own title event first does not use up the lost draft (review: mark -> title -> draft)', async () => {
    await lostDraft(F, 0)
    expect(changedFlowOf(ownTitle(F, 'gn_1'))).toBeNull()
    expect(changedFlowOf(ownDraft(F, 1))).toBe(F)
  })

  it('a lost layout and a lost draft: the layout commit first reads once, the later draft commit reads again', async () => {
    await lostLayout(F, ['gn_old'])
    await lostDraft(F, 1)
    expect(changedFlowOf(ownLayout(F, ['gn_old']))).toBe(F) // read: the draft is not committed yet
    expect(unansweredCanvasWrites(F)).toEqual([{ kind: 'draft', draftVersion: 2 }])
    expect(changedFlowOf(ownDraft(F, 2))).toBe(F) // the late draft commit is still shown
    expect(changedFlowOf(ownDraft(F, 2))).toBeNull()
  })

  it('a layout event of other cards, or an older draft version, settles nothing', async () => {
    await lostLayout(F, ['gn_a'])
    await lostDraft(F, 3)
    expect(changedFlowOf(ownLayout(F, ['gn_b']))).toBeNull()
    expect(changedFlowOf(ownDraft(F, 3))).toBeNull() // version 3 was made by an answered save before
    expect(unansweredCanvasWrites(F)).toHaveLength(2)
    expect(changedFlowOf(ownDraft(F, 4))).toBe(F)
    expect(changedFlowOf(ownLayout(F, ['gn_a', 'gn_c']))).toBe(F)
    expect(unansweredCanvasWrites(F)).toEqual([])
  })

  it('two lost saves of the same draft (retry after the re-read): one commit settles both', async () => {
    await lostDraft(F, 0)
    await lostDraft(F, 0)
    expect(unansweredCanvasWrites(F)).toHaveLength(2)
    expect(changedFlowOf(ownDraft(F, 1))).toBe(F)
    expect(unansweredCanvasWrites(F)).toEqual([])
  })

  it('a later answered draft save makes the lost one moot (it can no longer land)', async () => {
    await lostDraft(F, 0)
    putRequest.mockResolvedValueOnce({ data: { draft_version: 1, key_map: {} } })
    await saveFlowGraphDraft(F, draft(0) as never)
    expect(unansweredCanvasWrites(F)).toEqual([])
    expect(changedFlowOf(ownDraft(F, 1))).toBeNull()
  })

  it('a refused save (the server answered) and a viewport-only layout record nothing', async () => {
    putRequest.mockRejectedValueOnce(refused(409))
    await expect(saveFlowGraphDraft(F, draft(0) as never)).rejects.toMatchObject({ code: 'draft_version_conflict', status: 409 })
    putRequest.mockRejectedValueOnce(timeout())
    await expect(saveFlowCanvasLayout(F, { positions: [], viewport: { x: 1, y: 2, zoom: 1 } })).rejects.toThrow()
    expect(unansweredCanvasWrites(F)).toEqual([])
    expect(changedFlowOf(ownDraft(F, 1))).toBeNull()
  })
})
