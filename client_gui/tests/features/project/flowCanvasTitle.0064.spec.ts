/**
 * flowgate.v02.0064 T#4 (0064 P0006 §4.2.1 / §4.2.2 / §4.2.3 / §6.5) — the card title transport in features/project:
 *
 * - the Canvas read's `title_state` becomes `titleState: { title, titleSource, version }` — `null` as sent (a v0.1 free
 *   document / slot node) or malformed, absent when an older server sent none (no edit, no version compare);
 * - `nodeTitleSave` = PUT /api/v1/flows/{flow}/graph/nodes/{node}/title with the title only (no version), this tab's
 *   `x-canvas-client-id`, answering the saved `title_state`; a refusal keeps the server's code, params, status and sentence;
 * - the store loader `saveFlowNodeTitle` and the server Graph source's `saveNodeTitle` go through it;
 * - a `graph_canvas_changed` frame with `reason: "title"` (P0006 §4.2.4) is read like any other Canvas change: another
 *   tab's save and an AI apply (no origin) read the Flow again, this tab's own save does not (flowCanvasEvents.ts as is).
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  putRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

import { CANVAS_CLIENT_ID, FlowNodeTitleError, nodeTitleSave, toFlowCanvas, toTitleState } from '@/features/project/flowCanvasApi'
import { changedFlowOf } from '@/features/project/flowCanvasEvents'
import { useProjectStore } from '@/features/project'
import { createServerFlowGraphSource, toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'

const F = 'test2.default.0052'

const node = (key: string, extra: Record<string, unknown>) => ({
  node_key: key, identity_source: 'graph', origin: 'graph', kind: 'requirement', doc_type: 'R', title: '요구사항', state: 'draft',
  position: { x: 0, y: 0, source: 'saved' }, editable: { move: true, structure: true }, document: null, slot: null,
  graph_node: { node_type: 'document', label: '요구사항', palette_item_id: 'requirement', assignee: null, root_bound: false },
  ...extra,
})

const RAW = {
  flow_id: F,
  ownership: { state: 'graph' },
  projection: 'graph',
  nodes: [
    node('n_8c1d', { title_state: { title: null, title_source: null, version: 0 } }),
    node('n_2e7f', { title: '로그인 개선 요구 정리', title_state: { title: '로그인 개선 요구 정리', title_source: 'human', version: 3 } }),
    node('n_c0f1', { kind: 'custom', doc_type: null, title: '분기 보안 점검 메모', graph_node: { node_type: 'custom', label: '보안 점검', palette_item_id: null, assignee: null, root_bound: false }, title_state: { title: '분기 보안 점검 메모', title_source: 'human', version: 1 } }),
    node('doc:free', { identity_source: 'document', origin: 'document', graph_node: null, title_state: null }),
    // An older server: no `title_state` at all.
    node('n_old', {}),
  ],
  edges: [],
}

beforeEach(() => {
  getRequest.mockReset()
  putRequest.mockReset()
})

describe('Canvas read: title_state -> titleState (P0006 §4.2.1)', () => {
  it('keeps each bundle in camelCase, null as sent, and leaves it out when the server sent none', () => {
    const canvas = toFlowCanvas(RAW)
    const states = Object.fromEntries(canvas.nodes.map((n) => [n.nodeKey, 'titleState' in n ? n.titleState : 'absent']))
    expect(states).toEqual({
      n_8c1d: { title: null, titleSource: null, version: 0 },
      n_2e7f: { title: '로그인 개선 요구 정리', titleSource: 'human', version: 3 },
      n_c0f1: { title: '분기 보안 점검 메모', titleSource: 'human', version: 1 },
      'doc:free': null,
      n_old: 'absent',
    })
    // The card's shown title stays the server's `title` (L0006 2.5), never rebuilt here.
    expect(canvas.nodes.map((n) => n.title)).toEqual(['요구사항', '로그인 개선 요구 정리', '분기 보안 점검 메모', '요구사항', '요구사항'])
  })

  it('a malformed bundle is null (no version to compare)', () => {
    expect(toTitleState({ title: 'x', title_source: 'human' })).toBeNull()
    expect(toTitleState({ title: 'x', title_source: 'human', version: -1 })).toBeNull()
    expect(toTitleState({ title: 'x', title_source: 'human', version: 1.5 })).toBeNull()
    expect(toTitleState('x')).toBeNull()
    expect(toTitleState({ title: 7, title_source: 'ai', version: 2 })).toEqual({ title: null, titleSource: 'ai', version: 2 })
  })

  it('the Graph model keeps it as sent, and absent from an older server', () => {
    const graph = toFlowGraph(toFlowCanvas(RAW))
    expect(graph.nodes.find((n) => n.id === 'n_2e7f')?.titleState).toEqual({ title: '로그인 개선 요구 정리', titleSource: 'human', version: 3 })
    expect(graph.nodes.find((n) => n.id === 'doc:free')?.titleState).toBeNull()
    expect('titleState' in graph.nodes.find((n) => n.id === 'n_old')!).toBe(false)
  })
})

describe('nodeTitleSave (P0006 §4.2.2 / §4.2.3)', () => {
  it('PUTs the title only, with this tab’s client id, and answers the saved bundle', async () => {
    putRequest.mockResolvedValue({ data: { ok: true, flow_id: F, node_key: 'n_2e7f', title_state: { title: '로그인 개선 요구 정리', title_source: 'human', version: 4 } } })
    const saved = await nodeTitleSave(F, 'n_2e7f', '  로그인   개선 요구 정리 ')
    expect(putRequest).toHaveBeenCalledWith(
      `/api/v1/flows/${F}/graph/nodes/n_2e7f/title`,
      { title: '  로그인   개선 요구 정리 ' },
      { headers: { 'x-canvas-client-id': CANVAS_CLIENT_ID } },
    )
    // No version is sent: the server never refuses on one (L0006 2.7).
    expect(Object.keys(putRequest.mock.calls[0][1])).toEqual(['title'])
    expect(saved).toEqual({ title: '로그인 개선 요구 정리', titleSource: 'human', version: 4 })
  })

  it('encodes the Flow id and the node key in the path', async () => {
    putRequest.mockResolvedValue({ data: { title_state: { title: 'A', title_source: 'human', version: 1 } } })
    await nodeTitleSave('a/b', 'tmp x', 'A')
    expect(putRequest.mock.calls[0][0]).toBe('/api/v1/flows/a%2Fb/graph/nodes/tmp%20x/title')
  })

  it.each([
    [422, 'title_empty', {}, '제목을 입력하세요.'],
    [422, 'title_too_long', { max: 30, length: 33 }, '제목은 30자 이하여야 합니다.'],
    [404, 'node_not_found', { node_key: 'n_none' }, '카드를 찾을 수 없습니다.'],
    [422, 'node_title_not_supported', { node_key: 'doc:free' }, '이 카드는 제목을 따로 정할 수 없습니다.'],
    [409, 'node_title_not_editable', { node_key: 'n_2e7f' }, '지금은 이 카드의 제목을 바꿀 수 없습니다.'],
    [409, 'node_title_locked', { node_key: 'n_91ab' }, '실행 중인 카드의 제목은 바꿀 수 없습니다.'],
    [409, 'flow_disposed', { flow_id: F }, '폐기된 Flow는 변경할 수 없습니다.'],
  ])('a %s %s refusal keeps the server’s code, params, status and sentence', async (status, code, params, message) => {
    const transport = Object.assign(new Error('Request failed'), { response: { status, data: { ok: false, error: { code, message, params } } } })
    putRequest.mockRejectedValue(transport)
    const refusal = await nodeTitleSave(F, 'n_2e7f', 'x').catch((error: unknown) => error)
    expect(refusal).toBeInstanceOf(FlowNodeTitleError)
    expect(refusal).toMatchObject({ code, params, status, message })
    expect((refusal as Error & { cause?: unknown }).cause).toBe(transport)
  })

  it('no answer from the server: no code, the general sentence', async () => {
    putRequest.mockRejectedValue(new Error('Network Error'))
    await expect(nodeTitleSave(F, 'n_2e7f', 'x')).rejects.toMatchObject({ code: null, status: null, message: 'Failed to save the card title.' })
  })

  it('an answer without a title state is not taken as saved', async () => {
    putRequest.mockResolvedValue({ data: { ok: true } })
    await expect(nodeTitleSave(F, 'n_2e7f', 'x')).rejects.toBeInstanceOf(FlowNodeTitleError)
  })
})

describe('the store loader and the server Graph source', () => {
  it('saveFlowNodeTitle saves through the transport; the server source’s saveNodeTitle answers the bundle', async () => {
    setActivePinia(createPinia())
    const store = useProjectStore()
    putRequest.mockResolvedValue({ data: { title_state: { title: '외부 승인 대기 현황', title_source: 'human', version: 1 } } })
    expect(await store.saveFlowNodeTitle(F, 'n_c0f2', '외부 승인 대기 현황')).toEqual({ title: '외부 승인 대기 현황', titleSource: 'human', version: 1 })
    const source = createServerFlowGraphSource(store)
    expect(await source.saveNodeTitle!('p', F, 'n_c0f2', '외부 승인 대기 현황')).toEqual({ title: '외부 승인 대기 현황', titleSource: 'human', version: 1 })
    expect(putRequest).toHaveBeenCalledTimes(2)
    // A source without the loader keeps no card title (no inline edit).
    const { saveFlowNodeTitle: _omit, ...withoutTitles } = store
    expect(createServerFlowGraphSource(withoutTitles as typeof store).saveNodeTitle).toBeUndefined()
  })
})

describe('the title change frame (P0006 §4.2.4)', () => {
  const titleFrame = (origin: string | null, actor: string | null) => JSON.stringify({
    event_type: 'graph_canvas_changed',
    payload: { flow_id: F, reason: 'title', actor_user_id: actor, origin_client_id: origin, draft_version: null, revision_no: null, node_keys: ['n_2e7f'] },
    group_id: F,
    project: 'test2',
    doc_id: null,
  })

  it('another tab’s save and an AI apply read the Flow again; this tab’s own save does not', () => {
    expect(changedFlowOf(titleFrame('cvs_k3m9x2', 'u_hong'))).toBe(F)
    expect(changedFlowOf(titleFrame(null, null))).toBe(F)
    expect(changedFlowOf(titleFrame(CANVAS_CLIENT_ID, 'u_hong'))).toBeNull()
  })
})
