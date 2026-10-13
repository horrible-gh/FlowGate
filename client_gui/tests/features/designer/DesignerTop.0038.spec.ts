/**
 * flowgate.v02.0038 — Designer top band (0001-R rev2 / 0007-T): Header, Menu Bar and Toolbar
 * stacked in the Shell's Top container.
 *
 * The Shell is mounted with its real parts and the real project store; only the HTTP layer
 * (`@shared/api`) is mocked, as in DesignerShell.0025. Assertions go through the DOM the user
 * sees. The parts' own events are checked by mounting each part alone.
 *
 * flowgate.v02.0053 T#9: the run buttons drive the server Graph run; the HTTP mock answers the run
 * requests from an in-memory run server (flowRunServer.ts).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import { DesignerShell } from '@/features/designer'
import DesignerHeader from '@/features/designer/top/DesignerHeader.vue'
import DesignerMenuBar from '@/features/designer/top/DesignerMenuBar.vue'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import {
  ALL_MENU_TARGETS,
  createDesignerTopState,
  DESIGNER_ARRANGE_GROUPS,
  DESIGNER_MENUS,
  isMenuCommandEnabled,
  nextRunMode,
  zoomPercent,
  type DesignerWindowState,
} from '@/features/designer/top/designerTop'
import { useProjectStore, type Project } from '@/features/project'
import { createFlowRunServer, type FlowRunServer } from './flowRunServer'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

const TREE = [
  { id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha', type_code: null, number: null, filename: null, has_md: false, md_path: null },
  { id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선', type_code: null, number: null, filename: null, has_md: false, md_path: null },
  { id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', type_code: null, number: '0001', filename: null, has_md: false, md_path: null },
]

// --- Canvas environment for Vue Flow under jsdom (same stand-ins as DesignerShell.0025) -----
// flowgate.v02.0042: the work state needs an open Flow, so the Canvas renders in these suites.
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
// -------------------------------------------------------------------------------------------

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

beforeEach(() => {
  localStorage.clear()
  i18n.global.locale.value = 'ko'
  setCurrentUser({ username: 'test', email: null, isAdmin: false })
  getRequest.mockReset()
  runServer = createFlowRunServer()
  postRequest.mockReset()
  postRequest.mockImplementation(async (url: string) => {
    const answer = runServer.post(url)
    if (answer) return answer
    throw new Error(`unexpected request ${url}`)
  })
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    const run = runServer.get(url)
    if (run) return run
    if (/^\/api\/v1\/projects\/[^/]+\/groups\/tree$/.test(url)) return { data: { data: { nodes: TREE } } }
    if (/^\/api\/v1\/projects\/[^/]+\/git\/status$/.test(url)) {
      return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
    }
    if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
    throw new Error(`unexpected request ${url}`)
  })
})

let wrapper: VueWrapper<any> | null = null
let runServer: FlowRunServer = createFlowRunServer()

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setCurrentUser(null)
  i18n.global.locale.value = 'ko'
})

async function settle() {
  for (let i = 0; i < 6; i += 1) await flushPromises()
}

/** A real browser double click on the Project Explorer row with this label. */
async function doubleClickRow(w: VueWrapper<any>, label: string) {
  const row = w
    .findAll('[data-explorer-row]')
    .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)!
    .find('.explorer-row')
  for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
    row.element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
    await nextTick()
  }
  await settle()
}

// flowgate.v02.0039 T#3: entering leaves the Designer without a work target (Toolbar and most
// menu items unavailable — DesignerInitialState.0039.spec.ts). These suites check the band in
// the work state, so the user first opens a Flow (flowgate.v02.0042: only an open Flow is the
// work target; a plain click only selects).
async function mountShell() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useProjectStore().setCurrentProject(project('p', 'Alpha'))
  wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
  await settle()
  await doubleClickRow(wrapper, 'UI 개선')
  await doubleClickRow(wrapper, 'Canvas')
  return wrapper
}

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const menuTitle = (w: VueWrapper<any>, key: string) => w.get(`[data-test="designer-menu"][data-menu="${key}"]`)
const openPop = (w: VueWrapper<any>) => w.find('[data-test="designer-menu-pop"]')
const itemTexts = (el: VueWrapper<any> | ReturnType<VueWrapper<any>['get']>) =>
  el.findAll('.designer-menu-item').map((item) => item.text().replace(/^[✓●]/, '').trim())
