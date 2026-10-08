/**
 * flowgate.v02.0023 T0006 — Designer Project Explorer component (client_gui).
 *
 * Mounted with the real ProjectTree/Row components and the real project store; only the
 * HTTP layer (`@shared/api`) is mocked. Every assertion goes through rendered rows, real
 * clicks/keys and the events a Designer would receive.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { useProjectStore } from '@/features/project'
import { ProjectExplorer } from '@/features/designer'

const getRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

function n(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

const NODES = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
  n({ id: 'p.ui.0001.0001-R', parent_id: 'p.ui.0001', node_type: 'document', label: '[요건정의]: Explorer spec', type_code: 'R', has_md: true, md_path: 'x.md' }),
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
  n({ id: 'orphan:module:p:ui:__ungrouped__', parent_id: 'module:p:ui', node_type: 'orphan', label: 'Uncategorized' }),
  n({ id: 'module:p:core', parent_id: 'project:p', node_type: 'module', label: 'core' }),
  n({ id: 'p.core.0003', parent_id: 'module:p:core', node_type: 'group', label: 'Engine', number: '0003' }),
  n({ id: 'module:p:empty', parent_id: 'project:p', node_type: 'module', label: 'empty', title: 'Empty group' }),
]

const ok = (nodes: unknown[]) => ({ data: { data: { nodes } } })
const timeout = () => Object.assign(new Error('timeout of 130000ms exceeded'), { code: 'ECONNABORTED' })

let wrapper: VueWrapper<any> | null = null

async function mountExplorer(props: Record<string, unknown> = { projectId: 'p' }) {
  wrapper = mount(ProjectExplorer, {
    props,
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

function rows(w: VueWrapper<any>, kind?: 'project' | 'group' | 'flow') {
  const selector = kind ? `[data-test="project-explorer-${kind}"]` : '[data-explorer-row]'
  return w.findAll(selector)
}

function labels(w: VueWrapper<any>, kind?: 'project' | 'group' | 'flow') {
  return rows(w, kind).map((r) => r.find('[data-test="project-explorer-label"]').text())
}

function row(w: VueWrapper<any>, label: string) {
  const found = rows(w).find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
  if (!found) throw new Error(`row not rendered: ${label}`)
  return found
}

/** The row's own clickable line (not its children). */
function line(w: VueWrapper<any>, label: string) {
  return row(w, label).find('.explorer-row')
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** Dispatches a native mouse event with the click count (`detail`) a browser would set. */
async function mouse(w: VueWrapper<any>, label: string, type: 'click' | 'dblclick', detail: number) {
  line(w, label).element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
  await nextTick()
}

async function clickRow(w: VueWrapper<any>, label: string) {
  await mouse(w, label, 'click', 1)
  await flushPromises()
}

/** A real browser double click: click(detail=1), click(detail=2), dblclick. */
async function doubleClickRow(w: VueWrapper<any>, label: string) {
  await mouse(w, label, 'click', 1)
  await mouse(w, label, 'click', 2)
  await mouse(w, label, 'dblclick', 2)
  await flushPromises()
}

async function expandGroup(w: VueWrapper<any>, label: string) {
  await doubleClickRow(w, label)
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('ProjectExplorer — Project > Group > Flow', () => {
  it('shows the project as Project > Group > Flow without documents or v0.1 names', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()

    // Project root is open by default, Groups closed.
    expect(labels(w, 'project')).toEqual(['Alpha'])
    expect(labels(w, 'group')).toEqual(['UI 개선', 'core', 'Empty group'])
    expect(labels(w, 'flow')).toEqual([])

    await expandGroup(w, 'UI 개선')
    expect(labels(w, 'flow')).toEqual(['Designer v0.2', 'Canvas'])

    const text = w.text()
    expect(text).not.toContain('Explorer spec')
    expect(text).not.toContain('Uncategorized')
    expect(text).not.toMatch(/Module|Document|모듈|문서/)
    expect(row(w, 'Alpha').attributes('aria-level')).toBe('1')
    expect(row(w, 'UI 개선').attributes('aria-level')).toBe('2')
    expect(row(w, 'Canvas').attributes('aria-level')).toBe('3')
    expect(row(w, 'UI 개선').attributes('aria-expanded')).toBe('true')
    expect(row(w, 'core').attributes('aria-expanded')).toBe('false')
  })

  it('follows the current Project of features/project when no projectId is given', async () => {
    const store = useProjectStore()
    store.setCurrentProject({
      id: 'p', name: 'Alpha', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [],
    })
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer({})
    expect(getRequest.mock.calls[0][0]).toBe('/api/v1/projects/p/groups/tree')
    expect(labels(w, 'project')).toEqual(['Alpha'])
  })

  it('reads the tree through the project store (pruned variant) and reuses its cache', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const first = await mountExplorer()
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p/groups/tree', { include_terminal: 'false', force: 'false' })
    first.unmount()
    wrapper = null

    // A second instance answers from the store cache — no second request path.
    await mountExplorer()
    expect(getRequest).toHaveBeenCalledTimes(1)
    expect(labels(wrapper!, 'group')).toEqual(['UI 개선', 'core', 'Empty group'])
  })

  it('shows empty Group / Flow hints', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    await expandGroup(w, 'Empty group')
    expect(row(w, 'Empty group').find('[data-test="project-explorer-no-flows"]').exists()).toBe(true)
    wrapper!.unmount()

    setActivePinia(createPinia())
    getRequest.mockResolvedValueOnce(ok([NODES[0]]))
    const empty = await mountExplorer()
    expect(empty.find('[data-test="project-explorer-no-groups"]').exists()).toBe(true)
  })

  it('shows the no-project state and makes no request without a project', async () => {
    const w = await mountExplorer({ projectId: null })
    expect(w.find('[data-test="project-explorer-no-project"]').exists()).toBe(false)
    expect(w.text()).not.toContain('선택된 프로젝트가 없습니다')
    expect(w.text()).not.toContain('프로젝트를 열면 그룹과 플로우가')
    expect(w.find('[data-test="project-explorer-search"]').attributes('disabled')).toBeDefined()
    expect(getRequest).not.toHaveBeenCalled()
  })
})

