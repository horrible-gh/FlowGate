/**
 * flowgate.default.0406 T0017 — "저장도 안되고 멘트도 이상하고 뭐야 대체?"
 *
 * 반려문이 인용한 문장은 [작업계획 적용]으로 부은 시퀀스를 저장할 때 뜨는 것이다:
 *   "이 창을 연 뒤 작업계획이 바뀌었습니다. 창을 닫고 다시 열어 최신 계획을 부어 주세요."
 *
 * 붓기 후보(=부을 줄과 그 계획의 리비전)는 시퀀스 칸이 마운트될 때 한 번만 읽혔다. 그런데
 * 계획 편집기는 같은 화면 바로 아래에 있고, 거기서 계획을 저장할 때마다 그 문서의 리비전이
 * 오른다. 그래서 방금 고친 계획을 부으면
 *   1) 줄에 실려 오는 전달멘트가 옛 리비전의 것이고("멘트도 이상하고"),
 *   2) 저장은 낡은 wp_revision_no 때문에 wp_changed 로 거절되며("저장도 안되고"),
 *   3) 안내대로 창을 닫고 다시 열어도 그 캐시는 그대로여서 같은 실패가 반복됐다.
 *
 * 여기서 고정하는 것은 그 세 가지다. 차림표를 여는 순간 계획을 다시 읽고, 부을 때 그 답을
 * 기다린다 — 그러면 부어지는 멘트도 저장이 보내는 리비전도 언제나 "지금의 계획"이 된다.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
  patchRequest: (...a: unknown[]) => patchRequest(...a),
  putRequest: (...a: unknown[]) => postRequest(...a),
  deleteRequest: (...a: unknown[]) => postRequest(...a),
}))

import DocWorkflow from '@main/components/DocWorkflow.vue'
import WorkflowDecisionModal, { type PourPayload } from '@main/components/WorkflowDecisionModal.vue'

const WP_DOC_ID = 'flowgate.default.0406.0004-WP'
const OWNER_DOC_ID = 'flowgate.default.0406.0001-B'

const WP_TAB = {
  id: WP_DOC_ID,
  title: '0406 작업계획',
  path: 'documents/flowgate/main/default/0406/0004-WP_plan.json',
  type: 'json',
  typeCode: 'WP',
  projectId: 'flowgate',
}

/** 서버가 들고 있는 계획. 편집기에서 저장할 때마다 리비전이 오르고 멘트가 바뀐다. */
const plan = { revision: 1, note: '첫 멘트' }

/** 계획 편집기의 [저장]과 같은 일: 본문이 바뀌고 문서 리비전이 한 칸 오른다. */
function savePlanEdit(note: string) {
  plan.revision += 1
  plan.note = note
}

function candidateResponse(mode: 'append' | 'replace_after') {
  return {
    data: {
      wp_doc_id: WP_DOC_ID,
      wp_revision_no: plan.revision,
      workflow_doc_id: OWNER_DOC_ID,
      mode,
      plan_step_count: 1,
      rows: [
        {
          type: 'T', label: '작업지시', status: 'pending', locked: false, poured: true,
          note: plan.note, note_source: 'step', origin: 'plan', plan_key: 'T#1',
          source_doc_id: WP_DOC_ID, source_revision_no: plan.revision,
        },
        {
          type: 'TR', label: '작업레포트', status: 'pending', locked: false, poured: true,
          note: '', note_source: null, origin: 'auto', plan_key: null,
          source_doc_id: null, source_revision_no: null,
        },
      ],
      row_count_change: { before: 0, after: 2, deleted: 0, added: 2 },
      notifications: [],
      workflow_tag: `seq406-r${plan.revision}-i0`,
    },
  }
}

