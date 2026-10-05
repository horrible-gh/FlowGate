// flowgate.default.0668 T0004 — Git 충돌 처리 프로세스 간략화, 화면 쪽 계약.
//
// 정상 경로는 `승인 → 충돌 → AI/직접 해결 → 검토 및 승인 → 완료` 다. 이 스위트가 고정하는 것:
//
//   1. 충돌 카드에서 Provider 를 고르고 바로 [AI로 해결] 을 누른다 — Resolver 를 거치지 않는다.
//   2. AI 가 끝나면 검토 화면으로 넘어가는 것은 "Resolver 가 열려 있었는가"가 아니라 서버 상태가
//      정한다. 카드에서 시작했어도, Resolver 를 닫았어도 검토 대기면 검토 화면이 열린다.
//   3. TR 충돌도 같은 검토 화면으로 간다. AI 가 끝났는데 빈 Resolver 가 다시 열리던 결함은 없다.
//   4. 화면을 떠났다가 돌아오는 길: 미니플레이어의 [검토 열기] → 앱 루트의 리뷰 호스트가 서버에
//      무엇이 기다리는지 묻고 같은 검토 화면을 연다. 기다리는 것이 없으면 그렇게 말한다.
//
// 부재 단언에는 대조군을 붙였다(negative-ui-assertion-needs-a-positive-control).
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'
import GitFinalizePanel from '@main/components/GitFinalizePanel.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import GitConflictReviewHost from '@main/components/GitConflictReviewHost.vue'
import AiInvokeMiniplayer from '@main/components/AiInvokeMiniplayer.vue'
import { useProjectStore } from '@main/stores/project'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import {
  OPEN_CONFLICT_REVIEW_EVENT,
  groupParts,
  isReviewPendingState,
  requestConflictReview,
  reviewBadgeKeyOf,
} from '@main/composables/useConflictSession'

const { getRequest, postRequest, deleteRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), deleteRequest: vi.fn(), showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest, postRequest, deleteRequest,
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast, toasts: { value: [] } }),
}))

const GROUP_ID = 'test2.default.0005'
const MERGE_ID = 6
const TR_GROUP = 'test2.default.0332'
const TR_MERGE_ID = 42

const START_RESPONSE = {
  ok: true, run_id: 'aiv_20261005_000668', status: 'running', mode: 'single',
  group_id: GROUP_ID, project_id: 'test2', action_scope: 'resolve_conflict', merge_id: MERGE_ID,
  doc_ref: '', docs_target: 1, provider: { id: 'p1', name: 'Claude Haiku 4.5' },
  attempt_no: 1, started_at: new Date().toISOString(),
}
const PROVIDERS = {
  ok: true, project: 'test2', providers: [{ id: 'p1', name: 'Claude Haiku 4.5', exec_type: 'cli', kind: 'claude' }],
  default_provider_id: 'p1',
}

function finished(runId: string, groupId: string) {
  return {
    run_id: runId, group_id: groupId, doc_ref: '', status: 'finished',
    action_scope: 'resolve_conflict', outcome: 'complete', docs_reached: 0, docs_target: 0,
    reached_doc_ids: [], end_reason: 'exited', exit_code: 0,
    last_message_received: true, last_message: 'done',
  }
}

function mergeStatus(reviewState: string | null = null) {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'ready',
    ahead_count: 0, behind_count: 0, slots: [],
    pending: [{
      group_id: GROUP_ID, branch: 'test2_default_0005', status: 'conflict',
      default_action: 'merge', merge_id: MERGE_ID, review_state: reviewState,
    }],
    pending_count: 1,
  }
}

function trStatus(reviewState: 'open' | 'resolved') {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'ready',
    ahead_count: 0, behind_count: 0,
    slots: [{
      group_id: TR_GROUP, branch: 'test2_default_0332', status: 'conflict', merge_id: null,
      tr_commits: {
        live: 1, canceled: 0, no_commit: 0, commits: [], more: 0,
        reapply_pending: false, last_block: null,
        conflict_session: {
          merge_id: TR_MERGE_ID, kind: 'tr_revert', doc_id: `${TR_GROUP}.0009-TR`,
          doc_code: '0009-TR', subject: 'Revert', files: ['f.txt'],
          remaining: reviewState === 'open' ? ['f.txt'] : [], review_state: reviewState,
        },
      },
    }],
    pending: [], pending_count: 0,
  }
}

let panelStatus: unknown = mergeStatus()
let finalizeState: Record<string, unknown> = {}

beforeEach(() => {
  setActivePinia(createPinia())
  ;(Element.prototype as any).scrollTo = vi.fn()
  i18n.global.locale.value = 'ko'
  showToast.mockReset()
  postRequest.mockReset().mockResolvedValue({ data: { ok: true } })
  panelStatus = mergeStatus()
  finalizeState = {
    group_id: GROUP_ID, branch: 'test2_default_0005', base_branch: 'main',
    status: 'conflict', choices: [], ahead_count: 1, behind_count: 0, merge_id: MERGE_ID,
    review_state: null,
  }
  getRequest.mockReset().mockImplementation((url: string) => {
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { ok: true, state: { ...finalizeState } } })
    if (url.endsWith('/git/status')) return Promise.resolve({ data: { ok: true, status: panelStatus } })
    if (url.includes('/ai-invoke/providers')) return Promise.resolve({ data: PROVIDERS })
    if (url.includes('active-all')) return Promise.resolve({ data: { ok: true, runs: [], paused: [] } })
    if (url.includes('/conflicts')) {
      return Promise.resolve({ data: { ok: true, files: [{ path: 'f.txt', content: 'x\n', conflict_count: 0 }] } })
    }
    return Promise.resolve({ data: { ok: true } })
  })
})

