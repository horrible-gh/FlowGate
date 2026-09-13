// 0552 T0006 — 문서 context read 소유권 정리 및 중복 조회 제거 (지시서 §1~§4 / R1·R2·R3).
//
// 여기서 고정하는 계약은 네 줄이다.
//
//   1. `return-point` / `sequence` 의 조회 키는 `documents/detail` 을 읽는 쪽(DocHeader)이
//      확정한다. 확정 전에는 **아예 부르지 않는다** — 예전에는 `?? tabId` 로 떨어져 자식
//      문서(T/TR/N…) 를 대상으로 왕복이 나갔고 그 응답은 버려졌다(0552.0005-NR §2.2-4).
//   2. 같은 root 에 대해 DocHeader 가 몇 번을 다시 그리든 실제 왕복은 각각 1회다
//      (in-flight join + 마지막 결과 키).
//   3. 그러나 캐시가 아니다 — 워크플로 상태가 실제로 바뀌면 다시 읽는다. 왕복 중에 상태가
//      바뀐 경우에도 그 변화는 버려지지 않는다(trailing).
//   4. 늦게 도착한 옛 응답이 최신 상태를 덮어쓰지 않는다(generation guard).
//
// 그리고 전환이 확정된 mutation 성공 경로는 **떠나는 문서를 다시 읽지 않는다**(R3).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, defineComponent, reactive, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { mountMainPanel } from '../helpers/mountMainPanel'
import { useTabsStore } from '@main/stores/tabs'
import ConfirmModal from '@main/components/ConfirmModal.vue'

const GROUP = 'flowgate.default.0552'
const ROOT = `${GROUP}.0001-R`
const OTHER_ROOT = `${GROUP}.0002-R`
const TR_TAB = `${GROUP}.0011-TR`
const OTHER_TAB = `${GROUP}.0099-N`

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({ issueToken: vi.fn(), copyMentToClipboard: vi.fn() }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

// 칸 하나짜리 스트립. `slot-commits` 프롭이 곧 `returnSequences[root]` 의 관찰창이다.
const viewState = reactive({ canNextAction: false })
vi.mock('@main/workflow/workflowViewState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@main/workflow/workflowViewState')>()
  return {
    ...actual,
    resolveWorkflowViewState: () => ({
      mode: 'review' as const,
      canNextAction: viewState.canNextAction,
      currentStepCode: null,
      highlightStepCode: null,
      nextStepCode: null,
      nextStepActive: false,
      headDocLabel: null,
      headDocId: null,
      highlightDesignSeries: false,
      stepStates: [{ code: 'TR', visual: 'done', className: 'done', iconClass: 'check-circle' }],
      nextStepIndex: null,
    }),
  }
})

function sequenceWithCommit(commit: string) {
  return [{
    type: 'TR',
    result_doc_id: TR_TAB,
    result_seq: 11,
    label: '뒤 레포트',
    tr_commit: { state: 'live', commit },
  }]
}

const RETURN_POINT = {
  exists: true,
  front_seq: 11,
  front_label: '뒤 레포트',
  restorable_count: 1,
  current_min_seq: 11,
  destination_default: 11,
  destination_min: 11,
}

// ── DocHeader stand-in: it owns the detail read, so it owns the root id ────────────────
// `rootDocId === null` models "detail has not landed yet". The workflow-state fields are
// the ones the dedup key is built from.
const header = reactive({
  rootDocId: null as string | null,
  reviewStatus: null as string | null,
  headType: null as string | null,
  headDocId: null as string | null,
  steps: null as string[] | null,
})
const fetchDocSpy = vi.fn()