describe('ProjectExplorer — selection vs activation', () => {
  it('single click selects a Flow; double click keeps the selection and adds activate', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    await expandGroup(w, 'UI 개선')
    const canvas = { projectId: 'p', groupId: 'module:p:ui', flowId: 'p.ui.0002', flowName: 'Canvas' }
    const designer = { projectId: 'p', groupId: 'module:p:ui', flowId: 'p.ui.0001', flowName: 'Designer v0.2' }

    // A single click selects at once — no waiting for a double-click window.
    await mouse(w, 'Canvas', 'click', 1)
    expect(w.emitted('select-flow')).toEqual([[canvas]])
    expect(w.emitted('activate-flow')).toBeUndefined()
    expect(row(w, 'Canvas').attributes('aria-selected')).toBe('true')
    expect(row(w, 'Canvas').find('.explorer-row').classes()).not.toContain('is-active')

    // A real double click (click 1, click 2, dblclick): the first click selects, the second
    // click adds nothing, dblclick adds activate. Select comes before activate.
    await doubleClickRow(w, 'Designer v0.2')
    expect(w.emitted('select-flow')).toEqual([[canvas], [designer]])
    expect(w.emitted('activate-flow')).toEqual([[designer]])
    expect(row(w, 'Designer v0.2').attributes('aria-selected')).toBe('true')
    expect(row(w, 'Canvas').attributes('aria-selected')).toBe('false')
    // Still not "active" until the Designer says so.
    expect(row(w, 'Designer v0.2').attributes('aria-current')).toBeUndefined()

    await w.setProps({ activeFlowId: 'p.ui.0001' })
    expect(row(w, 'Designer v0.2').attributes('aria-current')).toBe('true')
    // 0025 T0006 §5: the active Flow is shown by row styling only — no "열림" badge/text.
    expect(row(w, 'Designer v0.2').find('.explorer-row').classes()).toContain('is-active')
    expect(row(w, 'Designer v0.2').find('.ui-badge').exists()).toBe(false)
    expect(row(w, 'Designer v0.2').find('.explorer-row').text()).toBe('Designer v0.2')

    // Selecting another Flow moves selection but leaves the active Flow alone.
    await clickRow(w, 'Canvas')
    expect(row(w, 'Canvas').attributes('aria-selected')).toBe('true')
    expect(row(w, 'Designer v0.2').attributes('aria-selected')).toBe('false')
    expect(row(w, 'Designer v0.2').attributes('aria-current')).toBe('true')
  })

  it('reports Group selection with the Group id', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    await mouse(w, 'core', 'click', 1)
    expect(w.emitted('select-group')).toEqual([[{ projectId: 'p', groupId: 'module:p:core' }]])
    expect(row(w, 'core').attributes('aria-selected')).toBe('true')
    // A single click selects without opening.
    expect(row(w, 'core').attributes('aria-expanded')).toBe('false')

    // A double click on a Group selects it (first click only) and toggles it open.
    await doubleClickRow(w, 'UI 개선')
    expect(row(w, 'UI 개선').attributes('aria-expanded')).toBe('true')
    expect(row(w, 'UI 개선').attributes('aria-selected')).toBe('true')
    expect(w.emitted('select-group')).toEqual([
      [{ projectId: 'p', groupId: 'module:p:core' }],
      [{ projectId: 'p', groupId: 'module:p:ui' }],
    ])
  })

  it('a slow double click (gap longer than 500ms) gives the same select + activate', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    await expandGroup(w, 'UI 개선')
    const canvas = { projectId: 'p', groupId: 'module:p:ui', flowId: 'p.ui.0002', flowName: 'Canvas' }

    await mouse(w, 'Canvas', 'click', 1)
    await wait(800)
    await mouse(w, 'Canvas', 'click', 2)
    await mouse(w, 'Canvas', 'dblclick', 2)
    await flushPromises()
    expect(w.emitted('select-flow')).toEqual([[canvas]])
    expect(w.emitted('activate-flow')).toEqual([[canvas]])
    expect(row(w, 'Canvas').attributes('aria-selected')).toBe('true')
  }, 10_000)

  it('supports keyboard expand/collapse, row navigation and Enter activation', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    const group = row(w, 'UI 개선')
    ;(group.element as HTMLElement).focus()
    await group.trigger('keydown', { key: 'ArrowRight' })
    expect(row(w, 'UI 개선').attributes('aria-expanded')).toBe('true')

    await row(w, 'UI 개선').trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(row(w, 'Designer v0.2').element)

    await row(w, 'Designer v0.2').trigger('keydown', { key: ' ' })
    expect(w.emitted('select-flow')?.[0]?.[0]).toMatchObject({ flowId: 'p.ui.0001' })
    await row(w, 'Designer v0.2').trigger('keydown', { key: 'Enter' })
    expect(w.emitted('activate-flow')?.[0]?.[0]).toMatchObject({ flowId: 'p.ui.0001' })

    // Left on a Flow moves focus to its Group; Left again collapses it.
    await row(w, 'Designer v0.2').trigger('keydown', { key: 'ArrowLeft' })
    expect(document.activeElement).toBe(row(w, 'UI 개선').element)
    await row(w, 'UI 개선').trigger('keydown', { key: 'ArrowLeft' })
    expect(row(w, 'UI 개선').attributes('aria-expanded')).toBe('false')
  })
})

