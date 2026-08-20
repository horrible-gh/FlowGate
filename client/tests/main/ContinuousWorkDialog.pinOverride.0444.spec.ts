// 0444 T0007 — NR0003 §4-5: the pin overrides a row's stored provider, and the screen has to
// say so.
//
// §2-5 of NR0003 re-ran ai_invoke_service.start_run() and confirmed the "pin wins globally"
// behaviour is real and intentional on the server (test_ai_invoke_pause_resume_0252 /
// test_ai_invoke_no_output_retry_0359 assert it). So the client is NOT flipped to
// "stored value wins" — that would make the screen disagree with what actually runs. The
// remaining defect is that the swap happened in silence.
//
// The second half is §4-5's other decision: `touchedSeqs` was ONE set for both the mention
// input and the provider select, so typing a sentence also froze that row's provider against
// the next plan re-read. It is two sets now.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest, patchRequest: vi.fn(), postRequest, putRequest: vi.fn(),
}))

const ROOT = 'flowgate.default.0444.0001-B'
const WP_DOC = 'flowgate.default.0444.0004-WP'

const PROVIDERS = [
  { id: 'stored', name: 'Stored Provider' },
  { id: 'default', name: 'Default Provider' },
  { id: 'other', name: 'Other Provider' },
  { id: 'third', name: 'Third Provider' },
]

/** Two plain (never auto-handled) rows, so switching the instruction mode re-reads the plan
 *  without changing which rows the table draws. */
function planRows() {
  return [
    {
      id: 1, item_seq: 1, type: 'D', label: 'Design', status: 'pending',
      provider_id: 'stored', provider_display_name: 'Stored Provider', provider_registered: true,
      note: 'stored sentence', source_doc_id: WP_DOC, source_revision_no: 8,
    },
    {
      id: 2, item_seq: 2, type: 'P', label: 'Plan', status: 'pending',
      provider_id: null, provider_display_name: null, provider_registered: null,
      note: '', source_doc_id: WP_DOC, source_revision_no: 8,
    },
  ]
}

function response(rows: any[]) {
  return {
    data: {
      doc_id: ROOT, doc_class: 'B', decided: true,
      items: JSON.parse(JSON.stringify(rows)), head: rows[0],
    },
  }
}

function mountDialog(
  { rows = planRows(), selectedProvider = 'default', providerPinned = false } = {},
) {
  getRequest.mockResolvedValue(response(rows))
  return mount(ContinuousWorkDialog, {
    props: {
      visible: true, docRef: ROOT, selectedProvider, providerPinned, providers: PROVIDERS,
    },
    global: { plugins: [i18n] },
  })
}

function providerTags(): HTMLElement[] {
  return [...document.querySelectorAll('.wsp-prov-tag')] as HTMLElement[]
}
async function openProviders() {
  ;(document.querySelectorAll('.cwd-tab')[1] as HTMLButtonElement).click()
  await flushPromises()
}
async function openMessages() {
  ;(document.querySelectorAll('.cwd-tab')[2] as HTMLButtonElement).click()
  await flushPromises()
}
async function switchInstructionMode(mode: 'auto_approved' | 'ai_direct') {
  ;(document.querySelectorAll('.cwd-tab')[0] as HTMLButtonElement).click()
  await flushPromises()
  const input = document.querySelector(`input[type="radio"][value="${mode}"]`) as HTMLInputElement
  input.click()
  await flushPromises()
}
function selects() {
  return document.querySelectorAll('.cwd-override-select .aip-select-input') as NodeListOf<HTMLSelectElement>
}
function messageInputs() {
  return document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
}
function planFill(notes: Record<number, string>, providers: Record<number, string>) {
  return {
    data: {
      wp_doc_id: WP_DOC, wp_revision_no: 8,
      fill_preview: { note_overrides: notes, provider_overrides: providers },
    },
  }
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  getRequest.mockReset().mockResolvedValue(response(planRows()))
  postRequest.mockReset().mockRejectedValue(new Error('no plan read in this test'))
})
afterEach(() => { document.body.innerHTML = '' })