const toolTexts = (w: VueWrapper<any>) => get(w, 'designer-toolbar').findAll('button.designer-tool').map((b) => b.text())
const runButton = (w: VueWrapper<any>, mode: string) => w.get(`[data-test="designer-tool-run"][data-run="${mode}"]`)

async function openMenu(w: VueWrapper<any>, key: string) {
  if (openPop(w).exists() && openPop(w).attributes('data-menu') === key) return openPop(w)
  await menuTitle(w, key).trigger('click')
  return w.get(`[data-test="designer-menu-pop"][data-menu="${key}"]`)
}

async function clickOutside() {
  document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
  await flushPromises()
}

describe('Designer top band — structure', () => {
  it('stacks Header, Menu Bar and Toolbar inside the Top container, above the Body', async () => {
    const w = await mountShell()
    const shell = get(w, 'designer-shell').element
    expect([...shell.children].map((c) => c.getAttribute('data-test'))).toEqual(['designer-top', 'designer-body'])
    expect([...get(w, 'designer-top').element.children].map((c) => c.getAttribute('data-test'))).toEqual([
      'designer-header',
      'designer-menubar',
      'designer-toolbar',
    ])
    expect(get(w, 'designer-top').find('[data-test^="designer-placeholder-"]').exists()).toBe(false)
  })

  it('shows the Header entries: brand, notification, AI, search, language, settings and user', async () => {
    const w = await mountShell()
    const header = get(w, 'designer-header')
    expect(header.get('[data-test="designer-header-brand"]').text()).toContain('FlowGate Designer')
    expect(header.get('[data-test="designer-header-notifications"]').attributes('aria-label')).toBe('알림')
    expect(header.get('[data-test="designer-header-ai"]').text()).toContain('AI')
    expect(header.get('[data-test="designer-search-input"]').attributes('placeholder')).toBe('Project 전체에서 검색...')
    expect(header.findAll('[data-test="designer-header-locale"] button').map((b) => b.text())).toEqual(['KO', 'EN', 'JA'])
    expect(header.get('[data-test="designer-header-settings"]').text()).toContain('설정')
    expect(header.get('[data-test="designer-header-user"]').text()).toContain('test')
  })

  it('has the six menus in order and no AI menu', async () => {
    const w = await mountShell()
    expect(w.findAll('[data-test="designer-menu"]').map((m) => m.text())).toEqual(['파일', '편집', '실행', '보기', '창', '도움말'])
    expect(get(w, 'designer-menubar').text()).not.toContain('AI')
  })
})

