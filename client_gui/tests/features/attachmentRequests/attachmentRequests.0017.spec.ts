import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.chat.0017 CH0002 — AI attachment upload requests on the client: the wire normalization, the refusal kinds,
// the chat timeline block (right below the run's AI message, refreshed by its event), the card tab badge and the
// request row's decision flow (preview / download first, keep both by default, overwrite only when chosen, a changed
// target confirmed explicitly, never followed silently).
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
const downloadBlobRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, getRequest, postRequest, putRequest, downloadBlobRequest }
})

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AttachmentRequestBulkBar from '@/features/attachmentRequests/AttachmentRequestBulkBar.vue'
import AttachmentRequestItem from '@/features/attachmentRequests/AttachmentRequestItem.vue'
import { toAttachmentActivity, toAttachmentGroups, toAttachmentRequest as toChatRequest } from '@/features/conversation/api'
import TimelineAttachments from '@/features/conversation/TimelineAttachments.vue'
import AttachmentRequestSettingsCard from '@/features/settings/components/AttachmentRequestSettingsCard.vue'
import { ATTACHMENT_REQUEST_BULK, ATTACHMENT_REQUEST_REFRESH, ATTACHMENT_REQUEST_ROW } from '@/features/conversation/types'
import { chatEventRefresh } from '@/features/conversation/conversationEvents'
import { buildTimeline } from '@/features/conversation/timeline'
import type { ConversationTurn, WorkChangeActivity } from '@/features/conversation/types'
import { attachmentsTabBadge } from '@/features/designer/inspector/features/attachments/attachmentsModel'
import {
  ATTACHMENT_REQUEST_BULK_MAX,
  attachmentRequestDecideMany,
  attachmentRequestsErrorOf,
  toAttachmentRequest,
  toAttachmentRequestGroups,
  type AttachmentRequest,
} from '@/features/document/api/attachmentRequests'

registerAppMessages(i18n)

const RAW = {
  request_id: 'aur_0123456789abcdef01234567',
  origin: 'chat',
  status: 'pending',
  project_id: 'fgp',
  flow_id: 'fgp.v02.0001',
  node_key: 'n_report',
  target_doc_id: 'fgp.v02.0001.0002-R',
  target_title: 'Report card',
  ai_run_id: 'run-1',
  provider_name: 'Claude',
  filename: 'report.md',
  original_filename: 'report.md',
  size: 2048,
  content_type: 'text/markdown',
  content_sha256: 'a'.repeat(64),
  note: 'weekly report',
  decision: null,
  conflict: null,
  preview: 'text',
  created_at: '2026-11-10T10:00:00+09:00',
}

const request = (extra: Record<string, unknown> = {}): AttachmentRequest => toAttachmentRequest({ ...RAW, ...extra })!

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  downloadBlobRequest.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('wire normalization and refusals', () => {
  it('reads a request, its conflict and a chat group', () => {
    const row = request({ conflict: { doc_id: 'd', filename: 'Report.md', case_only: true, size: 3, content_sha256: 'b'.repeat(64) } })
    expect(row.filename).toBe('report.md')
    expect(row.conflict).toEqual({ docId: 'd', filename: 'Report.md', caseOnly: true, size: 3, sha256: 'b'.repeat(64) })
    const [group] = toAttachmentRequestGroups([{ id: 'run-1', run_id: 'run-1', anchor_seq: 7, anchor_position: 'after', anchor_state: 'reply', pending: 1, requests: [RAW] }])
    expect(group.anchorSeq).toBe(7)
    expect(group.requests[0].id).toBe(RAW.request_id)
    expect(toAttachmentRequest({ request_id: 'x' })).toBeNull()
  })

  it('maps refusals to kinds, with the changed target carried', () => {
    const error = { isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'attachment_target_changed', message: 'm', details: { current_doc_id: 'new-doc', current_title: 'New' } } } } }
    expect(attachmentRequestsErrorOf(error)).toMatchObject({ kind: 'target_changed', currentDocId: 'new-doc', currentTitle: 'New' })
    const decided = { isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'attachment_request_already_decided' } } } }
    expect(attachmentRequestsErrorOf(decided).kind).toBe('already_decided')
    expect(attachmentRequestsErrorOf({ isAxiosError: true, response: { status: 403, data: {} } }).kind).toBe('forbidden')
  })
})

