/**
 * flowgate.v02.0074 T#2 — the document upload / download of a work plan (WP) in the coordinator (documentIo), for every
 * place that draws the group (D#1 §6.3 / §7.5, R §1.4, §5-3), with `@/features/document` mocked:
 * - which document is a work plan: its read type when READY, else its id `…-WP`;
 * - the download: the read view's `body` only (no read view fields), JSON.stringify(body, null, 2) + '\n', `<docId>.json`,
 *   application/json — never the Markdown download; a failure saves no file and says why with the existing keys; unsaved
 *   edits of the plan's window are not in the file and are told;
 * - the upload: the plan window's [JSON 가져오기] is asked for (an open window reused), never a file pick or the content
 *   save, also before the document is read; off with the plan window's reasons (locked, Step windows holding edits) or
 *   refused.
 */
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

const contentSave = vi.fn()
const download = vi.fn()
const infoRead = vi.fn()
const planRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    documentContentSave: (...args: unknown[]) => contentSave(...args),
    documentDownload: (...args: unknown[]) => download(...args),
    documentInfoRead: (...args: unknown[]) => infoRead(...args),
    workPlanEditRead: (...args: unknown[]) => planRead(...args),
  }
})

const { toDocumentInfo } = await vi.importActual<typeof import('@/features/document')>('@/features/document')

import { resetDocumentEditRegistry } from '@/features/documentWindow/documentEditRegistry'
import {
  documentIoButtons,
  documentIoDownload,
  documentIoDraw,
  documentIoInfo,
  documentIoIsWorkPlan,
  documentIoLeave,
  documentIoNotices,
  documentIoReadFile,
  documentIoUpload,
  resetDocumentIo,
} from '@/features/documentWindow/documentIo'
import { bodyOf, draftOf, editBodyOf } from '@/features/workPlanWindow/workPlanEditModel'
import {
  closeAllWorkPlanEditWindows,
  openWorkPlanEditWindow,
  setWorkPlanEditOpener,
  setWorkPlanEditWindowDirty,
  setWorkPlanStepEditHandler,
  workPlanEditWindowList,
  workPlanEditWindowOf,
} from '@/features/workPlanWindow/workPlanEditWindows'
import { workPlanBodyJsonText, workPlanJsonText } from '@/features/workPlanWindow/workPlanJson'
import { DOC, FLOW, hiddenPlanBody, readAnswer } from '../workPlanWindow/workPlanFixtures'

const TR = `${FLOW}.0007-TR`

const http = (status: number, data: unknown = { detail: 'x' }) => ({ isAxiosError: true, response: { status, data } })
const network = () => ({ isAxiosError: true, request: {} })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

/** What the document read answers per document (type_code, is_editable, …). */
const records: Record<string, Record<string, unknown>> = {}
const access: Record<string, number> = {}

const notices = () => documentIoNotices().map((item) => item.key)

/** A capture of the browser save: the files it was handed. */
function saver() {
  const files: Array<{ blob: Blob; name: string }> = []
  return { files, save: (blob: Blob, name: string) => void files.push({ blob, name }) }
}

async function ready(docId: string) {
  documentIoDraw(docId)
  await flushPromises()
}

/** The opener the Inspector registers (its verdict as given). */
const openerLock = ref<'read_only' | 'run_locked' | null>(null)
const saved = vi.fn()
function registerOpener() {
  return setWorkPlanEditOpener(() => ({ external: () => openerLock.value, saved }))
}

