// flowgate.default.0326 R0001 — the diff tab's data path: which endpoint it calls for
// each explorer mode, and that the two returned versions actually render as a diff.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (k: string, params?: Record<string, unknown>) => (params ? `${k}:${JSON.stringify(params)}` : k) }),
}))

const { mockGet } = vi.hoisted(() => ({ mockGet: vi.fn() }))
vi.mock('@shared/api', () => ({ default: { get: mockGet } }))

import FileDiffViewer from '@main/components/FileDiffViewer.vue'

function diffResponse(oldContent: string | null, newContent: string | null, status = 'M') {
  const side = (content: string | null) => ({
    exists: content !== null,
    binary: false,
    truncated: false,
    size: content?.length ?? 0,
    content,
  })
  return { data: { ok: true, data: { status, old: side(oldContent), new: side(newContent) } } }
}

async function mountViewer(props: Record<string, unknown>) {
  const wrapper = mount(FileDiffViewer, {
    props: { path: 'src/a.ts', projectId: 'p1', ...props },
  })
  await flushPromises()
  return wrapper
}

describe('FileDiffViewer', () => {
  beforeEach(() => {
    mockGet.mockReset()
  })

  it('reads the base-checkout endpoint when no group is selected', async () => {
    mockGet.mockResolvedValue(diffResponse('a\nb\n', 'a\nB\n'))
    await mountViewer({})

    expect(mockGet).toHaveBeenCalledWith(
      '/api/v1/projects/p1/git/diff',
      { params: { path: 'src/a.ts' } },
    )
  })

  it('reads the group endpoint pinned to the tree commit in group-branch mode', async () => {
    mockGet.mockResolvedValue(diffResponse('a\n', 'b\n'))
    await mountViewer({ gitGroupId: 'flowgate.default.0326', gitCommit: 'c'.repeat(40) })

    expect(mockGet).toHaveBeenCalledWith(
      '/api/v1/projects/p1/git/groups/flowgate.default.0326/diff',
      { params: { path: 'src/a.ts', ref: 'c'.repeat(40) } },
    )
  })

  it('renders the changed line on both sides with the old/new line numbers', async () => {
    mockGet.mockResolvedValue(diffResponse('keep\nold\n', 'keep\nnew\n'))
    const wrapper = await mountViewer({})

    const text = wrapper.text()
    expect(text).toContain('old')
    expect(text).toContain('new')
    // +1 / -1 for the single changed line (a change counts on both sides).
    expect(text).toContain('+1')
    expect(text).toContain('-1')
    expect(wrapper.findAll('.file-diff__row')).toHaveLength(2)
  })

  it('says so instead of rendering an empty grid when nothing differs', async () => {
    mockGet.mockResolvedValue(diffResponse('same\n', 'same\n'))
    const wrapper = await mountViewer({})

    expect(wrapper.text()).toContain('main.file_diff.no_changes')
    expect(wrapper.findAll('.file-diff__row')).toHaveLength(0)
  })

  it('refuses to fake a diff for a binary file', async () => {
    mockGet.mockResolvedValue({
      data: { ok: true, data: {
        status: 'M',
        old: { exists: true, binary: true, truncated: false, size: 9, content: null },
        new: { exists: true, binary: true, truncated: false, size: 9, content: null },
      } },
    })
    const wrapper = await mountViewer({})

    expect(wrapper.text()).toContain('main.file_diff.binary')
  })

  it('surfaces a load failure with a retry instead of an empty view', async () => {
    mockGet.mockRejectedValue(new Error('boom'))
    const wrapper = await mountViewer({})

    expect(wrapper.text()).toContain('main.file_diff.load_failed')
    mockGet.mockResolvedValue(diffResponse('a\n', 'b\n'))
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('main.file_diff.load_failed')
  })

  it('switches to unified rows without refetching', async () => {
    mockGet.mockResolvedValue(diffResponse('keep\nold\n', 'keep\nnew\n'))
    const wrapper = await mountViewer({})

    const unifiedToggle = wrapper
      .findAll('button')
      .find((button) => button.text() === 'main.file_diff.view_unified')
    await unifiedToggle?.trigger('click')

    // The changed row becomes two patch lines (- old, + new).
    expect(wrapper.findAll('.file-diff__grid--unified .file-diff__row')).toHaveLength(3)
    expect(mockGet).toHaveBeenCalledTimes(1)
  })
})
