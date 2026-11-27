/**
 * flowgate.v02test.0090 (0001-R §4.3(6), rejection rework) — the work plan window opened from the document [업로드] (not from
 * the card's 「작업계획」 line) follows the server's refusal of `project.document.update` too:
 *
 * - the real DesignerInspector registers the opener (setWorkPlanEditOpener); the real document [업로드] coordinator
 *   (documentIoUpload) opens the plan's window through it, DocumentWindows draws it;
 * - a viewer (`role_limit`, developer or above needed): the window is locked with the server's sentence as the visible reason
 *   (`data-reason="permission"`), [저장] is off, the import window is not opened, and nothing is sent;
 * - a developer (nothing refused): the window opens unlocked and the import window opens as before;
 * - the refusal is the DOCUMENT's project's answer (the one the upload asked with), never the selected card's;
 * - no answer yet: nothing is locked (the server still checks the save); an answer that arrives later locks the open window,
 *   one that is taken away unlocks it; the words follow ko / en / ja.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), deleteRequest: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@shared/api')>()), ...api }))

const infoRead = vi.fn()
const planRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentInfoRead: (...args: unknown[]) => infoRead(...args),
    workPlanEditRead: (...args: unknown[]) => planRead(...args),
    settingsNodeRead: vi.fn(() => Promise.reject(new Error('offline'))),
  }
})
const { toDocumentInfo } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import { DocumentWindows } from '@/features/documentWindow'
import { documentIoDraw, documentIoUpload, resetDocumentIo } from '@/features/documentWindow/documentIo'
import { resetDocumentEditRegistry } from '@/features/documentWindow/documentEditRegistry'
import { closeAllWorkPlanEditWindows, workPlanEditWindowList, workPlanEditWindowOf } from '@/features/workPlanWindow/workPlanEditWindows'
import { grantProject, resetCapabilities } from '../../helpers/capabilities'
import { PROJECT_ID, prepareInspectorI18n, selectionOf } from '../designer/inspector/support/inspectorSupport'
import { DOC, FLOW, hiddenPlanBody, readAnswer } from './workPlanFixtures'

const i18n = prepareInspectorI18n()
const VIEWER_SENTENCE = '이 프로젝트에서 보기 전용 역할이라 할 수 없습니다(개발자 이상 필요).'
const DEVELOPER_ONLY = { reason: 'role_limit', minimum_role: 'role_developer' }
const REFUSED = { 'project.document.update': DEVELOPER_ONLY, 'project.document.create': DEVELOPER_ONLY, 'project.document.delete': DEVELOPER_ONLY }

/** A card the Inspector shows (any card: the upload does not come from its line). */
const CARD: FlowGraphNode = {
  id: 'gn_t', kind: 'task', title: '새 카드', state: 'draft', docType: 'T', x: 0, y: 0, structureEditable: true,
  definition: { nodeType: 'document', docType: 'T', paletteItemId: null, label: '새 카드', assignee: null, rootBound: false },
  document: null,
  cardDocuments: [],
}
const GRAPH: FlowGraph = { flowId: FLOW, nodes: [CARD], edges: [], ownership: 'unassigned', viewMode: 'draft', draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: null } }

let inspector: VueWrapper<any> | null = null
let windows: VueWrapper<any> | null = null
const q = (key: string): HTMLElement | null => document.body.querySelector(`[data-test="${key}"]`)

