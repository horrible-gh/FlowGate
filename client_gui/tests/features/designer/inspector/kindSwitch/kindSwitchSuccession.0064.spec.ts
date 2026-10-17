/**
 * flowgate.v02.0064 T#4 — the kind switch with the server's judgement (P0006 §3.1 / §3.3 / §3.5 / §6.1):
 * the document state is read with `include=root_conversion` (kindSwitchPreviewRead, mocked as a small
 * server here); a number swap keeps the short confirmation, a succession names the new document — or its
 * expected number, fixed when the switch runs — and the child documents; a refused judgement disables the
 * badge with its reason while a judgement not read yet blocks nothing; the new refusals are shown with
 * their reason (a superseded root also reads the Canvas again); a succeeded document links the document it
 * came from — a header part of its own (features/kindSwitchOrigin) read without the judgement, kept on every
 * ownership: the new document's workflow decision turns its Flow v01 and the link stays (rework).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import KindSwitchAction from '@/features/designer/inspector/features/kindSwitch/KindSwitchAction.vue'
import {
  kindSwitchAvailability,
  kindSwitchRefreshesFlow,
  type KindSwitchFailureKind,
  type KindSwitchInput,
} from '@/features/designer/inspector/features/kindSwitch/kindSwitchModel'
import { INSPECTOR_HOST, type InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { SELECTIONS, UNASSIGNED_GRAPH, UNASSIGNED_REQ_NODE, prepareInspectorI18n, selectionOf, targetOf } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream } from '../support/projectEventStream'

const convertCall = vi.fn()
const previewRead = vi.fn()
const originRead = vi.fn()
const activityRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    kindSwitchConvert: (...args: unknown[]) => convertCall(...args),
    kindSwitchPreviewRead: (...args: unknown[]) => previewRead(...args),
    kindSwitchOriginRead: (...args: unknown[]) => originRead(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
  }
})
const opened = vi.fn()
vi.mock('@/features/designer/inspector/openDocument', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/designer/inspector/openDocument')>()),
  openDocument: (...args: unknown[]) => opened(...args),
}))

const { toKindSwitchPreview, toReviewActivity, toKindSwitchResult } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

const FLOW = UNASSIGNED_GRAPH.flowId
const ROOT_R = UNASSIGNED_REQ_NODE.document!.docId
const ROOT_B = `${FLOW}.0001-B`

/** What the server judges for the next reads (the `root_conversion` item), and the document's links. */
let judgement: Record<string, unknown> | null = null
let convertedFrom: Record<string, unknown> | null = null
let refusal: unknown = null

const rowOf = (docId: string, typeCode = docId.endsWith('-B') ? 'B' : 'R') => ({
  doc_id: docId, project_id: 'flowgate', group_id: FLOW, type_code: typeCode, seq: 1, title: '새 요구사항', status: 'draft',
  doc_review_status: null, group_disposed: false, converted_from: convertedFrom, converted_to: null, live_root_doc_id: docId,
  ...(judgement ? { root_conversion: { read_at: '2026-10-16T10:04:50.007+09:00', ...judgement } } : {}),
})

const judged = (decision: string, extra: Record<string, unknown> = {}) => ({
  target_type: 'B', decision, mode: decision === 'refused' ? null : decision, refusal: null, new_doc_id: ROOT_B,
  new_doc_exact: true, child_count: 0, chained: false, live_root_doc_id: null, ...extra,
})

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

