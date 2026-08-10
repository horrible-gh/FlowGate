/**
 * flowgate.default.0399 T0014 set 1 — [작업계획 적용] 버튼과 두 갈래 리스트, 그리고
 * 누르면 시퀀스 수정 창이 계획 줄로 채워진 채 열리는 데까지.
 *
 * These render the real DocWorkflow and the real WorkflowDecisionModal. The one thing the
 * design keeps repeating is that pressing a mode changes NOTHING until [저장] — so the
 * assertions are about what is on screen and what is sent, never about a prop handover.
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

const WP_DOC_ID = 'flowgate.default.0450.0002-WP'
const OWNER_DOC_ID = 'flowgate.default.0450.0001-R'

const WP_TAB = {
  id: WP_DOC_ID,
  title: '0450 작업계획',
  path: 'documents/flowgate/main/default/0450/0002-WP_plan.json',
  type: 'json',
  typeCode: 'WP',
  projectId: 'flowgate',
}

const STEP_STATES = [
  { code: 'R', className: 'done', iconClass: 'check-circle', visual: 'done' },
  { code: 'WP', className: 'current', iconClass: 'radio-button', visual: 'current' },
]

function row(type: string, over: Record<string, unknown> = {}) {
  return {
    type,
    label: type,
    status: 'pending',
    locked: false,
    poured: false,
    note: '',
    origin: 'manual',
    plan_key: null,
    source_doc_id: null,
    source_revision_no: null,
    ...over,
  }
}

const APPEND_ROWS = [
  row('R', { status: 'done', locked: true, label: '요건정의' }),
  row('WP', { status: 'done', locked: true, label: '작업계획' }),
  row('P', { label: '프로토콜설계' }),
  row('P', {
    label: '프로토콜설계', poured: true, origin: 'plan', plan_key: 'P#1',
    note: '레거시 API 호환 확인', source_doc_id: WP_DOC_ID, source_revision_no: 1,
  }),
  row('T', {
    label: '작업지시', poured: true, origin: 'plan', plan_key: 'T#1',
    note: '테스트 포함 구현', source_doc_id: WP_DOC_ID, source_revision_no: 1,
  }),
  row('TR', { label: '작업레포트', poured: true, origin: 'auto' }),
]

function candidateResponse(mode: 'append' | 'replace_after') {
  return {
    data: {
      wp_doc_id: WP_DOC_ID,
      wp_revision_no: 1,
      workflow_doc_id: OWNER_DOC_ID,
      mode,
      plan_step_count: 3,
      rows: APPEND_ROWS,
      row_count_change: mode === 'append'
        ? { before: 3, after: 6, deleted: 0, added: 3 }
        : { before: 3, after: 5, deleted: 1, added: 3 },
      notifications: mode === 'append'
        ? [{ code: 'type_overlap', severity: 'warning', count: 1, types: ['P'] }]
        : [],
      workflow_tag: 'seq451-r73510-i6',
    },
  }
}

function mountStrip(tab = WP_TAB) {
  return mount(DocWorkflow, {
    props: {
      tab: tab as never,
      workflowDecided: true,
      parentRDocId: OWNER_DOC_ID,
      stepStates: STEP_STATES as never,
      canNextAction: false,
    },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  // The sentences are the deliverable here (D0010/L0011 deferred every one of them to this
  // T stage), so the assertions read the Korean the reviewer will see.
  i18n.global.locale.value = 'ko'
  vi.clearAllMocks()
  postRequest.mockImplementation((url: string, body: { mode: 'append' | 'replace_after' }) => {
    if (String(url).endsWith('/work-plan/sequence-candidates')) {
      return Promise.resolve(candidateResponse(body.mode))
    }
    return Promise.resolve({ data: {} })
  })
  getRequest.mockResolvedValue({ data: { items: [] } })
  patchRequest.mockResolvedValue({ data: { status: 'updated' } })
})

describe('작업계획 문서의 [작업계획 적용] 버튼', () => {
  it('작업계획 문서에만 붙는다 — 다른 문서는 지금과 똑같다', async () => {
    const other = mountStrip({ ...WP_TAB, id: 'flowgate.default.0450.0004-D', typeCode: 'D' })
    await flushPromises()
    expect(other.find('.wf-apply-btn').exists()).toBe(false)
    // 그리고 그 문서에서는 계획을 읽으러 가지도 않는다.
    expect(postRequest).not.toHaveBeenCalled()

    const plan = mountStrip()
    await flushPromises()
    expect(plan.find('.wf-apply-btn').exists()).toBe(true)
  })

  it('두 갈래를 열기 전에 줄 수가 어떻게 변하는지 이미 적혀 있다', async () => {
    const wrapper = mountStrip()
    await flushPromises()
    await wrapper.find('.wf-apply-btn').trigger('click')

    const items = wrapper.findAll('.wf-apply-item')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('뒤에 이어 붙이기')
    expect(items[0].find('.wf-apply-delta').text().replace(/\s+/g, '')).toBe('3줄→6줄+3')
    expect(items[1].text()).toContain('이후 단계 교체')
    expect(items[1].find('.wf-apply-delta').text().replace(/\s+/g, '')).toBe('3줄→5줄−1+3')
  })

  it('어느 쪽을 눌러도 바로 적용되지 않는다고 차림표가 말한다', async () => {
    const wrapper = mountStrip()
    await flushPromises()
    await wrapper.find('.wf-apply-btn').trigger('click')
    expect(wrapper.find('.wf-apply-foot').text()).toContain('바로 적용되지 않습니다')
  })

  // ── 0399 M0020 — "버튼이 지글지글" 반려에 대응하는 자리 ────────────────────

  it('화면이 다시 그려져도 버튼은 막히지 않고, 계획도 다시 읽지 않는다', async () => {
    const wrapper = mountStrip()
    // 첫 그림에서부터 이미 눌 수 있어야 한다 — 계획을 다 읽기 전에도.
    expect(wrapper.find<HTMLButtonElement>('.wf-apply-btn').element.disabled).toBe(false)
    expect(wrapper.find('.wf-apply-reason').exists()).toBe(false)
    await flushPromises()

    // 페이지가 자리를 잡는 동안 시퀀스 칸은 여러 번 다시 그려진다. 예전에는 그때마다
    // 계획을 다시 읽으러 갔고, 늦게 온 답이 서로를 덮어써서 사유 문구가 번갈아 떴다.
    for (const extra of [3, 4, 5]) {
      await wrapper.setProps({
        stepStates: [...STEP_STATES, ...Array(extra - 2).fill(STEP_STATES[1])] as never,
      })
      await flushPromises()
      expect(wrapper.find<HTMLButtonElement>('.wf-apply-btn').element.disabled).toBe(false)
      expect(wrapper.find('.wf-apply-reason').exists()).toBe(false)
    }
    // 두 갈래를 한 번씩 물어본 것이 전부다.
    expect(postRequest).toHaveBeenCalledTimes(2)
  })

  it('승인 전 계획도 승인된 계획과 똑같이 눌리고 똑같이 열린다', async () => {
    postRequest.mockImplementation((_url: string, body: { mode: 'append' | 'replace_after' }) => {
      const res = candidateResponse(body.mode)
      res.data.notifications = [] as never
      // 문서가 검토 중이어도 서버는 이제 아무 말도 하지 않는다(승인 검사를 걷어냈다).
      return Promise.resolve(res)
    })
    const wrapper = mountStrip()
    await flushPromises()

    expect(wrapper.find<HTMLButtonElement>('.wf-apply-btn').element.disabled).toBe(false)
    await wrapper.find('.wf-apply-btn').trigger('click')
    expect(wrapper.findAll('.wf-apply-item')).toHaveLength(2)
    expect(wrapper.find('.wf-apply-menu').text()).not.toContain('승인')
  })

  it('옮길 단계가 없으면 누른 뒤 차림표 안에서 그렇게 말한다', async () => {
    postRequest.mockImplementation((_url: string, body: { mode: 'append' | 'replace_after' }) => {
      const res = candidateResponse(body.mode)
      res.data.plan_step_count = 0
      return Promise.resolve(res)
    })
    const wrapper = mountStrip()
    await flushPromises()
    expect(wrapper.find<HTMLButtonElement>('.wf-apply-btn').element.disabled).toBe(false)

    await wrapper.find('.wf-apply-btn').trigger('click')
    expect(wrapper.find('.wf-apply-msg').text()).toContain('옮길 계획 단계가 없습니다.')
    expect(wrapper.findAll('.wf-apply-item')).toHaveLength(0)
  })

  it('표로 열 수 없는 계획은 차림표 안에서 못 읽는다고 말하고 [다시 시도]를 준다', async () => {
    postRequest.mockRejectedValue({ response: { status: 409, data: { code: 'wp_unreadable' } } })
    const wrapper = mountStrip()
    await flushPromises()
    expect(wrapper.find<HTMLButtonElement>('.wf-apply-btn').element.disabled).toBe(false)

    await wrapper.find('.wf-apply-btn').trigger('click')
    expect(wrapper.find('.wf-apply-msg').text())
      .toContain('이 작업계획을 표로 열 수 없어 적용할 수 없습니다.')

    // 고쳐 두고 다시 시도하면 그 자리에서 두 갈래가 나온다.
    postRequest.mockImplementation((_url: string, body: { mode: 'append' | 'replace_after' }) =>
      Promise.resolve(candidateResponse(body.mode)))
    await wrapper.find('.wf-apply-retry').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.wf-apply-item')).toHaveLength(2)
  })

  it('한쪽 갈래만 실패해도 나머지 한 갈래는 그대로 고를 수 있다', async () => {
    postRequest.mockImplementation((_url: string, body: { mode: 'append' | 'replace_after' }) => {
      if (body.mode === 'replace_after') return Promise.reject({ response: { status: 500 } })
      return Promise.resolve(candidateResponse(body.mode))
    })
    const wrapper = mountStrip()
    await flushPromises()
    await wrapper.find('.wf-apply-btn').trigger('click')

    const items = wrapper.findAll('.wf-apply-item')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('뒤에 이어 붙이기')
    expect(wrapper.find('.wf-apply-msg').exists()).toBe(false)
  })


  it('저장하지 않고 닫으면 부어 넣은 목록이 창에 남지 않는다', async () => {
    // M0020 "[작업계획 적용] 한다음에 저장하지도 않았는데 주구장창 적용되어 있다".
    const wrapper = mountStrip()
    await flushPromises()
    await wrapper.find('.wf-apply-btn').trigger('click')
    await wrapper.findAll('.wf-apply-item')[0].trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.wdm-seq-item').length).toBeGreaterThan(0)

    const modal = wrapper.findComponent(WorkflowDecisionModal)
    await modal.vm.$emit('update:visible', false)
    await flushPromises()
    expect(modal.props('poured')).toBeNull()
    expect(patchRequest).not.toHaveBeenCalled()

    // 다시 [시퀀스 수정]으로 열면 서버에 저장된 시퀀스만 보인다.
    await wrapper.find('.wf-edit-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.wdm-banner').exists()).toBe(false)
    expect(wrapper.findAll('.wdm-seq-item')).toHaveLength(0)
  })

  it('방식을 고르면 시퀀스 수정 창이 그 상태로 열린다', async () => {
    const wrapper = mountStrip()
    await flushPromises()
    await wrapper.find('.wf-apply-btn').trigger('click')
    await wrapper.findAll('.wf-apply-item')[0].trigger('click')
    await flushPromises()

    const modal = wrapper.findComponent(WorkflowDecisionModal)
    expect(modal.props('visible')).toBe(true)
    const poured = modal.props('poured') as PourPayload
    expect(poured.mode).toBe('append')
    expect(poured.workflowTag).toBe('seq451-r73510-i6')
    expect(poured.rows).toHaveLength(APPEND_ROWS.length)
    // 차림표가 닫히고, 아무것도 저장되지 않았다.
    expect(wrapper.find('.wf-apply-menu').exists()).toBe(false)
    expect(patchRequest).not.toHaveBeenCalled()
  })
})

// ── 시퀀스 수정 창이 계획 줄로 채워진 상태 ──────────────────────────────────

function pourPayload(over: Partial<PourPayload> = {}): PourPayload {
  return {
    wpDocId: WP_DOC_ID,
    // 0403 NR0004 F2·F4 — 창이 열릴 때 함께 받아 저장할 때 되돌려 보내는 두 값.
    wpRevisionNo: 1,
    workflowDocId: OWNER_DOC_ID,
    wpShortCode: 'WP0002',
    mode: 'append',
    planStepCount: 3,
    rows: APPEND_ROWS as never,
    rowCountChange: { before: 3, after: 6, deleted: 0, added: 3 },
    notifications: [{ code: 'type_overlap', severity: 'warning', count: 1, types: ['P'] }],
    workflowTag: 'seq451-r73510-i6',
    ...over,
  }
}

/** Mounted closed and then opened, the way the strip actually opens it — the dialog builds
 *  its state on the open, not on the mount. */
