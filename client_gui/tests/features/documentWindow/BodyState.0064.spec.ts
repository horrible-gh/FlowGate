import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { documentContentRead, toDocumentRecord } from '@/features/document/api/document'
import { lineWarns, toCardLineRecord, UNREADABLE_BODY_STATES } from '@/features/document/api/cardLine'
import { toCardDocument } from '@/features/project/flowCanvasApi'
import { changedFlowOf } from '@/features/project/flowCanvasEvents'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import { slotLineCard } from '@/features/designer/inspector/parts/slotStateView'
import InspectorSlotRow from '@/features/designer/inspector/parts/InspectorSlotRow.vue'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import MdViewer from '@/features/documentWindow/MdViewer.vue'
import { DocumentWindows, closeAllDocumentWindows, openDocumentWindow } from '@/features/documentWindow'
import { setCurrentUser } from '@/core/auth/currentUser'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), patchRequest: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => ({ ...await importOriginal<typeof import('@shared/api')>(), ...api }))
registerAppMessages(i18n)

const DOC = 'flowgate.v02.0064.0001-R'
const wrappers: VueWrapper<any>[] = []
const failure = (status: number, data: object) => ({ isAxiosError: true, response: { status, data } })
const el = (w: VueWrapper<any>, id: string) => w.find(`[data-test="${id}"]`)
beforeEach(() => {
  api.getRequest.mockReset()
  api.patchRequest.mockReset()
  i18n.global.locale.value = 'ko'
  setCurrentUser(null)
  closeAllDocumentWindows()
})
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount())
  closeAllDocumentWindows()
  setCurrentUser(null)
})

describe('0064 body state wire contracts', () => {
  it.each(['file_path_missing', 'file_not_found', 'path_unresolved'] as const)('404 %s is unreadable, not a blank document', async (reason) => {
    api.getRequest.mockRejectedValue(failure(404, { detail: 'unchanged', reason }))
    expect(await documentContentRead(DOC)).toEqual({ docId: DOC, content: '', missing: true, state: 'unreadable', reason })
  })
  it('keeps old 404 behavior and throws other errors', async () => {
    api.getRequest.mockRejectedValue(failure(404, { detail: 'missing' }))
    expect(await documentContentRead(DOC)).toEqual({ docId: DOC, content: '', missing: true })
    for (const status of [403, 500]) {
      const error = failure(status, { reason: 'file_not_found' })
      api.getRequest.mockRejectedValue(error)
      await expect(documentContentRead(DOC)).rejects.toBe(error)
    }
  })
  it.each(['readable', 'file_path_missing', 'file_not_found', 'path_unresolved', 'read_error', null, undefined, 'future_state'])('preserves %s and uses the same warning for the row and card head', (state) => {
    const raw = { slot: 'requirement', doc_type: 'R', doc_id: state === null ? null : DOC, has_body: false,
      ...(state !== undefined ? { body_state: state } : {}) }
    const line = toCardDocument(raw)
    const written = toCardLineRecord(raw)!
    expect(line.bodyState).toBe(state)
    expect(written.bodyState).toBe(state)
    expect(Object.hasOwn(line, 'bodyState')).toBe(state !== undefined)
    expect(Object.hasOwn(written, 'bodyState')).toBe(state !== undefined)
    const warns = typeof state === 'string' && UNREADABLE_BODY_STATES.has(state)
    expect(lineWarns(line)).toBe(warns)
    const row = slotLineCard({ id: line.docId }, line, { changeable: true, running: () => false })
    const rowView = mount(InspectorSlotRow, { props: { name: '요구사항', id: line.docId, bodyState: row.bodyState }, global: { plugins: [i18n] } })
    wrappers.push(rowView)
    expect(el(rowView, 'inspector-slot-unreadable').exists()).toBe(warns)
    const graph = { nodes: [{ id: 'n', kind: 'requirement', title: 'R', x: 0, y: 0, cardDocuments: [line] }], edges: [] } as unknown as FlowGraph
    const data = toVueFlowNodes(graph, null)[0].data!
    expect(data.bodyUnreadable === true).toBe(warns)
    const head = mount(FlowNodeCard, { props: { id: 'n', data }, global: { plugins: [i18n], stubs: { Handle: true } } })
    wrappers.push(head)
    expect(el(head, 'designer-canvas-node-body-warning').exists()).toBe(warns)
    if (warns) expect(row.blank).toBe(false)
  })
  it('never warns for a documentless line, including a stale state', () => {
    expect(lineWarns({ docId: null, bodyState: 'read_error' })).toBe(false)
  })
  it.each(['documents', 'future_reason'])('refreshes Canvas for the %s reason', (reason) => {
    expect(changedFlowOf(JSON.stringify({ payload: { flow_id: 'flow', reason, origin_client_id: null } }))).toBe('flow')
  })
})

