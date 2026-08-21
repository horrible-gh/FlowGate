// 0448 T0005 §4 / §6 — supersedes 0444 T0007's "say what the pin displaced" contract.
//
// 0444 read the server's "pin wins globally" branch as the product decision and made the row
// narrate it: `고정 · X (저장값 Y 대신)` on the left tag and `⚠ 고정된 공급자가 저장값을 덮음`
// on the right. B0001 quoted both back ("멘트 주절주절 있는거 싫어하는거 알면서") and added the
// real complaint: `연계는 쳐 되어있지도 않고`. NR0003 §4 found the circular step — the pin those
// two sentences explained was created by the ORDINARY selector, not by anyone asking for a
// force-all, so the stored step provider was being cancelled by a plain default pick.
//
// 0448 removes the cause instead of the wording: an ordinary pick no longer pins (§2), so
// there is no displaced value to narrate and both strings are deleted with their renderers.
// What survives here is the boundary — a stored row still names its stored provider, and an
// EXPLICIT force-all names one effective provider per row, once.
//
// The second half is 0444 §4-5's other decision, untouched: `touchedSeqs` was ONE set for both
// the mention input and the provider select, so typing a sentence also froze that row's
// provider against the next plan re-read. It is two sets now.
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

describe('ContinuousWorkDialog provider disclosure (0448 T0005 §4)', () => {
  // Rewritten from 0444's "says nothing extra when the pin agrees with the stored value",
  // which injected `providerPinned` as a stand-in for an ordinary selection. §6: an ordinary
  // selection must not be read as a pin at all — it is the default for rows that stored
  // nothing, so a row that DID store a provider keeps naming that one.
  it('treats an ordinary selection as a default, so a stored row still names its stored provider', async () => {
    mountDialog({ selectedProvider: 'other', providerPinned: false })
    await flushPromises()

    const tags = providerTags()
    // Row 1 stores 'stored'; row 2 stores nothing and falls back to the ordinary selection.
    // Both are drawn, so neither assertion can pass on an empty query.
    expect(tags[0].textContent).toBe(
      i18n.global.t('main.continuous_work.provider_tag_stored', { name: 'Stored Provider' }),
    )
    expect(tags[1].textContent).toBe(
      i18n.global.t('main.continuous_work.provider_tag_default', { name: 'Other Provider' }),
    )
    expect(tags[0].textContent).not.toContain('Other Provider')
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

  // Replaces the three deleted 0444 tests (`names the pinned provider AND the stored value it
  // displaced`, `marks that tag with its own CSS class`, `badges the override table row whose
  // stored provider the pin displaced`) with the one boundary §4-2 keeps: even under an
  // EXPLICIT force-all the row states the effective provider once, and no second element
  // repeats it.
  it('names one effective provider per row under an explicit force-all, with no displaced-value copy', async () => {
    mountDialog({ providerPinned: true })
    await flushPromises()

    const tag = providerTags()[0]
    expect(tag).toBeDefined()
    expect(tag.textContent).toBe(
      i18n.global.t('main.continuous_work.provider_tag_default', { name: 'Default Provider' }),
    )
    expect(tag.textContent).not.toContain('Stored Provider')
    expect(tag.classList.contains('wsp-prov-tag--pinned')).toBe(false)

    await openProviders()
    // The right-hand table said the same thing a second time. Row 1's stored provider is
    // registered and row 2 stores nothing, so neither carries a badge now.
    expect(document.querySelectorAll('.cwd-filled-badge')).toHaveLength(0)
    // Positive control for that zero: the selects ARE rendered, one per execution row.
    expect(document.querySelectorAll('.cwd-override-select .aip-select-input')).toHaveLength(2)
  })

  // B0001 transcribed both sentences off the screen. They are gone from the catalogue, so
  // this reads the rendered Korean dialog rather than an i18n key that no longer resolves.
  it('leaves neither transcribed pin sentence anywhere in the Korean dialog', async () => {
    i18n.global.locale.value = 'ko'
    mountDialog({ providerPinned: true })
    await flushPromises()
    await openProviders()

    const body = document.body.textContent ?? ''
    expect(body).not.toContain('저장값 Stored Provider 대신')
    expect(body).not.toContain('고정된 공급자가 저장값을 덮음')
    // Positive control: the Korean dialog really did render the row tag, so the two absences
    // are not an empty document passing.
    expect(body).toContain(
      i18n.global.t('main.continuous_work.provider_tag_default', { name: 'Default Provider' }),
    )
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