async function mountScreen() {
  inspector = mount(DesignerInspector, {
    attachTo: document.body,
    props: { selection: selectionOf(GRAPH, CARD.id), flowRunLock: () => null },
    global: { plugins: [i18n] },
  })
  windows = mount(DocumentWindows, { attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
}

/** What the document [업로드] does: draw the document's buttons, then press the upload (the real coordinator). */
async function upload(projectId: string | null = PROJECT_ID) {
  documentIoDraw(DOC)
  await flushPromises()
  await documentIoUpload(DOC, { context: { projectId, groupId: FLOW } })
  await flushPromises()
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  for (const fn of Object.values(api)) fn.mockReset()
  api.getRequest.mockImplementation(() => Promise.reject(Object.assign(new Error('offline'), { request: {} })))
  resetDocumentEditRegistry()
  resetDocumentIo()
  closeAllWorkPlanEditWindows()
  infoRead.mockReset().mockImplementation(async () => toDocumentInfo({ doc_id: DOC, type_code: 'WP', project_id: PROJECT_ID, group_id: FLOW, is_editable: true, download_available: true }))
  planRead.mockReset().mockImplementation(async () => readAnswer({ body: hiddenPlanBody() }))
})

afterEach(() => {
  inspector?.unmount()
  windows?.unmount()
  inspector = null
  windows = null
  resetDocumentIo()
  closeAllWorkPlanEditWindows()
  resetCapabilities()
  document.body.innerHTML = ''
})

describe('[업로드] of a work plan: the window it opens follows the server\'s refusal of project.document.update', () => {
  it('a viewer: the window opens locked with the server\'s sentence as the reason, [저장] is off, no import window, nothing is sent', async () => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED)
    await mountScreen()
    await upload()
    expect(workPlanEditWindowList().map((win) => win.docId)).toEqual([DOC])
    expect(workPlanEditWindowOf(DOC)!.request.refusal?.()).toBe(VIEWER_SENTENCE)
    const lock = q('wp-edit-lock')
    expect(lock).not.toBeNull()
    expect(lock!.dataset.reason).toBe('permission')
    expect(lock!.textContent).toContain(VIEWER_SENTENCE)
    expect((q('wp-edit-save') as HTMLButtonElement).disabled).toBe(true)
    expect(q('wp-json-window')).toBeNull() // locked: no import
    expect(api.putRequest).not.toHaveBeenCalled()
    expect(api.postRequest).not.toHaveBeenCalled()
  })

  it('a developer (nothing refused): unlocked, and the import window opens as before', async () => {
    grantProject(PROJECT_ID, 'role_developer')
    await mountScreen()
    await upload()
    expect(workPlanEditWindowOf(DOC)!.request.refusal?.() ?? null).toBeNull()
    expect(q('wp-edit-lock')).toBeNull()
    expect(q('wp-json-window')?.dataset.mode).toBe('import')
  })

  it('the refusal is the document\'s project\'s answer, not the selected card\'s project', async () => {
    // the selected card's project may do everything; the document's project (the one the upload asked with) is view only
    grantProject(PROJECT_ID, 'role_developer')
    grantProject('other', 'role_viewer', REFUSED)
    await mountScreen()
    await upload('other')
    expect(workPlanEditWindowOf(DOC)!.request.projectId).toBe('other')
    expect(workPlanEditWindowOf(DOC)!.request.refusal?.()).toBe(VIEWER_SENTENCE)
    expect(q('wp-edit-lock')!.dataset.reason).toBe('permission')
    // and the other way round
    closeAllWorkPlanEditWindows()
    await flushPromises()
    resetDocumentIo()
    grantProject(PROJECT_ID, 'role_viewer', REFUSED)
    grantProject('other', 'role_developer')
    await upload('other')
    expect(workPlanEditWindowOf(DOC)!.request.refusal?.() ?? null).toBeNull()
    expect(q('wp-edit-lock')).toBeNull()
  })

  it('no answer from the server for that project yet: nothing is locked (the save is still checked by the server)', async () => {
    await mountScreen()
    await upload()
    expect(workPlanEditWindowOf(DOC)!.request.refusal?.() ?? null).toBeNull()
    expect(q('wp-edit-lock')).toBeNull()
  })

  it('an answer that arrives or changes later follows: refused → locked, granted → unlocked, in ko · en · ja', async () => {
    await mountScreen()
    await upload()
    expect(q('wp-edit-lock')).toBeNull()
    grantProject(PROJECT_ID, 'role_viewer', REFUSED)
    await flushPromises()
    expect(q('wp-edit-lock')!.textContent).toContain(VIEWER_SENTENCE)
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(q('wp-edit-lock')!.textContent).toMatch(/Viewer|View/i)
    i18n.global.locale.value = 'ja'
    await flushPromises()
    expect(q('wp-edit-lock')!.textContent).toContain('開発者')
    i18n.global.locale.value = 'ko'
    grantProject(PROJECT_ID, 'role_developer')
    await flushPromises()
    expect(q('wp-edit-lock')).toBeNull()
  })

  it('without a Designer (no opener registered) the window stays run-locked, as before', async () => {
    grantProject(PROJECT_ID, 'role_viewer', REFUSED)
    windows = mount(DocumentWindows, { attachTo: document.body, global: { plugins: [i18n] } })
    documentIoDraw(DOC)
    await flushPromises()
    await documentIoUpload(DOC, { context: { projectId: PROJECT_ID, groupId: FLOW } })
    await flushPromises()
    expect(workPlanEditWindowList()).toHaveLength(0) // the button's own reason blocks it: no verdict, no window
  })
})
