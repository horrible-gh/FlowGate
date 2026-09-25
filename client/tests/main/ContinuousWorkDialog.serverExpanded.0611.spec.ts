// flowgate.default.0611 TR0012 rev2 — final instruction-mode contract for a WorkPlan T/N step that
// already carries its instruction document (attached file or written text):
//   auto_approved → the SERVER expands it into the real T/N Markdown (no authoring AI);
//   ai_direct     → no server expansion; an authoring AI writes the T/N with the WP note /
//                   pre-instruction as input, then the normal review/approval flow applies.
// The dialog must show exactly that: under ai_direct such a step is an ordinary authoring step
// (a stop point, a provider row, a per-step toggle); under auto_approved it is a server step.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import { isServerExpandedWorkPlanInstruction } from '@main/types/workflowStepPicker'

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest, postRequest, putRequest, patchRequest: vi.fn(),
}))

const ROOT = 'test2.default.0018.0001-R'
const WP = 'test2.default.0018.0010-WP'
const ATTACHMENT = {
  doc_id: WP,
  filename: '__wp_pre_instruction__T-1__be97a935c7e04048a8cf24a4f1151e38.md',
  original_filename: 'brief.md',
  content_sha256: 'a'.repeat(64),
}

// The reviewer's test2.default.0018 shape: WP done, T#1 carries its instruction file,
// T#2 carries only a one-line note.
function seqResponse() {
  return {
    data: {
      doc_id: ROOT, doc_class: 'R', decided: true, note_max_chars: 1000,
      items: [
        { id: 30, item_seq: 30, type: 'WP', label: '작업계획', status: 'done' },
        { id: 31, item_seq: 31, type: 'T', label: '작업지시', status: 'pending', note: 'ab',
          source_doc_id: WP, pre_instruction_text: null, pre_instruction_attachment: ATTACHMENT },
        { id: 32, item_seq: 32, type: 'TR', label: '작업레포트', status: 'pending', note: 'b',
          source_doc_id: WP },
        { id: 33, item_seq: 33, type: 'T', label: '작업지시', status: 'pending', note: 'c',
          source_doc_id: WP, pre_instruction_text: null, pre_instruction_attachment: null },
        { id: 34, item_seq: 34, type: 'TR', label: '작업레포트', status: 'pending', note: 'd',
          source_doc_id: WP },
      ],
      head: { id: 31, item_seq: 31, type: 'T', label: '작업지시', status: 'pending' },
    },
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  getRequest.mockResolvedValue(seqResponse())
})
// Unmount even when an assertion failed, so a dialog left open by one test can never answer
// the next test's document-level queries.
const mounted: Array<{ unmount: () => void }> = []
afterEach(() => { while (mounted.length) mounted.pop()!.unmount() })

function mountDialog() {
  const wrapper = mount(ContinuousWorkDialog, {
    props: { visible: true, docRef: ROOT }, global: { plugins: [i18n] },
  })
  mounted.push(wrapper)
  return wrapper
}

async function openInAiDirect() {
  const wrapper = mountDialog()
  await flushPromises()
  const aiRadio = document.querySelectorAll('.cwd-mode input')[1] as HTMLInputElement
  aiRadio.checked = true
  aiRadio.dispatchEvent(new Event('change'))
  await flushPromises()
  return wrapper
}

describe('WorkPlan 지시서가 있는 T 는 auto_approved 에서만 서버 단계다 (0611 TR0012 rev2)', () => {
  it('ai_direct 에서는 지시서가 있는 T 도 지시서 작성 AI 단계로 그려진다', async () => {
    const wrapper = await openInAiDirect()
    const steps = document.querySelectorAll('.wsp-step')
    // [WP, T#1(지시서), TR, T#2(note만), TR]
    const withDocument = steps[1] as HTMLButtonElement
    const noteOnly = steps[3] as HTMLButtonElement
    // 지시서가 붙어 있어도 서버 전개 표시가 아니다: 고를 수 있고, 단계별 스위치도 있다.
    expect(withDocument.disabled).toBe(false)
    expect(withDocument.querySelector('.wsp-step-tag--auto')).toBeNull()
    expect(withDocument.querySelector('.wsp-step-auto-toggle')).not.toBeNull()
    // note 만 있는 T 도 같은 작성 단계다.
    expect(noteOnly.disabled).toBe(false)
    expect(noteOnly.querySelector('.wsp-step-tag--auto')).toBeNull()
    expect(noteOnly.querySelector('.wsp-step-auto-toggle')).not.toBeNull()

    ;([...document.querySelectorAll('[data-dialog-action-role="primary"]')][0] as HTMLButtonElement).click()
    await flushPromises()
    const payload = wrapper.emitted('confirm')![0][0] as Record<string, unknown>
    expect(payload.instructionMode).toBe('ai_direct')
    // 아무것도 서버 단계로 몰래 빼지 않는다 — 사용자가 고르지 않은 단계는 선택 목록에 없다.
    expect(payload.autoApproveItemSeqs).toEqual([])
  })

  it('auto_approved 에서는 지시서가 있는 T 가 서버 단계다(고를 수 없고 자동 승인 표시)', async () => {
    mountDialog()
    await flushPromises()
    const steps = document.querySelectorAll('.wsp-step')
    const withDocument = steps[1] as HTMLButtonElement
    expect(withDocument.disabled).toBe(true)
    expect(withDocument.querySelector('.wsp-step-tag--auto')!.textContent)
      .toContain(i18n.global.t('main.continuous_work.auto_step_tag'))
    expect(withDocument.querySelector('.wsp-step-auto-toggle')).toBeNull()
  })
})
