// 0406 T0007: guard the edit modal against legacy responses that omit row metadata.
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

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

import WorkflowDecisionModal, { type PourPayload } from '@main/components/WorkflowDecisionModal.vue'

const DOC_ID = 'flowgate.default.0406.0001-B'
const metadataKeys = ['note', 'source_doc_id', 'source_revision_no'] as const

const canonicalItems = [
  {
    id: 4061,
    item_seq: 1,
    type: 'M',
    label: 'Keep the handoff',
    doc_class: 'B',
    sort_order: 1,
    status: 'pending',
    note: 'Keep the saved handoff',
    source_doc_id: 'flowgate.default.0406.0004-WP',
    source_revision_no: 7,
  },
  {
    id: 4062,
    item_seq: 2,
    type: 'WP',
    label: 'Empty metadata values',
    doc_class: 'B',
    sort_order: 2,
    status: 'pending',
    note: '',
    source_doc_id: null,
    source_revision_no: null,
  },
  {
    id: 4063,
    item_seq: 3,
    type: 'D',
    label: 'Revision zero',
    doc_class: 'B',
    sort_order: 3,
    status: 'pending',
    note: 'Revision zero survives',
    source_doc_id: 'flowgate.default.0406.0003-WP',
    source_revision_no: 0,
  },
]

const legacyItem = {
  id: 4061,
  item_seq: 1,
  type: 'M',
  label: 'Legacy row',
  status: 'pending',
  sort_order: 1,
}

let workflowResponses: Array<Record<string, unknown>> = []
let workflowGetCount = 0

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value))
}