describe('chat timeline', () => {
  const turn = (seq: number, speaker = 'ai'): ConversationTurn => ({ seq, speaker, body: `t${seq}`, basedOnSeq: 0, createdAt: null }) as ConversationTurn

  it('puts the run block right below its AI message, after the run change', () => {
    const block = toAttachmentActivity({ id: 'run-1', runId: 'run-1', anchorSeq: 2, anchorPosition: 'after', anchorState: 'reply', createdAt: null, pending: 1, requests: [toChatRequest(RAW)!] })
    const change: WorkChangeActivity = { kind: 'change', id: 'run-1', runId: 'run-1', createdAt: null, files: [], filesChanged: 0, insertions: null, deletions: null, anchor: { seq: 2, position: 'after', state: 'reply' } }
    const items = buildTimeline([turn(1, 'user'), turn(2), turn(3, 'user')], [block, change], [])
    expect(items.map((i) => i.kind)).toEqual(['message', 'message', 'change', 'attachments', 'message'])
  })

  it('reads the chat block the same way the card row reads a request', () => {
    const [group] = toAttachmentGroups([{ id: 'run-1', run_id: 'run-1', anchor_seq: 7, anchor_position: 'after', anchor_state: 'reply', pending: 1, requests: [RAW] }])
    expect(group.requests[0]).toEqual(request())
  })

  it('draws the rows with the container component, and still lists them without one', () => {
    const block = toAttachmentActivity(toAttachmentGroups([{ id: 'run-1', run_id: 'run-1', pending: 1, requests: [RAW] }])[0])
    wrapper = mount(TimelineAttachments, { props: { activity: block }, global: { plugins: [i18n] } })
    expect(wrapper.find('[data-test="tl-attachments-pending"]').text()).toBe('승인 대기 1건')
    expect(wrapper.find('[data-test="tl-attachments-line"]').text()).toContain('report.md')
    wrapper.unmount()
    wrapper = mount(TimelineAttachments, {
      props: { activity: block },
      global: { plugins: [i18n], provide: { [ATTACHMENT_REQUEST_ROW]: { value: AttachmentRequestItem } } },
    })
    expect(wrapper.find('[data-test="ar-item"]').exists()).toBe(true)
  })

  it('reads the activities again when a request of the open chat changes', () => {
    const target = { projectId: 'fgp', groupId: 'fgp.v02.0001', chatId: 'fgp.v02.0001.0001-CH' }
    const frame = { type: 'attachment_request_updated', payload: { doc_id: target.chatId, group_id: target.groupId }, groupId: target.groupId, docId: null }
    expect(chatEventRefresh(frame, target)).toEqual({ activities: true })
    expect(chatEventRefresh({ ...frame, payload: { doc_id: 'other' } }, target)).toBeNull()
  })
})

describe('card tab badge', () => {
  it('shows waiting requests next to the number', () => {
    expect(attachmentsTabBadge(3, 2, (n) => `대기 ${n}`)).toBe('3 · 대기 2')
    expect(attachmentsTabBadge(null, 1, (n) => `대기 ${n}`)).toBe('0 · 대기 1')
    expect(attachmentsTabBadge(3, 0, (n) => `대기 ${n}`)).toBe(3)
    expect(attachmentsTabBadge(null, null, (n) => `대기 ${n}`)).toBeNull()
  })
})

