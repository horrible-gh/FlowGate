// 0332 T#1 — 워크플로 칸의 소스 커밋 표식 (D0005 §6.1).
//
// 여기서 고정하는 것은 셋이다.
//   1. 표식은 칸의 모양을 빼앗지 않는다 — 진행 상태 클래스도, 클릭도 그대로다.
//   2. 커밋이 없는 칸은 조용하다 — 표식이 아예 없다("없음"이라고 쓰지 않는다).
//   3. 취소된 커밋은 살아 있는 커밋과 다르게 보이고, 호버가 취소 커밋을 말한다.
//   4. (T0018 K11) 앞으로 복원으로 되살린 커밋은 다시 살아 있는 표식으로 돌아오되,
//      호버 한 줄이 "되살린 것"이라고 말한다.
//
// 칸 ↔ 슬롯 해석은 timeMachineSlot 의 것을 그대로 쓴다(반복 타입에서 표식과 클릭이 서로
// 다른 칸을 가리키지 않게). 그 계약은 아래 마지막 describe 에서 직접 확인한다.
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import { slotCommitMarks } from '@main/workflow/timeMachineSlot'
import type { StepState } from '@main/workflow/workflowViewState'

function ss(code: string, visual: 'done' | 'current' | 'future'): StepState {
  const className = { done: 'done', current: 'current', future: 'future dip-step-disabled' }[visual]
  const iconClass = { done: 'check-circle', current: 'radio-button', future: 'circle' }[visual]
  return { code, visual, className, iconClass }
}

const STEPS = [ss('R', 'done'), ss('TR', 'done'), ss('T', 'current'), ss('AC', 'future')]

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
})

function mountStrip(overrides: Record<string, unknown> = {}) {
  return mount(DocWorkflow, {
    props: {
      tab: { id: 'flowgate.default.0332.0001-R', typeCode: 'R' },
      workflowDecided: true,
      stepStates: STEPS,
      ...overrides,
    } as any,
    global: { plugins: [i18n], stubs: { WorkflowDecisionModal: true } },
  })
}

describe('DocWorkflow — TR 커밋 표식', () => {
  it('커밋이 실려 오지 않으면 표식을 하나도 그리지 않는다', () => {
    const wrapper = mountStrip()

    expect(wrapper.findAll('.wf-commit-mark')).toHaveLength(0)
    // 칸은 이 기능이 있기 전과 똑같다.
    expect(wrapper.findAll('.wf-step')).toHaveLength(4)
  })

  it('커밋이 있는 칸에만 표식이 붙고 나머지 칸은 조용하다', () => {
    const wrapper = mountStrip({
      slotCommits: [null, { state: 'live', commit: 'a1b2c3d', subject: '0009-TR: 제목' }, null, null],
    })

    const marks = wrapper.findAll('.wf-commit-mark')
    expect(marks).toHaveLength(1)
    expect(marks[0].classes()).toContain('is-live')
    // 표식이 붙은 칸의 진행 상태 표현은 그대로다(D0005 §6.1 — 빼앗지 않는다).
    expect(wrapper.findAll('.wf-step')[1].classes()).toContain('done')
  })

  it('취소된 커밋은 다른 표식으로 보인다', () => {
    const wrapper = mountStrip({
      slotCommits: [
        null,
        { state: 'canceled', commit: 'a1b2c3d', subject: '0009-TR: 제목', cancel_commit: 'f7a1c02' },
        null,
        null,
      ],
    })

    const mark = wrapper.find('.wf-commit-mark')
    expect(mark.classes()).toContain('is-canceled')
    expect(mark.classes()).not.toContain('is-live')
  })

  it('호버 힌트가 기존 문구를 밀어내지 않고 커밋 한 줄을 덧붙인다', () => {
    const wrapper = mountStrip({
      slotCommits: [null, { state: 'live', commit: 'a1b2c3d', subject: 's' }, null, null],
    })

    const title = wrapper.findAll('.wf-step')[1].attributes('title') ?? ''
    expect(title).toContain(i18n.global.t('main.doc_workflow.time_machine_hint'))
    expect(title).toContain('a1b2c3d')
  })

  it('취소된 칸의 호버는 취소 커밋을 말한다', () => {
    const wrapper = mountStrip({
      slotCommits: [
        null,
        { state: 'canceled', commit: 'a1b2c3d', subject: 's', cancel_commit: 'f7a1c02' },
        null,
        null,
      ],
    })

    const title = wrapper.findAll('.wf-step')[1].attributes('title') ?? ''
    expect(title).toContain('f7a1c02')
  })

  // 0332 T0018 K11 — 되살린 뒤 표식이 돌아온다는 것을 여기서 고정한다. 되살리기용
  // 표식 경로를 새로 만들지 않았고, 기존 "가장 새 행이 이긴다" 해석이 그대로 돌려
  // 놓는다는 뜻이다.
  it('되살린 커밋은 다시 살아 있는 표식으로 돌아온다', () => {
    const wrapper = mountStrip({
      slotCommits: [null, { state: 'live', commit: '9e0d4b7', subject: 's', restored: true }, null, null],
    })

    const mark = wrapper.find('.wf-commit-mark')
    expect(mark.classes()).toContain('is-live')
    expect(mark.classes()).not.toContain('is-canceled')
  })

  it('되살린 칸의 호버는 그냥 커밋이 아니라 되살린 커밋이라고 말한다', () => {
    const wrapper = mountStrip({
      slotCommits: [null, { state: 'live', commit: '9e0d4b7', subject: 's', restored: true }, null, null],
    })

    const title = wrapper.findAll('.wf-step')[1].attributes('title') ?? ''
    expect(title).toContain(i18n.global.t('main.doc_workflow.tr_commit_restored_hint', { commit: '9e0d4b7' }))
    // 대조군: 되살림이 아닌 커밋은 예전 문구 그대로다.
    const plain = mountStrip({
      slotCommits: [null, { state: 'live', commit: '9e0d4b7', subject: 's' }, null, null],
    })
    expect(plain.findAll('.wf-step')[1].attributes('title') ?? '').toContain(
      i18n.global.t('main.doc_workflow.tr_commit_hint', { commit: '9e0d4b7' }),
    )
  })

  it('표식이 있어도 칸 클릭의 뜻은 그대로다', async () => {
    const wrapper = mountStrip({
      slotCommits: [null, { state: 'live', commit: 'a1b2c3d', subject: 's' }, null, null],
    })

    await wrapper.findAll('.wf-step')[1].trigger('click')

    // 완료된 칸을 누르면 되돌리기다 — 표식이 그 뜻을 가로채지 않는다.
    expect(wrapper.emitted('time-machine')).toBeTruthy()
    expect(wrapper.emitted('time-machine')![0]).toEqual([{ index: 1, code: 'TR' }])
  })
})

