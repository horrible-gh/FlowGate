/**
 * flowgate.v02.0066 T#6 — C14: Center Diff in the real Designer (0066 D#2 §3-10). The real Shell,
 * right Dock, DesignerGitPanel (HTTP Git source), Center column and Workspace; only the HTTP layer
 * (`@shared/api`) is mocked. A changed-file row of the Git tab opens its group diff in the Center
 * over the hidden (still mounted) Flow tabs and Canvas; ✕ brings the Flow back; another Flow closes
 * it; a 0033 conflict file opens nothing and keeps its intent as it was.
 * T#7 — (b): a row of the Base apply `Diff` pane (target comparison list) opens the diff against
 * that target (`base=<target>`, "<target> 기준 ↔ 작업 파일"); changing the target closes it (C23 c).
 * flowgate.v02.0067 T#4 — the same cases with the diff where it is now: the detail of the Git modeless
 * `Diff` tab (0067-R §6). The Designer Center is never hidden or replaced, nothing goes up to the
 * Shell, and `목록으로` goes back to where the file was opened (`파일 관리` or the `Diff` list).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerShell } from '@/features/designer'
import { DesignerGitPanel } from '@/features/git'
import { useProjectStore, type Project } from '@/features/project'
import { answerFlowCanvas } from './flowCanvasResponse'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  putRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
})

afterAll(() => {
  vi.unstubAllGlobals()
  sizeProps.forEach((p, i) => {
    if (originalSize[i]) Object.defineProperty(HTMLElement.prototype, p, originalSize[i]!)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[p]
  })
})

function n(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

const TREE = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
]

// The server's Group worktree status (E1) of a group: one changed file.
const worktreeRaw = (groupId: string) => ({
  group_id: groupId,
  branch: `flow_${groupId}`,
  head: 'h1',
  worktree_available: true,
  worktree_token: 'wt-1',
  index_token: 'ix-1',
  git_state: { status: 'none' },
  floor: { work_base_ref: 'v0.2', work_base_state: 'verified' },
  committed: { commit_count: 0, changed_file_count: 0 },
  files: [
    {
      path: 'src/app.ts',
      in_head: true,
      staged: null,
      unstaged: 'M',
      untracked: false,
      class: 'normal',
      can_stage: true,
      can_unstage: false,
      can_revert: true,
      can_remove: false,
      insertions: 1,
      deletions: 1,
    },
  ],
  counts: { changed: 1, staged: 0, unstaged: 1, untracked: 0, excluded_new_untracked: 0, hidden: 0, managed_dirty: 0, unsupported: 0 },
  ownership: { available: true, reason: null },
  blockers: [],
})

const side = (content: string) => ({ exists: true, binary: false, truncated: false, size: content.length, content })

let pending: unknown[] = []

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  pending = []
  postRequest.mockReset()
  getRequest.mockReset()
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (url === '/api/v1/projects/p/groups/tree') return { data: { data: { nodes: TREE } } }
    if (url === '/api/v1/projects/p/git/status') {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [], pending } } }
    }
    const group = /^\/api\/v1\/projects\/p\/git\/groups\/([^/]+)\/(tree|changes|diff)$/.exec(url)
    if (group?.[2] === 'tree') return { data: { data: { branch: 'flow', commit: 'c0ffee', nodes: [] } } }
    if (group?.[2] === 'changes') {
      // The target comparison list (T#7 `Diff` pane): one file against `release`.
      const changes = params.base === 'release' ? [{ path: 'src/rel.ts', status: 'M' }] : []
      return { data: { data: { group_id: group[1], branch: 'flow', commit: 'c0ffee', base_branch: params.base ?? 'v0.2', changes } } }
    }
    if (group?.[2] === 'diff') {
      return {
        data: {
          data: {
            group_id: decodeURIComponent(group[1]),
            branch: 'flow',
            commit: null,
            base_branch: params.base ?? 'v0.2',
            merge_base: 'f1',
            path: params.path,
            status: 'M',
            old: side('const a = 1\nshared\n'),
            new: side('const a = 2\nshared\n'),
          },
        },
      }
    }
    const wt = /^\/api\/v1\/groups\/([^/]+)\/git\/(worktree\/status|manual-merge|merge-result|merge\/7\/conflicts)$/.exec(url)
    if (wt?.[2] === 'worktree/status') return { data: { ok: true, result: worktreeRaw(decodeURIComponent(wt[1])) } }
    if (wt?.[2] === 'manual-merge') {
      return {
        data: {
          ok: true,
          result: {
            can_merge: false,
            blocked: [],
            default_target: 'v0.2',
            targets: [
              { name: 'v0.2', kind: 'base', eligible: true, is_work_base: true },
              { name: 'release', kind: 'local', eligible: true, is_work_base: false },
            ],
            ahead_of_target: 0,
          },
        },
      }
    }
    if (wt?.[2] === 'merge-result') return { data: { ok: true, result: { results: [], push_candidates: 0 } } }
    if (wt?.[2] === 'merge/7/conflicts') return { data: { ok: true, merge_id: 7, files: [{ path: 'src/conflict.ts', conflict_count: 1 }] } }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    if (url === '/api/v1/ai-invoke/providers') return { data: { ok: true, providers: [{ id: 'opus' }], default_provider_id: 'opus' } }
    const canvas = answerFlowCanvas(url)
    if (canvas) return canvas
    throw new Error(`unexpected request ${url}`)
  })
})

let wrapper: VueWrapper<any> | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

async function settle() {
  for (let i = 0; i < 8; i += 1) await flushPromises()
}

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

async function mountShell() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project('p', 'Alpha'))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  return wrapper
}

function explorerRow(w: VueWrapper<any>, label: string) {
  const found = w.findAll('[data-explorer-row]').find((r) => r.find('[data-test="project-explorer-label"]').text() === label)
  if (!found) throw new Error(`row not rendered: ${label}`)
  return found.find('.explorer-row')
}

async function doubleClick(w: VueWrapper<any>, label: string) {
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    explorerRow(w, label).element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await nextTick()
  }
  await settle()
}

async function openFlow(w: VueWrapper<any>, label: string) {
  if (!w.findAll('[data-explorer-row]').some((r) => r.find('[data-test="project-explorer-label"]').text() === label)) {
    await doubleClick(w, 'UI 개선')
  }
  await doubleClick(w, label)
}

async function showGitTab(w: VueWrapper<any>) {
  const tab = w.findAll('[data-test="designer-right-tool-tabs"] [role="tab"]').find((t) => t.text() === 'Git')
  if (!tab) throw new Error('no Git tab')
  await tab.trigger('click')
  await settle()
}

const find = (test: string) => document.body.querySelector<HTMLElement>(`[data-test="${test}"]`)
const diffCalls = () => getRequest.mock.calls.filter(([url]) => /\/git\/groups\/[^/]+\/diff$/.test(String(url)))
const hidden = (el: HTMLElement | null) => !!el && el.style.display === 'none'

// flowgate.v02.0067 T#1 — the Git tab entries open the Git modeless on their tab; its top tabs switch.
async function openGitWindow(tab: string) {
  const selector = `[data-test="git-worktree-entry"][data-tab="${tab}"], [data-test="git-window-tab"][data-tab="${tab}"]`
  document.body.querySelector<HTMLElement>(selector)!.click()
  await settle()
}

async function openFileDiff(w: VueWrapper<any>) {
  await showGitTab(w)
  await openGitWindow('files')
  find('git-row-open')!.click()
  await settle()
}

async function chooseTarget(name: string) {
  await openGitWindow('merge')
  const select = find('git-apply-target') as HTMLSelectElement
  select.value = name
  select.dispatchEvent(new Event('change'))
  await settle()
}

// flowgate.v02.0067 T#4 — the diff is the Git modeless `Diff` tab detail; the Center stays the Flow's.
const inGitWindow = (el: HTMLElement | null) => !!el && !!find('git-window')?.contains(el)
const centerUntouched = (canvas: HTMLElement | null) => {
  expect(hidden(find('designer-center'))).toBe(false)
  expect(find('designer-canvas')).toBe(canvas)
  expect(find('designer-workspace')!.querySelector('[data-test="git-center-diff"]')).toBeNull()
}

describe('Designer Shell x Git tab — the diff in the Git modeless `Diff` tab (C14, 0067 T#4)', () => {
  it('(a) a changed-file row opens its group diff in the `Diff` tab; the Flow tabs and Canvas stay on screen', async () => {
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    const canvas = find('designer-canvas')
    expect(canvas).not.toBeNull()
    await openFileDiff(w)
    expect(diffCalls()).toEqual([['/api/v1/projects/p/git/groups/p.ui.0001/diff', { path: 'src/app.ts' }]])
    const view = find('git-center-diff')
    expect(inGitWindow(view)).toBe(true)
    expect(find('git-window')!.dataset.tab).toBe('diff')
    expect(find('git-center-diff-path')!.textContent).toBe('src/app.ts')
    expect(find('git-center-diff-compare')!.textContent).toBe('작업 기준(v0.2) ↔ 작업 파일')
    const kinds = [...document.body.querySelectorAll<HTMLElement>('.gw-diff-line')].map((l) => l.dataset.kind)
    expect(kinds).toEqual(['del', 'add', 'context'])
    centerUntouched(canvas)
    // Nothing goes up to the Shell any more.
    expect(w.findComponent(DesignerGitPanel).emitted('open-diff')).toBeUndefined()
    expect(w.findComponent(DesignerGitPanel).emitted('git-center-diff-refresh')).toBeUndefined()
    // `목록으로` goes back to `파일 관리`, where the file was opened.
    find('git-center-diff-back')!.click()
    await settle()
    expect(find('git-center-diff')).toBeNull()
    expect(find('git-window')!.dataset.tab).toBe('files')
    expect(find('git-row-open')).not.toBeNull()
  })

  it('(b) a `Diff` list row opens the diff against that target; `목록으로` is the list; a target change closes it', async () => {
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    const canvas = find('designer-canvas')
    await showGitTab(w)
    await chooseTarget('release')
    await openGitWindow('diff')
    const changesCalls = getRequest.mock.calls.filter(([url]) => /\/git\/groups\/[^/]+\/changes$/.test(String(url)))
    expect(changesCalls.at(-1)).toEqual(['/api/v1/projects/p/git/groups/p.ui.0001/changes', { base: 'release' }])
    find('git-apply-diff-row')!.click()
    await settle()
    expect(diffCalls()).toEqual([['/api/v1/projects/p/git/groups/p.ui.0001/diff', { path: 'src/rel.ts', base: 'release' }]])
    expect(find('git-center-diff-compare')!.textContent).toBe('release 기준 ↔ 작업 파일')
    expect(inGitWindow(find('git-center-diff'))).toBe(true)
    centerUntouched(canvas)
    find('git-center-diff-back')!.click()
    await settle()
    expect(find('git-window')!.dataset.tab).toBe('diff')
    expect(find('git-apply-diff-row')!.dataset.path).toBe('src/rel.ts')
    // C23(c): open it again, then back to the default target — the comparison diff closes.
    find('git-apply-diff-row')!.click()
    await settle()
    await chooseTarget('v0.2')
    await openGitWindow('diff')
    expect(find('git-center-diff')).toBeNull()
    expect(find('git-apply-diff-empty')).not.toBeNull()
  })

  it('(c) a worktree file diff stays open when the merge target changes', async () => {
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    await openFileDiff(w)
    await chooseTarget('release')
    await openGitWindow('diff')
    expect(find('git-center-diff')).not.toBeNull()
    expect(find('git-center-diff-compare')!.textContent).toBe('작업 기준(v0.2) ↔ 작업 파일')
  })

  it('(c) closing the Git modeless closes the diff; opened again, `Diff` shows its list first', async () => {
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    await openFileDiff(w)
    const win = find('git-window')!.closest<HTMLElement>('.ui-modeless-panel')!
    win.querySelector<HTMLElement>('button[data-modeless-action="close"]')!.click()
    await settle()
    expect(find('git-window')).toBeNull()
    await openGitWindow('diff')
    expect(find('git-center-diff')).toBeNull()
    expect(find('git-apply-info')).not.toBeNull()
  })

  it('(d) another Flow (another group) closes the window and the diff', async () => {
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    await openFileDiff(w)
    expect(find('git-center-diff')).not.toBeNull()
    await openFlow(w, 'Canvas')
    expect(find('git-center-diff')).toBeNull()
    expect(find('git-window')).toBeNull()
    expect(hidden(find('designer-center'))).toBe(false)
    expect(find('designer-canvas')?.getAttribute('data-flow-id')).toBe('p.ui.0002')
  })

  it('(e) a 0033 conflict file opens no diff; its intent is the 0033 value, unchanged', async () => {
    pending = [{ group_id: 'p.ui.0001', status: 'conflict', merge_id: 7, ac_doc_id: null, review_state: null, final_approval_bound: false }]
    const w = await mountShell()
    await openFlow(w, 'Designer v0.2')
    await showGitTab(w)
    const file = document.body.querySelector<HTMLElement>('[data-test="git-conflict-card"] .gx-file')
    expect(file).not.toBeNull()
    file!.click()
    await settle()
    expect(w.findComponent(DesignerGitPanel).emitted('open-diff')).toEqual([
      [{ projectId: 'p', groupId: 'p.ui.0001', mergeId: null, path: 'src/conflict.ts', status: 'C', origin: 'conflict', diffSource: 'base' }],
    ])
    expect(find('git-center-diff')).toBeNull()
    expect(diffCalls()).toEqual([])
    expect(hidden(find('designer-center'))).toBe(false)
  })
})
