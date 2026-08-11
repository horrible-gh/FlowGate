// R0001 (group 0067) "워크플로 결정 다이얼로그 정리":
//  ① V/VR 제거  ③④ 프리셋 재정의 (자동 보고서는 buildEntries 가 AUTO_MAP 으로 삽입)
//
// 0394 T0016 (NR0003 §6.2-라): this spec used to read WorkflowDecisionModal.vue as text and
// assert the shape of its config literals —
// `/AUTO_MAP[^=]*=\s*\{\s*N:\s*\['NR'\], .../`, `/preset_bugfix',\s*types:\s*\['N',\s*'T',\s*'TS'\]/`.
// None of that is a global invariant; it is the ordinary behaviour of a dialog the user
// clicks. And it was fragile in both directions: reformatting a literal (a line break after
// `types:`, a trailing comment between entries) broke the regex without changing a single
// rendered element, while renaming `AUTO_MAP` to something the picker never consults would
// have kept every assertion green.
//
// So the same contract is read off the mounted dialog: which types the picker offers, which
// ones are display-only, and what a preset click actually puts in the sequence editor.
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  postRequest: vi.fn().mockResolvedValue({ data: {} }),
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'

function mountModal() {
  return mount(WorkflowDecisionModal, {
    props: { visible: true },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

type Wrapper = ReturnType<typeof mountModal>

const tags = (wrapper: Wrapper, selector: string) =>
  wrapper.findAll(`${selector} .doc-tag`).map((el) => el.text())

/** Types the user can put into a sequence by clicking. */
const pickableTypes = (wrapper: Wrapper) => tags(wrapper, '.wdm-type-btn')
/** Types shown as "produced automatically", with no way to add them by hand. */
const autoOnlyTypes = (wrapper: Wrapper) => tags(wrapper, '.wdm-auto-item-btn')
/** The sequence as the editor lists it, auto reports included, in order. */
const sequenceTypes = (wrapper: Wrapper) => tags(wrapper, '.wdm-seq-item')

async function clickPreset(wrapper: Wrapper, key: string) {
  const label = i18n.global.t(`main.workflow_decision_modal.${key}`)
  const button = wrapper.findAll('.wdm-preset-btn').find((b) => b.text() === label)
  expect(button, `no preset button labelled "${label}" (${key})`).toBeTruthy()
  await button!.trigger('click')
}

async function clickType(wrapper: Wrapper, type: string) {
  const button = wrapper
    .findAll('.wdm-type-btn')
    .find((b) => b.find('.doc-tag').text() === type)
  expect(button, `type ${type} is not offered by the picker`).toBeTruthy()
  await button!.trigger('click')
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

describe('WorkflowDecisionModal — what the dialog offers and what a click produces', () => {
  it('① offers no V anywhere, and no VR at all', () => {
    const wrapper = mountModal()

    // V is no longer a picker action item; VR is fully retired — including from the
    // auto-only column, where a retired type would still read as a promised artifact.
    expect(pickableTypes(wrapper)).not.toContain('V')
    expect(pickableTypes(wrapper)).not.toContain('VR')
    expect(autoOnlyTypes(wrapper)).not.toContain('V')
    expect(autoOnlyTypes(wrapper)).not.toContain('VR')
    wrapper.unmount()
  })

  it('① lists exactly NR/TR/TSR as the automatically produced reports', () => {
    const wrapper = mountModal()

    expect(autoOnlyTypes(wrapper)).toEqual(['NR', 'TR', 'TSR'])
    // They are shown, never offered: adding one by hand would double the report.
    for (const auto of ['NR', 'TR', 'TSR']) {
      expect(pickableTypes(wrapper)).not.toContain(auto)
    }
    wrapper.unmount()
  })

  it('① pairs N/T/TS with their auto report, and leaves the others alone', async () => {
    const wrapper = mountModal()

    for (const [picked, expected] of [
      ['N', ['N', 'NR']],
      ['T', ['T', 'TR']],
      ['TS', ['TS', 'TSR']],
      ['DS', ['DS']],
      ['WP', ['WP']],
    ] as const) {
      const before = sequenceTypes(wrapper).length
      await clickType(wrapper, picked)
      expect(sequenceTypes(wrapper).slice(before), `picking ${picked}`).toEqual([...expected])
    }
    wrapper.unmount()
  })

  it('0395 T0021: the C (커밋) action item is gone from the picker', () => {
    // 지시: "[워크플로 시퀀스] 에 있는 [커밋] 은 제거". C stays a registered document
    // type — this only removes it as something you can place as a workflow step, so the
    // whole 액션 category goes with it rather than staying on screen as an empty heading.
    const wrapper = mountModal()

    expect(pickableTypes(wrapper)).not.toContain('C')
    expect(autoOnlyTypes(wrapper)).not.toContain('C')
    expect(wrapper.find('.wdm-cat-label.cat-action').exists()).toBe(false)
    wrapper.unmount()
  })

  it('0395 T0021: WP (작업계획) is placeable in the sequence', async () => {
    // NR0020: the only entry point for a work plan was an action-bar button that
    // vanished in the states where it was needed. D0007 §7 calls WP "요건정의 다음에
    // 오는 일반 칸", so it belongs in the type picker like any other step type — and
    // clicking it has to actually land a WP step, not just show a button.
    const wrapper = mountModal()

    expect(pickableTypes(wrapper)).toContain('WP')
    await clickType(wrapper, 'WP')
    expect(sequenceTypes(wrapper)).toEqual(['WP'])
    wrapper.unmount()
  })

  it('CH conversation type is selectable in the picker (TR0044.0010 rev1)', async () => {
    // R0044.0001 rejection: CH did not appear in the workflow-decision / sequence-edit
    // dialogs, so the new type could not be confirmed. It must be pickable, like its
    // general-series sibling M.
    const wrapper = mountModal()

    expect(pickableTypes(wrapper)).toContain('CH')
    expect(pickableTypes(wrapper)).toContain('M')
    await clickType(wrapper, 'CH')
    expect(sequenceTypes(wrapper)).toEqual(['CH'])
    wrapper.unmount()
  })

  it('③④ each preset builds its sequence, auto reports included', async () => {
    for (const [key, expected] of [
      // 간소화 → N (NR) T (TR)  [R0120.0001]
      ['preset_simple', ['N', 'NR', 'T', 'TR']],
      // 버그수정 → N (NR) T (TR) TS (TSR)  [R0120.0001]
      ['preset_bugfix', ['N', 'NR', 'T', 'TR', 'TS', 'TSR']],
      // 설계만 → DS D P L DB
      ['preset_design', ['DS', 'D', 'P', 'L', 'DB']],
      // 표준 풀 사이클 → DS D P L DB T (TR) TS (TSR)
      ['preset_standard', ['DS', 'D', 'P', 'L', 'DB', 'T', 'TR', 'TS', 'TSR']],
    ] as const) {
      const wrapper = mountModal()
      await clickPreset(wrapper, key)
      expect(sequenceTypes(wrapper), key).toEqual([...expected])
      expect(sequenceTypes(wrapper), `${key} must not resurrect V`).not.toContain('V')
      wrapper.unmount()
    }
  })

  it('③④ a preset replaces the sequence rather than appending to it', async () => {
    const wrapper = mountModal()

    await clickPreset(wrapper, 'preset_standard')
    await clickPreset(wrapper, 'preset_simple')

    expect(sequenceTypes(wrapper)).toEqual(['N', 'NR', 'T', 'TR'])
    wrapper.unmount()
  })
})
