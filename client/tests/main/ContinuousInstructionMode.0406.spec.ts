// flowgate.default.0406 T0022 — 새 계약을 못으로 박는다.
//
// 사용자 반려 원문: "연속 작업 (무인) 에 멘트가 똑바로 안들어간다고 / N/T/TS NR/TR
// 계열말야 안들어간다고". NR0021 이 실측으로 확정한 원인은 auto_approved 에서 N·T 는
// 서버가 고정 템플릿으로 만들고 승인해 AI 워커도 워커 멘트도 아예 없다는 것이었다.
// 여기서 고정하는 것은 그 수정의 화면 쪽 계약 넷이다.
//   1) 새 연속 실행의 기본 작성 주체는 AI(ai_direct)다 — N·T 도 워커 단계다.
//   2) 조용한 auto_approved 폴백이 없다 — 창은 받은 모드를 그대로 요청에 싣는다.
//   3) 서버가 대신 처리할 N/T 행은 이유가 보이는 배지를 단다.
//   4) 한줄 멘트는 서버가 말한 상한(1000)까지 쓰이고, 넘으면 막는 대신 알린다.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import AiInvokeDialog from '@main/components/AiInvokeDialog.vue'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import { DEFAULT_INSTRUCTION_MODE } from '@main/types/workPlanFillPreset'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest, postRequest, putRequest, patchRequest: vi.fn(),
}))

const ROOT = 'flowgate.default.0406.0001-R'