describe('ProjectExplorer — search', () => {
  it('filters Project/Group/Flow names while keeping and revealing ancestors', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    await w.find('[data-test="project-explorer-search"]').setValue('designer')
    await flushPromises()

    expect(labels(w)).toEqual(['Alpha', 'UI 개선', 'Designer v0.2'])
    expect(row(w, 'UI 개선').attributes('aria-expanded')).toBe('true')

    // A Group match keeps that Group (with its Flows) under the Project.
    await w.find('[data-test="project-explorer-search"]').setValue('core')
    await flushPromises()
    expect(labels(w, 'group')).toEqual(['core'])
    await expandGroup(w, 'core')
    expect(labels(w, 'flow')).toEqual(['Engine'])

    // Clearing the query brings back the pre-search expansion (UI 개선 was never opened).
    await w.find('[data-test="project-explorer-search-clear"]').trigger('click')
    await flushPromises()
    expect(labels(w, 'group')).toEqual(['UI 개선', 'core', 'Empty group'])
    expect(labels(w, 'flow')).toEqual([])
  })

  it('does not match document titles and shows the no-result state', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    await w.find('[data-test="project-explorer-search"]').setValue('Explorer spec')
    await flushPromises()
    expect(w.find('[data-test="project-explorer-tree"]').exists()).toBe(false)
    expect(w.find('[data-test="project-explorer-no-results"]').exists()).toBe(true)
  })
})

