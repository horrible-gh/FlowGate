import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// Regression coverage for bug flowgate.default.0145.0001-B:
// the "next action" dialog only fetched the first page (server default 100) of the
// module's group list, so a current document whose group sorted past page 1 was
// neither default-selected nor even present in the picker.
//
// The fix (TR0005 rev1) is a bounded, lazy contract — NOT an eager full-load:
//   - on open, load pages only until the current document's (preferred) group
//     appears, then stop, and default-select it;
//   - render the remaining pages incrementally as the user scrolls the list.
// These tests drive a fake paginated /groups endpoint and assert the preferred
// group is reachable and default-selected regardless of how many groups exist,
// while the list is not eagerly loaded in full.

// t() -> key so the template renders without a message catalog.
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (k: string) => k }),
}))

// Hoisted mock of the shared API layer; each test scripts its responses.
const { mockGetRequest } = vi.hoisted(() => ({ mockGetRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest: mockGetRequest }))

import NextActionModal from '@main/components/NextActionModal.vue'

function makeGroups(n: number) {
  return Array.from({ length: n }, (_, i) => {
    const code = String(i + 1).padStart(4, '0')
    return { group_id: `flowgate.default.${code}`, title: `group ${code}` }
  })
}

// A /groups server that honours limit/offset and reports `total`, capping each
// page at the server's real maximum (200) so a naive limit=200 fetch cannot cover
// a >200-group module — reaching a late group requires walking further pages.
function installPaginatedGroups(total: number, pageCap = 200) {
  const groups = makeGroups(total)
  mockGetRequest.mockImplementation(async (path: string, params: any = {}) => {
    if (path === '/api/v1/modules') {
      return { data: { items: [{ module_id: 'default', title: 'default' }] } }
    }
    if (/\/groups$/.test(path)) {
      const limit = Math.min(params.limit ?? 100, pageCap)
      const offset = params.offset ?? 0
      return { data: { ok: true, total, offset, limit, items: groups.slice(offset, offset + limit) } }
    }
    if (/\/documents$/.test(path)) return { data: { items: [] } }
    if (/\/predecessors$/.test(path)) return { data: { predecessor_doc_ids: [] } }
    return { data: {} }
  })
}

async function mountModal(groupId: string) {
  // Mounted hidden, then revealed — the data-load runs in a (non-immediate) watch on
  // `visible`, mirroring how the dialog is actually opened in the app.
  const wrapper = mount(NextActionModal, {
    props: {
      visible: false,
      nextStepLabel: 'TR',
      nextTypeCode: 'TR',
      projectId: 'flowgate',
      docModule: 'default',
      groupId,
    },
    global: { stubs: { teleport: true } },
  })
  await wrapper.setProps({ visible: true })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('NextActionModal group pagination (bug 0145.0001-B)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGetRequest.mockReset()
  })

  it('renders every group page, not just the first 100', async () => {
    installPaginatedGroups(146)
    const wrapper = await mountModal('flowgate.default.0145')
    expect(wrapper.findAll('.nad-group-item').length).toBe(146)
  })

  it('default-selects the current group even when it sorts past page 1', async () => {
    installPaginatedGroups(146)
    const wrapper = await mountModal('flowgate.default.0145')
    const active = wrapper.findAll('.nad-group-item.active')
    expect(active.length).toBe(1)
    expect(active[0].text()).toContain('0145')
  })

  it('lazy-loads up to the preferred group on open, then reaches the rest on scroll', async () => {
    // 450 groups, preferred = 0300 (the 300th). With a 200/page server the modal
    // loads pages only until 0300 appears (page 1 = 0001-0200, page 2 = 0201-0400),
    // then STOPS — it must not eagerly pull the whole 450-group module (TR0005 rev1
    // reviewer #1). So exactly 400 rows are rendered on open, and 0300 is selected.
    installPaginatedGroups(450)
    const wrapper = await mountModal('flowgate.default.0300')
    expect(wrapper.findAll('.nad-group-item').length).toBe(400)
    const active = wrapper.findAll('.nad-group-item.active')
    expect(active.length).toBe(1)
    expect(active[0].text()).toContain('0300')

    // Infinite scroll: scrolling the list pulls the remaining page(s) incrementally,
    // so the late group (0450) — past both the 100 and 200 boundaries — becomes
    // reachable, while the current selection is preserved across the lazy load.
    await wrapper.find('.nad-group-list').trigger('scroll')
    await flushPromises()
    const items = wrapper.findAll('.nad-group-item')
    expect(items.length).toBe(450)
    expect(items.some(n => n.text().includes('0450'))).toBe(true)
    const stillActive = wrapper.findAll('.nad-group-item.active')
    expect(stillActive.length).toBe(1)
    expect(stillActive[0].text()).toContain('0300')
  })

  it('still default-selects on the small single-page case', async () => {
    installPaginatedGroups(12)
    const wrapper = await mountModal('flowgate.default.0007')
    expect(wrapper.findAll('.nad-group-item').length).toBe(12)
    expect(wrapper.find('.nad-group-item.active').text()).toContain('0007')
  })
})
