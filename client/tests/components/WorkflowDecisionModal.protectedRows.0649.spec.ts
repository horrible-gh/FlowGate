/**
 * flowgate.default.0649 T#3 (NR0003 O0/O1/O2/O5, §5.3, §11 client) — the sequence editor and
 * the pour path speak the card/row contract the server enforces:
 *   * every stored row keeps its server id and goes back as `item_id`; fixed rows follow the
 *     server's `protected` (not the status), so a pending report right after a started
 *     instruction is fixed, cannot move or be deleted, and is echoed by id — two of them each
 *     with their own id; a fixed row is drawn at its stored position, also between editable
 *     rows (not a sort_order prefix), and an edit that would draw it elsewhere (an
 *     instruction+report block that does not fit the gap before it) is refused;
 *   * pour rows carry `item_id` / `source_wp_card_id`; candidate blockers keep [저장] off and
 *     say why (plan_rows_pending → use [이후 단계 교체]); retired_plan_rows is information;
 *   * legacy_card_unresolved lists the rows and needs a tick before [저장]; the tick rides the
 *     save as acknowledged_codes; the same flow starts from a 409;
 *   * protected_row_echo_ambiguous / sequence_item_stale ask for a reload; card-order 409s keep
 *     [저장] off with the reason.
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
  extractApiErrorMessage: (error: any, fallback: string) =>
    error?.response?.data?.detail ?? error?.response?.data?.error?.message ?? fallback,
}))

import { useToast } from '@main/components/common/useToast'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import WorkflowDecisionModal, { type PourPayload } from '@main/components/WorkflowDecisionModal.vue'

const WP_DOC_ID = 'flowgate.default.0649.0004-WP'
const OWNER_DOC_ID = 'flowgate.default.0649.0001-R'
const EXEC = {
  provider_id: null, provider_display_name: null, provider_registered: null,
  review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null,
  pre_instruction_text: null, pre_instruction_attachment: null,
}

function item(id: number, type: string, status: string, isProtected: boolean, over: Record<string, unknown> = {}) {
  return {
    id, item_id: id, item_seq: id, type, label: type, doc_class: 'R', sort_order: id, status,
    protected: isProtected, note: '', source_doc_id: WP_DOC_ID, source_revision_no: 1,
    source_wp_card_id: null, ...EXEC, ...over,
  }
}

/** T1 started · TR1 protected pending · T2 started · TR2 protected pending · X editable. */
function twoProtectedReports() {
  return [
    item(11, 'T', 'done', true, { source_wp_card_id: 'c_1', result_doc_id: 'x' }),
    item(12, 'TR', 'pending', true, { source_wp_card_id: 'c_1' }),
    item(13, 'T', 'in_progress', true, { source_wp_card_id: 'c_2' }),
    item(14, 'TR', 'pending', true, { source_wp_card_id: 'c_2' }),
    item(15, 'D', 'pending', false, { source_wp_card_id: 'c_x', note: 'x note' }),
  ]
}