describe('Designer top band — Menu Bar', () => {
  it('File holds Project / Group / Flow with recent submenus and Designer 닫기, and no Save', async () => {
    const w = await mountShell()
    const pop = await openMenu(w, 'file')
    const top = pop.findAll('.designer-menu-item').filter((el) => el.element.closest('[role="menu"]') === pop.element)
    expect(top.map((el) => el.text().replace('›', '').trim())).toEqual([
      '새 Project', 'Project 열기…', '최근 Project', 'Project 닫기',
      '새 Group', 'Group 열기…', '최근 Group', 'Group 닫기',
      '새 Flow', 'Flow 열기…', '최근 Flow', 'Flow 닫기',
      'Designer 닫기',
    ])
    expect(pop.text()).not.toMatch(/저장/)
    expect(pop.findAll('[role="separator"]')).toHaveLength(3)

    // flowgate.v02.0043: the recent submenus list what was opened — mountShell opened the Flow
    // `Canvas` of the Group `UI 개선` (DesignerFileMenu.0043.spec.ts covers the lists in full).
    await pop.get('[data-test="designer-menu-sub"][data-sub="recent-flow"]').element.parentElement!.dispatchEvent(new MouseEvent('mouseenter'))
    await flushPromises()
    expect(w.findAll('[data-test="designer-menu-subpop"]')).toHaveLength(1)
    const sub = w.get('[data-test="designer-menu-subpop"][data-sub="recent-flow"]')
    expect(sub.findAll('[data-test="designer-menu-recent"]').map((el) => [el.attributes('data-id'), el.text()])).toEqual([['p.ui.0001', 'Canvas']])
    expect(sub.find('[data-test="designer-menu-recent-empty"]').exists()).toBe(false)
  })

  it('Edit, Run, View and Help hold the confirmed items', async () => {
    const w = await mountShell()
    // flowgate.v02.0059 T#2: 정렬 / 배치 is a submenu (›) of the 정렬 / 분배 commands.
    expect(itemTexts(await openMenu(w, 'edit')).map((text) => text.replace('›', '').trim())).toEqual([
      '실행 취소', '다시 실행', '잘라내기', '복사', '붙여넣기', '삭제', '정렬 / 배치', '자동 배치', '전체 선택',
    ])
    expect(itemTexts(await openMenu(w, 'run'))).toEqual(['다음 단계 실행', '전체 실행', '실행 중지', '재실행', 'Flow 검증'])
    expect(itemTexts(await openMenu(w, 'view')).map((text) => text.replace('›', '').trim())).toEqual([
      '화면 맞춤', '확대', '축소', '100%', '배치 밀도',
    ])
    expect(itemTexts(await openMenu(w, 'help'))).toEqual(['단축키', '사용 가이드', 'FlowGate 정보'])
  })

  it('shows no shortcut text in any menu, the search or the Toolbar', async () => {
    const w = await mountShell()
    const shortcut = /Ctrl|Shift|Alt\+|F5|Del\b|⌘/
    for (const menu of DESIGNER_MENUS) {
      const pop = await openMenu(w, menu.key)
      expect(pop.text(), menu.key).not.toMatch(shortcut)
    }
    await get(w, 'designer-search-input').trigger('focus')
    expect(get(w, 'designer-top').text()).not.toMatch(shortcut)
  })

  it('opens on click, switches on hover while open, and closes on a choice, Escape or a press outside', async () => {
    const w = await mountShell()
    await menuTitle(w, 'file').trigger('mouseenter')
    expect(openPop(w).exists()).toBe(false)

    await menuTitle(w, 'file').trigger('click')
    expect(openPop(w).attributes('data-menu')).toBe('file')
    expect(menuTitle(w, 'file').attributes('aria-expanded')).toBe('true')

    await menuTitle(w, 'edit').trigger('mouseenter')
    expect(openPop(w).attributes('data-menu')).toBe('edit')
    expect(menuTitle(w, 'file').attributes('aria-expanded')).toBe('false')

    await openPop(w).get('[data-command="edit.copy"]').trigger('click')
    expect(openPop(w).exists()).toBe(false)

    await menuTitle(w, 'help').trigger('click')
    await openPop(w).get('[data-command="help.guide"]').trigger('keydown', { key: 'Escape' })
    expect(openPop(w).exists()).toBe(false)

    await menuTitle(w, 'view').trigger('click')
    await clickOutside()
    expect(openPop(w).exists()).toBe(false)

    await menuTitle(w, 'run').trigger('click')
    await menuTitle(w, 'run').trigger('click')
    expect(openPop(w).exists()).toBe(false)
  })

  it('moves between menus with the arrow keys', async () => {
    const w = await mountShell()
    await menuTitle(w, 'file').trigger('keydown', { key: 'ArrowDown' })
    await flushPromises()
    expect(openPop(w).attributes('data-menu')).toBe('file')
    expect(document.activeElement?.getAttribute('data-command')).toBe('project.new')

    await openPop(w).get('[data-command="project.new"]').trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement?.getAttribute('data-command')).toBe('project.open')

    await openPop(w).get('[data-command="project.open"]').trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(openPop(w).attributes('data-menu')).toBe('edit')
    // flowgate.v02.0059 T#2: no edit history yet, so Undo / Redo are disabled and the focus lands on
    // the first enabled item of the Edit menu.
    const items = openPop(w).findAll('[data-command]')
    const enabled = items.filter((el) => el.attributes('disabled') === undefined && el.attributes('aria-disabled') !== 'true')
    expect(items.find((el) => el.attributes('data-command') === 'edit.undo')!.attributes('aria-disabled') === 'true'
      || items.find((el) => el.attributes('data-command') === 'edit.undo')!.attributes('disabled') !== undefined).toBe(true)
    expect(document.activeElement).toBe(enabled[0].element)
    expect(document.activeElement?.getAttribute('data-command')).not.toMatch(/^edit\.(undo|redo)$/)
  })
})

