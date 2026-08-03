import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import {
  resolveWorkflowViewState,
  type WorkflowViewInput,
} from '@main/workflow/workflowViewState'
import fixture from './fixtures/autoReopenWorkflowHead.0381.json'

// 0381 R0001 — "워크플로 시퀀스가 [테스트시나리지시]가 승인되기 전으로 되돌아와야 하고,
//               녹색이 아니라 파란색 작업중이어야 하고,
//               액션바는 [테스트시나리지시]의 승인/반려 상태로 되돌아와야 한다."
//
// Every earlier revision asserted database columns only. The colour of the strip cell and the
// buttons in the action bar are computed in the browser, so those assertions could not fail
// even if the screen never changed. This spec closes that gap: the payload below is not
// hand-written — server/tests/test_auto_ts_reopen_0381.py::test_17 runs the real worker over a
// production-shaped group and asserts the document-detail response equals this same file.
// Here that payload goes through the real DocHeader, the real workflow SSOT and the real
// DocWorkflow / ReviewActionBar components, and we assert what the user sees.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function tsTab(docId: string) {
  return { id: docId, title: 'TS', path: '', type: 'md', typeCode: 'TS' }
}

const TS_TAB = tsTab(fixture.tab_doc_id)

/** Serve one snapshot of the pinned document-detail payload to the real DocHeader. */
function serveDetail(payload: Record<string, unknown>) {
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) {
      return Promise.resolve({ data: { ...payload, is_editable: true, status: 'open' } })
    }
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
}

/**
 * MainPanel.getWorkflowViewInput (MainPanel.vue:2450-2485) verbatim: the strip also renders
 * the workflow root and the AC gate, which are not sequence items, so the server's step list
 * is expanded on both ends and the server head index is shifted by the prepended root.
 */
function viewInputFromHeader(vm: any): WorkflowViewInput {
  const rawSteps: string[] = vm.workflowSteps ?? []
  const workflowSteps = rawSteps.length > 0
    ? [vm.workflowRootType ?? 'R', ...rawSteps, 'AC']
    : rawSteps
  const rawHeadIndex: number | null = vm.workflowHeadIndex
  return {
    tabTypeCode: vm.docTypeCode,
    tabReviewStatus: vm.docReviewStatus,
    workflowSteps,
    headType: vm.workflowHeadType,
    headIndex: rawHeadIndex != null && rawHeadIndex >= 0 ? rawHeadIndex + 1 : null,
    headStatus: vm.headStatus,
    headDocId: vm.headDocId,
    headDocReviewStatus: vm.headDocReviewStatus,
    nextStepExists: vm.nextStepExists === true,
    qStatus: null,
  }
}

