import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import WorkPlanAiScopeDialog from '@main/components/WorkPlanAiScopeDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const props = {
  visible: true,
  countableTypes: [{ code: 'D', label: '기본설계' }, { code: 'T', label: '작업' }],
  steps: [
    { key: 'D#1', type: 'D', label: '기본설계 1장', provider_id: 'prov-a', locked: false },
    { key: 'T#1', type: 'T', label: '작업지시 1세트', provider_id: null, locked: false },
    { key: 'TSR#1', type: 'TSR', label: '테스트 레포트 1세트', provider_id: null, locked: true },
  ],
  candidates: [
    { provider_id: 'prov-a', display_name: 'Provider A' },
    { provider_id: 'prov-b', display_name: 'Provider B' },
  ],
}

// flowgate.default.0560 T0018 (4순위, NR0011 원장 ID 32): this dialog is on the common layer now
// and DialogShell teleports it out of the wrapper, so every probe below reads the real document.
// The footer is DialogFooter's, so the buttons are addressed by their semantic action id rather
// than by position — which is the point of the migration: the ORDER is the layer's to decide
// (`aux → cancel → primary`), and a positional selector would have re-encoded the old sequence.
const wrappers: { unmount: () => void }[] = []

function mountDialog() {
  const wrapper = mount(WorkPlanAiScopeDialog, { props, global: { plugins: [i18n] } })
  wrappers.push(wrapper)
  return wrapper
}

function section(index: number): HTMLElement {
  const sections = document.body.querySelectorAll<HTMLElement>('.fg-dialog-body section')
  const found = sections[index]
  if (found == null) throw new Error(`scope section ${index} is not rendered`)
  return found
}

function checkedValues(root: ParentNode): string[] {
  return [...root.querySelectorAll<HTMLInputElement>('input:checked')].map((el) => el.value)
}

function action(id: string): HTMLButtonElement {
  const button = document.body.querySelector<HTMLButtonElement>(`[data-dialog-action-id="${id}"]`)
  if (button == null) throw new Error(`footer action "${id}" is not rendered`)
  return button
}

async function setChecked(value: string, checked: boolean): Promise<void> {
  const input = document.body.querySelector<HTMLInputElement>(`input[value="${value}"]`)
  if (input == null) throw new Error(`checkbox ${value} is not rendered`)
  input.checked = checked
  input.dispatchEvent(new Event('change'))
  await flushPromises()
}

afterEach(() => {
  while (wrappers.length > 0) {
    try {
      wrappers.pop()!.unmount()
    } catch {
      // already unmounted
    }
  }
  resetDialogSystem()
  document.body.innerHTML = ''
})

describe('WorkPlanAiScopeDialog', () => {
  it('defaults to no quantities, only unassigned unlocked steps, and all providers', () => {
    mountDialog()
    expect(checkedValues(section(0))).toHaveLength(0)
    expect(checkedValues(section(1))).toEqual(['T#1'])
    expect(section(1).querySelector('input[value="TSR#1"]')!.hasAttribute('disabled')).toBe(true)
    expect(checkedValues(section(2))).toHaveLength(2)
  })

  it('disables both submit choices when no provider remains', async () => {
    mountDialog()
    // The providers section's [clear].
    section(2).querySelectorAll('button')[1].click()
    await flushPromises()
    expect(action('project-map').disabled).toBe(true)
    expect(action('delegate').disabled).toBe(true)
  })

  it('emits the exact three-list scope', async () => {
    const wrapper = mountDialog()
    await setChecked('D', true)
    await setChecked('D#1', true)
    await setChecked('prov-b', false)
    action('project-map').click()
    await flushPromises()
    expect(wrapper.emitted('project-map')?.[0][0]).toEqual({
      quantity_type_codes: ['D'],
      step_keys: ['T#1', 'D#1'],
      provider_ids: ['prov-a'],
    })
  })

  // R0001 is "취소 버튼이 제각각". This footer used to paint `[취소] [프로젝트맵] [위임]`, with the
  // cancel button stranded away from the primary action. DialogFooter sorts by semantic role
  // before rendering, so it now reads `[프로젝트맵] [취소] [AI 위임]` — and no edit to this
  // component's actions array can move 취소 off the primary button's left again.
  it('paints the footer in the layer-owned order with cancel beside the primary action', () => {
    mountDialog()
    const roles = [...document.body.querySelectorAll('[data-dialog-action-role]')]
      .map((el) => el.getAttribute('data-dialog-action-role'))
    expect(roles).toEqual(['aux', 'cancel', 'primary'])
    const ids = [...document.body.querySelectorAll('[data-dialog-action-id]')]
      .map((el) => el.getAttribute('data-dialog-action-id'))
    expect(ids).toEqual(['project-map', 'cancel', 'delegate'])
  })
})