describe('Designer top band — Window menu', () => {
  const checked = (pop: ReturnType<VueWrapper<any>['get']>, selector: string) =>
    pop.findAll(selector).filter((el) => el.attributes('aria-checked') === 'true').map((el) => el.text().replace(/^[✓●]/, ''))

  it('separates panel show / hide check items from tab moves with one active tab per panel', async () => {
    const w = await mountShell()
    const pop = await openMenu(w, 'window')
    const panels = pop.findAll('[data-test="designer-menu-panel"]')
    expect(panels.map((el) => el.attributes('role'))).toEqual(Array(4).fill('menuitemcheckbox'))
    expect(checked(pop, '[data-test="designer-menu-panel"]')).toEqual(['좌측 패널', '우측 도구 패널', 'Inspector', '미니맵'])
    expect(panels.every((el) => el.text().startsWith('✓'))).toBe(true)

    const tabs = pop.findAll('[data-test="designer-menu-tab"]')
    expect(tabs.map((el) => el.text().replace(/^●/, ''))).toEqual(['프로젝트 탐색기', '파일 탐색기', '팔레트', '대화', 'Git'])
    expect(tabs.map((el) => el.attributes('role'))).toEqual(Array(5).fill('menuitemradio'))
    expect(checked(pop, '[data-test="designer-menu-tab"]')).toEqual(['프로젝트 탐색기', '팔레트'])
    expect(tabs.filter((el) => el.text().startsWith('●'))).toHaveLength(2)
  })

  it('hides and shows the left and right panels through the existing Shell layout', async () => {
    const w = await mountShell()
    await (await openMenu(w, 'window')).get('[data-panel="left"]').trigger('click')
    expect(get(w, 'designer-left-dock').attributes('data-collapsed')).toBe('true')
    expect(get(w, 'designer-body').attributes('data-left-width')).toBe('40')
    expect((await openMenu(w, 'window')).get('[data-panel="left"]').attributes('aria-checked')).toBe('false')

    await openPop(w).get('[data-panel="left"]').trigger('click')
    expect(get(w, 'designer-left-dock').attributes('data-collapsed')).toBe('false')

    await (await openMenu(w, 'window')).get('[data-panel="right"]').trigger('click')
    expect(get(w, 'designer-right-dock').attributes('data-collapsed')).toBe('true')
    expect(get(w, 'designer-body').attributes('data-right-width')).toBe('40')
  })

  it('a tab move opens a hidden panel and switches to that tab', async () => {
    const w = await mountShell()
    await (await openMenu(w, 'window')).get('[data-panel="left"]').trigger('click')
    await (await openMenu(w, 'window')).get('[data-tab="file"]').trigger('click')
    await settle()
    expect(get(w, 'designer-left-dock').attributes('data-collapsed')).toBe('false')
    expect(get(w, 'designer-left-dock').attributes('data-mode')).toBe('file')

    await (await openMenu(w, 'window')).get('[data-panel="right"]').trigger('click')
    await (await openMenu(w, 'window')).get('[data-tab="git"]').trigger('click')
    await settle()
    expect(get(w, 'designer-right-dock').attributes('data-collapsed')).toBe('false')
    expect(get(w, 'designer-right-dock').attributes('data-tool')).toBe('git')

    const pop = await openMenu(w, 'window')
    expect(pop.findAll('[data-test="designer-menu-tab"][aria-checked="true"]').map((el) => el.attributes('data-tab'))).toEqual(['file', 'git'])
  })
})