beforeEach(() => {
  resetDocumentEditRegistry()
  resetDocumentIo()
  closeAllWorkPlanEditWindows()
  setWorkPlanEditOpener(null)
  setWorkPlanStepEditHandler(null)
  openerLock.value = null
  saved.mockReset()
  for (const key of Object.keys(records)) delete records[key]
  for (const key of Object.keys(access)) delete access[key]
  records[DOC] = { doc_id: DOC, type_code: 'WP', project_id: 'flowgate', group_id: FLOW, is_editable: true }
  records[TR] = { doc_id: TR, type_code: 'TR', project_id: 'flowgate', group_id: FLOW, is_editable: true }
  contentSave.mockReset().mockResolvedValue(undefined)
  download.mockReset().mockResolvedValue({ blob: new Blob(['# md']), disposition: null })
  infoRead.mockReset().mockImplementation(async (docId: string) => {
    if (access[docId]) throw http(access[docId])
    return toDocumentInfo({ ...records[docId], download_available: true })
  })
  planRead.mockReset().mockImplementation(async () => readAnswer({ body: hiddenPlanBody() }))
})

afterEach(() => {
  resetDocumentEditRegistry()
  resetDocumentIo()
  closeAllWorkPlanEditWindows()
  setWorkPlanEditOpener(null)
  setWorkPlanStepEditHandler(null)
})

// --- Which document is a work plan ------------------------------------------------------------------------------------

describe('a work plan is told by its read type, else by its id (0074 T#2 §0)', () => {
  it('READY: the read type decides (WP, wp with spaces → WP; TR → not), whatever the id says', async () => {
    await ready(DOC)
    await ready(TR)
    expect(documentIoInfo(DOC).state).toBe('READY')
    expect(documentIoIsWorkPlan(DOC)).toBe(true)
    expect(documentIoIsWorkPlan(TR)).toBe(false)

    const odd = `${FLOW}.0009-WP`
    records[odd] = { doc_id: odd, type_code: 'TR' }
    const spaced = `${FLOW}.0010-X`
    records[spaced] = { doc_id: spaced, type_code: ' wp ' }
    await ready(odd)
    await ready(spaced)
    expect(documentIoIsWorkPlan(odd)).toBe(false)
    expect(documentIoIsWorkPlan(spaced)).toBe(true)
  })

  it('LOADING / ERROR / DENIED / GONE: the id `…-WP` (any case) decides', async () => {
    infoRead.mockImplementation(() => new Promise(() => {}))
    documentIoDraw(DOC)
    documentIoDraw(TR)
    await flushPromises()
    expect(documentIoInfo(DOC).state).toBe('LOADING')
    expect(documentIoIsWorkPlan(DOC)).toBe(true)
    expect(documentIoIsWorkPlan(`${FLOW}.0006-wp`)).toBe(true)
    expect(documentIoIsWorkPlan(TR)).toBe(false)

    for (const status of [500, 403, 404]) {
      resetDocumentIo()
      infoRead.mockImplementation(async () => {
        throw http(status)
      })
      await ready(DOC)
      expect(documentIoInfo(DOC).state).toBe(status === 403 ? 'DENIED' : status === 404 ? 'GONE' : 'ERROR')
      expect(documentIoIsWorkPlan(DOC)).toBe(true)
    }
  })
})

// --- Download ---------------------------------------------------------------------------------------------------------