function mountStrip() {
  return mount(DocWorkflow, {
    props: {
      tab: WP_TAB as never,
      workflowDecided: true,
      parentRDocId: OWNER_DOC_ID,
      stepStates: [] as never,
      canNextAction: false,
    },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

type Strip = ReturnType<typeof mountStrip>

/** 사람이 하는 그대로: 버튼을 눌러 차림표를 열고, 갈래 하나를 고른다. */
async function openAndPour(wrapper: Strip, index = 0) {
  await wrapper.find('.wf-apply-btn').trigger('click')
  await flushPromises()
  await wrapper.findAll('.wf-apply-item')[index].trigger('click')
  await flushPromises()
}

async function clickSave(wrapper: Strip) {
  await wrapper.findAll('button').find(b => b.text().includes('저장'))!.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  vi.clearAllMocks()
  plan.revision = 1
  plan.note = '첫 멘트'
  postRequest.mockImplementation((url: string, body: { mode: 'append' | 'replace_after' }) => {
    if (String(url).endsWith('/work-plan/sequence-candidates')) {
      return Promise.resolve(candidateResponse(body.mode))
    }
    return Promise.resolve({ data: {} })
  })
  getRequest.mockResolvedValue({ data: { items: [] } })
  patchRequest.mockResolvedValue({ data: { status: 'updated' } })
})

describe('0406 T0017 — 방금 고친 계획을 붓는다', () => {
  it('계획을 고쳐 저장한 뒤 차림표를 열면, 부어지는 멘트가 고친 멘트다', async () => {
    const wrapper = mountStrip()
    await flushPromises()

    // 화면 아래 편집기에서 계획을 고쳐 저장했다. 시퀀스 칸은 다시 마운트되지 않는다.
    savePlanEdit('고친 멘트')
    await openAndPour(wrapper)

    const poured = wrapper.findComponent(WorkflowDecisionModal).props('poured') as PourPayload
    expect(poured.wpRevisionNo).toBe(2)
    expect(poured.rows[0].note).toBe('고친 멘트')
    // 그리고 그것이 창에 실제로 보인다 — 사람이 읽는 것은 이 입력칸이다.
    expect(wrapper.find<HTMLInputElement>('.wdm-note-input').element.value).toBe('고친 멘트')
  })

  it('저장은 방금 읽은 리비전을 보낸다 — 서버가 wp_changed 로 거절할 이유가 없다', async () => {
    const wrapper = mountStrip()
    await flushPromises()

    savePlanEdit('고친 멘트')
    await openAndPour(wrapper)
    await clickSave(wrapper)

    const [url, body] = patchRequest.mock.calls[0]
    expect(url).toBe('/api/v1/workflow/sequence')
    expect(body.expected_plan).toEqual({
      wp_doc_id: WP_DOC_ID, wp_revision_no: 2, mode: 'append',
    })
    // 저장되는 줄의 멘트와 출처 리비전도 같은 계획의 것이다.
    expect(body.items[0]).toMatchObject({
      type: 'T', note: '고친 멘트', source_doc_id: WP_DOC_ID, source_revision_no: 2,
    })
  })

  it('정말로 남이 계획을 바꿨을 때, 안내대로 닫고 다시 열면 최신 계획이 부어진다', async () => {
    const wrapper = mountStrip()
    await flushPromises()
    await openAndPour(wrapper)

    // 창을 열어 둔 사이에 계획이 바뀌었다. 이때는 서버가 거절하는 것이 맞다.
    savePlanEdit('남이 고친 멘트')
    patchRequest.mockRejectedValueOnce({ response: { data: { error: 'wp_changed' } } })
    await clickSave(wrapper)

    const modal = wrapper.findComponent(WorkflowDecisionModal)
    expect(patchRequest.mock.calls[0][1].expected_plan.wp_revision_no).toBe(1)
    expect(modal.props('visible')).toBe(true)

    // 안내가 시키는 대로 창을 닫고 다시 연다. 예전에는 이 길이 막혀 있었다 — 후보가
    // 마운트 때의 것 그대로여서, 몇 번을 다시 열어도 같은 낡은 리비전을 보냈다.
    await modal.vm.$emit('update:visible', false)
    await flushPromises()
    await openAndPour(wrapper)

    const poured = modal.props('poured') as PourPayload
    expect(poured.wpRevisionNo).toBe(2)
    expect(poured.rows[0].note).toBe('남이 고친 멘트')

    await clickSave(wrapper)
    const lastBody = patchRequest.mock.calls[patchRequest.mock.calls.length - 1][1]
    expect(lastBody.expected_plan.wp_revision_no).toBe(2)
    expect(lastBody.items[0].note).toBe('남이 고친 멘트')
  })

  it('다시 읽는 동안에도 차림표는 비지 않는다 (M0020 — 아무것도 저 혼자 움직이지 않는다)', async () => {
    const wrapper = mountStrip()
    await flushPromises()

    // 답이 오기 전의 순간. 예전 값이 그대로 그려져 있어야 한다.
    await wrapper.find('.wf-apply-btn').trigger('click')
    expect(wrapper.findAll('.wf-apply-item')).toHaveLength(2)
    expect(wrapper.find('.wf-apply-msg').exists()).toBe(false)
    expect(wrapper.find<HTMLButtonElement>('.wf-apply-btn').element.disabled).toBe(false)

    await flushPromises()
    expect(wrapper.findAll('.wf-apply-item')).toHaveLength(2)
  })
})