describe('Designer top band — Toolbar', () => {
  it('keeps the confirmed tools only: [선택] for multi-selection, no add node / connect, with 정렬 / 배치 ▾', async () => {
    const w = await mountShell()
    // flowgate.v02.0045 TR0020 rev2: 검증 sits right before the run buttons. flowgate.v02.0059 T#1:
    // [선택] (multi-selection toggle) is back next to 정렬 / 배치 ▾.
    expect(toolTexts(w)).toEqual(['실행 취소', '다시 실행', '선택', '정렬 / 배치 ▾', '검증', '다음 단계', '전체 실행', '화면 맞춤', '축소', '확대'])
    expect(get(w, 'designer-toolbar').text()).not.toMatch(/노드 추가|연결/)
    // flowgate.v02.0059 0017-T: the zoom is one control showing the current zoom.
    expect((get(w, 'designer-tool-zoom').element as HTMLSelectElement).selectedOptions[0]?.text).toBe('100%')
    expect(get(w, 'designer-toolbar').findAll('output')).toHaveLength(0)
    expect(get(w, 'designer-tool-density').findAll('[role="radio"]').map((b) => b.attributes('aria-label'))).toEqual([
      '넓게', '보통', '좁게', '자율형',
    ])
  })

  // flowgate.v02.0053 T#9: 전체 실행 starts the server run; the button follows the run read back.
  it('전체 실행 starts the server run: it turns into ■ 중지 in place, disables 다음 단계, and ■ 중지 stops the run', async () => {
    const w = await mountShell()
    expect(w.find('[data-test="designer-run-status"]').exists()).toBe(false)
    await runButton(w, 'all').trigger('click')
    await settle()

    expect(runServer.calls).toEqual(['GET current p.ui.0001', 'POST start p.ui.0001'])
    expect(runButton(w, 'all').text()).toBe('중지')
    expect(runButton(w, 'all').attributes('aria-pressed')).toBe('true')
    expect(runButton(w, 'all').attributes('disabled')).toBeUndefined()
    expect(runButton(w, 'next').attributes('disabled')).toBeDefined()
    expect(w.findAll('[data-test="designer-tool-run"]')).toHaveLength(2)
    expect(get(w, 'designer-run-status').attributes('data-status')).toBe('running')
    expect(get(w, 'designer-run-status').attributes('title')).toBe('실행 중')

    // The Run menu follows the same state.
    const pop = await openMenu(w, 'run')
    expect(pop.get('[data-command="run.stop"]').attributes('disabled')).toBeUndefined()
    expect(pop.get('[data-command="run.next"]').attributes('disabled')).toBeDefined()
    expect(pop.get('[data-command="run.all"]').attributes('disabled')).toBeDefined()
    await clickOutside()

    await runButton(w, 'all').trigger('click')
    await settle()
    expect(runServer.calls.slice(2)).toEqual(['POST stop run_1'])
    expect(runServer.runs.get('p.ui.0001')?.status).toBe('stopped')
    expect(runButton(w, 'all').text()).toBe('전체 실행')
    expect(runButton(w, 'all').attributes('aria-pressed')).toBe('false')
    expect(runButton(w, 'next').attributes('disabled')).toBeUndefined()
    expect(get(w, 'designer-run-status').attributes('data-status')).toBe('stopped')

    // 전체 실행 again resumes the stopped run (never a second run).
    await runButton(w, 'all').trigger('click')
    await settle()
    expect(runServer.calls.slice(3)).toEqual(['GET current p.ui.0001', 'POST resume run_1'])
    expect(runButton(w, 'all').text()).toBe('중지')
  })

  it('다음 단계 runs one step: the run starts (or resumes) and stops at once, so the buttons come back', async () => {
    const w = await mountShell()
    await runButton(w, 'next').trigger('click')
    await settle()
    expect(runServer.calls).toEqual(['GET current p.ui.0001', 'POST start p.ui.0001', 'POST stop run_1'])
    expect(runButton(w, 'next').text()).toBe('다음 단계')
    expect(runButton(w, 'next').attributes('aria-pressed')).toBe('false')
    expect(runButton(w, 'all').attributes('disabled')).toBeUndefined()
    expect(get(w, 'designer-run-status').attributes('data-status')).toBe('stopped')

    await runButton(w, 'next').trigger('click')
    await settle()
    expect(runServer.calls.slice(3)).toEqual(['GET current p.ui.0001', 'POST resume run_1', 'POST stop run_1'])
    expect(runServer.runs.size).toBe(1)
  })

  it('the Run menu starts and stops the same server run', async () => {
    const w = await mountShell()
    expect((await openMenu(w, 'run')).get('[data-command="run.stop"]').attributes('disabled')).toBeDefined()
    await openPop(w).get('[data-command="run.all"]').trigger('click')
    await settle()
    expect(runButton(w, 'all').text()).toBe('중지')

    await (await openMenu(w, 'run')).get('[data-command="run.stop"]').trigger('click')
    await settle()
    expect(runButton(w, 'all').text()).toBe('전체 실행')
    expect(runServer.runs.get('p.ui.0001')?.status).toBe('stopped')
  })

  // The button waiting for the start answer shows ■ 중지 and is enabled: its click is kept, not dropped.
  it('■ 중지 clicked while the start is answered stops the run as soon as the start names it', async () => {
    const w = await mountShell()
    let release!: () => void
    const held = new Promise<void>((resolve) => (release = resolve))
    postRequest.mockImplementationOnce(async (url: string) => {
      await held
      return runServer.post(url)
    })
    await runButton(w, 'all').trigger('click')
    await settle()
    expect(runButton(w, 'all').text()).toBe('중지')
    expect(runButton(w, 'all').attributes('disabled')).toBeUndefined()

    await runButton(w, 'all').trigger('click')
    await settle()
    expect(runServer.calls).toEqual(['GET current p.ui.0001'])
    release()
    await settle()
    expect(runServer.calls).toEqual(['GET current p.ui.0001', 'POST start p.ui.0001', 'POST stop run_1'])
    expect(runServer.runs.get('p.ui.0001')?.status).toBe('stopped')
    expect(runButton(w, 'all').text()).toBe('전체 실행')
    expect(runButton(w, 'next').attributes('disabled')).toBeUndefined()
    expect(get(w, 'designer-run-status').attributes('data-status')).toBe('stopped')
  })

  it('a refused run shows one short toast in plain words and leaves the buttons as they were', async () => {
    const w = await mountShell()
    runServer.refuse('run_policy_undecided')
    await runButton(w, 'all').trigger('click')
    await settle()

    expect(runButton(w, 'all').text()).toBe('전체 실행')
    expect(runButton(w, 'all').attributes('aria-pressed')).toBe('false')
    expect(w.find('[data-test="designer-run-status"]').exists()).toBe(false)
    const toasts = [...document.body.querySelectorAll('.ui-toast-host')].map((host) => host.textContent?.trim() ?? '')
    expect(toasts.join(' ')).toContain('실행 규칙이 아직 정해지지 않아 실행할 수 없습니다.')
    expect(toasts.join(' ')).not.toMatch(/run_policy_undecided|undecided/)
  })

  it('the Toolbar density and the View menu density are one value', async () => {
    const w = await mountShell()
    const on = () => get(w, 'designer-tool-density').get('[aria-checked="true"]').attributes('data-density')
    expect(on()).toBe('normal')

    await get(w, 'designer-tool-density').get('[data-density="wide"]').trigger('click')
    expect(on()).toBe('wide')

    const pop = await openMenu(w, 'view')
    await pop.get('[data-test="designer-menu-sub"][data-sub="density"]').trigger('click')
    const sub = w.get('[data-test="designer-menu-subpop"][data-sub="density"]')
    expect(sub.get('[aria-checked="true"]').attributes('data-density')).toBe('wide')
    expect(itemTexts(sub)).toEqual(['넓게', '보통', '좁게', '자율형'])

    await sub.get('[data-density="free"]').trigger('click')
    expect(openPop(w).exists()).toBe(false)
    expect(on()).toBe('free')
  })
})