const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  props: { tab: { type: Object, required: true }, readOnly: { type: Boolean, default: false } },
  emits: ['doc-updated', 'workflow-decided', 'related-doc-created'],
  setup(_props, { expose }) {
    expose({
      workflowRootDocId: computed(() => header.rootDocId),
      parentRDocId: computed(() => header.rootDocId),
      docReviewStatus: computed(() => header.reviewStatus),
      workflowHeadType: computed(() => header.headType),
      workflowHeadIndex: ref(null),
      headDocId: computed(() => header.headDocId),
      headDocReviewStatus: ref(null),
      headStatus: ref(null),
      workflowSteps: computed(() => header.steps),
      docTypeCode: ref('TR'),
      docLoaded: computed(() => header.rootDocId != null),
      docProjectId: ref('flowgate'),
      docModule: ref('default'),
      groupId: ref(GROUP),
      groupDisposed: ref(false),
      workflowOrphan: ref(false),
      workflowCandidateSlots: ref([]),
      testRun: ref(null),
      groupTestRunActive: ref(false),
      aiReview: ref(null),
      aiReviewHistory: ref([]),
      rejectionReason: ref(null),
      rejectionHistory: ref([]),
      fetchDoc: fetchDocSpy,
    })
    return () => null
  },
})

// ── request plumbing ──────────────────────────────────────────────────────────────────
type Resolver = (value: unknown) => void
const urls: string[] = []
let pendingReturnPoint: Resolver[] = []
let pendingSequence: Resolver[] = []
let deferReturnPoint = false
let deferSequence = false
let sequenceBody = sequenceWithCommit('AAAAAAA')

function countOf(fragment: string): number {
  return urls.filter(u => u.includes(fragment)).length
}
function urlsWith(fragment: string): string[] {
  return urls.filter(u => u.includes(fragment))
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  fetchDocSpy.mockReset()
  urls.length = 0
  pendingReturnPoint = []
  pendingSequence = []
  deferReturnPoint = false
  deferSequence = false
  sequenceBody = sequenceWithCommit('AAAAAAA')
  viewState.canNextAction = false
  header.rootDocId = null
  header.reviewStatus = null
  header.headType = null
  header.headDocId = null
  header.steps = null

  getRequest.mockImplementation((url: string) => {
    urls.push(url)
    if (url.includes('/return-point')) {
      const body = { data: { ok: true, return_point: RETURN_POINT } }
      if (!deferReturnPoint) return Promise.resolve(body)
      return new Promise((resolve) => pendingReturnPoint.push(resolve as Resolver))
    }
    if (url.includes('/sequence')) {
      const body = { data: { sequence: sequenceBody } }
      if (!deferSequence) return Promise.resolve(body)
      return new Promise((resolve) => pendingSequence.push(resolve as Resolver))
    }
    return Promise.resolve({ data: { questions: [] } })
  })
})

async function openTrTab() {
  return mountMainPanel({
    tabs: [{ id: TR_TAB, title: 'TR', path: '', type: 'md', typeCode: 'TR' } as any],
    stubs: { DocHeader: DocHeaderStub, DocWorkflow: true },
  })
}

/** DocHeader 가 한 번 다시 그렸다고 알린다 (상태 초기화 / detail 반영 / relations 반영). */
async function announceDocUpdated(wrapper: any) {
  wrapper.findComponent({ name: 'DocHeader' }).vm.$emit('doc-updated', { docId: TR_TAB })
  await flushPromises()
}

/** detail 이 도착해 root 와 워크플로 상태가 확정된 상태로 만든다. */
async function landDetail(wrapper: any) {
  header.rootDocId = ROOT
  header.reviewStatus = 'pending_review'
  header.headType = 'TR'
  header.headDocId = TR_TAB
  header.steps = ['N', 'NR', 'T', 'TR']
  await announceDocUpdated(wrapper)
}

function slotCommits(wrapper: any): any[] {
  return wrapper.findComponent({ name: 'DocWorkflow' }).props('slotCommits') as any[]
}

