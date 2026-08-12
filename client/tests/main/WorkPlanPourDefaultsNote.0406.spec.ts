// 0406 T0009: render and save rows whose notes came from plan.defaults.note.
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
const WP_DOC_ID = 'flowgate.default.0406.0004-WP'
const SHARED_NOTE = 'Shared plan note'

const poured: PourPayload = {
  wpDocId: WP_DOC_ID,
  wpRevisionNo: 9,
  wpShortCode: 'WP0004',
  workflowDocId: DOC_ID,
  mode: 'append',
  planStepCount: 3,
  rows: [
    {
      type: 'D', label: 'Design', status: 'pending', locked: false, poured: true,
      note: SHARED_NOTE, note_source: 'defaults', origin: 'plan', plan_key: 'D#1',
      source_doc_id: WP_DOC_ID, source_revision_no: 9,
    },
    {
      type: 'L', label: 'Logic', status: 'pending', locked: false, poured: true,
      note: SHARED_NOTE, note_source: 'defaults', origin: 'plan', plan_key: 'L#1',
      source_doc_id: WP_DOC_ID, source_revision_no: 9,
    },
    {
      type: 'T', label: 'Task', status: 'pending', locked: false, poured: true,
      note: 'Step-specific note', note_source: 'step', origin: 'plan', plan_key: 'T#1',
      source_doc_id: WP_DOC_ID, source_revision_no: 9,
    },
    {
      type: 'TR', label: 'Task report', status: 'pending', locked: false, poured: true,
      note: '', note_source: null, origin: 'auto', plan_key: null,
      source_doc_id: null, source_revision_no: null,
    },
  ],
  rowCountChange: { before: 0, after: 4, deleted: 0, added: 4 },
  notifications: [],
  workflowTag: 'tag-0406-defaults',
}

function mountModal() {
  return mount(WorkflowDecisionModal, {
    props: { visible: true, mode: 'edit', docId: DOC_ID, poured },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

type Wrapper = ReturnType<typeof mountModal>

function saveButton(wrapper: Wrapper) {
  const label = i18n.global.t('main.workflow_edit_modal.save')
  const button = wrapper.findAll('.modal-ft button').find(candidate => candidate.text().includes(label))
  expect(button).toBeTruthy()
  return button!
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset().mockResolvedValue({ data: { providers: [], default_provider_id: null } })
  patchRequest.mockReset().mockResolvedValue({ data: { status: 'updated' } })
})

describe('WorkflowDecisionModal shared plan note fallback (0406 T0009)', () => {
  it('renders fallback notes, provenance titles, non-empty styling, and an auto-row input', async () => {
    const wrapper = mountModal()
    await flushPromises()

    const rows = wrapper.findAll('.wdm-seq-item')
    const badges = wrapper.findAll('.wdm-plan-badge')
    const inputs = wrapper.findAll<HTMLInputElement>('.wdm-note-input')
    const expectedBadge = i18n.global.t('main.work_plan_pour.from_plan', { doc: 'WP0004' })
    const expectedTitle = i18n.global.t('main.work_plan_pour.defaults_note_title')

    expect(rows).toHaveLength(4)
    // 0408 M0019 재반려 2: the report row carries its own mention, so it has an input like any
    // other row — under [자동 승인] its note is the one an AI worker is handed.
    expect(inputs).toHaveLength(4)
    expect(badges).toHaveLength(3)
    for (const index of [0, 1]) {
      expect(badges[index].text()).toBe(expectedBadge)
      expect(inputs[index].element.value).toBe(SHARED_NOTE)
      const message = rows[index].find('.wdm-seq-msg')
      expect(message.classes()).not.toContain('wdm-seq-msg--empty')
      expect(message.findComponent({ name: 'AppIcon' }).props('name')).toBe('chat-circle-dots')
      expect(badges[index].attributes('title')).toBe(expectedTitle)
    }
    expect(badges[2].attributes('title')).toBeUndefined()
    expect(rows[3].find('.wdm-note-input').exists()).toBe(true)
    expect(inputs[3].element.value).toBe('')

    console.log('DEFAULTS_NOTE_UI=' + JSON.stringify({
      badgeText: badges[0].text(),
      inputValue: inputs[0].element.value,
      rowClasses: rows[0].classes(),
      messageClasses: rows[0].find('.wdm-seq-msg').classes(),
      badgeTitle: badges[0].attributes('title'),
    }))
  })

  it('clears defaults provenance on typing and keeps note_source out of the seven-key PATCH rows', async () => {
    const wrapper = mountModal()
    await flushPromises()

    const firstInput = wrapper.findAll<HTMLInputElement>('.wdm-note-input')[0]
    await firstInput.setValue('Edited shared note')
    expect(wrapper.findAll('.wdm-plan-badge')[0].attributes('title')).toBeUndefined()

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(patchRequest).toHaveBeenCalledTimes(1)
    const body = patchRequest.mock.calls[0][1]
    const keys = ['label', 'note', 'provider_display_name', 'provider_id', 'source_doc_id', 'source_revision_no', 'type']
    for (const item of body.items) {
      expect(Object.keys(item).sort()).toEqual(keys)
      expect(item).not.toHaveProperty('note_source')
    }
    expect(body.items[0]).toEqual({
      type: 'D', label: 'Design', note: 'Edited shared note',
      source_doc_id: WP_DOC_ID, source_revision_no: 9,
      provider_id: null, provider_display_name: null,
    })
    expect(body.items[1]).toEqual({
      type: 'L', label: 'Logic', note: SHARED_NOTE,
      source_doc_id: WP_DOC_ID, source_revision_no: 9,
      provider_id: null, provider_display_name: null,
    })
    expect(body.items[2]).toEqual({
      type: 'T', label: 'Task', note: 'Step-specific note',
      source_doc_id: WP_DOC_ID, source_revision_no: 9,
      provider_id: null, provider_display_name: null,
    })
    expect(body.items[3]).toEqual({
      type: 'TR', label: 'Task report', note: '',
      source_doc_id: null, source_revision_no: null,
      provider_id: null, provider_display_name: null,
    })
    console.log('DEFAULTS_NOTE_PATCH=' + JSON.stringify(body.items))
  })
})