async function mountModal(poured: PourPayload | null = pourPayload()) {
  const wrapper = mount(WorkflowDecisionModal, {
    props: { visible: false, mode: 'edit' as const, docId: OWNER_DOC_ID, poured },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
  await wrapper.setProps({ visible: true })
  await flushPromises()
  return wrapper
}

describe('계획 줄로 채워진 시퀀스 수정 창', () => {
  it('무엇을 부었는지와 아직 저장되지 않았음을 위에 적고, 되돌릴 길을 준다', async () => {
    const wrapper = await mountModal()

    const banner = wrapper.find('.wdm-banner')
    expect(banner.text()).toContain('WP0002')
    expect(banner.text()).toContain('3단계')
    expect(banner.text()).toContain('아직 저장되지 않았습니다')
    expect(wrapper.find('.wdm-undo').exists()).toBe(true)
  })

  it('겹침 알림을 띄우되 저장을 막지는 않는다', async () => {
    const wrapper = await mountModal()

    const warnings = wrapper.findAll('.wdm-banner--warn').map(w => w.text()).join('\n')
    expect(warnings).toContain('겹칩니다(P)')
    const save = wrapper.findAll('button').find(b => b.text().includes('저장'))
    expect(save?.attributes('disabled')).toBeUndefined()
  })

  // ── 0399 M0020 / 시안 fgh29xnk v3 · 화면 3 — 부은 뒤 손본 양을 숫자로 말한다 ──

  it('멘트 없는 단계는 갯수만이 아니라 이름과 사유까지 적고, 채우면 사라진다', async () => {
    const wrapper = await mountModal()

    const missing = wrapper.findAll('.wdm-banner--warn')
      .map(w => w.text()).find(text => text.includes('멘트 없는 단계'))
    expect(missing).toContain('멘트 없는 단계 1개')
    expect(missing).toContain('P 프로토콜설계(직접 넣은 줄)')

    // 서버가 부을 때 세어 둔 값이 아니라 지금 목록에서 센 값이므로, 채우면 그 자리에서 준다.
    const empty = wrapper.findAll('.wdm-note-input')
      .find(input => (input.element as HTMLInputElement).value === '')
    await empty!.setValue('여기를 채웠다')
    expect(wrapper.findAll('.wdm-banner--warn')
      .map(w => w.text()).some(text => text.includes('멘트 없는 단계'))).toBe(false)
  })

  it('부은 뒤 줄을 지우면 배너와 미리보기 캡션이 그 차이를 적는다', async () => {
    const wrapper = await mountModal()

    // 부은 직후에는 손댄 것이 없으므로 "그 뒤 직접 …" 이 아예 없다.
    expect(wrapper.find('.wdm-banner').text()).not.toContain('그 뒤 직접')
    expect(wrapper.find('.wdm-preview-label').text()).toContain('계획 3단계 중 2개 적용')

    // 계획에서 온 줄(P) 하나를 지운다.
    const planRow = wrapper.findAll('.wdm-seq-item.from-plan')[0]
    const del = planRow.findAll('button')
      .find(b => b.attributes('title')?.includes('삭제') || b.classes().includes('del'))
      ?? planRow.findAll('.wdm-seq-btn').at(-1)
    await del!.trigger('click')

    expect(wrapper.find('.wdm-banner').text()).toContain('1줄 지움')
    const caption = wrapper.find('.wdm-preview-label').text()
    expect(caption).toContain('계획 3단계 중 1개 적용')
    // 붓기 전부터 계획 3단계 중 1개는 놓을 수 없어 빠져 있었고, 여기서 1개를 더 지웠다.
    expect(caption).toContain('2개 뺌')
  })

  it('계획에서 온 줄은 멘트와 출처를 달고 그려진다', async () => {
    const wrapper = await mountModal()

    const planRows = wrapper.findAll('.wdm-seq-item.from-plan')
    expect(planRows).toHaveLength(2)
    expect((planRows[0].find('.wdm-note-input').element as HTMLInputElement).value)
      .toBe('레거시 API 호환 확인')
    expect(planRows[0].find('.wdm-plan-badge').text()).toBe('계획 WP0002')
    // 사람이 넣어 둔 빈 줄은 멘트가 없다고 눈에 띄게 말한다.
    const rows = wrapper.findAll('.wdm-seq-item')
    expect(rows[0].find('.wdm-seq-msg--empty').find('.wdm-note-input').attributes('placeholder'))
      .toContain('멘트 없음')
  })

  it('줄마다 멘트를 직접 입력할 수 있다', async () => {
    const wrapper = await mountModal()

    const input = wrapper.findAll('.wdm-seq-item')[0].find('.wdm-note-input')
    await input.setValue('직접 적은 멘트')
    await flushPromises()

    await wrapper.findAll('button').find(b => b.text().includes('저장'))!.trigger('click')
    await flushPromises()
    const [, body] = patchRequest.mock.calls[0]
    expect(body.items[0].note).toBe('직접 적은 멘트')
  })

  it('줄을 옮기면 멘트가 그 줄을 따라간다', async () => {
    const wrapper = await mountModal()

    // 자동으로 따라붙는 줄에는 멘트 칸 자체가 없으므로 빈 문자열로 읽는다.
    const notes = () => wrapper.findAll('.wdm-seq-item')
      .map(r => (r.find('.wdm-note-input').exists()
        ? (r.find('.wdm-note-input').element as HTMLInputElement).value
        : ''))

    const before = notes()
    expect(before[1]).toContain('레거시 API 호환 확인')

    // 계획에서 온 P 줄을 한 칸 위로.
    const upButtons = wrapper.findAll('.wdm-seq-item')[1].findAll('.wdm-seq-btn')
    await upButtons[0].trigger('click')
    await flushPromises()

    expect(notes()[0]).toContain('레거시 API 호환 확인')
  })

  it('줄의 타입을 바꾸면 멘트가 비워지고 [계획에서 옴] 표시 대신 변경 표시가 붙는다', async () => {
    const wrapper = await mountModal()

    const planRow = wrapper.findAll('.wdm-seq-item.from-plan')[0]
    expect((planRow.find('.wdm-note-input').element as HTMLInputElement).value)
      .toBe('레거시 API 호환 확인')

    await planRow.find('.wdm-type-select').setValue('DB')
    await flushPromises()

    const changed = wrapper.findAll('.wdm-seq-item')
      .find(r => r.find('.doc-tag.c-DB').exists())!
    expect(changed.classes()).not.toContain('from-plan')
    expect(changed.find('.wdm-plan-badge').exists()).toBe(false)
    expect(changed.find('.wdm-changed-badge').exists()).toBe(true)
    expect((changed.find('.wdm-note-input').element as HTMLInputElement).value).toBe('')

    await wrapper.findAll('button').find(b => b.text().includes('저장'))!.trigger('click')
    await flushPromises()
    const [, body] = patchRequest.mock.calls[0]
    // docTypeStore has no seeded labelMap in this test, so getLabel() falls back to the
    // type code itself — the point under test is the type/note/source, not the label text.
    expect(body.items).toContainEqual({
      type: 'DB', label: 'DB', note: '',
      source_doc_id: WP_DOC_ID, source_revision_no: 1,
    })
  })

  it('타입을 바꾼 줄이 T였다면 뒤따르던 자동 레포트 줄도 새 타입에 맞춰 바뀐다', async () => {
    const wrapper = await mountModal()

    const tRow = wrapper.findAll('.wdm-seq-item').find(r => r.find('.doc-tag.c-T').exists())!
    await tRow.find('.wdm-type-select').setValue('DS')
    await flushPromises()

    // T 뒤에 자동으로 붙던 TR이 사라지고, 그 자리에 TR을 남기지 않는다.
    const types = wrapper.findAll('.wdm-seq-item').map(r => r.find('.doc-tag').text())
    expect(types).not.toContain('TR')
  })

  it('줄을 지우면 멘트도 같이 사라진다', async () => {
    const wrapper = await mountModal()

    const del = wrapper.findAll('.wdm-seq-item')[1].findAll('.wdm-seq-btn')[2]
    await del.trigger('click')
    await flushPromises()

    const texts = wrapper.findAll('.wdm-seq-item').map(r => r.text())
    expect(texts.some(t => t.includes('레거시 API 호환 확인'))).toBe(false)
  })

  it('저장하면 멘트와 출처와 지문을 함께 보낸다', async () => {
    const wrapper = await mountModal()

    await wrapper.findAll('button').find(b => b.text().includes('저장'))!.trigger('click')
    await flushPromises()

    const [url, body] = patchRequest.mock.calls[0]
    expect(url).toBe('/api/v1/workflow/sequence')
    expect(body.doc_id).toBe(OWNER_DOC_ID)
    expect(body.expected_workflow_tag).toBe('seq451-r73510-i6')
    expect(body.items).toEqual([
      { type: 'P', label: '프로토콜설계', note: '', source_doc_id: null, source_revision_no: null },
      {
        type: 'P', label: '프로토콜설계', note: '레거시 API 호환 확인',
        source_doc_id: WP_DOC_ID, source_revision_no: 1,
      },
      {
        type: 'T', label: '작업지시', note: '테스트 포함 구현',
        source_doc_id: WP_DOC_ID, source_revision_no: 1,
      },
      { type: 'TR', label: '작업레포트', note: '', source_doc_id: null, source_revision_no: null },
    ])
  })

  it('되돌리기는 부어 넣기 직전 상태로 통째로 되돌린다', async () => {
    getRequest.mockResolvedValue({
      data: {
        items: [
          { id: 1, item_seq: 1, type: 'R', label: '요건정의', doc_class: 'R', sort_order: 0, status: 'done', note: '' },
          { id: 2, item_seq: 5, type: 'P', label: '프로토콜설계', doc_class: 'R', sort_order: 1, status: 'pending', note: '' },
        ],
      },
    })
    const wrapper = await mountModal()
    expect(wrapper.findAll('.wdm-seq-item.from-plan')).toHaveLength(2)

    await wrapper.find('.wdm-undo').trigger('click')
    await flushPromises()

    expect(wrapper.find('.wdm-banner').exists()).toBe(false)
    expect(wrapper.findAll('.wdm-seq-item.from-plan')).toHaveLength(0)
    expect(wrapper.findAll('.wdm-seq-item')).toHaveLength(1)
    // 되돌리기는 저장하지 않는다 — 창 안의 목록만 되돌린다.
    expect(patchRequest).not.toHaveBeenCalled()
  })

  it('작업계획을 거치지 않은 보통 저장은 지문을 보내지 않는다', async () => {
    getRequest.mockResolvedValue({
      data: {
        items: [
          {
            id: 1, item_seq: 5, type: 'P', label: '프로토콜설계', doc_class: 'R',
            sort_order: 0, status: 'pending', note: '남아 있던 멘트',
            source_doc_id: WP_DOC_ID, source_revision_no: 1,
          },
        ],
      },
    })
    const wrapper = await mountModal(null)

    expect(wrapper.find('.wdm-banner').exists()).toBe(false)
    await wrapper.findAll('button').find(b => b.text().includes('저장'))!.trigger('click')
    await flushPromises()

    const [, body] = patchRequest.mock.calls[0]
    expect(body.expected_workflow_tag).toBeUndefined()
    // 그리고 이미 저장돼 있던 멘트를 이 저장이 지우지 않는다.
    expect(body.items).toEqual([{
      type: 'P', label: '프로토콜설계', note: '남아 있던 멘트',
      source_doc_id: WP_DOC_ID, source_revision_no: 1,
    }])
  })

  it('사람이 새로 넣은 줄은 멘트가 빈 채로 들어온다', async () => {
    const wrapper = await mountModal()

    const dsButton = wrapper.findAll('.wdm-type-btn').find(b => b.text().includes('DS'))
    await dsButton!.trigger('click')
    await flushPromises()

    const added = wrapper.findAll('.wdm-seq-item').at(-1)!
    expect(added.classes()).not.toContain('from-plan')
    expect(added.find('.wdm-seq-msg--empty').exists()).toBe(true)
  })
})