describe('request row', () => {
  const mountItem = (row: AttachmentRequest, props: Record<string, unknown> = {}) => {
    wrapper = mount(AttachmentRequestItem, { props: { request: row, showTarget: true, ...props }, global: { plugins: [i18n] } })
    return wrapper
  }
  const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)

  it('previews the text content as text before deciding', async () => {
    getRequest.mockResolvedValueOnce({ data: { ok: true, kind: 'text', content: '<b>not html</b>', truncated: false } })
    mountItem(request())
    await find('ar-preview').trigger('click')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/attachment-requests/${RAW.request_id}/preview`)
    expect(find('ar-preview-text').text()).toBe('<b>not html</b>')
    expect(find('ar-preview-text').html()).not.toContain('<b>not')
  })

  it('approves with keep both against the shown document', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, request: { ...RAW, status: 'approved', final_filename: 'report.md', decision: 'keep_both' } } })
    mountItem(request())
    await find('ar-approve').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/attachment-requests/${RAW.request_id}/decision`, {
      decision: 'approve', expected_doc_id: RAW.target_doc_id, conflict: 'keep_both',
    })
    expect(find('ar-state').text()).toBe('승인됨')
    expect(wrapper!.emitted('decided')).toHaveLength(1)
  })

  it('asks keep both or overwrite on a same-name attachment and sends the shown hash', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, request: { ...RAW, status: 'approved', decision: 'overwrite' } } })
    mountItem(request({ conflict: { doc_id: RAW.target_doc_id, filename: 'report.md', case_only: false, size: 9, content_sha256: 'c'.repeat(64) } }))
    await find('ar-approve').trigger('click')
    expect(find('ar-choice').exists()).toBe(true)
    expect((find('ar-keep-both').element as HTMLInputElement).checked).toBe(true)
    await find('ar-overwrite').setValue(true)
    await find('ar-confirm').trigger('click')
    await flushPromises()
    expect(postRequest.mock.calls[0][1]).toEqual({
      decision: 'approve', expected_doc_id: RAW.target_doc_id, conflict: 'overwrite', expected_existing_sha256: 'c'.repeat(64),
    })
  })

  it('never follows a changed target silently: the person confirms the new document', async () => {
    postRequest
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'attachment_target_changed', details: { current_doc_id: 'fgp.v02.0001.0009-B', current_title: 'Moved' } } } } })
      .mockResolvedValueOnce({ data: { ok: true, request: { ...RAW, status: 'approved', final_doc_id: 'fgp.v02.0001.0009-B' } } })
    mountItem(request())
    await find('ar-approve').trigger('click')
    await flushPromises()
    expect(find('ar-retarget').text()).toContain('Moved')
    await find('ar-retarget-approve').trigger('click')
    await flushPromises()
    expect(postRequest.mock.calls[1][1]).toMatchObject({ decision: 'approve', expected_doc_id: 'fgp.v02.0001.0009-B', conflict: 'keep_both' })
  })

  it('rejects, and shows who was first when someone else decided', async () => {
    postRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { ok: false, error: { code: 'attachment_request_already_decided' } } } })
    mountItem(request())
    await find('ar-reject').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`/api/v1/attachment-requests/${RAW.request_id}/decision`, { decision: 'reject' })
    expect(find('ar-error').text()).toBe('이미 다른 사람이 처리한 요청입니다.')
  })

  it('offers the decision buttons whatever the screen mode (no read-only switch)', () => {
    mountItem(request())
    expect(find('ar-approve').attributes('disabled')).toBeUndefined()
    expect(find('ar-reject').attributes('disabled')).toBeUndefined()
  })
})

