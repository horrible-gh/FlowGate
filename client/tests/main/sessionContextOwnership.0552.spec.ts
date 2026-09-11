import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'
import { useAiInvokeRunsStore, UI_SETTINGS_PATH } from '@main/stores/aiInvokeRuns'

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

const DOC_A = 'flowgate.default.0552.0001-R'
const DOC_B = 'flowgate.default.0552.0019-T'
const DOC_C = 'flowgate.default.0553.0001-R'
const OWNER_A = 'owner-a'

function detail(docId: string) {
  const other = docId === DOC_C
  return {
    data: {
      doc_id: docId,
      title: docId,
      status: 'open',
      type_code: docId.endsWith('-T') ? 'T' : 'R',
      project_id: 'flowgate',
      group_id: other ? 'flowgate.default.0553' : 'flowgate.default.0552',
      owner_id: other ? 'owner-b' : OWNER_A,
    },
  }
}

function tab(id: string) {
  return { id, title: id, path: '', type: 'md', typeCode: id.endsWith('-T') ? 'T' : 'R' } as any
}

function mountHeader(id: string) {
  return shallowMount(DocHeader, {
    props: { tab: tab(id) },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) {
      const id = decodeURIComponent(url.split('doc_id=')[1] ?? DOC_A)
      return Promise.resolve(detail(id))
    }
    if (url === `/api/v1/users/${OWNER_A}`) return Promise.resolve({ data: { username: 'Owner A' } })
    if (url === '/api/v1/users/owner-b') return Promise.resolve({ data: { display_name: 'Owner B' } })
    if (url === '/api/v1/groups/flowgate.default.0552') {
      return Promise.resolve({ data: { status: 'success', group: { group_id: 'flowgate.default.0552', project_id: 'flowgate', title: 'Set C' } } })
    }
    if (url === '/api/v1/groups/flowgate.default.0553') {
      return Promise.resolve({ data: { status: 'success', group: { group_id: 'flowgate.default.0553', project_id: 'flowgate', title: 'Other group' } } })
    }
    return Promise.resolve({ data: {} })
  })
})

describe('0552 T0019 — session fact ownership', () => {
  it('does not read ui-settings for a document mutation signal', async () => {
    const store = useAiInvokeRunsStore()
    getRequest.mockClear()

    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'flowgate' } }))
    await flushPromises()

    expect(getRequest.mock.calls.filter(([url]) => url === UI_SETTINGS_PATH)).toHaveLength(0)
    store.$dispose()
  })

  it('uses a single-group endpoint and reuses owner/group facts across same-context documents', async () => {
    const first = mountHeader(DOC_A)
    await flushPromises()
    expect((first.vm as any).ownerName).toBe('Owner A')
    expect((first.vm as any).groupTitle).toBe('Set C')
    first.unmount()

    const second = mountHeader(DOC_B)
    await flushPromises()
    expect((second.vm as any).ownerName).toBe('Owner A')
    expect((second.vm as any).groupTitle).toBe('Set C')

    const urls = getRequest.mock.calls.map(([url]) => url)
    expect(urls.filter((url) => url === `/api/v1/users/${OWNER_A}`)).toHaveLength(1)
    expect(urls.filter((url) => url === '/api/v1/groups/flowgate.default.0552')).toHaveLength(1)
    expect(urls.filter((url) => url === '/api/v1/groups')).toHaveLength(0)
    second.unmount()
  })

  it('loads fresh facts after switching to a different owner and group', async () => {
    const first = mountHeader(DOC_A)
    await flushPromises()
    first.unmount()

    const other = mountHeader(DOC_C)
    await flushPromises()
    expect((other.vm as any).ownerName).toBe('Owner B')
    expect((other.vm as any).groupTitle).toBe('Other group')
    expect(getRequest.mock.calls.filter(([url]) => url === '/api/v1/users/owner-b')).toHaveLength(1)
    expect(getRequest.mock.calls.filter(([url]) => url === '/api/v1/groups/flowgate.default.0553')).toHaveLength(1)
    other.unmount()
  })
})