describe('download of a work plan — the stored plan body as <docId>.json (0074 T#2 §1)', () => {
  it('writes the read view’s body only, as the plan window’s export does; never the Markdown download', async () => {
    const view = readAnswer({ body: hiddenPlanBody(), stored_path: 'documents/x/0006-WP_document.md', updated_by: 'u1' })
    planRead.mockResolvedValue(view)
    await ready(DOC)
    const { files, save } = saver()
    await documentIoDownload(DOC, { save })

    expect(planRead).toHaveBeenCalledWith(DOC)
    expect(download).not.toHaveBeenCalled()
    expect(files).toHaveLength(1)
    expect(files[0].name).toBe(`${DOC}.json`)
    expect(files[0].blob.type).toBe('application/json;charset=utf-8')
    const text = await documentIoReadFile(files[0].blob)
    // compared with the read view's body (what the server read and checked), not the stored bytes
    expect(text).toBe(`${JSON.stringify(view.body, null, 2)}\n`)
    const out = JSON.parse(text)
    for (const key of ['wp_version', 'binding', 'counted_types', 'quantities', 'provider_candidates', 'defaults', 'steps']) {
      expect(out, key).toHaveProperty(key)
    }
    for (const key of ['ok', 'doc_id', 'doc_type', 'title', 'status', 'editable', 'revision_no', 'stored_path', 'updated_by', 'registered_providers', 'body']) {
      expect(out, key).not.toHaveProperty(key)
    }
    expect(out.x_origin_note).toEqual({ kept: true })
    // the pre-instruction attachment stays a reference, as in the body (no file content put in, none dropped)
    const refs = (body: { steps: Array<Record<string, unknown>> }) => body.steps.map((s) => s.pre_instruction_attachment ?? null)
    expect(refs(out)).toEqual(refs(view.body as { steps: Array<Record<string, unknown>> }))
    expect(refs(out).filter(Boolean)).toEqual([{ doc_id: DOC, filename: 'r_1.txt', original_filename: 'brief.txt', content_sha256: 'a'.repeat(64) }])
    expect(notices()).toEqual([])
  })

  it('the same text as the plan window’s export of that body (one format)', () => {
    const draft = draftOf(editBodyOf(hiddenPlanBody()))
    expect(workPlanJsonText(draft)).toBe(workPlanBodyJsonText(bodyOf(draft)))
    expect(workPlanBodyJsonText({ a: [1] })).toBe('{\n  "a": [\n    1\n  ]\n}\n')
  })

  it('pressed before the document is read (LOADING) it is still the plan’s .json', async () => {
    infoRead.mockImplementation(() => new Promise(() => {}))
    documentIoDraw(DOC)
    const { files, save } = saver()
    await documentIoDownload(DOC, { save })
    expect(download).not.toHaveBeenCalled()
    expect(files.map((file) => file.name)).toEqual([`${DOC}.json`])
  })

  it.each([
    ['409 wp_unreadable', http(409, { code: 'wp_unreadable', raw: '{broken', revision_no: 3 }), 'download.download_failed', 'READY'],
    ['422', http(422, { detail: 'not a work plan' }), 'download.download_failed', 'READY'],
    ['403', http(403), 'download.forbidden', 'DENIED'],
    ['404', http(404), 'download.gone', 'GONE'],
    ['network', network(), 'download.network', 'READY'],
    ['500', http(500), 'download.download_failed', 'READY'],
  ])('%s: no file, no Markdown fallback, the existing notice', async (_name, error, key, state) => {
    await ready(DOC)
    planRead.mockRejectedValue(error)
    const { files, save } = saver()
    await expect(documentIoDownload(DOC, { save })).resolves.toBeUndefined()
    expect(files).toHaveLength(0)
    expect(download).not.toHaveBeenCalled()
    expect(notices()).toEqual([key])
    expect(documentIoInfo(DOC).state).toBe(state)
    expect(documentIoButtons(DOC).download.busy).toBe(false)
  })

  it.each([
    ['an array', []],
    ['null', null],
    ['text', '{"wp_version": 2}'],
  ])('a body that is %s: no file, download_failed', async (_name, body) => {
    planRead.mockResolvedValue(readAnswer({ body }))
    const { files, save } = saver()
    await documentIoDownload(DOC, { save })
    expect(files).toHaveLength(0)
    expect(notices()).toEqual(['download.download_failed'])
  })

  it('a browser save that throws: download_failed, never a rejection', async () => {
    await expect(
      documentIoDownload(DOC, {
        save: () => {
          throw new Error('blocked')
        },
      }),
    ).resolves.toBeUndefined()
    expect(notices()).toEqual(['download.download_failed'])
  })

  it('one at a time; an answer after the Designer was left saves and tells nothing', async () => {
    const answer = deferred<Record<string, unknown>>()
    planRead.mockReturnValue(answer.promise)
    const { files, save } = saver()
    const first = documentIoDownload(DOC, { save })
    expect(documentIoButtons(DOC).download).toMatchObject({ busy: true, reason: 'downloading' })
    await documentIoDownload(DOC, { save })
    expect(planRead).toHaveBeenCalledTimes(1)
    documentIoLeave()
    answer.resolve(readAnswer({ body: hiddenPlanBody() }))
    await first
    expect(files).toHaveLength(0)
    expect(notices()).toEqual([])
  })

  it('unsaved edits of the plan window (or its Step windows) are not in the file and are told once; a clean window tells nothing', async () => {
    openWorkPlanEditWindow({ docId: DOC, projectId: 'flowgate' })
    const { files, save } = saver()
    await documentIoDownload(DOC, { save })
    expect(files).toHaveLength(1)
    expect(notices()).toEqual([])

    setWorkPlanEditWindowDirty(DOC, true)
    expect(workPlanEditWindowOf(DOC)?.dirty).toBe(true)
    await documentIoDownload(DOC, { save })
    expect(files).toHaveLength(2)
    expect(await documentIoReadFile(files[1].blob)).toBe(workPlanBodyJsonText(hiddenPlanBody()))
    expect(notices()).toEqual(['download_unsaved_excluded'])

    setWorkPlanEditWindowDirty(DOC, false)
    setWorkPlanStepEditHandler({ open: () => {}, pending: (docId) => docId === DOC })
    await documentIoDownload(DOC, { save })
    expect(notices()).toEqual(['download_unsaved_excluded', 'download_unsaved_excluded'])
  })

  it('a document that is not a work plan keeps the Markdown download', async () => {
    await ready(TR)
    const { files, save } = saver()
    await documentIoDownload(TR, { save })
    expect(download).toHaveBeenCalledWith(TR)
    expect(planRead).not.toHaveBeenCalled()
    expect(files.map((file) => file.name)).toEqual([`${TR}.md`])
  })
})