describe('Designer top band — search assist', () => {
  it('shows scopes and examples on focus and keeps the chosen scope after the panel closes', async () => {
    const w = await mountShell()
    expect(w.find('[data-test="designer-search-assist"]').exists()).toBe(false)

    await get(w, 'designer-search-input').trigger('focus')
    const assist = get(w, 'designer-search-assist')
    expect(assist.findAll('[data-test="designer-search-scope"]').map((b) => b.text())).toEqual(['전체', 'Flow', 'Node', '파일', '문서'])
    expect(assist.get('[aria-checked="true"]').text()).toBe('전체')
    expect(assist.findAll('[data-test="designer-search-example"]').map((b) => b.text())).toEqual([
      'Designer', '문서 ID · 0675', '파일 · workflow.py', 'Flow · 인증 오류 조사',
    ])

    await assist.get('[data-scope="node"]').trigger('click')
    expect(get(w, 'designer-search-scope-chip').text()).toContain('Node')
    expect(get(w, 'designer-search-input').attributes('placeholder')).toBe('Node 이름 또는 ID로 검색...')

    await clickOutside()
    expect(w.find('[data-test="designer-search-assist"]').exists()).toBe(false)
    expect(get(w, 'designer-search-scope-chip').text()).toContain('Node')
    expect(get(w, 'designer-search-input').attributes('placeholder')).toBe('Node 이름 또는 ID로 검색...')

    // The chip reopens the panel with the kept scope selected.
    await get(w, 'designer-search-scope-chip').trigger('click')
    expect(get(w, 'designer-search-assist').get('[aria-checked="true"]').attributes('data-scope')).toBe('node')
  })

  it('an example fills the input; Escape closes the panel', async () => {
    const w = await mountShell()
    await get(w, 'designer-search-input').trigger('focus')
    await get(w, 'designer-search-assist').get('[data-example="file_name"]').trigger('click')
    expect((get(w, 'designer-search-input').element as HTMLInputElement).value).toBe('workflow.py')

    await get(w, 'designer-search-input').trigger('keydown', { key: 'Escape' })
    expect(w.find('[data-test="designer-search-assist"]').exists()).toBe(false)
  })
})