describe('bulk approve / reject', () => {
  const ids = ['aur_000000000000000000000001', 'aur_000000000000000000000002', 'aur_000000000000000000000003']
  const rows = (extra: Record<string, unknown>[] = []) => ids.map((id, i) => request({ request_id: id, filename: `f${i}.md`, ...(extra[i] ?? {}) }))
  const conflict = { doc_id: RAW.target_doc_id, filename: 'f0.md', case_only: false, size: 1, content_sha256: 'c'.repeat(64) }
  const done = (id: string, status: string) => ({ request_id: id, outcome: 'done', request: { ...RAW, request_id: id, status } })
  const mountBar = (props: Record<string, unknown>) => {
    wrapper = mount(AttachmentRequestBulkBar, { props: { selected: [], ...props }, global: { plugins: [i18n] } })
    return wrapper
  }
  const find = (sel: string) => wrapper!.find(`[data-test="${sel}"]`)

  it('appears from two waiting requests up', () => {
    mountBar({ requests: rows().slice(0, 1) })
    expect(find('ar-bulk').exists()).toBe(false)
    wrapper!.unmount()
    mountBar({ requests: rows() })
    expect(find('ar-bulk-approve').text()).toBe('전체 승인 (3)')
    expect(find('ar-bulk-reject').text()).toBe('전체 반려 (3)')
  })

  it('approves all with keep both in one call, leaving a same-name request to its row', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, decision: 'approve', results: [done(ids[1], 'approved'), done(ids[2], 'approved')], counts: { done: 2, skipped: 0, failed: 0 } } })
    mountBar({ requests: rows([{ conflict }]), expectedDocId: 'live-doc' })
    expect(find('ar-bulk-approve').text()).toBe('전체 승인 (2)')
    expect(find('ar-bulk-excluded').text()).toBe('같은 이름 1건 제외')
    await find('ar-bulk-approve').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/attachment-requests/decisions', {
      decision: 'approve',
      items: [{ request_id: ids[1], expected_doc_id: 'live-doc' }, { request_id: ids[2], expected_doc_id: 'live-doc' }],
    })
    expect(find('ar-bulk-result').text()).toBe('2건 승인')
    const [summary] = wrapper!.emitted('decided')![0] as [{ done: number; results: { request: AttachmentRequest | null }[] }]
    expect(summary.done).toBe(2)
    expect(summary.results[0].request?.status).toBe('approved')
  })

  it('acts on the ticked rows only, and asks once before rejecting', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, decision: 'reject', results: [
      done(ids[0], 'rejected'),
      { request_id: ids[2], outcome: 'skipped', ok: false, error: { code: 'attachment_request_already_decided' } },
    ] } })
    mountBar({ requests: rows(), selected: [ids[0], ids[2]] })
    expect(find('ar-bulk-approve').text()).toBe('선택 승인 (2)')
    await find('ar-bulk-reject').trigger('click')
    expect(postRequest).not.toHaveBeenCalled()
    expect(find('ar-bulk-confirm').text()).toContain('2건을 반려할까요?')
    await find('ar-bulk-reject-yes').trigger('click')
    await flushPromises()
    expect(postRequest.mock.calls[0][1]).toEqual({ decision: 'reject', items: [{ request_id: ids[0] }, { request_id: ids[2] }] })
    expect(find('ar-bulk-result').text()).toBe('1건 반려 · 1건은 개별 확인 필요')
    expect(wrapper!.emitted('update:selected')!.at(-1)).toEqual([[]])
  })

  it('selects all through the header box', async () => {
    mountBar({ requests: rows() })
    await find('ar-bulk-all').setValue(true)
    expect(wrapper!.emitted('update:selected')![0]).toEqual([ids])
  })

  it('never claims a result when the call itself failed', async () => {
    postRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: {} } })
    mountBar({ requests: rows() })
    await find('ar-bulk-approve').trigger('click')
    await flushPromises()
    expect(find('ar-bulk-result').text()).toBe('처리 결과를 확인하지 못했습니다. 목록을 다시 확인해 주세요.')
    expect(wrapper!.emitted('decided')![0]).toEqual([null])
  })

  it('sends large selections in chunks the server accepts', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, results: [] } })
    const items = Array.from({ length: ATTACHMENT_REQUEST_BULK_MAX + 20 }, (_, i) => ({ id: `aur_${String(i).padStart(24, '0')}` }))
    await attachmentRequestDecideMany('reject', items)
    expect(postRequest).toHaveBeenCalledTimes(2)
    expect((postRequest.mock.calls[0][1] as { items: unknown[] }).items).toHaveLength(ATTACHMENT_REQUEST_BULK_MAX)
    expect((postRequest.mock.calls[1][1] as { items: unknown[] }).items).toHaveLength(20)
  })

  it('the row shows a tick box only when the list offers bulk decisions and it still waits', async () => {
    wrapper = mount(AttachmentRequestItem, { props: { request: request(), selectable: true, selected: false }, global: { plugins: [i18n] } })
    await find('ar-select').setValue(true)
    expect(wrapper.emitted('select')![0]).toEqual([RAW.request_id, true])
    await wrapper.setProps({ request: request({ status: 'approved' }) })
    expect(find('ar-select').exists()).toBe(false)
  })

  it('the chat block draws the bar and shows the rows a bulk call decided', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, decision: 'approve', results: ids.map((id) => done(id, 'approved')) } })
    const raws = ids.map((id, i) => ({ ...RAW, request_id: id, filename: `f${i}.md` }))
    const block = toAttachmentActivity(toAttachmentGroups([{ id: 'run-1', run_id: 'run-1', pending: 3, requests: raws }])[0])
    wrapper = mount(TimelineAttachments, {
      props: { activity: block },
      global: {
        plugins: [i18n],
        provide: { [ATTACHMENT_REQUEST_ROW]: { value: AttachmentRequestItem }, [ATTACHMENT_REQUEST_BULK]: { value: AttachmentRequestBulkBar } },
      },
    })
    expect(wrapper.findAll('[data-test="ar-select"]')).toHaveLength(3)
    await find('ar-bulk-approve').trigger('click')
    await flushPromises()
    expect(postRequest.mock.calls[0][1]).toMatchObject({ decision: 'approve' })
    expect(wrapper.findAll('[data-test="ar-state"]').map((w) => w.text())).toEqual(['승인됨', '승인됨', '승인됨'])
    expect(find('tl-attachments-pending').exists()).toBe(false)
  })

  // CH0002 seq 22/24 review fixes.
  it('the reject confirm keeps the requests it named, whatever is ticked meanwhile', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, decision: 'reject', results: [done(ids[0], 'rejected')] } })
    mountBar({ requests: rows(), selected: [ids[0]] })
    await find('ar-bulk-reject').trigger('click')
    expect(find('ar-bulk-confirm').text()).toContain('1건을 반려할까요?')
    await wrapper!.setProps({ selected: [] })
    expect(find('ar-bulk-confirm').text()).toContain('1건을 반려할까요?')
    await find('ar-bulk-reject-yes').trigger('click')
    await flushPromises()
    expect(postRequest.mock.calls[0][1]).toEqual({ decision: 'reject', items: [{ request_id: ids[0] }] })
  })

  it('locks the rows while its confirm is open, and frees them on cancel', async () => {
    mountBar({ requests: rows() })
    expect(wrapper!.emitted('update:locked')!.at(-1)).toEqual([false])
    await find('ar-bulk-reject').trigger('click')
    expect(wrapper!.emitted('update:locked')!.at(-1)).toEqual([true])
    expect(find('ar-bulk-all').attributes('disabled')).toBeDefined()
    await find('ar-bulk-reject-no').trigger('click')
    expect(wrapper!.emitted('update:locked')!.at(-1)).toEqual([false])
  })

  it('keeps its result shown after the list emptied', async () => {
    postRequest.mockResolvedValueOnce({ data: { ok: true, decision: 'approve', results: ids.map((id) => done(id, 'approved')) } })
    mountBar({ requests: rows() })
    await find('ar-bulk-approve').trigger('click')
    await flushPromises()
    await wrapper!.setProps({ requests: [] })
    expect(find('ar-bulk-result').text()).toBe('3건 승인')
    expect(wrapper!.emitted('update:visible')!.at(-1)).toEqual([true])
  })

  it('the row locks its decisions and tick box when the list says so, not its preview', () => {
    wrapper = mount(AttachmentRequestItem, { props: { request: request(), selectable: true, selected: false, disabled: true }, global: { plugins: [i18n] } })
    expect(find('ar-approve').attributes('disabled')).toBeDefined()
    expect(find('ar-reject').attributes('disabled')).toBeDefined()
    expect(find('ar-select').attributes('disabled')).toBeDefined()
    expect(find('ar-preview').attributes('disabled')).toBeUndefined()
  })

  it('the chat block locks its rows during the confirm and reads the activities again after a failed call', async () => {
    postRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: {} } })
    const refresh = vi.fn()
    const raws = ids.map((id, i) => ({ ...RAW, request_id: id, filename: `f${i}.md` }))
    const block = toAttachmentActivity(toAttachmentGroups([{ id: 'run-1', run_id: 'run-1', pending: 3, requests: raws }])[0])
    wrapper = mount(TimelineAttachments, {
      props: { activity: block },
      global: {
        plugins: [i18n],
        provide: {
          [ATTACHMENT_REQUEST_ROW]: { value: AttachmentRequestItem },
          [ATTACHMENT_REQUEST_BULK]: { value: AttachmentRequestBulkBar },
          [ATTACHMENT_REQUEST_REFRESH]: refresh,
        },
      },
    })
    await find('ar-bulk-reject').trigger('click')
    expect(wrapper.findAll('[data-test="ar-approve"]').every((b) => b.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.findAll('[data-test="ar-select"]').every((b) => b.attributes('disabled') !== undefined)).toBe(true)
    await find('ar-bulk-reject-yes').trigger('click')
    await flushPromises()
    expect(find('ar-bulk-result').text()).toBe('처리 결과를 확인하지 못했습니다. 목록을 다시 확인해 주세요.')
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('[data-test="ar-approve"]').every((b) => b.attributes('disabled') === undefined)).toBe(true)
  })
})