const STUBS = { AppIcon: true, GitMergeReviewDialog: true, teleport: true }

describe('useConflictSession — one vocabulary for every surface', () => {
  it('maps review states and badges the way both panels used to, once', () => {
    expect(isReviewPendingState('resolved_pending_review')).toBe(true)
    expect(isReviewPendingState('re_review')).toBe(true)
    expect(isReviewPendingState('reconciling')).toBe(true)
    // 대조군: 해결 중/없음은 검토가 아니다.
    expect(isReviewPendingState(null)).toBe(false)
    expect(isReviewPendingState('completed')).toBe(false)
    expect(reviewBadgeKeyOf('re_review')).toBe('re_review')
    expect(reviewBadgeKeyOf('resolved_pending_review')).toBe('pending')
    expect(groupParts('test2.default.0005')).toEqual({ project: 'test2', module: 'default', group: '0005' })
  })

  it('asks the root host for a review through one window event', () => {
    const seen: unknown[] = []
    const listener = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener(OPEN_CONFLICT_REVIEW_EVENT, listener)
    requestConflictReview(GROUP_ID, MERGE_ID)
    window.removeEventListener(OPEN_CONFLICT_REVIEW_EVENT, listener)
    expect(seen).toEqual([{ group_id: GROUP_ID, merge_id: MERGE_ID }])
  })
})

describe('GitStatusPanel — the conflict card is the entry point (R1/R2)', () => {
  it('starts the AI from the card with the chosen provider and never opens the resolver', async () => {
    const wrapper = mount(GitStatusPanel, { props: { projectId: 'test2' }, global: { plugins: [i18n], stubs: STUBS } })
    await flushPromises()
    postRequest.mockImplementation((url: string) => (
      url === '/api/v1/ai-invoke/start'
        ? Promise.resolve({ data: START_RESPONSE })
        : Promise.resolve({ data: { ok: true } })
    ))

    await wrapper.find('[data-test="conflict-card-ai"]').trigger('click')
    await flushPromises()

    const start = postRequest.mock.calls.find(([url]) => url === '/api/v1/ai-invoke/start')
    expect(start![1]).toMatchObject({
      action_scope: 'resolve_conflict', merge_id: MERGE_ID, provider_id: 'p1',
      project: 'test2', module: 'default', group: '0005',
    })
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    // The card itself says the run is working (the operator may leave).
    expect(wrapper.find('[data-test="conflict-card-run"]').text()).toContain('Claude Haiku 4.5')
    wrapper.unmount()
  })

  it('opens the review when a card-started run reaches review — no resolver had to be open', async () => {
    const wrapper = mount(GitStatusPanel, { props: { projectId: 'test2' }, global: { plugins: [i18n], stubs: STUBS } })
    await flushPromises()
    postRequest.mockImplementation((url: string) => (
      url === '/api/v1/ai-invoke/start'
        ? Promise.resolve({ data: START_RESPONSE })
        : Promise.resolve({ data: { ok: true } })
    ))
    await wrapper.find('[data-test="conflict-card-ai"]').trigger('click')
    await flushPromises()
    // 대조군: 실행 중에는 검토 화면이 없다.
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(false)

    panelStatus = mergeStatus('resolved_pending_review')
    useAiInvokeRunsStore().trackFinished(finished(START_RESPONSE.run_id, GROUP_ID))
    await flushPromises()
    await flushPromises()

    const review = wrapper.findComponent(GitMergeReviewDialog)
    expect(review.exists()).toBe(true)
    expect(review.props('mergeId')).toBe(MERGE_ID)
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    wrapper.unmount()
  })

  it('a TR run that resolved the conflict opens the review, not an empty resolver', async () => {
    panelStatus = trStatus('open')
    const wrapper = mount(GitStatusPanel, { props: { projectId: 'test2' }, global: { plugins: [i18n], stubs: STUBS } })
    await flushPromises()
    // Direct resolver open first — the old bug needed it to be on screen.
    await wrapper.find('.git-trc-conflict-btn').trigger('click')
    await flushPromises()
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(true)
    useAiInvokeRunsStore().trackStarted({ ...START_RESPONSE, group_id: TR_GROUP, merge_id: TR_MERGE_ID })
    await flushPromises()
    const conflictReadsBefore = getRequest.mock.calls.filter(([u]) => String(u).includes('/conflicts')).length

    panelStatus = trStatus('resolved')
    useAiInvokeRunsStore().trackFinished(finished(START_RESPONSE.run_id, TR_GROUP))
    await flushPromises()
    await flushPromises()

    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)
    expect(wrapper.findComponent(GitMergeReviewDialog).props('mergeId')).toBe(TR_MERGE_ID)
    const conflictReadsAfter = getRequest.mock.calls.filter(([u]) => String(u).includes('/conflicts')).length
    expect(conflictReadsAfter).toBe(conflictReadsBefore)
    wrapper.unmount()
  })

  it('a run that left conflicts refreshes the open resolver instead', async () => {
    const wrapper = mount(GitStatusPanel, { props: { projectId: 'test2' }, global: { plugins: [i18n], stubs: STUBS } })
    await flushPromises()
    await wrapper.find('.git-status-row-main .btn-danger').trigger('click')
    await flushPromises()
    useAiInvokeRunsStore().trackStarted({ ...START_RESPONSE })
    await flushPromises()
    const before = getRequest.mock.calls.filter(([u]) => String(u).includes('/conflicts')).length

    useAiInvokeRunsStore().trackFinished(finished(START_RESPONSE.run_id, GROUP_ID))
    await flushPromises()
    await flushPromises()

    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(false)
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(true)
    expect(getRequest.mock.calls.filter(([u]) => String(u).includes('/conflicts')).length).toBeGreaterThan(before)
    wrapper.unmount()
  })
})

