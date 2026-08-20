// 0444 T0005 §4-4 / NR0003 §2-2: the pour path now refuses a provider that is not
// registered (or is switched off) and says so. The server sends the envelope; the dialog's
// generic notificationText() maps the code to main.work_plan_pour.notify_<code>, so what has
// to be proven here is that the three locale files actually carry the key and that the
// existing wdm-banner--warn row renders it. No template and no CSS class was added.
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

import WorkflowDecisionModal, {
  type PourNotification,
  type PourPayload,
} from '@main/components/WorkflowDecisionModal.vue'

const DOC_ID = 'flowgate.default.0444.0001-B'
const WP_DOC_ID = 'flowgate.default.0444.0004-WP'
const LOCALES = ['ko', 'en', 'ja'] as const

const UNREGISTERED: PourNotification = {
  code: 'provider_not_registered',
  severity: 'warning',
  count: 2,
  items: [
    { plan_key: 'D#1', provider_id: 'gone' },
    { plan_key: 'defaults', provider_id: 'also-gone' },
  ],
}

function payload(notifications: PourNotification[]): PourPayload {
  return {
    wpDocId: WP_DOC_ID,
    wpRevisionNo: 3,
    wpShortCode: 'WP0004',
    workflowDocId: DOC_ID,
    mode: 'append',
    planStepCount: 1,
    rows: [
      {
        type: 'D', label: 'Design', status: 'pending', locked: false, poured: true,
        note: 'a note', note_source: 'step', origin: 'plan', plan_key: 'D#1',
        source_doc_id: WP_DOC_ID, source_revision_no: 3,
      },
    ],
    rowCountChange: { before: 0, after: 1, deleted: 0, added: 1 },
    notifications,
    workflowTag: 'tag-0444',
  } as PourPayload
}

function mountModal(notifications: PourNotification[]) {
  return mount(WorkflowDecisionModal, {
    props: { visible: true, mode: 'edit', docId: DOC_ID, poured: payload(notifications) },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

function warnBannerTexts(wrapper: ReturnType<typeof mountModal>) {
  return wrapper.findAll('.wdm-banner--warn').map(banner => banner.text())
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  getRequest.mockReset().mockResolvedValue({ data: { providers: [], default_provider_id: null } })
  patchRequest.mockReset().mockResolvedValue({ data: { status: 'updated' } })
})

describe('WorkflowDecisionModal provider_not_registered banner (0444 T0005)', () => {
  it.each(LOCALES)('has a distinct, non-placeholder message in %s', locale => {
    i18n.global.locale.value = locale
    const key = 'main.work_plan_pour.notify_provider_not_registered'
    const text = i18n.global.t(key, { n: 2 })
    expect(text).not.toBe(key)
    expect(text.trim().length).toBeGreaterThan(0)
    expect(text).toContain('2')
  })

  it('gives each locale its own sentence', () => {
    const seen = LOCALES.map(locale => {
      i18n.global.locale.value = locale
      return i18n.global.t('main.work_plan_pour.notify_provider_not_registered', { n: 2 })
    })
    expect(new Set(seen).size).toBe(LOCALES.length)
  })

  it('renders the envelope as one warning banner', async () => {
    const wrapper = mountModal([UNREGISTERED])
    await flushPromises()

    const expected = i18n.global.t('main.work_plan_pour.notify_provider_not_registered', { n: 2 })
    const texts = warnBannerTexts(wrapper)
    expect(texts.some(text => text.includes(expected))).toBe(true)
    // The count comes from the envelope, not from the row list on screen.
    expect(texts.filter(text => text.includes(expected))).toHaveLength(1)
  })

  it('renders nothing of the sort when the server sends no such code', async () => {
    // Positive control for the negative assertion: same mount, same selector, one code
    // swapped. Without this, "not rendered" would also pass if the banner never renders.
    const other: PourNotification = { code: 'type_overlap', severity: 'warning', count: 1, types: ['D'] }
    const withCode = mountModal([UNREGISTERED, other])
    const withoutCode = mountModal([other])
    await flushPromises()

    const expected = i18n.global.t('main.work_plan_pour.notify_provider_not_registered', { n: 2 })
    const overlap = i18n.global.t('main.work_plan_pour.notify_type_overlap', { n: 1, types: 'D' })

    expect(warnBannerTexts(withCode).some(text => text.includes(expected))).toBe(true)
    expect(warnBannerTexts(withoutCode).some(text => text.includes(expected))).toBe(false)
    // ...and the control code renders in both, so the selector was live either way.
    expect(warnBannerTexts(withCode).some(text => text.includes(overlap))).toBe(true)
    expect(warnBannerTexts(withoutCode).some(text => text.includes(overlap))).toBe(true)
  })
})
