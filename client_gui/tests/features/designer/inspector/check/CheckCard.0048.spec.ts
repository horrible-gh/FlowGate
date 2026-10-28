/**
 * flowgate.v02.0048 T#12 — the check card with `@/features/document` mocked as a small server (D#1 §12.1):
 * the section (AC check state, the Flow's Git reflection state, the final approval job, the Git panel
 * and ↗ 열기) and the header action (the final approval with the Git choice the server offers). The
 * server keeps the AC status, the Git slot and the AI activity, changes them on a final approval and
 * refuses like the real one (409 Git busy / precheck / state, 422 stale choice, 423 AI running, 403,
 * 404, 500, no answer). Normalization and error reading stay real; events go through the real hub.
 * Last, the real registry puts the section above the documents list and the action in the header of
 * AC cards only, the Git panel opens through the Frame's Host intent, and no Graph gate / delegation
 * button exists.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import type { FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import { closeAllDocumentWindows, documentWindowList } from '@/features/documentWindow'
import CheckAction from '@/features/designer/inspector/features/check/CheckAction.vue'
import CheckTab from '@/features/designer/inspector/features/check/CheckTab.vue'
import { CHECK_ACTIVITY_EVENTS, CHECK_DOCUMENT_EVENTS, CHECK_GIT_EVENTS, useCheck } from '@/features/designer/inspector/features/check/useCheck'
import { INSPECTOR_HOST, type InspectorHostIntent } from '@/features/designer/inspector/inspectorHost'
import { createInspectorRegistry, inspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { SELECTIONS, prepareInspectorI18n, registrationModules } from '../support/inspectorSupport'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../support/projectEventStream'
import {
  AC_DOC,
  AC_NODE,
  FLOW,
  NONE_STATE,
  OTHER_AC_DOC,
  acSelection,
  acTarget,
  axiosError,
  docRow,
  executed,
  finalizeState,
  handedOff,
  networkError,
  queued,
} from './checkFixtures'

// The calls are mocked; normalization and error reading stay real.
const documentRead = vi.fn()
const gitRead = vi.fn()
const activityRead = vi.fn()
const approveCall = vi.fn()
const reviewRead = vi.fn()
const detailRead = vi.fn()
const relationsRead = vi.fn()
const qaReadCall = vi.fn()
const attachmentsReadCall = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    checkDocumentRead: (...args: unknown[]) => documentRead(...args),
    checkGitRead: (...args: unknown[]) => gitRead(...args),
    checkApprove: (...args: unknown[]) => approveCall(...args),
    reviewActivityRead: (...args: unknown[]) => activityRead(...args),
    // The documents tab, the header actions, the Q&A and attachments tab numbers share the Frame.
    reviewDocumentRead: (...args: unknown[]) => reviewRead(...args),
    documentDetailRead: (...args: unknown[]) => detailRead(...args),
    documentRelationsRead: (...args: unknown[]) => relationsRead(...args),
    qaRead: (...args: unknown[]) => qaReadCall(...args),
    attachmentsRead: (...args: unknown[]) => attachmentsReadCall(...args),
  }
})

const { toReviewDocument, toCheckGitState, toCheckApproveResult, toReviewActivity, toDocumentDetail, toQaThread, toAttachmentsList } =
  await vi.importActual<typeof import('@/features/document')>('@/features/document')

const i18n = prepareInspectorI18n()

// --- The server ------------------------------------------------------------------------------------

interface ServerAc {
  status: string
  disposed: boolean
}

const acs = new Map<string, ServerAc>()
let slot: Record<string, unknown> = finalizeState()
let running = false
/** The next answer of the final approval (once): a refusal to throw, or a payload to answer with. */
let nextRefusal: unknown = null
let nextAnswer: ((docId: string) => unknown) | null = null
/** The next Git read fails with this (once). */
let gitRefusal: unknown = null

function serve() {
  documentRead.mockImplementation(async (docId: string) => {
    const ac = acs.get(docId)
    if (!ac) throw axiosError(404, { detail: 'Document not found' })
    return toReviewDocument(docRow(docId, ac.status, ac.disposed))
  })
  gitRead.mockImplementation(async () => {
    if (gitRefusal) {
      const refusal = gitRefusal
      gitRefusal = null
      throw refusal
    }
    return toCheckGitState({ ok: true, state: slot })
  })
  activityRead.mockImplementation(async (groupId: string) =>
    toReviewActivity(running ? { ok: true, active: true, run_id: 'run_1', status: 'running', group_id: groupId } : { ok: true, active: false, group_id: groupId }),
  )
  approveCall.mockImplementation(async ({ docId, choice }: { docId: string; choice: string | null }) => {
    if (nextRefusal) {
      const refusal = nextRefusal
      nextRefusal = null
      throw refusal
    }
    if (nextAnswer) {
      const answer = nextAnswer(docId)
      nextAnswer = null
      return toCheckApproveResult(answer)
    }
    acs.get(docId)!.status = 'approved'
    if (choice === 'merge' || choice === 'merge_only') {
      slot = finalizeState({ status: 'merged', choices: [], merge_commit: 'abcdef0123456789', ahead_count: 0 })
      return toCheckApproveResult(executed(docId, { action: choice, status: 'merged', merge_commit: 'abcdef0123456789', pushed: choice === 'merge', conflict_files: [] }))
    }
    // wait / no choice: the plain transition; the slot stays awaiting a choice for the Git panel.
    return toCheckApproveResult({ document: docRow(docId, 'approved') })
  })
  reviewRead.mockImplementation(async (docId: string) => toReviewDocument(docRow(docId, acs.get(docId)?.status ?? 'pending_review')))
  detailRead.mockImplementation(async (docId: string) => toDocumentDetail(docRow(docId, acs.get(docId)?.status ?? 'pending_review')))
  relationsRead.mockImplementation(async () => [])
  qaReadCall.mockImplementation(async (docId: string) => toQaThread({ ok: true, qa: { doc_id: docId, status: null, items: [] } }))
  attachmentsReadCall.mockImplementation(async (docId: string) => toAttachmentsList({ data: { doc_id: docId, attachments: [], count: 0 } }))
}