describe('GitFinalizePanel — same card, same handover', () => {
  function mountFinalize() {
    useProjectStore().setCurrentProject('test2')
    return mount(GitFinalizePanel, { props: { groupId: GROUP_ID }, global: { plugins: [i18n], stubs: STUBS } })
  }

  it('starts the AI from the conflict card and opens the review when the run reaches it', async () => {
    const wrapper = mountFinalize()
    await flushPromises()
    postRequest.mockImplementation((url: string) => (
      url === '/api/v1/ai-invoke/start'
        ? Promise.resolve({ data: START_RESPONSE })
        : Promise.resolve({ data: { ok: true } })
    ))

    await wrapper.find('[data-test="conflict-card-ai"]').trigger('click')
    await flushPromises()
    expect(postRequest.mock.calls.some(([url]) => url === '/api/v1/ai-invoke/start')).toBe(true)
    expect(wrapper.findComponent(GitConflictResolverDialog).exists()).toBe(false)

    finalizeState.review_state = 'resolved_pending_review'
    useAiInvokeRunsStore().trackFinished(finished(START_RESPONSE.run_id, GROUP_ID))
    await flushPromises()
    await flushPromises()

    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('GitConflictReviewHost — the way back from anywhere (R2)', () => {
  it('opens the common review screen for what the server says is waiting', async () => {
    finalizeState.review_state = 'resolved_pending_review'
    const wrapper = mount(GitConflictReviewHost, { global: { plugins: [i18n], stubs: STUBS } })
    // 대조군: 요청 전에는 아무것도 없다.
    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(false)

    requestConflictReview(GROUP_ID)
    await flushPromises()

    const review = wrapper.findComponent(GitMergeReviewDialog)
    expect(review.exists()).toBe(true)
    expect(review.props('groupId')).toBe(GROUP_ID)
    expect(review.props('mergeId')).toBe(MERGE_ID)
    wrapper.unmount()
  })

  it('says nothing is waiting instead of opening an empty screen', async () => {
    finalizeState.review_state = null
    const wrapper = mount(GitConflictReviewHost, { global: { plugins: [i18n], stubs: STUBS } })

    requestConflictReview(GROUP_ID)
    await flushPromises()

    expect(wrapper.findComponent(GitMergeReviewDialog).exists()).toBe(false)
    expect(showToast).toHaveBeenCalledWith(i18n.global.t('main.git_review.no_review_pending'), 'warning')
    wrapper.unmount()
  })
})

describe('AiInvokeMiniplayer — [검토 열기] on a finished conflict run', () => {
  it('offers the review for a finished resolve_conflict card and asks the host for it', async () => {
    const wrapper = mount(AiInvokeMiniplayer, { global: { plugins: [i18n] } })
    const store = useAiInvokeRunsStore()
    store.trackStarted({ ...START_RESPONSE })
    store.trackFinished(finished(START_RESPONSE.run_id, GROUP_ID))
    // 대조군: 일반 문서 실행 카드에는 검토 단추가 없다.
    store.trackFinished({
      run_id: 'run-doc', group_id: 'test2.default.0007', doc_ref: 'test2.default.0007.0001-R',
      status: 'finished', outcome: 'complete', end_reason: 'exited',
    })
    await flushPromises()
    await wrapper.find('.aiv-mini__chip').trigger('click')
    await flushPromises()

    const buttons = wrapper.findAll('[data-test="ai-miniplayer-open-review"]')
    expect(buttons).toHaveLength(1)
    const seen: unknown[] = []
    const listener = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener(OPEN_CONFLICT_REVIEW_EVENT, listener)
    await buttons[0].trigger('click')
    window.removeEventListener(OPEN_CONFLICT_REVIEW_EVENT, listener)

    expect(seen).toEqual([{ group_id: GROUP_ID, merge_id: null }])
    wrapper.unmount()
  })
})