async function viewStateFor(payload: Record<string, any>) {
  serveDetail(payload)
  const header = shallowMount(DocHeader, {
    props: { tab: tsTab(payload.doc_id) as any },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  const input = viewInputFromHeader(header.vm as any)
  header.unmount()
  return { input, state: resolveWorkflowViewState(input) }
}

function mountStrip(state: ReturnType<typeof resolveWorkflowViewState>, tab = TS_TAB) {
  return mount(DocWorkflow, {
    props: {
      tab,
      workflowDecided: true,
      stepStates: state.stepStates,
      canNextAction: state.canNextAction,
    } as any,
    global: { plugins: [i18n], stubs: { WorkflowDecisionModal: true } },
  })
}

function mountActionBar(state: ReturnType<typeof resolveWorkflowViewState>, payload: any) {
  return mount(ReviewActionBar, {
    props: {
      docId: payload.doc_id,
      projectId: payload.project_id,
      groupId: payload.group_id,
      docRef: payload.doc_id,
      reviewStatus: payload.doc_review_status,
      mode: state.mode,
      headDocId: state.headDocId,
      viewedDocId: payload.doc_id,
    } as any,
    global: { plugins: [i18n] },
  })
}

/** The strip cell for one step type, as rendered. */
function cellClasses(strip: ReturnType<typeof mountStrip>, code: string): string[] {
  const idx = strip.props('stepStates' as any).findIndex((s: any) => s.code === code)
  return strip.findAll('.wf-step')[idx].classes()
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
})

describe('0381 — CODE RED 자동 복귀 뒤 사용자가 보는 화면', () => {
  it('되감기 전: 시퀀스가 전부 녹색(완료)이고 액션바에 승인/반려가 없다 — 반려에서 지적된 상태', async () => {
    const { state } = await viewStateFor(fixture.before)

    // Every cell is 'done' → .wf-step.done → --success #16a34a (app.css:403). Green.
    expect(state.stepStates.map(s => s.visual)).toEqual(
      ['done', 'done', 'done', 'done', 'done', 'done', 'done', 'done'],
    )
    const strip = mountStrip(state)
    expect(cellClasses(strip, 'TS')).toContain('done')
    expect(cellClasses(strip, 'TS')).not.toContain('current')
    strip.unmount()

    // The action bar renders nothing at all — no 승인, no 반려.
    expect(state.mode).toBe('sequence-complete')
    const bar = mountActionBar(state, fixture.before)
    expect(bar.text()).not.toContain('승인')
    expect(bar.text()).not.toContain('반려')
    expect(bar.findAll('button.btn-success').length).toBe(0)
    expect(bar.findAll('button.btn-danger').length).toBe(0)
    bar.unmount()
  })

  it('되감기 후: [테스트시나리오지시] 칸이 파란색 작업중이고, 그 뒤 칸은 완료가 아니다', async () => {
    const { input, state } = await viewStateFor(fixture.after)

    // The strip shows root + sequence + AC; the head lands on the TS cell, not the TSR cell.
    expect(input.workflowSteps).toEqual(['B', 'N', 'NR', 'T', 'TR', 'TS', 'TSR', 'AC'])
    expect(input.workflowSteps[input.headIndex as number]).toBe('TS')
    expect(state.stepStates.map(s => s.visual)).toEqual(
      ['done', 'done', 'done', 'done', 'done', 'current', 'future', 'future'],
    )

    const strip = mountStrip(state)
    // .wf-step.current → --primary #2563eb (app.css:405). Blue, and no longer green.
    expect(cellClasses(strip, 'TS')).toContain('current')
    expect(cellClasses(strip, 'TS')).not.toContain('done')
    // [테스트레포트] is behind the head again — it must not read as 완료.
    expect(cellClasses(strip, 'TSR')).not.toContain('done')
    expect(cellClasses(strip, 'TSR')).toContain('future')
    strip.unmount()
  })

  it('되감기 후: 액션바가 [테스트시나리오지시]의 승인/반려로 돌아온다', async () => {
    const { state } = await viewStateFor(fixture.after)

    expect(state.mode).toBe('review')
    const bar = mountActionBar(state, fixture.after)
    const approve = bar.find('button.btn-success')
    const reject = bar.find('button.btn-danger')
    expect(approve.exists()).toBe(true)
    expect(reject.exists()).toBe(true)
    expect(approve.text()).toContain('승인')
    expect(reject.text()).toContain('반려')
    // The bar must act on the TS itself — not offer to jump to some other head document.
    expect(state.headDocId ?? fixture.after.workflow_head_doc_id).toBe(fixture.tab_doc_id)
    bar.unmount()
  })
})

// The group the reporter actually ran. Its runs all aborted in the 준비(setup) 단계, which used
// to do nothing at all, so this pair is the literal before/after of the complaint.
describe('0381 — 신고된 그룹(test.test.0042)의 화면', () => {
  const TAB = tsTab(fixture.reported_tab_doc_id)

  it('준비 단계 실패 직후(수정 전): [테스트시나리오지시]가 녹색 완료이고 액션바에 승인/반려가 없다', async () => {
    const { state } = await viewStateFor(fixture.reported_before)

    // Head is the empty 테스트레포트 slot, so the TS sits behind it → 'done' → green.
    expect(state.stepStates.map(s => s.visual)).toEqual(
      ['done', 'done', 'done', 'done', 'done', 'done', 'current', 'future'],
    )
    const strip = mountStrip(state, TAB)
    expect(cellClasses(strip, 'TS')).toContain('done')
    expect(cellClasses(strip, 'TS')).not.toContain('current')
    strip.unmount()

    // The bar is the forward [다음 단계] affordance — there is nothing to approve or reject.
    expect(state.mode).toBe('next')
    const bar = mountActionBar(state, fixture.reported_before)
    expect(bar.findAll('button.btn-success').length).toBe(0)
    expect(bar.findAll('button.btn-danger').length).toBe(0)
    bar.unmount()
  })

  it('수정 후: 같은 실패가 [테스트시나리오지시]를 파란색 작업중 + 승인/반려로 되돌린다', async () => {
    const { input, state } = await viewStateFor(fixture.reported_after)

    expect(input.workflowSteps).toEqual(['R', 'N', 'NR', 'T', 'TR', 'TS', 'TSR', 'AC'])
    expect(input.workflowSteps[input.headIndex as number]).toBe('TS')
    expect(state.stepStates.map(s => s.visual)).toEqual(
      ['done', 'done', 'done', 'done', 'done', 'current', 'future', 'future'],
    )

    const strip = mountStrip(state, TAB)
    expect(cellClasses(strip, 'TS')).toContain('current')
    expect(cellClasses(strip, 'TS')).not.toContain('done')
    expect(cellClasses(strip, 'TSR')).not.toContain('done')
    strip.unmount()

    expect(state.mode).toBe('review')
    const bar = mountActionBar(state, fixture.reported_after)
    expect(bar.find('button.btn-success').text()).toContain('승인')
    expect(bar.find('button.btn-danger').text()).toContain('반려')
    bar.unmount()
  })
})