describe('0552 T0006 — 문서 context read 소유권', () => {
  it('detail 이 root 를 확정하기 전에는 자식 문서 id 로 헛요청이 나가지 않는다 (R2)', async () => {
    const wrapper = await openTrTab()

    // mount 직후: 소유자가 아직 답하지 않았다.
    expect(countOf('/return-point')).toBe(0)
    expect(countOf('/sequence')).toBe(0)

    // DocHeader 가 상태를 비우며 한 번 알린다(detail 이전 라운드).
    await announceDocUpdated(wrapper)
    expect(countOf('/return-point')).toBe(0)
    expect(countOf('/sequence')).toBe(0)
    // 자식 문서 id 를 대상으로 한 요청은 단 한 건도 없다.
    expect(urlsWith(encodeURIComponent(TR_TAB))).toEqual([])
  })

  it('root 가 확정되면 각각 1회만, 그리고 언제나 워크플로 루트를 대상으로 부른다 (R1·R2)', async () => {
    const wrapper = await openTrTab()
    await landDetail(wrapper)

    expect(countOf('/return-point')).toBe(1)
    expect(countOf('/sequence')).toBe(1)
    expect(urlsWith('/return-point')[0]).toContain(encodeURIComponent(ROOT))
    expect(urlsWith('/sequence')[0]).toContain(encodeURIComponent(ROOT))
    expect(urlsWith(encodeURIComponent(TR_TAB))).toEqual([])
    // 응답은 실제로 쓰인다 — 스트립의 커밋 표식이 시퀀스에서 나온다.
    expect(slotCommits(wrapper)[0]).toMatchObject({ state: 'live', commit: 'AAAAAAA' })
  })

  it('같은 상태로 DocHeader 가 다시 알려도(관계 반영 등) 다시 읽지 않는다 (R1)', async () => {
    const wrapper = await openTrTab()
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)

    // relations 반영, 포커스 복귀 silent refresh 등 — 워크플로 상태는 그대로다.
    await announceDocUpdated(wrapper)
    await announceDocUpdated(wrapper)

    expect(countOf('/return-point')).toBe(1)
    expect(countOf('/sequence')).toBe(1)
  })

  it('워크플로 상태가 실제로 바뀌면 다시 읽는다 — 캐시가 아니다', async () => {
    const wrapper = await openTrTab()
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)

    sequenceBody = sequenceWithCommit('BBBBBBB')
    header.headDocId = `${GROUP}.0012-N`
    header.reviewStatus = 'approved'
    await announceDocUpdated(wrapper)

    expect(countOf('/return-point')).toBe(2)
    expect(countOf('/sequence')).toBe(2)
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'BBBBBBB' })
  })

  // 0552 T0009 — pouring/saving a work plan changes sequence rows and commit markers without
  // necessarily moving the workflow head. That mutation signal must therefore bypass the
  // unchanged workflow signature and retire any pre-mutation round via force.
  it('붓기 sequence-updated 이후 서명이 같아도 return-point와 sequence를 강제 재조회한다', async () => {
    const wrapper = await openTrTab()
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)
    expect(countOf('/sequence')).toBe(1)
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'AAAAAAA' })

    sequenceBody = sequenceWithCommit('POURED1')
    wrapper.findComponent({ name: 'DocWorkflow' }).vm.$emit('sequence-updated')
    await flushPromises()

    // The real handler also refreshes detail. Its ensuing doc-updated has the same signature,
    // so it must dedup against the just-completed forced round instead of adding a third GET.
    await announceDocUpdated(wrapper)

    expect(fetchDocSpy).toHaveBeenCalledWith(TR_TAB)
    expect(countOf('/return-point')).toBe(2)
    expect(countOf('/sequence')).toBe(2)
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'POURED1' })
  })

  it('왕복이 아직 끝나지 않았으면 재진입은 그 왕복에 합류한다 (in-flight join)', async () => {
    deferReturnPoint = true
    const wrapper = await openTrTab()
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)

    // 같은 상태로 두 번 더 알림 — 합류만 하고 새 GET 은 없다.
    await announceDocUpdated(wrapper)
    await announceDocUpdated(wrapper)
    expect(countOf('/return-point')).toBe(1)
    expect(countOf('/sequence')).toBe(0)

    pendingReturnPoint.forEach(r => r({ data: { ok: true, return_point: RETURN_POINT } }))
    await flushPromises()
    expect(countOf('/return-point')).toBe(1)
    expect(countOf('/sequence')).toBe(1)
  })

  it('왕복 도중에 상태가 바뀌면 그 변화는 버려지지 않고 한 번 더 읽는다 (trailing)', async () => {
    deferReturnPoint = true
    const wrapper = await openTrTab()
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)

    // 왕복이 떠 있는 동안 워크플로가 실제로 전진했다.
    sequenceBody = sequenceWithCommit('CCCCCCC')
    header.headDocId = `${GROUP}.0012-N`
    await announceDocUpdated(wrapper)
    expect(countOf('/return-point')).toBe(1) // 아직은 합류

    deferReturnPoint = false
    pendingReturnPoint.forEach(r => r({ data: { ok: true, return_point: RETURN_POINT } }))
    await flushPromises()

    // 합류한 쪽의 키가 달랐으므로 끝난 뒤 한 번 더 읽는다.
    expect(countOf('/return-point')).toBe(2)
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'CCCCCCC' })
  })

  it('늦게 도착한 옛 응답이 최신 시퀀스를 덮어쓰지 않는다 (generation guard)', async () => {
    const wrapper = await openTrTab()
    await landDetail(wrapper)
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'AAAAAAA' })

    // 새 왕복을 띄워 두고(응답 보류), 그 사이 강제 재조회가 세대를 올린다.
    deferSequence = true
    header.headDocId = `${GROUP}.0012-N`
    await announceDocUpdated(wrapper)
    expect(countOf('/sequence')).toBe(2)

    // 앞으로 복원 → 성공 경로가 force 재조회를 돌린다(세대 +1).
    deferSequence = false
    sequenceBody = sequenceWithCommit('DDDDDDD')
    postRequest.mockResolvedValue({
      data: { ok: true, restored: [TR_TAB], stopped_doc_id: null, reached_front: true },
    })
    const strip = wrapper.findComponent({ name: 'DocWorkflow' })
    strip.vm.$emit('return-to', { index: 0, code: 'TR' })
    await flushPromises()
    const modal = wrapper.findAllComponents(ConfirmModal).find(m => m.props('visible') === true)
    expect(modal, '앞으로 복원 확인창이 떠 있어야 한다').toBeTruthy()
    modal!.vm.$emit('confirm')
    await flushPromises()
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'DDDDDDD' })

    // 이제서야 도착한 보류 응답 — 최신 상태를 되돌리면 안 된다.
    pendingSequence.forEach(r => r({ data: { sequence: sequenceWithCommit('STALE00') } }))
    await flushPromises()
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'DDDDDDD' })
  })

  it('전환이 확정된 생성 경로는 떠나는 문서를 다시 읽지 않는다 (R3)', async () => {
    viewState.canNextAction = true
    const wrapper = await openTrTab()
    await landDetail(wrapper)
    header.headType = 'N'
    await flushPromises()
    fetchDocSpy.mockClear()

    postRequest.mockResolvedValue({ data: { doc_id: `${GROUP}.0012-N` } })
    wrapper.findComponent({ name: 'ReviewActionBar' }).vm.$emit('create-approved')
    await flushPromises()
    const modal = wrapper.findAllComponents(ConfirmModal).find(m => m.props('visible') === true)
    expect(modal, '승인문서 생성 확인창이 떠 있어야 한다').toBeTruthy()
    modal!.vm.$emit('confirm')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/documents/next-approved',
      expect.objectContaining({ type_code: 'N', prev_doc_id: ROOT }),
    )
    // 새 문서로 이동이 확정된 경로다 — 떠나는 문서의 detail 번들을 다시 띄우지 않는다.
    expect(fetchDocSpy).not.toHaveBeenCalled()
    expect(wrapper.emitted('related-doc-created')).toBeTruthy()
  })

  it('다른 루트의 문서로 옮겨 가면 새 루트로 다시 읽는다', async () => {
    const wrapper = await openTrTab()
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)

    header.rootDocId = OTHER_ROOT
    await announceDocUpdated(wrapper)

    expect(countOf('/return-point')).toBe(2)
    expect(urlsWith('/return-point')[1]).toContain(encodeURIComponent(OTHER_ROOT))
  })

  // rev0 rejection: `returnPointResultKeys` reused a completed result for as long as
  // MainPanel lived, with no path that ever emptied it. Leaving the tab and coming back
  // to an IDENTICAL workflow signature must still ask the server — the DocHeader instance
  // that produced the cached key is gone, and whatever changed server-side while it was
  // gone (a rewind's return-point range, a sequence entry's tr_commit, …) is exactly what
  // the signature does not capture.
  it('탭을 벗어났다 같은 상태로 돌아오면 캐시된 결과를 재사용하지 않는다 (재오픈은 새 세대)', async () => {
    const wrapper = await mountMainPanel({
      tabs: [
        { id: TR_TAB, title: 'TR', path: '', type: 'md', typeCode: 'TR' } as any,
        { id: OTHER_TAB, title: 'N', path: '', type: 'md', typeCode: 'N' } as any,
      ],
      activeTabId: TR_TAB,
      stubs: { DocHeader: DocHeaderStub, DocWorkflow: true },
    })
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)
    expect(countOf('/sequence')).toBe(1)

    // 탭을 벗어난다 — DocHeader 인스턴스가 통째로 unmount 된다.
    const tabsStore = useTabsStore()
    tabsStore.activeTabId = OTHER_TAB
    await flushPromises()

    // 같은 탭으로, 같은 root·같은 워크플로 상태로 돌아온다. 서명만 보면 이전과 100% 같다.
    tabsStore.activeTabId = TR_TAB
    await flushPromises()
    await announceDocUpdated(wrapper)

    expect(countOf('/return-point')).toBe(2)
    expect(countOf('/sequence')).toBe(2)
  })

  // rev1 rejection: clearing only `returnPointResultKeys` on unmount left the outgoing
  // in-flight round untouched. Leaving mid-flight and coming straight back to an IDENTICAL
  // signature must NOT join that outgoing round (it belongs to a session the user already
  // left), and the outgoing round's late arrival must not be allowed to write a result for
  // the root at all — it has to be retired outright, generation and all.
  it('return-point 왕복 중에 탭을 벗어났다 같은 상태로 돌아오면 그 왕복에 합류하지 않고 새로 왕복한다', async () => {
    deferReturnPoint = true
    const wrapper = await mountMainPanel({
      tabs: [
        { id: TR_TAB, title: 'TR', path: '', type: 'md', typeCode: 'TR' } as any,
        { id: OTHER_TAB, title: 'N', path: '', type: 'md', typeCode: 'N' } as any,
      ],
      activeTabId: TR_TAB,
      stubs: { DocHeader: DocHeaderStub, DocWorkflow: true },
    })
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)
    expect(countOf('/sequence')).toBe(0) // return-point 가 아직 안 끝나 시퀀스는 아직 나가지 않았다
    const outgoing = pendingReturnPoint.slice()
    expect(outgoing).toHaveLength(1)

    // 탭을 벗어난다 — return-point 왕복이 떠 있는 채로 DocHeader 인스턴스가 unmount 된다.
    const tabsStore = useTabsStore()
    tabsStore.activeTabId = OTHER_TAB
    await flushPromises()

    // 같은 탭으로, 같은 root·같은 워크플로 상태로 돌아온다.
    tabsStore.activeTabId = TR_TAB
    await flushPromises()
    await announceDocUpdated(wrapper)

    // 재진입은 옛(떠 있는) 왕복에 합류하지 않고 새 GET 을 낸다 — 합류였다면 여전히 1이다.
    expect(countOf('/return-point')).toBe(2)
    expect(pendingReturnPoint.filter(r => !outgoing.includes(r))).toHaveLength(1)

    // 옛 왕복이 이제서야 도착한다 — 세대가 낡아 다음 단계(시퀀스)로 넘어가지 못하고 죽는다.
    outgoing.forEach(r => r({ data: { ok: true, return_point: RETURN_POINT } }))
    await flushPromises()
    expect(countOf('/sequence')).toBe(0)

    // 재진입으로 시작된 새 왕복이 도착하면 정상적으로 시퀀스까지 이어진다.
    pendingReturnPoint.filter(r => !outgoing.includes(r)).forEach(r => r({ data: { ok: true, return_point: RETURN_POINT } }))
    await flushPromises()
    expect(countOf('/sequence')).toBe(1)
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'AAAAAAA' })
  })

  // rev1 rejection: the same race exists a step later — leaving while the SEQUENCE half of
  // the pair is the one still out (return-point already answered). The outgoing round's
  // stale sequence body must never land after the fact and must not block the fresh round
  // that the re-open starts from writing its own (correct) result.
  it('sequence 왕복 중에 탭을 벗어났다 같은 상태로 돌아오면 그 왕복에 합류하지 않고 새로 왕복한다', async () => {
    deferSequence = true
    const wrapper = await mountMainPanel({
      tabs: [
        { id: TR_TAB, title: 'TR', path: '', type: 'md', typeCode: 'TR' } as any,
        { id: OTHER_TAB, title: 'N', path: '', type: 'md', typeCode: 'N' } as any,
      ],
      activeTabId: TR_TAB,
      stubs: { DocHeader: DocHeaderStub, DocWorkflow: true },
    })
    await landDetail(wrapper)
    expect(countOf('/return-point')).toBe(1)
    expect(countOf('/sequence')).toBe(1)
    const outgoingSequence = pendingSequence.slice()
    expect(outgoingSequence).toHaveLength(1)

    // 탭을 벗어난다 — sequence 왕복이 떠 있는 채로 DocHeader 인스턴스가 unmount 된다.
    const tabsStore = useTabsStore()
    tabsStore.activeTabId = OTHER_TAB
    await flushPromises()

    // 같은 탭으로, 같은 root·같은 워크플로 상태로 돌아온다.
    tabsStore.activeTabId = TR_TAB
    await flushPromises()
    await announceDocUpdated(wrapper)

    // 재진입은 옛 왕복에 합류하지 않고 return-point 부터 다시 새로 왕복한다.
    expect(countOf('/return-point')).toBe(2)
    expect(countOf('/sequence')).toBe(2)
    const freshSequence = pendingSequence.filter(r => !outgoingSequence.includes(r))
    expect(freshSequence).toHaveLength(1)

    // 옛 왕복의 시퀀스 응답이 이제서야 도착한다 — stale 값이지만 세대가 낡아 버려져야 한다.
    outgoingSequence.forEach(r => r({ data: { sequence: sequenceWithCommit('STALEOLD') } }))
    await flushPromises()
    expect(slotCommits(wrapper)).toEqual([])

    // 재진입으로 시작된 새 왕복의 시퀀스 응답이 도착하면 그 값이 정상적으로 반영된다.
    sequenceBody = sequenceWithCommit('FRESH01')
    freshSequence.forEach(r => r({ data: { sequence: sequenceBody } }))
    await flushPromises()
    expect(slotCommits(wrapper)[0]).toMatchObject({ commit: 'FRESH01' })
  })
})
