import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// r5/r6: opening a discard (DC) document showed a blank .doc-chip, and the ⋯ menu /
// title pencil must not appear. The chip read only tab.typeCode, but a file-less DC
// tab restored from persistence reaches the header with an empty tab.typeCode while
// the loaded document still carries type_code='DC'. The header must resolve the type
// from the document, exactly like the status badge already did.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function detailResponse() {
  return {
    data: {
      doc_id: 'test.test.0012.0003-DC',
      title: 'Group Discard',
      status: 'closed',
      type_code: 'DC',
      doc_review_status: null,
      is_editable: false,
      project_id: 'test',
      group_id: 'test.test.0012',
    },
  }
}

// The tab deliberately carries NO typeCode — the failing real-world case.
function makeTab() {
  return { id: 'test.test.0012.0003-DC', title: 'Group Discard', path: '', type: 'unsupported' }
}

function mountHeader() {
  return shallowMount(DocHeader, { props: { tab: makeTab() as any }, global: { plugins: [i18n] } })
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) return Promise.resolve(detailResponse())
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('DocHeader discard (DC) chip and affordances', () => {
  it('renders the "그룹 폐기" chip even when the tab has no typeCode', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    const chip = wrapper.find('.doc-chip')
    expect(chip.exists()).toBe(true)
    // The chip resolves the Discard label from the document (locale-agnostic) — the
    // key point is it is NOT blank, which was the r5/r6 regression.
    expect(chip.text().trim()).toMatch(/그룹 폐기|Group Discard|グループ廃棄/)
    expect(chip.classes()).toContain('c-DC')
    wrapper.unmount()
  })

  it('hides the ⋯ group menu and the title pencil on a discard document', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect(wrapper.find('.doc-hdr-more-btn').exists()).toBe(false)
    expect(wrapper.find('.doc-title-pencil').exists()).toBe(false)
    wrapper.unmount()
  })

  // r7: the chip markup was correct (class c-DC + label text present) yet the chip
  // rendered visually BLANK — `.doc-chip` forces `color: white` and gets its background
  // solely from the `.c-<type>` class, but `.c-DC` was never defined in app.css. White
  // text + white icon on the white header = invisible. jsdom doesn't apply CSS, so the
  // markup assertions above stayed green through six rejections. Guard the stylesheet
  // itself: every chip type code DocHeader can render must have a chip background defined.
  it('defines a chip background colour for every DocHeader type code (no white-on-white)', () => {
    const css = readFileSync(join(process.cwd(), 'shared/app.css'), 'utf-8')
    // The codes DocHeader's chip can render (TYPE_ICONS keys + the DC discard pseudo-doc).
    const chipTypeCodes = ['R', 'DS', 'D', 'T', 'TR', 'DC']
    for (const code of chipTypeCodes) {
      const re = new RegExp(`\\.c-${code}\\b\\s*\\{[^}]*background`)
      expect(re.test(css), `.c-${code} must define a background colour in app.css`).toBe(true)
    }
  })
})