// --- Upload -----------------------------------------------------------------------------------------------------------

describe('upload of a work plan — the plan window’s [JSON 가져오기] (0074 T#2 §2, D#1 §6.3)', () => {
  it('opens the plan window asking for the import; no file pick, no content save — also called directly', async () => {
    registerOpener()
    await ready(DOC)
    const pick = vi.fn(async () => new File(['# x'], 'x.md'))
    await documentIoUpload(DOC, { pick, context: { projectId: 'p1', groupId: FLOW } })
    expect(pick).not.toHaveBeenCalled()
    expect(contentSave).not.toHaveBeenCalled()
    expect(workPlanEditWindowList()).toHaveLength(1)
    const request = workPlanEditWindowOf(DOC)!.request
    expect(request).toMatchObject({ docId: DOC, projectId: 'p1', groupId: FLOW, initialMode: 'import' })
    // the opener's part: its verdict and its save callback
    expect(request.external?.()).toBeNull()
    request.saved?.(3)
    expect(saved).toHaveBeenCalledWith(3)
    expect(documentIoButtons(DOC).upload.busy).toBe(false)
  })

  it('pressed while the document is read (LOADING): the same — never the generic upload', async () => {
    registerOpener()
    infoRead.mockImplementation(() => new Promise(() => {}))
    documentIoDraw(DOC)
    const pick = vi.fn(async () => new File(['# x'], 'x.md'))
    await documentIoUpload(DOC, { pick })
    expect(documentIoInfo(DOC).state).toBe('LOADING')
    expect(pick).not.toHaveBeenCalled()
    expect(contentSave).not.toHaveBeenCalled()
    // no context and no read: the Flow from the id
    expect(workPlanEditWindowOf(DOC)?.request).toMatchObject({ projectId: null, groupId: FLOW, initialMode: 'import' })
  })

  it('without a context the read’s project and Flow', async () => {
    registerOpener()
    await ready(DOC)
    await documentIoUpload(DOC)
    expect(workPlanEditWindowOf(DOC)?.request).toMatchObject({ projectId: 'flowgate', groupId: FLOW })
  })

  it('an open window is reused: one window, its external · saved · title kept, brought back and to the front', async () => {
    registerOpener()
    await ready(DOC)
    const external = () => null
    const ownSaved = vi.fn()
    openWorkPlanEditWindow({ docId: DOC, projectId: 'flowgate', groupId: FLOW, title: '작업계획', external, saved: ownSaved })
    const win = workPlanEditWindowOf(DOC)!
    win.minimized = true
    await documentIoUpload(DOC)
    expect(workPlanEditWindowList()).toHaveLength(1)
    expect(win.raise).toBe(1)
    expect(win.minimized).toBe(false)
    expect(win.request.external).toBe(external)
    expect(win.request.saved).toBe(ownSaved)
    expect(win.request.title).toBe('작업계획')
    expect(win.request.initialMode).toBe('import')
  })

  it('blocked: nothing opens, nothing is saved', async () => {
    await ready(DOC)
    await documentIoUpload(DOC) // no opener registered: run-locked
    openerLock.value = 'read_only'
    registerOpener()
    await documentIoUpload(DOC)
    expect(workPlanEditWindowList()).toHaveLength(0)
    expect(contentSave).not.toHaveBeenCalled()
  })

  it('a document that is not a work plan keeps the file pick and the content save', async () => {
    await ready(TR)
    await documentIoUpload(TR, { pick: async () => new File(['# 새 본문\n'], 'x.md', { type: 'text/markdown' }), read: async () => '# 새 본문\n' })
    expect(contentSave).toHaveBeenCalledWith(TR, '# 새 본문\n')
    expect(workPlanEditWindowList()).toHaveLength(0)
  })
})