let pinia: Pinia
let wrapper: VueWrapper<any> | null = null
let intents: InspectorHostIntent[] = []

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  installProjectEventStream()
  judgement = null
  convertedFrom = null
  refusal = null
  intents = []
  for (const mock of [convertCall, previewRead, originRead, activityRead, opened]) mock.mockReset()
  previewRead.mockImplementation(async (docId: string) => toKindSwitchPreview(rowOf(docId)))
  originRead.mockImplementation(async (docId: string) => toKindSwitchPreview(rowOf(docId)).convertedFrom)
  activityRead.mockImplementation(async (groupId: string) => toReviewActivity({ ok: true, active: false, group_id: groupId }))
  convertCall.mockImplementation(async (docId: string, newType: string) => {
    if (refusal) throw refusal
    const nextId = docId.replace(/-[RB]$/, `-${newType}`)
    return toKindSwitchResult({ data: rowOf(nextId, newType), conversion: { mode: 'succession', old_doc_id: docId, new_doc_id: nextId, child_count: 3 } }, docId)
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  removeProjectEventStream()
  document.body.innerHTML = ''
})

async function mountSwitch(target: InspectorTarget = targetOf(SELECTIONS.unassigned)) {
  wrapper = mount(KindSwitchAction, {
    attachTo: document.body,
    props: { target },
    global: { plugins: [i18n, pinia], provide: { [INSPECTOR_HOST as symbol]: { request: (intent: InspectorHostIntent) => intents.push(intent) } } },
  })
  await flushPromises()
  return wrapper
}