describe('Designer top band — language', () => {
  it('switches the shared locale and remembers it the way the existing switches do', async () => {
    const w = await mountShell()
    await get(w, 'designer-header-locale').get('[data-locale="en"]').trigger('click')
    expect(i18n.global.locale.value).toBe('en')
    expect(localStorage.getItem('preferred_locale')).toBe('en')
    expect(w.findAll('[data-test="designer-menu"]').map((m) => m.text())).toEqual(['File', 'Edit', 'Run', 'View', 'Window', 'Help'])
    expect(get(w, 'designer-header-locale').get('[aria-pressed="true"]').text()).toBe('EN')
  })
})

describe('Designer top band — part events', () => {
  const WINDOW: DesignerWindowState = {
    panels: { left: true, right: true, inspector: true, minimap: true },
    activeTabs: ['project', 'palette'],
  }

  // flowgate.v02.0059 T#2 (0011-CH contract D): the same 정렬 / 분배 commands in the Toolbar drop-down (with
  // 자동 배치 after them) and in the Edit menu's 정렬 / 배치 submenu, emitted as they are.
  it('the Toolbar 정렬 / 배치 ▾ and the Edit menu 정렬 / 배치 list and emit the same commands', async () => {
    const targets = { ...ALL_MENU_TARGETS, selectedCount: 3 }
    const toolbar = mount(DesignerToolbar, {
      props: { runMode: null, density: 'normal', zoom: 100, targets },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    expect(toolbar.find('[data-test="designer-arrange-menu"]').exists()).toBe(false)
    await toolbar.get('[data-tool="arrange"]').trigger('click')
    expect(toolbar.get('[data-tool="arrange"]').attributes('aria-expanded')).toBe('true')
    const toolbarItems = toolbar.findAll('[data-test="designer-arrange-item"]')
    expect(toolbarItems.map((el) => el.attributes('data-arrange'))).toEqual([...DESIGNER_ARRANGE_GROUPS.flat(), 'edit.auto-layout'])
    expect(toolbarItems.map((el) => el.text())).toEqual([
      '왼쪽 정렬', '가로 가운데 정렬', '오른쪽 정렬', '위쪽 정렬', '세로 가운데 정렬', '아래쪽 정렬', '가로 균등 분배', '세로 균등 분배', '자동 배치',
    ])
    await toolbar.get('[data-arrange="edit.align-left"]').trigger('click')
    expect(toolbar.emitted('command')).toEqual([['edit.align-left']])
    expect(toolbar.find('[data-test="designer-arrange-menu"]').exists()).toBe(false)

    const menubar = mount(DesignerMenuBar, {
      props: { runMode: null, density: 'normal', windowState: WINDOW, targets },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await menubar.get('[data-menu="edit"]').trigger('click')
    await menubar.get('[data-sub="arrange"]').trigger('click')
    const sub = menubar.get('[data-test="designer-menu-subpop"][data-sub="arrange"]')
    expect(sub.findAll('[data-command]').map((el) => el.attributes('data-command'))).toEqual(DESIGNER_ARRANGE_GROUPS.flat())
    await sub.get('[data-command="edit.align-left"]').trigger('click')
    expect(menubar.emitted('command')).toEqual([['edit.align-left']])
    toolbar.unmount()
    menubar.unmount()
  })

  it('every Menu Bar command, panel, tab and density choice is emitted as an event', async () => {
    const menubar = mount(DesignerMenuBar, {
      props: { runMode: null, density: 'normal', windowState: WINDOW },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await menubar.get('[data-menu="file"]').trigger('click')
    await menubar.get('[data-command="flow.open"]').trigger('click')
    await menubar.get('[data-menu="window"]').trigger('click')
    await menubar.get('[data-panel="minimap"]').trigger('click')
    await menubar.get('[data-menu="window"]').trigger('click')
    await menubar.get('[data-tab="conversation"]').trigger('click')
    await menubar.get('[data-menu="view"]').trigger('click')
    await menubar.get('[data-sub="density"]').trigger('click')
    await menubar.get('[data-density="wide"]').trigger('click')

    expect(menubar.emitted('command')).toEqual([['flow.open']])
    expect(menubar.emitted('toggle-panel')).toEqual([['minimap']])
    expect(menubar.emitted('show-tab')).toEqual([['conversation']])
    expect(menubar.emitted('update:density')).toEqual([['wide']])
    menubar.unmount()
  })

  it('recent entries fed from outside are listed and emitted as open-recent', async () => {
    const menubar = mount(DesignerMenuBar, {
      props: {
        runMode: null,
        density: 'normal',
        windowState: WINDOW,
        recent: { project: [], group: [], flow: [{ id: 'p.ui.0002', name: 'Canvas' }] },
      },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    await menubar.get('[data-menu="file"]').trigger('click')
    await menubar.get('[data-sub="recent-flow"]').trigger('click')
    await menubar.get('[data-test="designer-menu-recent"][data-id="p.ui.0002"]').trigger('click')
    expect(menubar.emitted('open-recent')).toEqual([[{ kind: 'flow', id: 'p.ui.0002' }]])
    menubar.unmount()
  })

  it('the Header entry points emit commands and Enter hands out the scoped query', async () => {
    const header = mount(DesignerHeader, { attachTo: document.body, global: { plugins: [i18n] } })
    await header.get('[data-test="designer-header-notifications"]').trigger('click')
    await header.get('[data-test="designer-header-ai"]').trigger('click')
    await header.get('[data-test="designer-header-settings"]').trigger('click')
    await header.get('[data-test="designer-header-user"]').trigger('click')
    expect(header.emitted('command')).toEqual([['header.notifications'], ['header.ai'], ['header.settings'], ['header.user']])

    await header.get('[data-test="designer-search-input"]').trigger('focus')
    await header.get('[data-scope="document"]').trigger('click')
    await header.get('[data-test="designer-search-input"]').setValue('  0675 ')
    await header.get('[data-test="designer-search-input"]').trigger('keydown', { key: 'Enter' })
    expect(header.emitted('search')).toEqual([[{ scope: 'document', query: '0675' }]])
    header.unmount()
  })
})

describe('designerTop model', () => {
  it('run mode: a start sets the used button, a second start is ignored, stop clears', () => {
    expect(nextRunMode(null, 'run.next')).toBe('next')
    expect(nextRunMode(null, 'run.all')).toBe('all')
    expect(nextRunMode('next', 'run.all')).toBe('next')
    expect(nextRunMode('all', 'run.stop')).toBeNull()
    expect(nextRunMode(null, 'run.stop')).toBeNull()
    expect(nextRunMode(null, 'edit.undo')).toBeNull()
    expect(createDesignerTopState()).toEqual({ runMode: null, density: 'normal' })
  })

  it('menu enablement follows the run mode only', () => {
    expect(isMenuCommandEnabled('run.stop', null)).toBe(false)
    expect(isMenuCommandEnabled('run.stop', 'all')).toBe(true)
    expect(isMenuCommandEnabled('run.rerun', 'next')).toBe(false)
    expect(isMenuCommandEnabled('run.validate', 'next')).toBe(true)
    expect(isMenuCommandEnabled('project.new', null)).toBe(true)
  })

  it('zoom percent rounds the Canvas zoom and is 100 without one', () => {
    expect(zoomPercent(undefined)).toBe(100)
    expect(zoomPercent(null)).toBe(100)
    expect(zoomPercent(0.756)).toBe(76)
    expect(zoomPercent(2)).toBe(200)
  })
})
