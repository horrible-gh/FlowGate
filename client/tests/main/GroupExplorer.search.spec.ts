// (A) 유지 — 0394 T0016 / NR0003 §6.3.
// 두 케이스만 GroupExplorer.vue의 <style scoped> 블록을 읽는다. 어두운 사이드바 위 글자색과
// 폰트 스택은 CSS 선언이고, jsdom은 scoped 스타일을 계산하지 않는다. 나머지 케이스는 전부
// 마운트해서 검색 동작을 본다.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GroupExplorer from '@main/components/GroupExplorer.vue'
import { useTabsStore } from '@main/stores/tabs'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

function n(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

const NODES = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'P' }),
  n({ id: 'module:p:default', parent_id: 'project:p', node_type: 'module', label: 'default' }),
  n({ id: 'p.default.0001', parent_id: 'module:p:default', node_type: 'group', label: 'G1' }),
  n({ id: 'p.default.0001.0001-R', parent_id: 'p.default.0001', node_type: 'document', type_code: 'R', number: '0001-R', filename: 'login.md', label: '[R]: r1', has_md: true, md_path: 'r1.md' }),
]

// Meta (title/doc_id) hit. rev10: the default meta endpoint now ALSO returns a
// brief body preview so every result row shows the document's simplified body —
// not only when "내용까지 검색" is checked. Content hit adds a match-centred excerpt.
const META_ITEM = {
  doc_id: 'p.default.0001.0001-R',
  type: 'R',
  title: 'Login requirement',
  status: 'open',
  project_id: 'p',
  group_id: 'p.default.0001',
  snippet: 'test 1234 brief body preview',
  matched_in: 'doc_id',
}
const CONTENT_ITEM = { ...META_ITEM, snippet: '...the login flow must support SSO...', matched_in: 'body' }

const GroupTreeNodeStub = {
  name: 'GroupTreeNode',
  props: ['node', 'allNodes', 'treeNodes', 'projectId'],
  template: '<li class="stub-node" />',
}

function mockApi(metaItems = [META_ITEM], contentItems = [CONTENT_ITEM]) {
  getRequest.mockImplementation((url: string) => {
    // content endpoint is a superset path of the meta one — match it first.
    if (url.includes('/search/documents/content')) {
      return Promise.resolve({ data: { ok: true, items: contentItems, total: contentItems.length } })
    }
    if (url.includes('/search/documents')) {
      return Promise.resolve({ data: { ok: true, items: metaItems, total: metaItems.length } })
    }
    // group tree
    return Promise.resolve({ data: { data: { nodes: NODES } } })
  })
}

async function mountExplorer() {
  const wrapper = mount(GroupExplorer, {
    props: { projectId: 'p' },
    global: { plugins: [i18n], stubs: { GroupTreeNode: GroupTreeNodeStub } },
  })
  await flushPromises()
  return wrapper
}

// The search box is hidden until the filter button reveals it (rev2).
async function openSearch(wrapper: Awaited<ReturnType<typeof mountExplorer>>) {
  await wrapper.find('[data-test="explorer-search-toggle"]').trigger('click')
  await flushPromises()
}

async function type(wrapper: Awaited<ReturnType<typeof mountExplorer>>, value: string) {
  await wrapper.find('[data-test="explorer-search-input"]').setValue(value)
  vi.advanceTimersByTime(300)
  await flushPromises()
}