const find = (test: string) => wrapper!.find(`[data-test="${test}"]`)
const openButton = () => find('kind-switch-open')
const confirmButtons = () => [...document.body.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions button')]
const confirmText = () => document.body.querySelector('.ui-confirm-message')?.textContent ?? ''

async function pickOther() {
  await openButton().trigger('click')
  await flushPromises()
  const to = openButton().attributes('data-to') as 'R' | 'B'
  document.body.querySelector<HTMLButtonElement>(`[data-test="kind-switch-item-${to}"]`)?.click()
  await flushPromises()
}

describe('the confirmation follows the judgement (P0006 §6.1)', () => {
  it('a number swap keeps the short confirmation, with the judged number', async () => {
    judgement = judged('number_swap')
    await mountSwitch()
    await pickOther()
    expect(previewRead).toHaveBeenCalledWith(ROOT_R)
    expect(confirmText()).toBe('요구사항 문서를 버그 문서로 바꿉니다. 문서 번호(0001-R → 0001-B)가 바뀌고, Canvas를 다시 읽은 뒤 선택이 풀립니다.')
  })

  it('a succession names the new document, the child documents and what stays', async () => {
    judgement = judged('succession', { child_count: 3 })
    await mountSwitch()
    await pickOther()
    expect(document.body.textContent).toContain('종류 전환 — 새 문서로 이어 가기')
    expect(confirmText()).toBe(
      '새 버그 문서 0001-B를 만들어 이 요구사항 문서를 이어 갑니다. · 원래 요구사항 문서 0001-R와 하위 문서 3건, 승인·실행 이력은 그대로 남습니다. · 이후 작업은 새 버그 문서를 기준으로 합니다.',
    )
  })

  it('an expected number says it is fixed when the switch runs', async () => {
    judgement = judged('succession', { new_doc_id: `${FLOW}.0005-B`, new_doc_exact: false, chained: true })
    await mountSwitch()
    await pickOther()
    expect(confirmText()).toContain('새 버그 문서 (예상 번호 0005-B, 실행 시 확정)를 만들어')
    const [, submit] = confirmButtons()
    submit.click()
    await flushPromises()
    expect(convertCall).toHaveBeenCalledWith(ROOT_R, 'B')
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
  })

  it('en / ja carry the same succession strings', async () => {
    judgement = judged('succession', { child_count: 2, new_doc_exact: false, new_doc_id: `${FLOW}.0004-B` })
    i18n.global.locale.value = 'en'
    await mountSwitch()
    await pickOther()
    expect(confirmText()).toContain('(expected number 0004-B, fixed when the switch runs)')
    expect(confirmText()).toContain('its 2 child documents')
    wrapper!.unmount()
    document.body.innerHTML = ''
    i18n.global.locale.value = 'ja'
    await mountSwitch()
    await pickOther()
    expect(confirmText()).toContain('(予定番号 0004-B、実行時に確定)')
    expect(confirmText()).toContain('下位文書 2件')
  })
})

describe('the judgement and the badge', () => {
  it.each([
    ['root_superseded', { live_root_doc_id: ROOT_B }, '이미 다른 문서로 전환되어 이어진 문서입니다. 이어 가는 문서에서 전환하세요. Canvas를 다시 읽었습니다.'],
    ['root_body_unreadable', { reason: 'file_not_found', mode: 'succession' }, '문서 본문을 읽을 수 없어 전환할 수 없습니다. 본문을 먼저 복구하거나 파일 권한·인코딩을 확인하세요.'],
    ['front_matter_identity_unsafe', { reason: 'duplicate_key', field: 'type' }, '문서 머리말의 종류·번호를 안전하게 바꿀 수 없어 전환할 수 없습니다. 머리말을 고친 뒤 다시 전환하세요.'],
    ['attachment_target_rows_exist', { target_doc_id: ROOT_B, mode: 'succession' }, '첨부 파일 때문에 전환할 수 없습니다. 아무것도 바뀌지 않았습니다. 첨부를 확인한 뒤 다시 전환하세요.'],
    ['decision_taken', {}, '이미 단계 진행이 정해진 문서라 전환이 거절되었습니다. Canvas를 다시 읽었습니다.'],
  ])('a refused judgement (%s) disables the badge with its reason', async (code, extra, text) => {
    judgement = judged('refused', { new_doc_id: null, new_doc_exact: false, refusal: { code, ...extra } })
    await mountSwitch()
    expect(openButton().attributes('disabled')).toBeDefined()
    expect(openButton().attributes('title')).toBe(text)
  })

  it('no judgement yet (or none from an older server) blocks nothing', async () => {
    previewRead.mockImplementation(() => new Promise(() => undefined))
    await mountSwitch()
    expect(openButton().attributes('disabled')).toBeUndefined()
    wrapper!.unmount()
    previewRead.mockImplementation(async (docId: string) => toKindSwitchPreview(rowOf(docId)))   // no root_conversion
    await mountSwitch()
    expect(openButton().attributes('disabled')).toBeUndefined()
    await pickOther()
    expect(confirmText()).toContain('문서 번호(0001-R → 0001-B)')
  })

  it('a judgement for the other kind is not used', async () => {
    judgement = judged('refused', { target_type: 'R', refusal: { code: 'root_superseded' } })
    await mountSwitch()
    expect(openButton().attributes('disabled')).toBeUndefined()
  })
})

describe('refusals when the switch runs (P0006 §3.5)', () => {
  it.each([
    ['root_superseded', 'superseded', true],
    ['root_body_unreadable', 'body_unreadable', false],
    ['front_matter_identity_unsafe', 'identity_unsafe', false],
    ['attachments_changed', 'attachment', false],
    ['attachment_path_unsafe', 'attachment', false],
  ])('409 %s shows failure.%s (Canvas read again: %s)', async (code, kind, refresh) => {
    refusal = axiosError(409, { error: code, detail: 'x', doc_id: ROOT_R })
    await mountSwitch()
    await pickOther()
    confirmButtons()[1].click()
    await flushPromises()
    const line = find('kind-switch-line')
    expect(line.attributes('data-kind')).toBe('failure')
    expect(line.text()).toBe(i18n.global.t(`gui.inspector.kindSwitch.failure.${kind}`))
    expect(intents).toEqual(refresh ? [{ kind: 'refresh-flow', flowId: FLOW }] : [])
  })
})

describe('the document a succeeded root came from (P0006 §3.3, in the Frame with the real registry)', () => {
  const B_NODE: FlowGraphNode = { ...UNASSIGNED_REQ_NODE, id: `doc:${ROOT_B}`, docType: 'B', document: { ...UNASSIGNED_REQ_NODE.document!, docId: ROOT_B, typeCode: 'B' } }
  /** The succeeded B root on a Flow of the given ownership (v01: after its own workflow decision). */
  const bOn = (graph: Pick<FlowGraph, 'ownership' | 'viewMode'>) => selectionOf({ ...UNASSIGNED_GRAPH, ...graph, nodes: [B_NODE] }, B_NODE.id)

  async function mountFrame(selection: ReturnType<typeof selectionOf>) {
    wrapper = mount(DesignerInspector, { attachTo: document.body, props: { selection }, global: { plugins: [i18n, pinia] } })
    await flushPromises()
    return wrapper
  }

  const links = () => wrapper!.get('[data-test="designer-inspector-header"]').findAll('[data-test="kind-switch-converted-from"]')

  beforeEach(() => {
    convertedFrom = { doc_id: ROOT_R, mode: 'succession', at: '2026-10-16T10:20:44+09:00', by: 'u_hong', children: 3 }
  })

  it.each([
    ['unassigned', { ownership: 'unassigned', viewMode: 'draft' }, true],
    ['v01', { ownership: 'v01', viewMode: null }, false],
  ] as const)('on a %s Flow the header links it once and opens it (switch shown: %s)', async (_label, graph, switchShown) => {
    await mountFrame(bOn(graph))
    expect(links()).toHaveLength(1)
    expect(links()[0].text()).toBe('전환 전 문서: 0001-R')
    expect(find('inspector-kind-switch').exists()).toBe(switchShown)
    expect(originRead).toHaveBeenCalledWith(ROOT_B)
    await links()[0].trigger('click')
    expect(opened).toHaveBeenCalledWith(ROOT_R)
  })

  it('stays when the new document takes its workflow decision (unassigned -> v01)', async () => {
    await mountFrame(bOn({ ownership: 'unassigned', viewMode: 'draft' }))
    expect(links()).toHaveLength(1)
    await wrapper!.setProps({ selection: bOn({ ownership: 'v01', viewMode: null }) })
    await flushPromises()
    expect(find('inspector-kind-switch').exists()).toBe(false)
    expect(links()).toHaveLength(1)
    await links()[0].trigger('click')
    expect(opened).toHaveBeenCalledWith(ROOT_R)
  })

  it('is absent without a succession, on any ownership', async () => {
    convertedFrom = null
    await mountFrame(bOn({ ownership: 'unassigned', viewMode: 'draft' }))
    expect(links()).toHaveLength(0)
    wrapper!.unmount()
    await mountFrame(bOn({ ownership: 'v01', viewMode: null }))
    expect(links()).toHaveLength(0)
  })

  it('the switch alone does not draw it', async () => {
    await mountSwitch(targetOf(bOn({ ownership: 'unassigned', viewMode: 'draft' })))
    expect(find('inspector-kind-switch').exists()).toBe(true)
    expect(find('kind-switch-converted-from').exists()).toBe(false)
  })
})

describe('kindSwitchModel', () => {
  const input = (refusalKind: KindSwitchFailureKind | null): KindSwitchInput => ({
    cardKind: 'requirement', docType: 'R', hasDocument: true, ownership: 'unassigned', readOnly: null, aiRunning: false,
    groupDisposed: false, busy: false, refusal: refusalKind,
  })

  it('a judged refusal is the last reason, after the existing ones', () => {
    expect(kindSwitchAvailability(input('superseded')).switch).toEqual({ visible: true, enabled: false, reason: 'failure.superseded' })
    expect(kindSwitchAvailability({ ...input('superseded'), groupDisposed: true }).switch.reason).toBe('reason.disposed')
    expect(kindSwitchAvailability(input(null)).switch).toEqual({ visible: true, enabled: true, reason: null })
  })

  it('only a superseded root (of the new kinds) reads the Canvas again', () => {
    expect(kindSwitchRefreshesFlow('superseded')).toBe(true)
    for (const kind of ['body_unreadable', 'identity_unsafe', 'attachment'] as const) expect(kindSwitchRefreshesFlow(kind)).toBe(false)
  })
})