describe('0064 unreadable viewer and re-entry', () => {
  it.each(['ko', 'en', 'ja'] as const)('shows the reason and AI warning in %s; only authorized re-entry is offered', async (locale) => {
    i18n.global.locale.value = locale
    api.getRequest.mockRejectedValue(failure(404, { reason: 'file_path_missing' }))
    const w = mount(MdViewer, { props: { docId: DOC }, global: { plugins: [i18n] } })
    wrappers.push(w)
    await flushPromises()
    expect(el(w, 'md-viewer-unreadable').exists()).toBe(true)
    expect(el(w, 'md-viewer-unreadable').text()).toContain(i18n.global.t('gui.document.bodyState.aiUnreadable'))
    expect(w.text()).not.toContain('gui.document.bodyState')
    expect(el(w, 'md-viewer-content').exists()).toBe(false)
    expect(el(w, 'md-viewer-reenter').exists()).toBe(false)
    await w.setProps({ canReenter: true })
    await el(w, 'md-viewer-reenter').trigger('click')
    expect(w.emitted('reenter')).toHaveLength(1)
  })
  it('keeps a frontmatter-only document empty and clears warning after a successful reload', async () => {
    api.getRequest.mockRejectedValue(failure(404, { reason: 'file_not_found' }))
    const w = mount(MdViewer, { props: { docId: DOC }, global: { plugins: [i18n] } })
    wrappers.push(w)
    await flushPromises()
    api.getRequest.mockResolvedValue({ data: { content: '---\ntitle: Empty\n---\n' } })
    await w.vm.loadContent()
    await flushPromises()
    expect(el(w, 'md-viewer-unreadable').exists()).toBe(false)
    expect(el(w, 'md-viewer-content').text()).toBe('')
  })
  it('opens the existing editor and preserves input with collision recovery guidance', async () => {
    setCurrentUser({ username: 'worker', email: null, isAdmin: false })
    api.getRequest.mockImplementation(async (url: string) => {
      if (url.includes('/content?')) throw failure(404, { reason: 'file_path_missing' })
      return { data: { doc_id: DOC, type_code: 'R', title: 'R', status: 'draft', is_editable: true } }
    })
    api.patchRequest.mockRejectedValue(failure(409, { detail: 'file_exists_unrecorded' }))
    const w = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
    wrappers.push(w)
    openDocumentWindow(DOC)
    await flushPromises()
    await el(w, 'md-viewer-reenter').trigger('click')
    await flushPromises()
    await el(w, 'document-window-textarea').setValue('New body')
    await el(w, 'document-window-save').trigger('click')
    await flushPromises()
    expect(api.patchRequest).toHaveBeenCalledWith('/api/v1/documents/content', { doc_id: DOC, content: 'New body' })
    expect(el(w, 'document-window-save-error').text()).toContain('진단·복구')
    expect((el(w, 'document-window-textarea').element as HTMLTextAreaElement).value).toBe('New body')
    expect(w.find('[data-test="md-viewer-recover"]').exists()).toBe(false)
  })
  it('reads the server editability without inventing permission from a missing field', () => {
    expect(toDocumentRecord({ doc_id: DOC }).isEditable).toBeUndefined()
    expect(toDocumentRecord({ doc_id: DOC, is_editable: false }).isEditable).toBe(false)
  })
})