describe('project settings: blocked extensions', () => {
  const view = (overrides: Record<string, unknown>) => ({
    data: { overrides, inherited: { blocked_extensions: ['exe'] }, effective: {}, sources: {}, bounds: {} },
  })
  const mountCard = async () => {
    wrapper = mount(AttachmentRequestSettingsCard, { props: { scope: 'project', projectId: 'fgp', embedded: true }, global: { plugins: [i18n] } })
    await flushPromises()
    return wrapper
  }
  const pick = (id: string) => wrapper!.find(`[data-testid="${id}"]`)
  const savedOverrides = () => (putRequest.mock.calls[0][1] as { overrides: Record<string, unknown> }).overrides

  it('an empty field follows the system list; "차단 없음" saves an empty list', async () => {
    getRequest.mockResolvedValueOnce(view({}))
    putRequest.mockResolvedValueOnce(view({ blocked_extensions: [] }))
    await mountCard()
    expect(wrapper!.find('[data-field="blocked_extensions"]').text()).toContain('비우면 시스템 설정을 따릅니다')
    expect((pick('ar-settings-no-block').element as HTMLInputElement).checked).toBe(false)
    await pick('ar-settings-no-block').setValue(true)
    expect(pick('ar-settings-blocked_extensions').attributes('disabled')).toBeDefined()
    await pick('ar-settings-save').trigger('click')
    await flushPromises()
    expect(savedOverrides().blocked_extensions).toEqual([])
    expect((pick('ar-settings-no-block').element as HTMLInputElement).checked).toBe(true)
  })

  it('a stored empty list reads back as "차단 없음"; unticking it returns to the system list', async () => {
    getRequest.mockResolvedValueOnce(view({ blocked_extensions: [] }))
    putRequest.mockResolvedValueOnce(view({}))
    await mountCard()
    expect((pick('ar-settings-no-block').element as HTMLInputElement).checked).toBe(true)
    await pick('ar-settings-no-block').setValue(false)
    await pick('ar-settings-save').trigger('click')
    await flushPromises()
    expect(savedOverrides().blocked_extensions).toBeNull()
  })
})