// --- Mounting --------------------------------------------------------------------------------------

type W = VueWrapper<any>

let pinia: Pinia
let stream: ProjectEventStream
let wrapper: W | null = null
let intents: InspectorHostIntent[] = []
const host = { request: (intent: InspectorHostIntent) => intents.push(intent) }

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  acs.clear()
  acs.set(AC_DOC, { status: 'pending_review', disposed: false })
  acs.set(OTHER_AC_DOC, { status: 'pending_review', disposed: false })
  slot = finalizeState()
  running = false
  nextRefusal = null
  nextAnswer = null
  gitRefusal = null
  intents = []
  for (const mock of [documentRead, gitRead, activityRead, approveCall, reviewRead, detailRead, relationsRead, qaReadCall, attachmentsReadCall]) mock.mockReset()
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  removeProjectEventStream()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function mountPart(component: typeof CheckTab | typeof CheckAction, target: InspectorTarget = acTarget()) {
  wrapper = mount(component as any, { props: { target }, global: { plugins: [i18n, pinia], provide: { [INSPECTOR_HOST as symbol]: host } }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const el = (w: W, name: string) => w.find(`[data-test="${name}"]`)
const isDisabled = (w: W, name: string) => el(w, name).attributes('disabled') !== undefined
const body = () => document.body
const inDialog = (name: string) => body().querySelector<HTMLElement>(`[data-test="${name}"]`)
const line = (w: W) => el(w, 'check-action-line')

async function click(w: W, name: string) {
  await el(w, name).trigger('click')
  await flushPromises()
}

async function openDialog(w: W) {
  await click(w, 'check-approve')
  expect(inDialog('check-dialog')).not.toBeNull()
}

async function choose(choice: string) {
  const input = body().querySelector<HTMLInputElement>(`[data-test="check-choice-${choice}"] input`)!
  input.checked = true
  input.dispatchEvent(new Event('change'))
  await flushPromises()
}

async function submit() {
  inDialog('check-dialog-submit')!.click()
  await flushPromises()
}

const selectedChoice = () => body().querySelector('[data-test^="check-choice-"][data-selected="true"]')?.getAttribute('data-test')?.replace('check-choice-', '') ?? null

async function settle(ms = INSPECTOR_REFRESH_COALESCE_MS) {
  await vi.advanceTimersByTimeAsync(ms)
  await flushPromises()
}

// --- The section -----------------------------------------------------------------------------------

describe('check section — state shown (flowgate.v02.0048 T#12)', () => {
  it('a waiting AC of a Git active Flow: check state, Git state, branch, ahead / behind, the Git panel and ↗ 열기', async () => {
    const w = await mountPart(CheckTab)
    expect(el(w, 'check-review').attributes('title')).toBe('확인 대기')
    expect(el(w, 'check-git').text()).toBe('반영 방식 선택 대기')
    expect(el(w, 'check-git').attributes('data-tone')).toBe('warning')
    expect(el(w, 'check-git-branch').text()).toBe('브랜치 test2_default_0034')
    expect(w.findAll('[data-test="check-git-note"]').map((note) => note.text())).toEqual(['본 브랜치보다 2커밋 앞서고 0커밋 뒤처져 있습니다'])
    // flowgate.v02.0051 stage 2: both are the 확인 결과 slot line's buttons. flowgate.v02.0053 T#5: the Git panel
    // (another screen) keeps its ↗; the open button is the document icon (tooltip 열기) and the check state
    // is the line's dot (its wording the tooltip).
    expect(el(w, 'check-open-git').text()).toBe('Git 패널 열기 ↗')
    expect(el(w, 'check-open').attributes('data-icon')).toBe('document')
    expect(el(w, 'check-open').attributes('title')).toBe('열기')
    expect(el(w, 'check-open').text()).toBe('')
    expect(documentRead).toHaveBeenCalledWith(AC_DOC)
    expect(gitRead).toHaveBeenCalledWith(FLOW)
    expect(activityRead).toHaveBeenCalledWith(FLOW)
    expect([documentRead, gitRead, activityRead].map((mock) => mock.mock.calls.length)).toEqual([1, 1, 1])
    // The section has no final approval of its own (the header has it).
    expect(el(w, 'check-approve').exists()).toBe(false)
  })

  it('Git 패널 열기 asks the Host for the Git tool; ↗ 열기 opens the AC in its document window (flowgate.v02.0053 T#3)', async () => {
    closeAllDocumentWindows()
    const open = vi.fn()
    vi.stubGlobal('open', open)
    const w = await mountPart(CheckTab)
    await click(w, 'check-open-git')
    expect(intents).toEqual([{ kind: 'show-tool', tool: 'git' }])
    await click(w, 'check-open')
    expect(open).not.toHaveBeenCalled()
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[AC_DOC, 'view']])
    closeAllDocumentWindows()
    // Looking changes nothing on the server.
    expect(approveCall).not.toHaveBeenCalled()
  })

  it.each([
    ['none', NONE_STATE, '반영할 Git 작업 없음', [], false],
    ['merged', finalizeState({ status: 'merged', choices: [], merge_commit: 'abcdef0123456789', ahead_count: 0 }), '머지 완료', ['머지 커밋 abcdef0123', '본 브랜치보다 0커밋 앞서고 0커밋 뒤처져 있습니다'], true],
    ['pushed', finalizeState({ status: 'pushed', choices: [], merge_commit: null, ahead_count: null, behind_count: null }), '푸시 완료', [], true],
    ['conflict', finalizeState({ status: 'conflict', choices: [], merge_id: 12 }), '충돌 — 해결이 필요합니다', ['본 브랜치보다 2커밋 앞서고 0커밋 뒤처져 있습니다'], true],
    ['waiting', finalizeState({ status: 'waiting' }), '보류 중', ['본 브랜치보다 2커밋 앞서고 0커밋 뒤처져 있습니다'], true],
    ['unknown', finalizeState({ status: 'reconciling', choices: [] }), 'Git 상태: reconciling', ['본 브랜치보다 2커밋 앞서고 0커밋 뒤처져 있습니다'], true],
  ])('Git %s', async (_label, state, text, notes, branch) => {
    slot = state
    const w = await mountPart(CheckTab)
    expect(el(w, 'check-git').text()).toBe(text)
    expect(w.findAll('[data-test="check-git-note"]').map((note) => note.text())).toEqual(notes)
    expect(el(w, 'check-git-branch').exists()).toBe(branch)
  })

  it('an approved AC whose Git finished without its approval: the retry note', async () => {
    acs.get(AC_DOC)!.status = 'pending_review'
    slot = finalizeState({ status: 'merged', choices: [], approval_pending: true, merge_commit: 'abc1234' })
    const w = await mountPart(CheckTab)
    expect(w.findAll('[data-test="check-git-note"]')[0].text()).toBe('Git은 끝났고 승인이 남았습니다. 최종 확인을 다시 누르면 승인만 다시 시도합니다.')
  })

  it.each([
    ['approved', '확인됨'],
    ['rejected', '반려됨'],
  ])('AC %s -> %s', async (status, text) => {
    acs.get(AC_DOC)!.status = status
    const w = await mountPart(CheckTab)
    expect(el(w, 'check-review').attributes('title')).toBe(text)
  })

  // flowgate.v02.0056 F1: a state that asks nothing of the operator (a draft AC, neutral) draws no badge.
  it('AC draft -> no state badge on the line', async () => {
    acs.get(AC_DOC)!.status = 'draft'
    const w = await mountPart(CheckTab)
    expect(el(w, 'check-review').exists()).toBe(false)
  })

  it('a final approval job of this AC: queued line; a recovery one says so', async () => {
    slot = finalizeState({ approval_in_flight: true, approval_job: { job_id: 'job_7', status: 'blocked', stage: 'freeze_wait', doc_id: AC_DOC } })
    const w = await mountPart(CheckTab)
    expect(el(w, 'check-job').text()).toBe('최종 확인을 처리하는 중입니다(freeze_wait, 작업 job_7).')
    w.unmount()
    slot = finalizeState({ approval_job: { job_id: 'job_9', status: 'recovery_required', stage: 'recovery_required', doc_id: AC_DOC } })
    const w2 = await mountPart(CheckTab)
    expect(el(w2, 'check-job').text()).toBe('최종 확인 작업 job_9이 복구를 기다립니다. Git 패널에서 확인한 뒤 최종 확인을 다시 누릅니다.')
  })

  it.each([
    ['403', axiosError(403, { ok: false, error: { code: 'forbidden', message: 'Forbidden' } }), 'Git 상태를 볼 권한이 없습니다.'],
    ['500', axiosError(500, {}), 'Git 상태를 읽지 못했습니다.'],
    ['network', networkError(), 'Git 상태를 읽지 못했습니다.'],
  ])('a Git read %s: the reason, the check state and the Git panel stay', async (_label, refusal, text) => {
    gitRefusal = refusal
    const w = await mountPart(CheckTab)
    expect(el(w, 'check-git-error').text()).toBe(text)
    expect(el(w, 'check-git').exists()).toBe(false)
    expect(el(w, 'check-review').attributes('title')).toBe('확인 대기')
    expect(isDisabled(w, 'check-open-git')).toBe(false)
  })

  it('read only (revision view, ownership conflict): looking stays usable', async () => {
    for (const fields of [{ ownership: 'graph', viewMode: 'revision', readOnly: 'revision' as const }, { ownership: 'conflict', readOnly: 'conflict' as const }]) {
      const w = await mountPart(CheckTab, acTarget(fields))
      expect(isDisabled(w, 'check-open-git')).toBe(false)
      expect(isDisabled(w, 'check-open')).toBe(false)
      w.unmount()
      wrapper = null
    }
  })
})

describe('check section — server events read again (D#1 §7.4)', () => {
  it('listens to the document, Git and AI activity events only', async () => {
    await mountPart(CheckTab)
    const [connection] = stream.connections()
    for (const event of [...CHECK_DOCUMENT_EVENTS, ...CHECK_GIT_EVENTS, ...CHECK_ACTIVITY_EVENTS]) expect(connection.listened(event), event).toBeGreaterThan(0)
    expect(connection.listened('qna_q_registered')).toBe(0)
    expect(connection.listened('test_run_started')).toBe(0)
  })

  it('a Git frame of the Flow reads the Git state again; another Flow does not', async () => {
    vi.useFakeTimers()
    const w = await mountPart(CheckTab)
    stream.open()
    slot = finalizeState({ status: 'merged', choices: [], merge_commit: 'fedcba9876543210' })
    stream.emit('git_pending_changed', { groupId: 'test2.default.0035', payload: { status: 'merged' } })
    await settle()
    expect(gitRead).toHaveBeenCalledTimes(1)
    for (const event of ['git_pending_changed', 'git_finalize_done', 'git_approval_job_changed', 'git_merge_conflict']) {
      const before = gitRead.mock.calls.length
      stream.emit(event, { groupId: FLOW, payload: { group_id: FLOW } })
      await settle()
      expect(gitRead.mock.calls.length, event).toBe(before + 1)
    }
    expect(el(w, 'check-git').text()).toBe('머지 완료')
  })

  it('the Git frames that end a final approval (git_finalize_done, git_approval_job_changed) read the AC again too', async () => {
    vi.useFakeTimers()
    const w = await mountPart(CheckTab)
    stream.open()
    acs.get(AC_DOC)!.status = 'approved'
    stream.emit('git_finalize_done', { groupId: FLOW, payload: { group_id: FLOW, status: 'merged' } })
    await settle()
    expect(documentRead).toHaveBeenCalledTimes(2)
    expect(el(w, 'check-review').attributes('title')).toBe('확인됨')
    stream.emit('git_approval_job_changed', { groupId: FLOW, payload: { status: 'succeeded' } })
    await settle()
    expect(documentRead).toHaveBeenCalledTimes(3)
    // git_pending_changed is a Git slot signal only: the AC is not read for it.
    stream.emit('git_pending_changed', { groupId: FLOW })
    await settle()
    expect(documentRead).toHaveBeenCalledTimes(3)
    // Another Flow's frames read nothing.
    stream.emit('git_finalize_done', { groupId: 'test2.default.0035' })
    await settle()
    expect(documentRead).toHaveBeenCalledTimes(3)
  })

  it('doc_review_status_changed of the AC reads the AC and its Git state again', async () => {
    vi.useFakeTimers()
    const w = await mountPart(CheckTab)
    stream.open()
    acs.get(AC_DOC)!.status = 'approved'
    stream.emit('doc_review_status_changed', { docId: AC_DOC, groupId: FLOW, payload: { prev_status: 'pending_review', next_status: 'approved' } })
    await settle()
    expect(documentRead).toHaveBeenCalledTimes(2)
    expect(el(w, 'check-review').attributes('title')).toBe('확인됨')
    // Another document's change reads nothing.
    stream.emit('doc_review_status_changed', { docId: OTHER_AC_DOC, groupId: 'test2.default.0016' })
    await settle()
    expect(documentRead).toHaveBeenCalledTimes(2)
  })

  it('a reconnect reads every resource again', async () => {
    vi.useFakeTimers()
    await mountPart(CheckTab)
    stream.open()
    stream.reconnect()
    await settle()
    expect([documentRead, gitRead, activityRead].map((mock) => mock.mock.calls.length)).toEqual([2, 2, 2])
  })
})

// --- The header action -----------------------------------------------------------------------------

describe('final approval — dialog and calls', () => {
  it('a waiting AC: "최종 확인"; the dialog offers the server choices, starts on the default without push, names the target', async () => {
    const w = await mountPart(CheckAction)
    expect(el(w, 'check-approve').text()).toBe('최종 확인')
    expect(isDisabled(w, 'check-approve')).toBe(false)
    expect(line(w).exists()).toBe(false)
    await openDialog(w)
    const labels = [...body().querySelectorAll('[data-test^="check-choice-"] strong')].map((node) => node.textContent)
    expect(labels).toEqual(['머지 + 푸시', '머지만 — 푸시는 나중에', '대기 — 반영은 나중에'])
    expect(selectedChoice()).toBe('merge_only')
    expect(inDialog('check-dialog-target')!.textContent).toBe('병합 대상: main')
    expect(approveCall).not.toHaveBeenCalled()
  })

  it('merge + push: one call with the choice, everything read again, the dialog closes and the result line shows', async () => {
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await choose('merge')
    await submit()
    expect(approveCall).toHaveBeenCalledTimes(1)
    expect(approveCall).toHaveBeenCalledWith({ docId: AC_DOC, choice: 'merge' })
    expect([documentRead, gitRead, activityRead].map((mock) => mock.mock.calls.length)).toEqual([2, 2, 2])
    expect(inDialog('check-dialog')).toBeNull()
    expect(el(w, 'check-approve').exists()).toBe(false)
    expect(line(w).attributes('data-kind')).toBe('outcome')
    expect(line(w).text()).toBe('최종 확인했습니다 — 머지·푸시 완료 abcdef0123')
    // The Canvas card and the header are read again quietly (no review event on the Git path).
    expect(intents).toEqual([{ kind: 'refresh-flow', flowId: FLOW }])
  })

  it('merge only: merged locally, not pushed', async () => {
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await submit()
    expect(approveCall).toHaveBeenCalledWith({ docId: AC_DOC, choice: 'merge_only' })
    expect(line(w).text()).toBe('최종 확인했습니다 — 머지 완료(푸시 전) abcdef0123')
  })

  it('wait: approved, the Git slot keeps waiting for the Git panel', async () => {
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await choose('wait')
    await submit()
    expect(approveCall).toHaveBeenCalledWith({ docId: AC_DOC, choice: 'wait' })
    expect(line(w).text()).toBe('최종 확인했습니다.')
  })

  it('nothing to reflect (none): a plain final approval, no choice asked', async () => {
    slot = NONE_STATE
    const w = await mountPart(CheckAction)
    await openDialog(w)
    expect(inDialog('check-dialog-choices')).toBeNull()
    expect(inDialog('check-dialog-plain')!.textContent).toBe('Git 반영 방식은 묻지 않습니다. 반영할 것이 생기면 Git 패널에서 합니다.')
    await submit()
    expect(approveCall).toHaveBeenCalledWith({ docId: AC_DOC, choice: null })
    expect(line(w).text()).toBe('최종 확인했습니다.')
  })

  it('the Git state cannot be read (403): still a plain final approval (the v0.1 dialog does the same)', async () => {
    gitRefusal = axiosError(403, { ok: false, error: { code: 'forbidden' } })
    const w = await mountPart(CheckAction)
    await openDialog(w)
    expect(inDialog('check-dialog-plain')).not.toBeNull()
    await submit()
    expect(approveCall).toHaveBeenCalledWith({ docId: AC_DOC, choice: null })
  })

  it('cancel closes the dialog without a call', async () => {
    const w = await mountPart(CheckAction)
    await openDialog(w)
    inDialog('check-dialog-cancel')!.click()
    await flushPromises()
    expect(inDialog('check-dialog')).toBeNull()
    expect(approveCall).not.toHaveBeenCalled()
  })

  it('handed to a conflict review: the line says so and the Git panel opens; the AC still waits', async () => {
    nextAnswer = () => {
      slot = finalizeState({ status: 'conflict', choices: [], merge_id: 12 })
      return handedOff(['a.py', 'b.py'])
    }
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await choose('merge')
    await submit()
    expect(line(w).text()).toBe('충돌 2건이 있어 확인이 보류되었습니다. Git 패널에서 해결하세요.')
    expect(intents).toEqual([{ kind: 'show-tool', tool: 'git' }, { kind: 'refresh-flow', flowId: FLOW }])
    expect(el(w, 'check-approve').exists()).toBe(true)
  })

  it('a failed Git step beside a standing approval: warning line and the Git panel opens', async () => {
    nextAnswer = (docId) => {
      acs.get(docId)!.status = 'approved'
      return { ok: true, git: { ok: false, error: { code: 'base_dirty' } }, approval: { approved: true }, document: docRow(docId, 'approved') }
    }
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await submit()
    expect(line(w).text()).toBe('최종 확인했지만 Git 반영이 실패했습니다. Git 패널에서 확인하세요.')
    expect(line(w).find('[data-tone="warning"]').exists()).toBe(true)
    expect(intents).toEqual([{ kind: 'show-tool', tool: 'git' }, { kind: 'refresh-flow', flowId: FLOW }])
  })

  it('queued: the job holds the group (approval running reason); its event approves later and the line goes', async () => {
    vi.useFakeTimers()
    nextAnswer = () => {
      slot = finalizeState({ approval_in_flight: true, approval_job: { job_id: 'job_2', status: 'blocked', stage: 'freeze_wait', doc_id: AC_DOC } })
      return queued('job_2')
    }
    const w = await mountPart(CheckAction)
    stream.open()
    await openDialog(w)
    await choose('merge')
    await submit()
    expect(isDisabled(w, 'check-approve')).toBe(true)
    expect(line(w).text()).toBe('이 흐름의 최종 확인을 서버가 처리하는 중입니다.')
    // The job finishes: the server approves and tells.
    acs.get(AC_DOC)!.status = 'approved'
    slot = finalizeState({ status: 'merged', choices: [], merge_commit: 'abcdef0123456789' })
    stream.emit('git_approval_job_changed', { groupId: FLOW, payload: { status: 'succeeded' } })
    stream.emit('doc_review_status_changed', { docId: AC_DOC, groupId: FLOW })
    await settle()
    expect(el(w, 'check-approve').exists()).toBe(false)
    expect(line(w).exists()).toBe(false)
    expect(w.find('[data-test="inspector-check-action"]').exists()).toBe(false)
  })

  it('while the call waits the button is busy and a second click sends nothing', async () => {
    let answer: (value: unknown) => void = () => {}
    approveCall.mockImplementation(() => new Promise((resolve) => (answer = resolve)))
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await submit()
    expect(w.get('[data-test="inspector-check-action"]').attributes('data-busy')).toBe('approve')
    expect(isDisabled(w, 'check-approve')).toBe(true)
    inDialog('check-dialog-submit')!.click()
    await el(w, 'check-approve').trigger('click')
    // The dialog cannot be closed while waiting.
    inDialog('check-dialog-cancel')!.click()
    await flushPromises()
    expect(inDialog('check-dialog')).not.toBeNull()
    acs.get(AC_DOC)!.status = 'approved'
    answer(toCheckApproveResult({ document: docRow(AC_DOC, 'approved') }))
    await flushPromises()
    expect(approveCall).toHaveBeenCalledTimes(1)
    expect(el(w, 'check-approve').exists()).toBe(false)
  })
})

describe('final approval — the composable guards', () => {
  /** useCheck alone in a probe component, its state handed out. */
  async function mountProbe(target: InspectorTarget = acTarget()) {
    let state: ReturnType<typeof useCheck> | null = null
    const Probe = defineComponent({
      setup() {
        state = useCheck(() => target)
        return () => h('div')
      },
    })
    wrapper = mount(Probe, { global: { plugins: [i18n, pinia], provide: { [INSPECTOR_HOST as symbol]: host } } })
    await flushPromises()
    return state!
  }

  it('a choice the server does not offer now is not sent: a plain final approval', async () => {
    slot = NONE_STATE
    const state = await mountProbe()
    expect(state.offer.value.choices).toEqual([])
    expect(await state.approve('merge')).toBe(true)
    expect(approveCall).toHaveBeenCalledWith({ docId: AC_DOC, choice: null })
  })

  it('an offered choice is sent as chosen', async () => {
    const state = await mountProbe()
    await state.approve('merge')
    expect(approveCall).toHaveBeenCalledWith({ docId: AC_DOC, choice: 'merge' })
  })

  it('busy lasts until the state is read again, so the old "under review" never offers the approval twice', async () => {
    const w = await mountPart(CheckAction)
    let release: () => void = () => {}
    documentRead.mockImplementationOnce(
      (docId: string) => new Promise((resolve) => (release = () => resolve(toReviewDocument(docRow(docId, acs.get(docId)!.status))))),
    )
    await openDialog(w)
    await choose('merge')
    await submit()
    // Answered (approved), but the AC is not read again yet.
    expect(approveCall).toHaveBeenCalledTimes(1)
    expect(w.get('[data-test="inspector-check-action"]').attributes('data-busy')).toBe('approve')
    expect(isDisabled(w, 'check-approve')).toBe(true)
    await el(w, 'check-approve').trigger('click')
    inDialog('check-dialog-submit')?.click()
    await flushPromises()
    expect(approveCall).toHaveBeenCalledTimes(1)
    release()
    await flushPromises()
    expect(el(w, 'check-approve').exists()).toBe(false)
  })
})

describe('final approval — refusals are shown and the state is read again', () => {
  it.each([
    ['409 git_busy', axiosError(409, { ok: false, error: { code: 'git_busy' } }), '다른 Git 작업이 진행 중이라 거절되었습니다. 잠시 뒤 다시 확인하세요.'],
    ['409 merge_target_busy', axiosError(409, { ok: false, error: { code: 'merge_target_busy', details: {} }, approval: {} }), '다른 Git 작업이 진행 중이라 거절되었습니다. 잠시 뒤 다시 확인하세요.'],
    ['409 approval_precheck_failed', axiosError(409, { ok: false, error: { code: 'approval_precheck_failed' } }), '지금은 최종 확인할 수 없는 상태라 거절되었습니다. 상태를 다시 읽었습니다.'],
    ['409 state', axiosError(409, { detail: 'Invalid review transition' }), '지금 상태에서는 확인할 수 없어 거절되었습니다. 상태를 다시 읽었습니다.'],
    ['403', axiosError(403, { ok: false, error: { code: 'approval_precheck_failed' } }), '최종 확인할 권한이 없습니다.'],
    ['404', axiosError(404, { detail: 'Document not found' }), '문서를 찾을 수 없습니다.'],
    ['500', axiosError(500, {}), '최종 확인을 마치지 못했습니다.'],
    ['network', networkError(), '서버에 연결하지 못했습니다.'],
  ])('%s: the dialog stays open with the reason, the header line too, everything read again', async (_label, refusal, text) => {
    nextRefusal = refusal
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await submit()
    expect(inDialog('check-dialog')).not.toBeNull()
    expect(inDialog('check-dialog-failure')!.textContent).toBe(text)
    expect(line(w).attributes('data-kind')).toBe('failure')
    expect(line(w).text()).toBe(text)
    expect([documentRead, gitRead, activityRead].map((mock) => mock.mock.calls.length)).toEqual([2, 2, 2])
    expect(isDisabled(w, 'check-approve')).toBe(false)
    // A refused call changed nothing: no Canvas read again.
    expect(intents).toEqual([])
    // Opening the dialog again starts clean.
    inDialog('check-dialog-cancel')!.click()
    await flushPromises()
    await openDialog(w)
    expect(inDialog('check-dialog-failure')).toBeNull()
  })

  it('422 stale choice: read again — the slot no longer asks, the dialog shows the plain approval', async () => {
    nextRefusal = axiosError(422, { ok: false, error: { code: 'invalid_request' } })
    slot = finalizeState()
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await choose('merge')
    slot = NONE_STATE
    await submit()
    expect(inDialog('check-dialog-failure')!.textContent).toBe('Git 반영 상태가 바뀌어 고른 방식을 쓸 수 없습니다. 상태를 다시 읽었으니 다시 고르세요.')
    expect(inDialog('check-dialog-choices')).toBeNull()
    expect(inDialog('check-dialog-plain')).not.toBeNull()
  })

  it('423 AI running: the reason, the activity read again, the final approval disabled', async () => {
    nextRefusal = axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED' } })
    const w = await mountPart(CheckAction)
    await openDialog(w)
    // A run started meanwhile: the server refuses and the read again knows it.
    running = true
    await submit()
    expect(inDialog('check-dialog-failure')!.textContent).toBe('AI 실행 중이라 거절되었습니다. 상태를 다시 읽었습니다.')
    expect(isDisabled(w, 'check-approve')).toBe(true)
  })

  it('409 group_disposed: the reason; the read again knows the group is disposed', async () => {
    nextRefusal = axiosError(409, { detail: `group_disposed: ${AC_DOC}` })
    const w = await mountPart(CheckAction)
    await openDialog(w)
    acs.get(AC_DOC)!.disposed = true
    await submit()
    expect(line(w).text()).toBe('폐기된 그룹이라 거절되었습니다. 상태를 다시 읽었습니다.')
    inDialog('check-dialog-cancel')!.click()
    await flushPromises()
    expect(isDisabled(w, 'check-approve')).toBe(true)
  })

  it('a refusal answered after the Node changed is not shown on the new Node', async () => {
    let fail: (error: unknown) => void = () => {}
    approveCall.mockImplementation(() => new Promise((_resolve, reject) => (fail = reject)))
    const w = await mountPart(CheckAction)
    await openDialog(w)
    await submit()
    await w.setProps({ target: acTarget({ docId: OTHER_AC_DOC, nodeId: `doc:${OTHER_AC_DOC}`, flowId: 'test2.default.0016' }) })
    fail(axiosError(409, { ok: false, error: { code: 'git_busy' } }))
    await flushPromises()
    expect(line(w).exists()).toBe(false)
    expect(inDialog('check-dialog')).toBeNull()
    expect(isDisabled(w, 'check-approve')).toBe(false)
  })
})

describe('final approval — availability inputs', () => {
  it('an AI run of the group disables it with its reason; a click sends nothing', async () => {
    running = true
    const w = await mountPart(CheckAction)
    expect(isDisabled(w, 'check-approve')).toBe(true)
    expect(line(w).attributes('data-kind')).toBe('reason')
    expect(line(w).text()).toBe('AI 실행 중이라 확인할 수 없습니다.')
    await el(w, 'check-approve').trigger('click')
    expect(inDialog('check-dialog')).toBeNull()
  })

  it('a disposed group disables it', async () => {
    acs.get(AC_DOC)!.disposed = true
    const w = await mountPart(CheckAction)
    expect(line(w).text()).toBe('폐기된 그룹이라 확인할 수 없습니다.')
  })

  it('the server still runs this flow\'s final approval: disabled until its event says it is free', async () => {
    vi.useFakeTimers()
    slot = finalizeState({ approval_in_flight: true, approval_job: null })
    const w = await mountPart(CheckAction)
    stream.open()
    expect(isDisabled(w, 'check-approve')).toBe(true)
    expect(line(w).text()).toBe('이 흐름의 최종 확인을 서버가 처리하는 중입니다.')
    slot = finalizeState()
    stream.emit('git_finalize_done', { groupId: FLOW })
    await settle()
    expect(isDisabled(w, 'check-approve')).toBe(false)
    expect(line(w).exists()).toBe(false)
  })

  it('a recovery job leaves the retry to the person: usable', async () => {
    slot = finalizeState({ approval_in_flight: true, approval_job: { job_id: 'job_9', status: 'recovery_required', stage: 'recovery_required', doc_id: AC_DOC } })
    const w = await mountPart(CheckAction)
    expect(isDisabled(w, 'check-approve')).toBe(false)
  })

  it('a Graph revision view: shown disabled with the read-only reason; the draft view: usable', async () => {
    const w = await mountPart(CheckAction, acTarget({ ownership: 'graph', viewMode: 'revision', readOnly: 'revision' }))
    expect(isDisabled(w, 'check-approve')).toBe(true)
    expect(line(w).text()).toBe('Graph revision 보기에서는 확인할 수 없습니다. draft 보기로 옮기세요.')
    await w.setProps({ target: acTarget({ ownership: 'graph', viewMode: 'draft', readOnly: null }) })
    await flushPromises()
    expect(isDisabled(w, 'check-approve')).toBe(false)
  })

  it('an ownership conflict: disabled with the conflict reason', async () => {
    const w = await mountPart(CheckAction, acTarget({ ownership: 'conflict', viewMode: 'revision', readOnly: 'conflict' }))
    expect(line(w).text()).toBe('소유 충돌 플로우라 확인할 수 없습니다.')
  })

  it('the read status wins over the Canvas one; an approved AC shows nothing (no empty place)', async () => {
    acs.get(AC_DOC)!.status = 'approved'
    const w = await mountPart(CheckAction)
    expect(w.find('[data-test="inspector-check-action"]').exists()).toBe(false)
  })
})

// --- In the Frame ----------------------------------------------------------------------------------

describe('check card in the Frame (real registry)', () => {
  async function mountFrame(selection: FlowGraphSelection, registry = inspectorRegistry) {
    wrapper = mount(DesignerInspector, { props: { selection, registry }, global: { plugins: [i18n, pinia] }, attachTo: document.body })
    await flushPromises()
    return wrapper
  }

  const tabLabels = (w: W) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((tab) => tab.text())

  it('the registration: key check, order 430, AC cards with a document, documents tab and header action', () => {
    const entry = inspectorRegistry.entries.find((candidate) => candidate.key === 'check')!
    expect(entry).toMatchObject({ key: 'check', order: 430, cardKinds: ['check'], requiresDocument: true })
    expect(entry.tab?.key).toBe('documents')
    expect(entry.headerAction).toBeDefined()
    expect(entry.docTypes).toBeUndefined()
  })

  it.each(['v01', 'graph', 'unassigned'] as const)('AC in a %s Flow: the section leads the documents tab alone, the final approval in the header', async (ownership) => {
    const w = await mountFrame(acSelection({ ownership, viewMode: ownership === 'v01' ? null : 'draft' }))
    const section = el(w, 'inspector-check')
    expect(section.exists()).toBe(true)
    // flowgate.v02.0052: the common documents list is not repeated beneath the card.
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(false)
    const actions = w.get('[data-test="designer-inspector-actions"]')
    expect(actions.find('[data-test="check-approve"]').text()).toBe('최종 확인')
    // The review header actions (T#6) are not on an AC.
    expect(actions.find('[data-test="inspector-actions"]').exists()).toBe(false)
  })

  it('the section\'s Git panel button leaves the Frame as the Host intent show-tool: git', async () => {
    const w = await mountFrame(acSelection())
    await click(w, 'check-open-git')
    expect(w.emitted('host')).toEqual([[{ kind: 'show-tool', tool: 'git' }]])
  })

  it.each([
    ['v01 TR', SELECTIONS.v01],
    ['v01 slot without a document', SELECTIONS.v01NoDocument],
    ['graph TS', SELECTIONS.graph],
    ['graph gate (no Graph gate confirm here)', SELECTIONS.graphGate],
    ['unassigned R', SELECTIONS.unassigned],
  ])('%s: no check section, no final approval, no Git read', async (_label, selection) => {
    const w = await mountFrame(selection)
    expect(el(w, 'inspector-check').exists()).toBe(false)
    expect(el(w, 'inspector-check-action').exists()).toBe(false)
    expect(gitRead).not.toHaveBeenCalled()
    expect(documentRead).not.toHaveBeenCalled()
  })

  it('an AC Node without its document: header only', async () => {
    const w = await mountFrame(acSelection({}, { ...AC_NODE, document: null }))
    expect(el(w, 'inspector-check').exists()).toBe(false)
    expect(gitRead).not.toHaveBeenCalled()
  })

  it('without the check registration the AC tab row is the same (the section adds no tab)', async () => {
    const w = await mountFrame(acSelection())
    const withCheck = tabLabels(w)
    w.unmount()
    const modules = registrationModules(inspectorRegistry.entries.filter((entry) => entry.key !== 'check'))
    const w2 = await mountFrame(acSelection(), createInspectorRegistry(modules))
    expect(tabLabels(w2)).toEqual(withCheck)
    expect(el(w2, 'inspector-check').exists()).toBe(false)
  })

  it('a failed Git read leaves the header as it is', async () => {
    // Both check parts (section and header action) read the Git state; both fail.
    gitRead.mockRejectedValue(axiosError(500, {}))
    const w = await mountFrame(acSelection())
    expect(el(w, 'check-git-error').exists()).toBe(true)
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(false)
    expect(w.get('[data-test="designer-inspector-header"]').text()).toContain('Final Approval')
  })

  it.each(['pending_review', 'approved'])('AC %s: no undecided button or wording (Graph gate · delegation · expansion · memo · AI hand-off)', async (status) => {
    acs.get(AC_DOC)!.status = status
    const w = await mountFrame(acSelection({}, { ...AC_NODE, document: { ...AC_NODE.document!, docReviewStatus: status } }))
    const text = w.text()
    // flowgate.v02.0051: the undecided wording stays out of the check card itself. flowgate.v02.0053 T#9: the
    // v7 header's ✎ 메모 works on every card with a document (features/memo), so it is usable here.
    for (const word of ['게이트', 'gate', '위임', '전개', 'AI 전달', '재시도', '마지막 확인', '연동 전']) expect(text).not.toContain(word)
    expect(w.get('[data-test="designer-inspector-memo"]').attributes('disabled')).toBeUndefined()
  })
})