describe('the work plan upload button — off with the plan window’s reasons (0074 T#2 §2 c)', () => {
  it('locked: the opener reads only / run-locked, or no opener; READY not editable; GONE', async () => {
    await ready(DOC)
    expect(documentIoButtons(DOC).upload).toEqual({ enabled: false, busy: false, reason: 'locked' })

    registerOpener()
    expect(documentIoButtons(DOC).upload).toEqual({ enabled: true, busy: false, reason: null })
    const reason = computed(() => documentIoButtons(DOC).upload.reason)
    expect(reason.value).toBeNull()
    openerLock.value = 'read_only'
    expect(reason.value).toBe('locked')
    openerLock.value = 'run_locked'
    expect(reason.value).toBe('locked')
    openerLock.value = null
    expect(reason.value).toBeNull()

    records[DOC] = { ...records[DOC], is_editable: false }
    resetDocumentIo()
    await ready(DOC)
    expect(documentIoButtons(DOC).upload.reason).toBe('locked')

    resetDocumentIo()
    access[DOC] = 404
    await ready(DOC)
    expect(documentIoButtons(DOC).upload.reason).toBe('locked')
  })

  it('step_pending: a Step window of the plan holds edits (followed as it changes)', async () => {
    registerOpener()
    await ready(DOC)
    const pending = ref(false)
    setWorkPlanStepEditHandler({ open: () => {}, pending: (docId) => docId === DOC && pending.value })
    const reason = computed(() => documentIoButtons(DOC).upload.reason)
    expect(reason.value).toBeNull()
    pending.value = true
    expect(reason.value).toBe('step_pending')
    expect(documentIoButtons(DOC).upload.enabled).toBe(false)
    pending.value = false
    expect(reason.value).toBeNull()
  })

  it('forbidden first: DENIED, or an upload refused with 403 — over every lock', async () => {
    openerLock.value = 'run_locked'
    registerOpener()
    access[DOC] = 403
    await ready(DOC)
    expect(documentIoButtons(DOC).upload.reason).toBe('forbidden')
  })

  it('the download button keeps its own rules; a document that is not a work plan keeps its upload rules', async () => {
    await ready(DOC)
    await ready(TR)
    expect(documentIoButtons(DOC).download).toEqual({ enabled: true, busy: false, reason: null })
    // no opener, Step windows pending: nothing of the plan's applies to a report
    setWorkPlanStepEditHandler({ open: () => {}, pending: () => true })
    expect(documentIoButtons(TR).upload).toEqual({ enabled: true, busy: false, reason: null })
  })
})