async function mountEdit(items: unknown[]) {
  getRequest.mockResolvedValue({ data: { items, note_max_chars: 1000 } })
  const wrapper = mount(WorkflowDecisionModal, {
    props: { visible: false, mode: 'edit' as const, docId: OWNER_DOC_ID },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
  await wrapper.setProps({ visible: true })
  await flushPromises()
  return wrapper
}

function saveButton(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('button').find(b => b.text().includes('저장'))!
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  vi.clearAllMocks()
  patchRequest.mockResolvedValue({ data: { status: 'updated' } })
  postRequest.mockResolvedValue({ data: {} })
})

describe('시퀀스 수정 창 — 보호 행과 item_id (NR0003 O1)', () => {
  it('protected 표시로 고정 행을 나누고, 보호 report 두 행을 각자 item_id로 echo 한다', async () => {
    const wrapper = await mountEdit(twoProtectedReports())

    const fixed = wrapper.findAll('[data-test="protected-row"]')
    expect(fixed.map(r => r.find('.doc-tag').text())).toEqual(['TR', 'TR'])
    // fixed rows: no drag, no ▲▼/delete, no type select
    for (const row of fixed) {
      expect(row.attributes('draggable')).toBeUndefined()
      expect(row.find('.wdm-seq-btn').exists()).toBe(false)
      expect(row.find('.wdm-type-select').exists()).toBe(false)
    }
    const editable = wrapper.findAll('.wdm-seq-item:not(.is-protected)')
    expect(editable.map(r => r.find('.doc-tag').text())).toEqual(['D'])

    await saveButton(wrapper).trigger('click')
    await flushPromises()
    const [, body] = patchRequest.mock.calls[0]
    expect(body.items).toEqual([
      { type: 'TR', label: 'TR', item_id: 12 },
      { type: 'TR', label: 'TR', item_id: 14 },
      {
        type: 'D', label: 'D', note: 'x note', source_doc_id: WP_DOC_ID, source_revision_no: 1,
        provider_id: null, provider_display_name: null,
        review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null,
        pre_instruction_text: null, pre_instruction_attachment: null,
        item_id: 15, source_wp_card_id: 'c_x',
      },
    ])
    expect(body.acknowledged_codes).toBeUndefined()
  })

  it('보호 행이 sort_order 접두가 아니어도 고정 행은 고정되고 사이의 대기 행만 편집된다', async () => {
    const wrapper = await mountEdit([
      item(21, 'T', 'done', true, { source_wp_card_id: 'c_1' }),
      item(22, 'D', 'pending', false, { source_wp_card_id: 'c_d' }),
      item(23, 'T', 'in_progress', true, { source_wp_card_id: 'c_2' }),
      item(24, 'TR', 'pending', true, { source_wp_card_id: 'c_2' }),
    ])
    expect(wrapper.findAll('[data-test="protected-row"]').map(r => r.find('.doc-tag').text())).toEqual(['TR'])
    expect(wrapper.findAll('.wdm-seq-item:not(.is-protected)').map(r => r.find('.doc-tag').text())).toEqual(['D'])
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    const [, body] = patchRequest.mock.calls[0]
    expect(body.items.map((i: any) => [i.type, i.item_id])).toEqual([['TR', 24], ['D', 22]])
  })

  it('보호 행이 대기 행 사이에 끼어 있으면 그 자리 그대로 고정해 그리고, 편집 행만 그 둘레로 움직인다', async () => {
    const wrapper = await mountEdit([
      item(31, 'D', 'pending', false, { source_wp_card_id: 'c_d1', note: 'd1' }),
      item(32, 'T', 'in_progress', true, { source_wp_card_id: 'c_2' }),
      item(33, 'TR', 'pending', true, { source_wp_card_id: 'c_2' }),
      item(34, 'D', 'pending', false, { source_wp_card_id: 'c_d2', note: 'd2' }),
    ])
    const listed = () => wrapper.findAll('.wdm-seq-editor .wdm-seq-item').map(r => [
      r.find('.doc-tag').text(), r.find('.wdm-seq-num').text(), r.classes().includes('is-protected'),
    ])
    // server order D · T(started, preview only) · TR(protected) · D — not the fixed rows on top
    expect(listed()).toEqual([['D', '1', false], ['TR', '3', true], ['D', '4', false]])
    expect(wrapper.findAll('.wdm-preview .doc-tag').map(t => t.text())).toEqual(['D', 'T', 'TR', 'D'])
    // the save re-inserts editable rows after the last protected one (O1), and the screen says so
    expect(wrapper.get('[data-test="protected-interleaved-note"]').text()).toContain('1')

    // moving an editable row leaves the fixed rows where they are
    const lastEditable = wrapper.findAll('.wdm-seq-item:not(.is-protected)')[1]
    await lastEditable.findAll('.wdm-seq-btn')[0].trigger('click')
    await flushPromises()
    expect(listed()).toEqual([['D', '1', false], ['TR', '3', true], ['D', '4', false]])
    expect(wrapper.findAll('.wdm-seq-item:not(.is-protected) .wdm-note-input')
      .map(i => (i.element as HTMLInputElement).value)).toEqual(['d2', 'd1'])
    expect(wrapper.findAll('.wdm-preview .doc-tag').map(t => t.text())).toEqual(['D', 'T', 'TR', 'D'])

    await saveButton(wrapper).trigger('click')
    await flushPromises()
    const [, body] = patchRequest.mock.calls[0]
    expect(body.items.map((i: any) => [i.type, i.item_id])).toEqual([['TR', 33], ['D', 34], ['D', 31]])
  })

  describe('지시+자동 report 묶음이 고정 행을 건너가는 이동 (rev 2 회귀)', () => {
    // D(pending) · T(started) · TR(protected) · T(pending) · TR(pending, auto of that T)
    function crossing() {
      return [
        item(41, 'D', 'pending', false, { source_wp_card_id: 'c_d', note: 'd' }),
        item(42, 'T', 'in_progress', true, { source_wp_card_id: 'c_2' }),
        item(43, 'TR', 'pending', true, { source_wp_card_id: 'c_2' }),
        item(44, 'T', 'pending', false, { source_wp_card_id: 'c_3', note: 't3' }),
        item(45, 'TR', 'pending', false, { source_wp_card_id: 'c_3' }),
      ]
    }
    const blockedText = () => i18n.global.t('main.workflow_edit_modal.protected_slot_blocked')
    const toastShown = () => useToast().toasts.value.some(t => t.message === blockedText())
    const listed = (wrapper: ReturnType<typeof mount>) => wrapper.findAll('.wdm-seq-editor .wdm-seq-item').map(r => [
      r.find('.doc-tag').text(), r.find('.wdm-seq-num').text(), r.classes().includes('is-protected'),
    ])
    const preview = (wrapper: ReturnType<typeof mount>) => wrapper.findAll('.wdm-preview .doc-tag').map(t => t.text())
    const UNCHANGED = [['D', '1', false], ['TR', '3', true], ['T', '4', false], ['TR', '5', false]]

    beforeEach(() => {
      useToast().toasts.value = []
    })

    it('불러온 순서 그대로 고정 행이 2·3번 자리에 있다', async () => {
      const wrapper = await mountEdit(crossing())
      expect(listed(wrapper)).toEqual(UNCHANGED)
      expect(preview(wrapper)).toEqual(['D', 'T', 'TR', 'T', 'TR'])
    })

    it('대기 T/TR 묶음을 ▲로 D 위로 올리면 막고, 고정 행의 자리와 순서는 그대로다', async () => {
      const wrapper = await mountEdit(crossing())
      const tRow = wrapper.findAll('.wdm-seq-item:not(.is-protected)')[1]
      expect(tRow.find('.doc-tag').text()).toBe('T')
      await tRow.findAll('.wdm-seq-btn')[0].trigger('click')
      await flushPromises()

      expect(listed(wrapper)).toEqual(UNCHANGED)
      expect(preview(wrapper)).toEqual(['D', 'T', 'TR', 'T', 'TR'])
      expect(toastShown()).toBe(true)

      await saveButton(wrapper).trigger('click')
      await flushPromises()
      const [, body] = patchRequest.mock.calls[0]
      expect(body.items.map((i: any) => [i.type, i.item_id])).toEqual([['TR', 43], ['D', 41], ['T', 44], ['TR', 45]])
    })

    it('D를 ▼로 묶음 뒤로 내리는 것도 같은 이동이라 막는다', async () => {
      const wrapper = await mountEdit(crossing())
      const dRow = wrapper.findAll('.wdm-seq-item:not(.is-protected)')[0]
      await dRow.findAll('.wdm-seq-btn')[1].trigger('click')
      await flushPromises()
      expect(listed(wrapper)).toEqual(UNCHANGED)
      expect(toastShown()).toBe(true)
    })

    it('묶음을 끌어 D 위에 놓아도 막는다', async () => {
      const wrapper = await mountEdit(crossing())
      const rows = wrapper.findAll('.wdm-seq-item:not(.is-protected)')
      await rows[1].trigger('dragstart')
      await rows[0].trigger('drop')
      await flushPromises()
      expect(listed(wrapper)).toEqual(UNCHANGED)
      expect(preview(wrapper)).toEqual(['D', 'T', 'TR', 'T', 'TR'])
      expect(toastShown()).toBe(true)
    })

    it('고정 행 앞 한 칸짜리 D를 T로 바꾸면 자동 TR이 붙어 자리가 모자라므로 막고, 선택값도 되돌린다', async () => {
      const wrapper = await mountEdit(crossing())
      const select = wrapper.findAll('.wdm-seq-item:not(.is-protected)')[0].get('.wdm-type-select')
      await select.setValue('T')
      await flushPromises()
      expect(listed(wrapper)).toEqual(UNCHANGED)
      expect((select.element as HTMLSelectElement).value).toBe('D')
      expect(toastShown()).toBe(true)
    })

    it('한 칸짜리 행끼리의 이동과 맨 끝 추가는 고정 행의 자리를 바꾸지 않으므로 그대로 된다', async () => {
      const wrapper = await mountEdit([
        ...crossing(),
        item(46, 'D', 'pending', false, { source_wp_card_id: 'c_d2', note: 'd2' }),
      ])
      const rows = wrapper.findAll('.wdm-seq-item:not(.is-protected)')
      // the last D (single row) up past the T/TR block, then above the first D
      await rows[3].findAll('.wdm-seq-btn')[0].trigger('click')
      await flushPromises()
      await wrapper.findAll('.wdm-seq-item:not(.is-protected)')[1].findAll('.wdm-seq-btn')[0].trigger('click')
      await flushPromises()
      expect(listed(wrapper)).toEqual([
        ['D', '1', false], ['TR', '3', true], ['D', '4', false], ['T', '5', false], ['TR', '6', false],
      ])
      expect(wrapper.findAll('.wdm-seq-item:not(.is-protected) .wdm-note-input')
        .map(i => (i.element as HTMLInputElement).value)).toEqual(['d2', 'd', 't3', ''])
      expect(toastShown()).toBe(false)
    })

    it('지우기는 막지 않는다 — 앞의 행이 줄어 고정 행은 앞으로만 당겨지고 뒤로 밀리지 않는다', async () => {
      const wrapper = await mountEdit(crossing())
      const dRow = wrapper.findAll('.wdm-seq-item:not(.is-protected)')[0]
      await dRow.find('.wdm-seq-btn.del').trigger('click')
      await flushPromises()
      expect(listed(wrapper)).toEqual([['TR', '2', true], ['T', '3', false], ['TR', '4', false]])
      expect(preview(wrapper)).toEqual(['T', 'TR', 'T', 'TR'])
      expect(toastShown()).toBe(false)
    })
  })

  it('보호 행이 접두이면 안내 띠를 띄우지 않는다', async () => {
    const wrapper = await mountEdit(twoProtectedReports())
    expect(wrapper.find('[data-test="protected-interleaved-note"]').exists()).toBe(false)
    expect(wrapper.findAll('.wdm-seq-editor .wdm-seq-item').map(r => r.find('.wdm-seq-num').text()))
      .toEqual(['2', '4', '5'])
  })

  it('protected 를 모르는 이전 서버 응답은 지금처럼 상태로 나눈다', async () => {
    const legacy = twoProtectedReports().map(({ protected: _p, item_id: _i, ...rest }) => rest)
    const wrapper = await mountEdit(legacy)
    expect(wrapper.findAll('[data-test="protected-row"]')).toHaveLength(0)
    expect(wrapper.findAll('.wdm-seq-item').map(r => r.find('.doc-tag').text())).toEqual(['TR', 'TR', 'D'])
  })

  it.each(['protected_row_echo_ambiguous', 'sequence_item_stale'])(
    '%s 를 받으면 다시 불러오기를 안내하고, 다시 불러오면 저장할 수 있다',
    async (code) => {
      patchRequest.mockRejectedValueOnce({ response: { status: 409, data: { error: code } } })
      const wrapper = await mountEdit(twoProtectedReports())
      await saveButton(wrapper).trigger('click')
      await flushPromises()

      const strip = wrapper.get('[data-test="reload-needed"]')
      expect(strip.text()).toContain(i18n.global.t(`main.work_plan_pour.error_${code}`))
      expect(saveButton(wrapper).attributes('disabled')).toBeDefined()

      const getsBefore = getRequest.mock.calls.length
      await wrapper.get('[data-test="reload-sequence"]').trigger('click')
      await flushPromises()
      expect(getRequest.mock.calls.length).toBe(getsBefore + 1)
      expect(wrapper.find('[data-test="reload-needed"]').exists()).toBe(false)
      expect(saveButton(wrapper).attributes('disabled')).toBeUndefined()
    },
  )
})

// ── pour path ────────────────────────────────────────────────────────────────

function pourRow(type: string, over: Record<string, unknown> = {}) {
  return {
    type, label: type, status: 'pending', locked: false, poured: false, note: '',
    note_source: null, origin: 'manual', plan_key: null, source_doc_id: null, source_revision_no: null,
    ...EXEC, source_wp_card_id: null, item_id: null, protected: false, ...over,
  }
}

const POUR_ROWS = [
  pourRow('T', { status: 'done', locked: true, protected: true, item_id: 31, source_doc_id: WP_DOC_ID, source_revision_no: 0, source_wp_card_id: 'c_a' }),
  pourRow('TR', { locked: true, protected: true, item_id: 32, source_doc_id: WP_DOC_ID, source_revision_no: 0, source_wp_card_id: 'c_a', origin: 'auto' }),
  pourRow('D', { origin: 'manual', item_id: 33 }),
  pourRow('T', { poured: true, origin: 'plan', plan_key: 'T#2', note: 'B', source_doc_id: WP_DOC_ID, source_revision_no: 1, source_wp_card_id: 'c_b' }),
  pourRow('TR', { poured: true, origin: 'auto', source_doc_id: WP_DOC_ID, source_revision_no: 1, source_wp_card_id: 'c_b' }),
]

function pourPayload(over: Partial<PourPayload> = {}): PourPayload {
  return {
    wpDocId: WP_DOC_ID, wpRevisionNo: 1, workflowDocId: OWNER_DOC_ID, wpShortCode: 'WP0004',
    mode: 'replace_after', planStepCount: 1, rows: POUR_ROWS as never,
    rowCountChange: { before: 3, after: 5, deleted: 0, added: 2 },
    notifications: [], workflowTag: 'seq1-r1-i3', blockers: [],
    ...over,
  }
}

async function mountPour(poured: PourPayload) {
  getRequest.mockResolvedValue({ data: { items: [] } })
  const wrapper = mount(WorkflowDecisionModal, {
    props: { visible: false, mode: 'edit' as const, docId: OWNER_DOC_ID, poured },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
  await wrapper.setProps({ visible: true })
  await flushPromises()
  return wrapper
}

const LEGACY_NOTE = {
  code: 'legacy_card_unresolved', severity: 'blocker', count: 1, acknowledge_code: 'legacy_card_unresolved',
  items: [{ item_id: 31, position: 1, type: 'T', result_doc_id: 'flowgate.default.0649.0101-T', r_b: 0, candidate_keys: ['T#1', 'T#2'] }],
}

describe('붓기 창 — 카드 순서 계약 (NR0003 O0/O2/O5)', () => {
  it('붓기 행은 item_id·source_wp_card_id 를 싣고, 보호 report 는 id 로 echo 된다', async () => {
    const wrapper = await mountPour(pourPayload())
    expect(wrapper.findAll('[data-test="protected-row"]').map(r => r.find('.doc-tag').text())).toEqual(['TR'])
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    const [, body] = patchRequest.mock.calls[0]
    expect(body.items.map((i: any) => [i.type, i.item_id ?? null, i.source_wp_card_id ?? null])).toEqual([
      ['TR', 32, null], ['D', 33, null], ['T', null, 'c_b'], ['TR', null, 'c_b'],
    ])
    expect(body.expected_plan).toEqual({ wp_doc_id: WP_DOC_ID, wp_revision_no: 1, mode: 'replace_after' })
  })

  it('legacy_card_unresolved 는 행 목록을 보여 주고, 확인해야 저장이 켜지며 acknowledged_codes 가 실린다', async () => {
    const wrapper = await mountPour(pourPayload({ notifications: [LEGACY_NOTE] as never }))
    const strip = wrapper.get('[data-test="legacy-card-unresolved"]')
    expect(strip.text()).toContain('T#1 · T#2')
    expect(strip.text()).toContain('T0101')
    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="legacy-ack"]').setValue(true)
    expect(saveButton(wrapper).attributes('disabled')).toBeUndefined()
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    const [, body] = patchRequest.mock.calls[0]
    expect(body.acknowledged_codes).toEqual(['legacy_card_unresolved'])
  })

  it('저장 409 legacy_card_unresolved 도 같은 확인 흐름을 연다', async () => {
    patchRequest.mockRejectedValueOnce({
      response: { status: 409, data: { error: 'legacy_card_unresolved', rows: LEGACY_NOTE.items } },
    })
    const wrapper = await mountPour(pourPayload())
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="legacy-card-unresolved"]').exists()).toBe(true)
    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="legacy-ack"]').setValue(true)
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(patchRequest).toHaveBeenCalledTimes(2)
    expect(patchRequest.mock.calls[1][1].acknowledged_codes).toEqual(['legacy_card_unresolved'])
  })

  it.each([
    ['plan_rows_pending', { suggested_mode: 'replace_after', items: [{}, {}] }, '[이후 단계 교체]'],
    ['order_conflicts_started', { cards: [{ card_id: 'c_a', key: 'T#2' }] }, 'T#2'],
    ['started_card_removed', { cards: [{ card_id: 'c_a', key: null }] }, 'c_a'],
  ])('차단 코드 %s 가 오면 문구를 보이고 저장을 끈다', async (code, extra, text) => {
    const wrapper = await mountPour(pourPayload({
      mode: code === 'plan_rows_pending' ? 'append' : 'replace_after',
      blockers: [code],
      notifications: [{ code, severity: 'blocker', count: 2, ...extra }] as never,
    }))
    const strip = wrapper.get(`[data-test="pour-note-${code}"]`)
    expect(strip.classes()).toContain('wdm-banner--block')
    expect(strip.text()).toContain(text)
    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()
    await saveButton(wrapper).trigger('click')
    expect(patchRequest).not.toHaveBeenCalled()
  })

  it('retired_plan_rows·steps_already_done·foreign_rows_before 는 안내만 하고 저장을 막지 않는다', async () => {
    const wrapper = await mountPour(pourPayload({
      notifications: [
        { code: 'retired_plan_rows', severity: 'info', count: 1, items: [] },
        { code: 'steps_already_done', severity: 'info', count: 1, items: [] },
        { code: 'foreign_rows_before', severity: 'info', count: 2 },
      ] as never,
    }))
    expect(wrapper.get('[data-test="pour-note-retired_plan_rows"]').text())
      .toBe(i18n.global.t('main.work_plan_pour.notify_retired_plan_rows', { n: 1 }))
    expect(wrapper.find('[data-test="pour-note-steps_already_done"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="pour-note-foreign_rows_before"]').exists()).toBe(true)
    expect(saveButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('저장 409 plan_order_violation 이면 사유와 함께 저장을 끈다', async () => {
    patchRequest.mockRejectedValueOnce({
      response: { status: 409, data: { error: 'plan_order_violation', detail: { reason: 'order' } } },
    })
    const wrapper = await mountPour(pourPayload())
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="save-blocked"]').text())
      .toBe(i18n.global.t('main.work_plan_pour.error_plan_order_violation', { reason: 'order' }))
    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()
  })
})

describe('[작업계획 적용] 메뉴 — 차단 사유를 그 자리에서 (NR0003 O5)', () => {
  it('append 가 plan_rows_pending 이면 이어 붙이기 아래에 [이후 단계 교체] 안내를 적고, 붓기 창에 blockers 를 넘긴다', async () => {
    postRequest.mockImplementation((url: string, body: { mode: 'append' | 'replace_after' }) => {
      if (!String(url).endsWith('/work-plan/sequence-candidates')) return Promise.resolve({ data: {} })
      const blocked = body.mode === 'append'
      return Promise.resolve({
        data: {
          wp_doc_id: WP_DOC_ID, wp_revision_no: 1, workflow_doc_id: OWNER_DOC_ID, mode: body.mode,
          plan_step_count: 1, rows: POUR_ROWS,
          row_count_change: { before: 3, after: 5, deleted: 0, added: 2 },
          notifications: blocked
            ? [{ code: 'plan_rows_pending', severity: 'blocker', count: 1, suggested_mode: 'replace_after' }]
            : [],
          workflow_tag: 'seq1-r1-i3',
          blockers: blocked ? ['plan_rows_pending'] : [],
        },
      })
    })
    getRequest.mockResolvedValue({ data: { items: [] } })
    const wrapper = mount(DocWorkflow, {
      props: {
        tab: { id: WP_DOC_ID, title: 'WP', path: 'x', type: 'json', typeCode: 'WP', projectId: 'flowgate' } as never,
        workflowDecided: true,
        parentRDocId: OWNER_DOC_ID,
        stepStates: [{ code: 'R', className: 'done', iconClass: 'check-circle', visual: 'done' }] as never,
        canNextAction: false,
      },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()
    await wrapper.find('.wf-apply-btn').trigger('click')
    await flushPromises()

    const [append, replace] = wrapper.findAll('.wf-apply-item')
    expect(append.get('[data-test="pour-blocked-append"]').text())
      .toBe(i18n.global.t('main.work_plan_pour.menu_blocked_plan_rows_pending'))
    expect(replace.find('[data-test="pour-blocked-replace_after"]').exists()).toBe(false)

    await append.trigger('click')
    await flushPromises()
    const modal = wrapper.findComponent(WorkflowDecisionModal)
    expect(modal.props('poured')?.blockers).toEqual(['plan_rows_pending'])
    expect(modal.find('[data-test="pour-note-plan_rows_pending"]').exists()).toBe(true)
  })
})