function lastSearchCall() {
  return [...getRequest.mock.calls].reverse().find((c) => String(c[0]).includes('/search/documents'))
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('GroupExplorer in-explorer document search', () => {
  it('hides the search box by default and shows it when the filter button is pressed', async () => {
    mockApi()
    const wrapper = await mountExplorer()
    // Hidden by default — only the tree shows.
    expect(wrapper.find('[data-test="explorer-search-input"]').exists()).toBe(false)
    expect(wrapper.findComponent(GroupTreeNodeStub).exists()).toBe(true)

    await openSearch(wrapper)
    expect(wrapper.find('[data-test="explorer-search-input"]').exists()).toBe(true)
    // Empty box → still the tree, no results.
    expect(wrapper.find('[data-test="explorer-search-results"]').exists()).toBe(false)
    expect(wrapper.findComponent(GroupTreeNodeStub).exists()).toBe(true)
  })

  it('searches title/ID (meta endpoint) by default and renders the matched documents', async () => {
    mockApi()
    const wrapper = await mountExplorer()
    await openSearch(wrapper)
    await type(wrapper, 'login')

    const call = lastSearchCall()
    expect(call).toBeTruthy()
    // default is the meta endpoint (no /content), scoped to the project
    expect(call![0]).toBe('/api/v1/search/documents')
    expect(call![1]).toMatchObject({ q: 'login', project: 'p' })

    const results = wrapper.findAll('[data-test="explorer-search-result"]')
    expect(results).toHaveLength(1)
    // rev4: the row shows the document's full id and title name (not number:filename)
    expect(results[0].find('[data-test="explorer-search-result-id"]').text()).toBe('p.default.0001.0001-R')
    expect(results[0].find('[data-test="explorer-search-result-title"]').text()).toBe('Login requirement')
    // rev10: the default meta search now shows the brief body preview too — this is
    // the user's repeated rejection ("검색결과에 문서의 간략한 본문이 안나온다"). The body
    // excerpt must appear on the default search, not only with "내용까지 검색" checked.
    expect(wrapper.find('[data-test="explorer-search-snippet"]').text()).toContain('test 1234')
    // the tree is replaced while searching
    expect(wrapper.findComponent(GroupTreeNodeStub).exists()).toBe(false)
  })

  it('switches to the content endpoint and shows a snippet when "search content" is checked', async () => {
    mockApi()
    const wrapper = await mountExplorer()
    await openSearch(wrapper)
    await type(wrapper, 'login')
    expect(lastSearchCall()![0]).toBe('/api/v1/search/documents')

    // Tick the checkbox → active query re-runs against the content endpoint.
    await wrapper.find('[data-test="explorer-search-content"]').setValue(true)
    await flushPromises()

    expect(lastSearchCall()![0]).toBe('/api/v1/search/documents/content')
    expect(wrapper.find('[data-test="explorer-search-snippet"]').text()).toContain('SSO')
  })

  it('shows the full doc id and the title name on each result row (rev4)', async () => {
    // A hit outside the loaded tree still renders the backend id + title.
    const stray = {
      doc_id: 'p.default.0099.0001-R',
      type: 'R',
      title: 'Stray hidden-group requirement',
      status: 'open',
      project_id: 'p',
      group_id: 'p.default.0099',
      snippet: '...body excerpt without any heading...',
      matched_in: 'body',
    }
    mockApi([stray], [stray])
    const wrapper = await mountExplorer()
    await openSearch(wrapper)
    await wrapper.find('[data-test="explorer-search-content"]').setValue(true)
    await type(wrapper, 'body')

    const row = wrapper.find('[data-test="explorer-search-result"]')
    expect(row.find('[data-test="explorer-search-result-id"]').text()).toBe('p.default.0099.0001-R')
    expect(row.find('[data-test="explorer-search-result-title"]').text()).toBe('Stray hidden-group requirement')
  })

  it('opens the matched document (reusing the local tree node) on click', async () => {
    mockApi()
    const wrapper = await mountExplorer()
    const tabs = useTabsStore()
    const openSpy = vi.spyOn(tabs, 'openTab')

    await openSearch(wrapper)
    await type(wrapper, 'login')
    await wrapper.find('[data-test="explorer-search-result"]').trigger('click')

    expect(openSpy).toHaveBeenCalledTimes(1)
    expect(openSpy.mock.calls[0][0]).toMatchObject({ id: 'p.default.0001.0001-R', type: 'md', mdPath: 'r1.md' })
  })

  it('renders an empty state when nothing matches', async () => {
    mockApi([], [])
    const wrapper = await mountExplorer()
    await openSearch(wrapper)
    await type(wrapper, 'zzzz')
    expect(wrapper.find('[data-test="explorer-search-empty"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-test="explorer-search-result"]')).toHaveLength(0)
  })

  it('returns to the tree when the query is cleared', async () => {
    mockApi()
    const wrapper = await mountExplorer()
    await openSearch(wrapper)
    await type(wrapper, 'login')
    expect(wrapper.find('[data-test="explorer-search-results"]').exists()).toBe(true)

    await wrapper.find('[data-test="explorer-search-clear"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="explorer-search-results"]').exists()).toBe(false)
    expect(wrapper.findComponent(GroupTreeNodeStub).exists()).toBe(true)
  })

  // Regression guard for "검색된 결과 폰트가 전부 시커멓잖아" (rev3): the sidebar is
  // dark navy, so the result rows MUST set an explicit light color. jsdom can't
  // compute scoped CSS, so assert it against the component source instead — the
  // result block must carry a white-ish color and the snippet must not be a
  // black-on-black opacity-only rule.
  it('gives search result rows an explicit light color (not the near-black body --text)', () => {
    const src = readFileSync(
      resolve(process.cwd(), 'src/main/components/GroupExplorer.vue'),
      'utf-8',
    )
    const resultBlock = src.slice(src.indexOf('.sdb-result {'), src.indexOf('.sdb-result:hover'))
    expect(resultBlock).toMatch(/color:\s*rgba\(255,\s*255,\s*255/)
    const snippetBlock = src.slice(src.indexOf('.sdb-result-snippet {'))
    expect(snippetBlock).toMatch(/color:\s*rgba\(255,\s*255,\s*255/)
  })

  // Regression guard for "doc의 폰트가 예쁘지 않다" (rev5): the rev4 doc-id font
  // stack listed only Apple fonts (ui-monospace/SFMono-Regular/Menlo) before the
  // generic fallback, so on Windows it dropped to the default Courier-style
  // `monospace` and looked ugly. The id must use the app-wide 'JetBrains Mono'
  // stack (same as .doc-id-badge / DocInfoPanel / NextActionModal). jsdom can't
  // compute scoped CSS, so assert against the component source.
  it('renders the result doc id in the app-wide JetBrains Mono stack (rev5 font)', () => {
    const src = readFileSync(
      resolve(process.cwd(), 'src/main/components/GroupExplorer.vue'),
      'utf-8',
    )
    const idBlock = src.slice(src.indexOf('.sdb-result-id {'), src.indexOf('.sdb-result-title {'))
    expect(idBlock).toMatch(/font-family:\s*'JetBrains Mono'/)
    // The broken Apple-only-first ordering must not lead the stack any more.
    expect(idBlock).not.toMatch(/font-family:\s*ui-monospace/)
  })

  it('hides the search box and returns to the tree when the filter button is toggled off', async () => {
    mockApi()
    const wrapper = await mountExplorer()
    await openSearch(wrapper)
    await type(wrapper, 'login')
    expect(wrapper.find('[data-test="explorer-search-results"]').exists()).toBe(true)

    // Toggle the filter button off → box hidden, query dropped, tree back.
    await wrapper.find('[data-test="explorer-search-toggle"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="explorer-search-input"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="explorer-search-results"]').exists()).toBe(false)
    expect(wrapper.findComponent(GroupTreeNodeStub).exists()).toBe(true)
  })
})