// N/NR done, T (head) / TR / TS pending.
function seqResponse() {
  return {
    data: {
      doc_id: ROOT, doc_class: 'R', decided: true, note_max_chars: 1000,
      items: [
        { id: 1, item_seq: 1, type: 'N', label: '조사지시', status: 'done' },
        { id: 2, item_seq: 2, type: 'NR', label: '조사레포트', status: 'done' },
        { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
        { id: 4, item_seq: 4, type: 'TR', label: '작업레포트', status: 'pending' },
        { id: 5, item_seq: 5, type: 'TS', label: '테스트시나리오', status: 'pending' },
      ],
      head: { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
    },
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  putRequest.mockResolvedValue({ data: { ok: true } })
  postRequest.mockResolvedValue({ data: { run_id: 'aiv_1', status: 'running' } })
})
afterEach(() => { document.body.innerHTML = '' })

describe('연속 실행의 N/T 작성 주체 (0406 T0022 작업 1·2·3)', () => {
  it('기본값은 한 곳에서 온다 — ai_direct', () => {
    expect(DEFAULT_INSTRUCTION_MODE).toBe('ai_direct')
  })

  it('연속 작업 창은 기본 ai_direct 로 확정을 내보낸다', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mount(ContinuousWorkDialog, {
      props: { visible: true, docRef: ROOT }, global: { plugins: [i18n] },
    })
    await flushPromises()
    ;([...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement).click()
    await flushPromises()

    const payload = wrapper.emitted('confirm')![0][0] as Record<string, unknown>
    expect(payload.instructionMode).toBe('ai_direct')
    wrapper.unmount()
  })

  it('서버가 대신 처리할 N/T 행은 이유가 보이는 배지를 단다', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mount(ContinuousWorkDialog, {
      props: { visible: true, docRef: ROOT }, global: { plugins: [i18n] },
    })
    await flushPromises()
    // ai_direct 에서는 T 가 평범한 워커 단계다 — 배지도 없고 고를 수도 있다.
    expect(document.querySelector('.wsp-step-tag--auto')).toBeNull()

    const autoRadio = document.querySelectorAll('.cwd-mode input')[0] as HTMLInputElement
    autoRadio.checked = true
    autoRadio.dispatchEvent(new Event('change'))
    await flushPromises()

    // 명시적으로 고른 뒤에는, 그 행이 왜 사라졌는지가 글로 적혀 있어야 한다.
    const tag = document.querySelectorAll('.wsp-step')[2].querySelector('.wsp-step-tag--auto')
    expect(tag!.textContent).toContain(i18n.global.t('main.continuous_work.auto_step_tag'))
    expect(i18n.global.t('main.continuous_work.auto_step_tag')).toContain('AI 멘트 없음')
    wrapper.unmount()
  })

  it.each([
    ['ai_direct', false],
    ['auto_approved', true],
  ] as const)('AI 호출 창은 받은 모드(%s)를 그대로 싣는다 — 조용한 폴백 없음', async (mode, tIsAutoHandled) => {
    getRequest.mockImplementation((url: string) => url === '/api/v1/workflow/sequence'
      ? Promise.resolve(seqResponse())
      : Promise.resolve({ data: { providers: [] } }))
    const wrapper = mount(AiInvokeDialog, {
      props: {
        visible: true, project: 'flowgate', module: 'default', group: '0406',
        docRef: 'flowgate.default.0406.0003-T', sequenceDocRef: ROOT, actionScope: 'edit',
        continuationInstructionMode: mode,
      },
      global: { plugins: [i18n] },
    })
    const radio = document.querySelector('input[type="radio"][value="continuous"]') as HTMLInputElement
    radio.checked = true
    radio.dispatchEvent(new Event('change'))
    await flushPromises()

    // 모드가 그대로 판정에 쓰인다: ai_direct 면 T 도 고를 수 있는 워커 단계다.
    const steps = document.querySelectorAll('.wsp-step')
    expect((steps[2] as HTMLButtonElement).disabled).toBe(tIsAutoHandled)

    ;(document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).click()
    await flushPromises()
    const body = postRequest.mock.calls[0][1] as Record<string, unknown>
    expect(body.continuation_instruction_mode).toBe(mode)
    wrapper.unmount()
  })
})

describe('한줄 멘트 길이 계약 (0406 T0022 작업 6 / M0019)', () => {
  const PLAN_BODY = {
    wp_version: 1,
    binding: 'advisory',
    counted_types: ['T'],
    quantities: { T: { unit: 'set', count: 1 } },
    provider_candidates: [],
    defaults: { provider_id: null, note: '' },
    steps: [
      { key: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TR#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
    ],
  }
  const TYPES = [
    { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 1 },
    { code: 'TR', label: '작업레포트', category: 'work', countable: false, sort_order: 2 },
  ]

  function mountEditor() {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
      if (url.includes('/work-plan')) return Promise.resolve({ data: {
        ok: true, doc_id: 'flowgate.default.0406.0004-WP', doc_type: 'WP', title: '0406 작업계획',
        group_id: 'flowgate.default.0406', parent_doc_id: ROOT, status: 'open',
        doc_review_status: 'pending_review', revision_no: 1,
        stored_path: 'documents/flowgate/main/default/0406/0004-WP_document.json',
        origin: 'human', created_by: 'sjm', updated_by: 'sjm',
        updated_at: '2026-08-11T20:26:35+09:00', body: structuredClone(PLAN_BODY),
        provider_status: [], assignment_summary: [], unassigned_step_count: 2,
        totals: { design_sheets: 0, work_sets: 1, steps: 2 },
        // 상한의 정본은 서버다 (documents.constants.STEP_NOTE_MAX_CHARS).
        limits: { note_max_chars: 1000 },
        last_application: null,
      } })
      return Promise.reject(new Error(`unexpected url: ${url}`))
    })
    useProjectStore().currentProjectId = 'flowgate'
    return mount(WorkPlanEditor, {
      props: { docId: 'flowgate.default.0406.0004-WP', projectId: 'flowgate' },
      global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
    })
  }

  it('입력을 막지 않는다 — maxlength 로 200 자에서 타이핑이 죽던 자리', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    const notes = wrapper.findAll('.wp-defaults-note, .wp-step-msg')
    expect(notes.length).toBeGreaterThan(0)
    for (const note of notes) expect(note.attributes('maxlength')).toBeUndefined()
    wrapper.unmount()
  })

  it('서버가 말한 상한으로 남은 글자 수를 보여주고, 넘으면 조용히 자르지 않고 알린다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    await wrapper.find('.wp-defaults-note').setValue('가'.repeat(1000))
    expect(wrapper.find('.wp-note-count').text())
      .toBe(i18n.global.t('main.work_plan.note_char_count', { current: 1000, max: 1000 }))
    expect(wrapper.find('.wp-defaults-note').classes()).not.toContain('is-over-limit')

    await wrapper.find('.wp-defaults-note').setValue('가'.repeat(1001))
    // 글자는 그대로 남아 있다. 잘리지 않는다는 것이 이 시험의 요점이다.
    expect((wrapper.find('.wp-defaults-note').element as HTMLInputElement).value.length).toBe(1001)
    expect(wrapper.find('.wp-note-count').text())
      .toBe(i18n.global.t('main.work_plan.note_char_over', { current: 1001, max: 1000 }))
    expect(wrapper.find('.wp-defaults-note').classes()).toContain('is-over-limit')
    wrapper.unmount()
  })
})
