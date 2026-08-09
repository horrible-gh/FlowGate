import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import WorkPlanAiScopeDialog from '@main/components/WorkPlanAiScopeDialog.vue'

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

function mountDialog() {
  return mount(WorkPlanAiScopeDialog, { props, global: { plugins: [i18n] } })
}

describe('WorkPlanAiScopeDialog', () => {
  it('defaults to no quantities, only unassigned unlocked steps, and all providers', () => {
    const wrapper = mountDialog()
    const sections = wrapper.findAll('section')
    expect(sections[0].findAll('input:checked')).toHaveLength(0)
    expect(sections[1].findAll('input:checked').map((input) => input.attributes('value'))).toEqual(['T#1'])
    expect(sections[1].find('input[value="TSR#1"]').attributes('disabled')).toBeDefined()
    expect(sections[2].findAll('input:checked')).toHaveLength(2)
  })

  it('disables both submit choices when no provider remains', async () => {
    const wrapper = mountDialog()
    await wrapper.findAll('section')[2].findAll('button')[1].trigger('click')
    const actions = wrapper.findAll('footer button')
    expect(actions[1].attributes('disabled')).toBeDefined()
    expect(actions[2].attributes('disabled')).toBeDefined()
  })

  it('emits the exact three-list scope', async () => {
    const wrapper = mountDialog()
    await wrapper.find('input[value="D"]').setValue(true)
    await wrapper.find('input[value="D#1"]').setValue(true)
    await wrapper.find('input[value="prov-b"]').setValue(false)
    await wrapper.findAll('footer button')[1].trigger('click')
    expect(wrapper.emitted('project-map')?.[0][0]).toEqual({
      quantity_type_codes: ['D'],
      step_keys: ['T#1', 'D#1'],
      provider_ids: ['prov-a'],
    })
  })
})