describe('ContinuousWorkDialog pin-over-stored disclosure (0444 T0007 §5-2)', () => {
  it('names the pinned provider AND the stored value it displaced', async () => {
    mountDialog({ providerPinned: true })
    await flushPromises()

    const tag = providerTags()[0]
    expect(tag).toBeDefined()
    expect(tag.textContent).toBe(i18n.global.t(
      'main.continuous_work.provider_tag_pinned_over_stored',
      { name: 'Default Provider', stored: 'Stored Provider' },
    ))
    // Both halves have to be readable on the row — a tag that only names the winner does not
    // tell the person what changed.
    expect(tag.textContent).toContain('Default Provider')
    expect(tag.textContent).toContain('Stored Provider')
  })

  it('marks that tag with its own CSS class', async () => {
    mountDialog({ providerPinned: true })
    await flushPromises()
    // Re-query rather than reusing a wrapper handle: a DOMWrapper's classes() goes stale.
    expect(providerTags()[0].classList.contains('wsp-prov-tag--pinned')).toBe(true)
    expect(providerTags()[0].classList.contains('wsp-prov-tag')).toBe(true)
  })

  it('says nothing extra when the pin agrees with the stored value', async () => {
    const rows = planRows()
    rows[0].provider_id = 'default'
    rows[0].provider_display_name = 'Default Provider'
    mountDialog({ rows, providerPinned: true })
    await flushPromises()

    const tag = providerTags()[0]
    // Positive control: the row DOES render a tag, so "the new copy is absent" cannot be a
    // typo'd selector silently passing.
    expect(tag.textContent).toBe(
      i18n.global.t('main.continuous_work.provider_tag_default', { name: 'Default Provider' }),
    )
    expect(tag.classList.contains('wsp-prov-tag--pinned')).toBe(false)
  })

  it('keeps the plain stored tag when nothing is pinned', async () => {
    mountDialog({ providerPinned: false })
    await flushPromises()

    const tag = providerTags()[0]
    expect(tag.textContent).toBe(
      i18n.global.t('main.continuous_work.provider_tag_stored', { name: 'Stored Provider' }),
    )
    expect(tag.classList.contains('wsp-prov-tag--pinned')).toBe(false)
  })

  it('badges the override table row whose stored provider the pin displaced', async () => {
    mountDialog({ providerPinned: true })
    await flushPromises()
    await openProviders()

    const badges = [...document.querySelectorAll('.cwd-filled-badge')].map(node => node.textContent?.trim())
    expect(badges).toContain(
      i18n.global.t('main.continuous_work.sequence_provider_pin_overridden'),
    )
    // The row with no stored provider is not displacing anything, so it gets no badge.
    expect(badges).toHaveLength(1)
  })
})

describe('ContinuousWorkDialog note/provider touch sets are separate (0444 T0007 §5-3)', () => {
  it('re-syncs the provider of a row whose mention was typed by hand', async () => {
    postRequest.mockReset().mockResolvedValue(planFill({ 1: 'plan sentence A' }, { 1: 'other' }))
    mountDialog()
    await flushPromises()
    await openProviders()
    expect(selects()[0].value).toBe('other')

    await openMessages()
    const input = messageInputs()[0]
    input.value = 'typed by hand'
    input.dispatchEvent(new Event('input'))
    await flushPromises()

    // The plan moved again while the dialog was open, and the mode switch re-reads it.
    postRequest.mockResolvedValue(planFill({ 1: 'plan sentence B' }, { 1: 'third' }))
    await switchInstructionMode('ai_direct')
    await flushPromises()

    await openMessages()
    expect(messageInputs()[0].value).toBe('typed by hand')
    await openProviders()
    expect(selects()[0].value).toBe('third')
  })

  it('keeps a provider the person chose, however often the plan is re-read', async () => {
    postRequest.mockReset().mockResolvedValue(planFill({ 1: 'plan sentence A' }, { 1: 'other' }))
    mountDialog()
    await flushPromises()
    await openProviders()

    selects()[0].value = 'third'
    selects()[0].dispatchEvent(new Event('change'))
    await flushPromises()
    expect(selects()[0].value).toBe('third')

    postRequest.mockResolvedValue(planFill({ 1: 'plan sentence B' }, { 1: 'other' }))
    await switchInstructionMode('ai_direct')
    await flushPromises()

    await openProviders()
    expect(selects()[0].value).toBe('third')
    await openMessages()
    expect(messageInputs()[0].value).toBe('plan sentence B')
  })
})