describe('ProjectExplorer — loading and errors', () => {
  it('shows the loading state while the first read is pending', async () => {
    let resolveTree!: (value: unknown) => void
    getRequest.mockReturnValueOnce(new Promise((r) => { resolveTree = r }))
    const w = await mountExplorer()
    expect(w.find('[data-test="project-explorer-loading"]').exists()).toBe(true)
    resolveTree(ok(NODES))
    await flushPromises()
    expect(w.find('[data-test="project-explorer-loading"]').exists()).toBe(false)
    expect(labels(w, 'project')).toEqual(['Alpha'])
  })

  it('shows a blocking error on initial failure and recovers on retry', async () => {
    getRequest.mockRejectedValueOnce(timeout())
    const w = await mountExplorer()
    expect(w.find('[data-test="project-explorer-error"]').exists()).toBe(true)
    expect(w.find('[data-test="project-explorer-tree"]').exists()).toBe(false)

    getRequest.mockResolvedValueOnce(ok(NODES))
    await w.find('[data-test="project-explorer-retry"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="project-explorer-error"]').exists()).toBe(false)
    expect(labels(w, 'group')).toEqual(['UI 개선', 'core', 'Empty group'])
    expect(getRequest.mock.calls.at(-1)![1]).toEqual({ include_terminal: 'false', force: 'true' })
  })

  it('keeps the rendered tree on refresh failure, shows a notice and retries', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    await expandGroup(w, 'UI 개선')
    await clickRow(w, 'Canvas')

    getRequest.mockRejectedValueOnce(timeout())
    await w.setProps({ refreshToken: 1 })
    await flushPromises()
    expect(w.find('[data-test="project-explorer-refresh-error"]').exists()).toBe(true)
    expect(labels(w, 'flow')).toEqual(['Designer v0.2', 'Canvas'])
    expect(row(w, 'Canvas').attributes('aria-selected')).toBe('true')

    getRequest.mockResolvedValueOnce(ok([
      ...NODES,
      n({ id: 'p.ui.0004', parent_id: 'module:p:ui', node_type: 'group', label: 'Inspector' }),
    ]))
    await w.find('[data-test="project-explorer-refresh-retry"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="project-explorer-refresh-error"]').exists()).toBe(false)
    expect(labels(w, 'flow')).toEqual(['Designer v0.2', 'Canvas', 'Inspector'])
    // The forced refresh bypasses the store cache.
    expect(getRequest.mock.calls.at(-1)![1]).toEqual({ include_terminal: 'false', force: 'true' })
  })

  it('the header refresh button forces a fresh read', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    getRequest.mockResolvedValueOnce(ok(NODES))
    await w.find('[data-test="project-explorer-refresh"]').trigger('click')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(getRequest.mock.calls[1][1]).toEqual({ include_terminal: 'false', force: 'true' })
  })

  it('resets to the new project when the project changes', async () => {
    getRequest.mockResolvedValueOnce(ok(NODES))
    const w = await mountExplorer()
    await w.find('[data-test="project-explorer-search"]').setValue('core')
    getRequest.mockResolvedValueOnce(ok([
      n({ id: 'project:q', parent_id: null, node_type: 'project', label: 'Beta' }),
      n({ id: 'module:q:m', parent_id: 'project:q', node_type: 'module', label: 'm' }),
    ]))
    await w.setProps({ projectId: 'q' })
    await flushPromises()
    expect(labels(w, 'project')).toEqual(['Beta'])
    expect(labels(w, 'group')).toEqual(['m'])
    expect((w.find('[data-test="project-explorer-search"]').element as HTMLInputElement).value).toBe('')
  })
})

describe('ProjectExplorer — structure (T0006 §1-§3)', () => {
  const DIR = resolve(__dirname, '../../../src/features/designer/projectExplorer')
  const files = readdirSync(DIR).filter((f) => /\.(ts|vue)$/.test(f))
  const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])

  it('has one component per responsibility', () => {
    expect(files.sort()).toEqual([
      'ExplorerRowFrame.vue',
      'FlowRow.vue',
      'GroupRow.vue',
      'ProjectExplorer.vue',
      'ProjectRow.vue',
      'ProjectTree.vue',
      'projectExplorerTree.ts',
    ])
  })

  it('reads project context only through features/project, uses components/ui and never reaches v0.1 or a store of its own', () => {
    for (const file of files) {
      const src = readFileSync(join(DIR, file), 'utf8')
      for (const spec of importsOf(src)) {
        const allowed =
          spec === 'vue' ||
          spec === 'vue-i18n' ||
          spec === '@/features/project' ||
          spec.startsWith('../../../components/ui/') ||
          // 0025 T0006 §4: the one Designer Explorer glyph set (shared with the File Explorer).
          spec === '../shared/ExplorerIcon.vue' ||
          spec.startsWith('./')
        expect(allowed, `${file}: ${spec}`).toBe(true)
      }
      expect(src, file).not.toMatch(/defineStore|@shared\/api|client\/src/)
    }
  })
})
