import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import NewRequirementModal from '@main/components/NewRequirementModal.vue'
import { useProjectStore } from '@main/stores/project'
import { useExplorerStore } from '@main/stores/explorer'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postUrlEncoded } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postUrlEncoded: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  getRequest,
  postUrlEncoded,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

const GROUP = 'flowgate.default.0424'

function groupFormGroup(wrapper: ReturnType<typeof mount>) {
  const match = wrapper.findAll('.form-group').find((fg) => fg.find('.group-toggle').exists())
  if (!match) throw new Error('group form-group not found')
  return match
}

// flowgate.default.0424 TR0005 rework — rejection: "AI실행중에 버튼들이 안눌리게
// 하던가 없애야지 토스트 띄우면 다인가?". The "existing group" picker let an
// operator select and submit a "요구/버그 생성" against a group with an active AI
// run — NR0003's action table explicitly names this action as in-scope for item 7's
// busy lock, but only the per-node GroupTreeNode context-menu entry was gated. This
// global entry point (GroupExplorer's toolbar [+], and any other opener) was not.
describe('NewRequirementModal — active AI run on the target group (0424)', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    useProjectStore().setCurrentProject('flowgate')
    i18n.global.locale.value = 'en'
    getRequest.mockReset()
    postUrlEncoded.mockReset()
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/projects') {
        return Promise.resolve({ data: [{ project: 'flowgate', modules: ['default'] }] })
      }
      return Promise.resolve({ data: { data: { nodes: [] } } })
    })
    const explorer = useExplorerStore()
    // 0454 T0006 §2.2 — group-tree cache keys are `${project}:${branch}:full|pruned`. This
  // dialog reads the FULL variant (its group list must not shrink because the sidebar is
  // hiding completed groups), so that is the variant seeded here.
  explorer.groupTreeCache['flowgate:main:full'] = [
      {
        id: GROUP,
        parent_id: 'default',
        node_type: 'group',
        type_code: null,
        number: '0424',
        filename: null,
        label: 'Busy Group',
        title: 'Busy Group',
        has_md: false,
        md_path: null,
      },
    ] as any
  })

  it('disables the busy group option and the submit button, and a disabled-button click sends nothing', async () => {
    const wrapper = mount(NewRequirementModal, { global: { plugins: [i18n] } })
    await flushPromises()
    useAiInvokeRunsStore().trackStarted({
      run_id: 'aiv_0424',
      group_id: GROUP,
      doc_ref: `${GROUP}.0001-R`,
      status: 'running',
    })
    await flushPromises()

    const toggles = wrapper.findAll('.group-toggle-btn')
    await toggles[0].trigger('click') // "existing group" mode
    await flushPromises()

    // The group <select> lives in the same .form-group as the existing/new toggle
    // — the project/module/owner/template selects are siblings elsewhere in the
    // form, so scope the lookup instead of assuming DOM position.
    const groupSelect = groupFormGroup(wrapper).get('select')
    const option = groupSelect.get('option')
    expect(option.attributes('disabled')).toBeDefined()
    expect(option.text()).toContain('AI run')

    const submitBtn = wrapper.get('.btn.btn-primary')
    expect(submitBtn.attributes('disabled')).toBeDefined()
    expect(submitBtn.attributes('title')).toContain('AI run')

    await submitBtn.trigger('click')
    await flushPromises()
    expect(postUrlEncoded).not.toHaveBeenCalled()
  })

  it('leaves the group selectable and the submit button enabled while idle', async () => {
    const wrapper = mount(NewRequirementModal, { global: { plugins: [i18n] } })
    await flushPromises()

    const toggles = wrapper.findAll('.group-toggle-btn')
    await toggles[0].trigger('click')
    await flushPromises()

    const groupSelect = groupFormGroup(wrapper).get('select')
    const option = groupSelect.get('option')
    expect(option.attributes('disabled')).toBeUndefined()

    const submitBtn = wrapper.get('.btn.btn-primary')
    expect(submitBtn.attributes('disabled')).toBeUndefined()
  })
})