describe('slotCommitMarks — 칸과 슬롯의 대응', () => {
  it('반복되는 타입에서도 각 칸이 자기 슬롯의 커밋을 받는다', () => {
    const cells = [ss('R', 'done'), ss('TR', 'done'), ss('T', 'done'), ss('TR', 'done')]
    const items = [
      { type: 'R', result_doc_id: 'r', result_seq: 1 },
      { type: 'TR', result_doc_id: 'tr1', result_seq: 2, tr_commit: { state: 'live', commit: 'aaaaaaa' } },
      { type: 'T', result_doc_id: 't', result_seq: 3 },
      { type: 'TR', result_doc_id: 'tr2', result_seq: 4, tr_commit: { state: 'canceled', commit: 'bbbbbbb', cancel_commit: 'ccccccc' } },
    ]

    const marks = slotCommitMarks(cells, items)

    expect(marks[0]).toBeNull()
    expect(marks[1]).toMatchObject({ state: 'live', commit: 'aaaaaaa' })
    expect(marks[2]).toBeNull()
    expect(marks[3]).toMatchObject({ state: 'canceled', cancel_commit: 'ccccccc' })
  })

  it('되살림 표시는 슬롯이 실어 온 그대로 칸에 전달된다', () => {
    const cells = [ss('TR', 'done')]
    const items = [
      { type: 'TR', result_doc_id: 'tr1', result_seq: 2,
        tr_commit: { state: 'live', commit: '9e0d4b7', restored: true } },
    ]

    expect(slotCommitMarks(cells, items as any)[0]).toMatchObject({
      state: 'live', commit: '9e0d4b7', restored: true,
    })
  })

  it('서버가 no_commit 을 보내도(혹은 아무 것도 안 보내도) 표식을 만들지 않는다', () => {
    const cells = [ss('TR', 'done'), ss('TR', 'done')]
    const items = [
      { type: 'TR', result_doc_id: 'a', result_seq: 1, tr_commit: null },
      { type: 'TR', result_doc_id: 'b', result_seq: 2, tr_commit: { state: 'no_commit', commit: null } },
    ]

    expect(slotCommitMarks(cells, items as any)).toEqual([null, null])
  })
})