function mountModal(extraProps: Record<string, unknown> = {}) {
  return mount(WorkflowDecisionModal, {
    props: { visible: true, mode: 'edit', docId: DOC_ID, ...extraProps },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

type Wrapper = ReturnType<typeof mountModal>

function footerButton(wrapper: Wrapper, key: string) {
  const label = i18n.global.t(key)
  const button = wrapper.findAll('.modal-ft button').find(candidate => candidate.text().includes(label))
  expect(button, `missing footer button for ${key} (${label})`).toBeTruthy()
  return button!
}

function sentMetadata() {
  const body = patchRequest.mock.calls[0][1]
  return body.items.map((item: Record<string, unknown>) =>
    Object.fromEntries(metadataKeys.map(key => [key, item[key]])),
  )
}

function loadedMetadata(items: Array<Record<string, unknown>>) {
  return items.map(item => Object.fromEntries(metadataKeys.map(key => [key, item[key]])))
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  patchRequest.mockReset().mockResolvedValue({ data: { status: 'updated' } })
  getRequest.mockReset()
  workflowResponses = [{ items: clone(canonicalItems) }]
  workflowGetCount = 0
  getRequest.mockImplementation((url: string) => {
    if (url === '/api/v1/workflow/sequence') {
      const data = workflowResponses[Math.min(workflowGetCount, workflowResponses.length - 1)] ?? { items: [] }
      workflowGetCount += 1
      return Promise.resolve({ data: clone(data) })
    }
    return Promise.resolve({ data: { providers: [], default_provider_id: null } })
  })
})

describe('WorkflowDecisionModal metadata response contract (0406 T0007)', () => {
  it('round-trips canonical note and source fields unchanged without user edits', async () => {
    const wrapper = mountModal()
    await flushPromises()

    await footerButton(wrapper, 'main.workflow_edit_modal.save').trigger('click')
    await flushPromises()

    const before = loadedMetadata(canonicalItems)
    const after = sentMetadata()
    expect(after).toEqual(before)
    expect(after[1]).toEqual({ note: '', source_doc_id: null, source_revision_no: null })
    expect(after[2].source_revision_no).toBe(0)
    console.log('ROUNDTRIP_METADATA=' + JSON.stringify({ before, after }))
  })

  it('blocks a legacy sequence response that omits all three metadata keys', async () => {
    workflowResponses = [{ sequence: [legacyItem] }]
    const wrapper = mountModal()
    await flushPromises()

    const warning = wrapper.find('.wem-meta-contract-warning')
    const saveButton = footerButton(wrapper, 'main.workflow_edit_modal.save')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toContain(i18n.global.t('main.workflow_edit_modal.meta_contract_missing'))
    expect(saveButton.attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('.wdm-seq-item')).toHaveLength(1)
    expect(footerButton(wrapper, 'main.workflow_edit_modal.mention_copy').attributes('disabled')).toBeUndefined()
    expect(footerButton(wrapper, 'main.workflow_edit_modal.invoke_ai').attributes('disabled')).toBeUndefined()

    await saveButton.trigger('click')
    const save = (wrapper.vm as any).$?.setupState?.save
    expect(save).toBeTypeOf('function')
    await save()
    await flushPromises()

    expect(patchRequest).toHaveBeenCalledTimes(0)
    console.log('META_CONTRACT_UI=' + JSON.stringify({
      text: warning.text(),
      saveDisabled: saveButton.attributes('disabled') !== undefined,
      patchCalls: patchRequest.mock.calls.length,
    }))
  })

  it('blocks a legacy sequence body even when its rows carry all three keys', async () => {
    // 0406 T0013: the guard is about the SHAPE, not only the row keys. A body without `items`
    // cannot come from the canonical handler, so the rows in it are unverifiable provenance —
    // rendering them as a normal editable list is how the whole pending block got wiped.
    workflowResponses = [{ sequence: clone(canonicalItems) }]
    const wrapper = mountModal()
    await flushPromises()

    expect(wrapper.find('.wem-meta-contract-warning').exists()).toBe(true)
    expect(footerButton(wrapper, 'main.workflow_edit_modal.save').attributes('disabled')).toBeDefined()
    // The rows still show — the user must be able to see what is at risk before reloading.
    expect(wrapper.findAll('.wdm-seq-item').length).toBe(canonicalItems.length)
    expect(patchRequest).toHaveBeenCalledTimes(0)
  })

  it('accepts present keys whose values are empty string and null', async () => {
    const empty = { ...canonicalItems[1] }
    workflowResponses = [{ items: [empty] }]
    const wrapper = mountModal()
    await flushPromises()

    expect(wrapper.find('.wem-meta-contract-warning').exists()).toBe(false)
    const saveButton = footerButton(wrapper, 'main.workflow_edit_modal.save')
    expect(saveButton.attributes('disabled')).toBeUndefined()
    await saveButton.trigger('click')
    await flushPromises()

    expect(sentMetadata()).toEqual([{ note: '', source_doc_id: null, source_revision_no: null }])
  })

  it('blocks the whole save when only one of two rows omits metadata keys', async () => {
    workflowResponses = [{ items: [clone(canonicalItems[0]), legacyItem] }]
    const wrapper = mountModal()
    await flushPromises()

    expect(wrapper.find('.wem-meta-contract-warning').exists()).toBe(true)
    expect(footerButton(wrapper, 'main.workflow_edit_modal.save').attributes('disabled')).toBeDefined()
    expect(patchRequest).toHaveBeenCalledTimes(0)
  })

  it('recovers after reload returns the canonical response', async () => {
    workflowResponses = [{ sequence: [legacyItem] }, { items: clone(canonicalItems) }]
    const wrapper = mountModal()
    await flushPromises()

    expect(wrapper.find('.wem-meta-contract-warning').exists()).toBe(true)
    await wrapper.find('.wem-reload-btn').trigger('click')
    await flushPromises()

    expect(workflowGetCount).toBe(2)
    expect(wrapper.find('.wem-meta-contract-warning').exists()).toBe(false)
    const saveButton = footerButton(wrapper, 'main.workflow_edit_modal.save')
    expect(saveButton.attributes('disabled')).toBeUndefined()
    await saveButton.trigger('click')
    await flushPromises()
    expect(sentMetadata()).toEqual(loadedMetadata(canonicalItems))
  })

  it('does not apply the GET contract guard to the poured payload path', async () => {
    const poured: PourPayload = {
      wpDocId: 'flowgate.default.0406.0004-WP',
      wpRevisionNo: 4,
      wpShortCode: 'WP0004',
      workflowDocId: DOC_ID,
      mode: 'append',
      planStepCount: 1,
      rows: [{
        type: 'M',
        label: 'Poured row',
        status: 'pending',
        locked: false,
        poured: true,
        note: 'Poured note',
        origin: 'plan',
        plan_key: 'step-1',
        source_doc_id: 'flowgate.default.0406.0004-WP',
        source_revision_no: 4,
      }],
      rowCountChange: { before: 0, after: 1, deleted: 0, added: 1 },
      notifications: [],
      workflowTag: 'tag-0406',
    }
    const wrapper = mountModal({ poured })
    await flushPromises()

    expect(workflowGetCount).toBe(0)
    expect(wrapper.find('.wem-meta-contract-warning').exists()).toBe(false)
    const saveButton = footerButton(wrapper, 'main.workflow_edit_modal.save')
    expect(saveButton.attributes('disabled')).toBeUndefined()
    await saveButton.trigger('click')
    await flushPromises()

    expect(sentMetadata()).toEqual([{
      note: 'Poured note',
      source_doc_id: 'flowgate.default.0406.0004-WP',
      source_revision_no: 4,
    }])
  })
})