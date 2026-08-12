import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

const getRequest = vi.fn()
const patchRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  patchRequest: (...args: unknown[]) => patchRequest(...args),
  postRequest: vi.fn().mockResolvedValue({ data: {} }),
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'

const DOC_ID = 'flowgate.default.0408.0001-B'
const rows = [
  {
    id: 1, item_seq: 1, type: 'D', label: 'Design', status: 'pending', sort_order: 1,
    note: 'keep', source_doc_id: 'flowgate.default.0408.0004-WP', source_revision_no: 8,
    provider_id: 'active', provider_display_name: 'Active Provider', provider_registered: true,
  },
  {
    id: 2, item_seq: 2, type: 'P', label: 'Protocol', status: 'pending', sort_order: 2,
    note: '', source_doc_id: null, source_revision_no: null,
    provider_id: null, provider_display_name: null, provider_registered: null,
  },
  {
    id: 3, item_seq: 3, type: 'M', label: 'Memo', status: 'pending', sort_order: 3,
    note: '', source_doc_id: null, source_revision_no: null,
    provider_id: 'deleted', provider_display_name: 'Deleted Snapshot', provider_registered: false,
  },
  {
    id: 4, item_seq: 4, type: 'WP', label: 'Unknown status', status: 'pending', sort_order: 4,
    note: '', source_doc_id: null, source_revision_no: null,
    provider_id: 'unreadable', provider_display_name: 'Unreadable Snapshot', provider_registered: null,
  },
]

function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) }
function mountModal() {
  return mount(WorkflowDecisionModal, {
    props: { visible: true, mode: 'edit', docId: DOC_ID },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}
function saveButton(wrapper: ReturnType<typeof mountModal>) {
  return wrapper.findAll('.modal-ft button').find(button =>
    button.text().includes(i18n.global.t('main.workflow_edit_modal.save')),
  )!
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset().mockImplementation((url: string) => Promise.resolve(
    url === '/api/v1/workflow/sequence'
      ? { data: { items: clone(rows) } }
      : { data: { providers: [], default_provider_id: null } },
  ))
  patchRequest.mockReset().mockResolvedValue({ data: { status: 'updated' } })
})

describe('WorkflowDecisionModal provider persistence contract (0408)', () => {
  it('renders no chip for null, normal chips for true/null, and warning only for false', async () => {
    const wrapper = mountModal()
    await flushPromises()
    const chips = wrapper.findAll('.wdm-provider-chip')
    expect(chips).toHaveLength(3)
    expect(chips[0].text()).toContain('Active Provider')
    expect(chips[0].classes()).not.toContain('is-unavailable')
    expect(chips[1].text()).toContain('Deleted Snapshot')
    expect(chips[1].classes()).toContain('is-unavailable')
    expect(chips[2].text()).toContain('Unreadable Snapshot')
    expect(chips[2].classes()).not.toContain('is-unavailable')
    expect(wrapper.find('.wdm-provider-rule').text()).toContain(
      i18n.global.t('main.workflow_edit_modal.provider_readonly_rule'),
    )
  })

  it('keeps the parent provider and copies it to a rebuilt report after type change', async () => {
    getRequest.mockImplementation((url: string) => Promise.resolve(
      url === '/api/v1/workflow/sequence' ? { data: { items: [clone(rows[0])] } } : { data: { providers: [] } },
    ))
    const wrapper = mountModal()
    await flushPromises()
    const select = wrapper.find('.wdm-type-select')
    await select.setValue('T')
    await flushPromises()
    const chips = wrapper.findAll('.wdm-provider-chip')
    expect(chips).toHaveLength(2)
    expect(chips.every(chip => chip.text().includes('Active Provider'))).toBe(true)
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    const sent = patchRequest.mock.calls[0][1].items
    expect(sent.map((item: any) => [item.type, item.provider_id, item.provider_display_name])).toEqual([
      ['T', 'active', 'Active Provider'], ['TR', 'active', 'Active Provider'],
    ])
  })

  it('keeps provider pairs attached through row reordering and deletion', async () => {
    getRequest.mockImplementation((url: string) => Promise.resolve(
      url === '/api/v1/workflow/sequence'
        ? { data: { items: clone(rows.slice(0, 3)) } }
        : { data: { providers: [] } },
    ))
    const wrapper = mountModal()
    await flushPromises()

    const initialRows = wrapper.findAll('.wdm-seq-item')
    await initialRows[0].findAll('.wdm-seq-btn')[1].trigger('click')
    await flushPromises()
    const reorderedRows = wrapper.findAll('.wdm-seq-item')
    expect(reorderedRows.map(row => row.find('.doc-tag').text())).toEqual(['P', 'D', 'M'])

    await reorderedRows[2].find('.wdm-seq-btn.del').trigger('click')
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(patchRequest.mock.calls[0][1].items.map((item: any) => [
      item.type, item.provider_id, item.provider_display_name,
    ])).toEqual([
      ['P', null, null],
      ['D', 'active', 'Active Provider'],
    ])
  })

  it('includes the provider pair when only the note is edited and saved', async () => {
    getRequest.mockImplementation((url: string) => Promise.resolve(
      url === '/api/v1/workflow/sequence' ? { data: { items: [clone(rows[0])] } } : { data: { providers: [] } },
    ))
    const wrapper = mountModal()
    await flushPromises()
    await wrapper.find('.wdm-note-input').setValue('note only changed')
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(patchRequest.mock.calls[0][1].items[0]).toMatchObject({
      note: 'note only changed', provider_id: 'active', provider_display_name: 'Active Provider',
    })
  })

  // 0408 M0019 재반려 2·3: the report row's own mention is what an auto-approved run delivers.
  // Loading blanked it, so a save that changed nothing erased the plan's sentence for NR/TR.
  it('shows a report row\'s stored note and keeps it through an untouched save', async () => {
    const pair = [
      { ...clone(rows[0]), id: 5, item_seq: 5, type: 'T', label: 'Task', note: 'T note' },
      {
        id: 6, item_seq: 6, type: 'TR', label: 'Task report', status: 'pending', sort_order: 6,
        note: 'TR note', source_doc_id: null, source_revision_no: null,
        provider_id: 'active', provider_display_name: 'Active Provider', provider_registered: true,
      },
    ]
    getRequest.mockImplementation((url: string) => Promise.resolve(
      url === '/api/v1/workflow/sequence' ? { data: { items: clone(pair) } } : { data: { providers: [] } },
    ))
    const wrapper = mountModal()
    await flushPromises()

    const inputs = wrapper.findAll<HTMLInputElement>('.wdm-note-input')
    expect(inputs).toHaveLength(2)
    expect(inputs[1].element.value).toBe('TR note')

    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(patchRequest.mock.calls[0][1].items.map((item: any) => item.note))
      .toEqual(['T note', 'TR note'])
  })

  it('blocks saving when a canonical row omits provider_id', async () => {
    const missing = clone(rows[0]) as any
    delete missing.provider_id
    getRequest.mockImplementation((url: string) => Promise.resolve(
      url === '/api/v1/workflow/sequence' ? { data: { items: [missing] } } : { data: { providers: [] } },
    ))
    const wrapper = mountModal()
    await flushPromises()
    expect(wrapper.find('.wem-meta-contract-warning').exists()).toBe(true)
    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()
    await (wrapper.vm as any).$?.setupState?.save()
    await flushPromises()
    expect(patchRequest).not.toHaveBeenCalled()
  })
})