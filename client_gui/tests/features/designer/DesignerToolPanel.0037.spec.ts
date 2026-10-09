/**
 * flowgate.v02.0037 — right Tool Panel attached to the Designer Shell (0037-R §2 / §8). The
 * right Dock is mounted with the real Palette part; the Conversation and Git parts are
 * replaced by prop-recording stand-ins so no server contract is exercised here (their own
 * specs cover them). Asserts the [팔레트] [대화] [Git] header row, the mount-on-first-show
 * policy, the scope handed to each part and the collapse behaviour.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerRightDock from '@/features/designer/shell/DesignerRightDock.vue'
import {
  createDesignerLayout,
  DESIGNER_RIGHT_TOOLS,
  rightColumnWidth,
  type DesignerRightTool,
} from '@/features/designer/shell/designerLayout'

registerAppMessages(i18n)

const ConversationStub = defineComponent({
  name: 'ConversationPanel',
  props: { projectId: { type: String, default: null }, groupId: { type: String, default: null } },
  setup: (props) => () =>
    h('section', { 'data-test': 'conversation-panel', 'data-project': props.projectId ?? '', 'data-group': props.groupId ?? '' }),
})

const GitStub = defineComponent({
  name: 'DesignerGitPanel',
  props: { projectId: { type: String, default: null }, flow: { type: Object, default: null }, active: { type: Boolean, default: true } },
  setup: (props) => () =>
    h('section', {
      'data-test': 'designer-git-panel',
      'data-project': props.projectId ?? '',
      'data-group': (props.flow as { groupId?: string } | null)?.groupId ?? '',
      'data-active': String(props.active),
    }),
})

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

interface DockProps {
  projectId: string | null
  activeFlow: { groupId: string; name: string } | null
  tool: DesignerRightTool
  collapsed: boolean
}

async function mountDock(props: Partial<DockProps> = {}) {
  const all: DockProps = { projectId: 'p', activeFlow: null, tool: 'palette', collapsed: false, ...props }
  wrapper = mount(DesignerRightDock, {
    attachTo: document.body,
    props: { ...all, 'onUpdate:tool': (tool: DesignerRightTool) => wrapper!.setProps({ tool }) },
    global: { plugins: [i18n], stubs: { ConversationPanel: ConversationStub, DesignerGitPanel: GitStub } },
  })
  await flushPromises()
  return wrapper
}

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const tabs = (w: VueWrapper<any>) => w.findAll('[data-test="designer-right-tool-tabs"] [role="tab"]')
const isShown = (el: Element) => (el as HTMLElement).style.display !== 'none'

async function selectTool(w: VueWrapper<any>, label: string) {
  const tab = tabs(w).find((t) => t.text() === label)
  if (!tab) throw new Error(`tab not rendered: ${label}`)
  await tab.trigger('click')
  await flushPromises()
}

describe('designerLayout — right Tool Panel state', () => {
  it('starts on the Palette and keeps the 300px right column', () => {
    const layout = createDesignerLayout()
    expect(DESIGNER_RIGHT_TOOLS).toEqual(['palette', 'conversation', 'git'])
    expect(layout.activeRightTool).toBe('palette')
    expect(rightColumnWidth(layout)).toBe(300)
  })
})

describe('i18n — right Tool Panel', () => {
  it.each(['ko', 'en', 'ja'])('%s has a label for every tool and no right placeholder strings', async (locale) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    const shell = messages.designer.shell
    for (const tool of DESIGNER_RIGHT_TOOLS) expect(shell[`tool_${tool}`], tool).toBeTruthy()
    expect(shell.tool_tabs_label).toBeTruthy()
    expect(shell).not.toHaveProperty('right_label')
    expect(shell).not.toHaveProperty('right_parts')
  })
})

describe('DesignerRightDock — Tool Panel', () => {
  it('shows one header row [팔레트] [대화] [Git] and the real Palette part, no placeholder', async () => {
    const w = await mountDock()
    expect(tabs(w).map((t) => t.text())).toEqual(['팔레트', '대화', 'Git'])
    expect(w.get('[data-test="designer-right-tool-tabs"] [aria-selected="true"]').text()).toBe('팔레트')
    expect(get(w, 'designer-right-dock').attributes('data-tool')).toBe('palette')
    expect(get(w, 'designer-right-pane-palette').find('[data-test="designer-palette"]').exists()).toBe(true)
    expect(w.findAll('[data-test="designer-palette-section"]').length).toBeGreaterThan(0)
    expect(w.find('[data-test^="designer-placeholder-"]').exists()).toBe(false)
    // Conversation / Git are not mounted before their tab is first shown.
    expect(w.find('[data-test="conversation-panel"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-git-panel"]').exists()).toBe(false)
  })

  it('passes Palette selection to the Shell and reflects placement cancellation', async () => {
    const w = await mountDock()
    const card = w.get('[data-test="designer-palette-item"][data-item-id="task"]')
    await card.trigger('click')
    const selected = w.emitted('palette-select')?.at(-1)?.[0] as { itemId: string }
    expect(selected.itemId).toBe('task')
    await w.setProps({ selectedPaletteId: selected.itemId })
    expect(card.attributes('aria-pressed')).toBe('true')
    await w.setProps({ selectedPaletteId: null })
    expect(card.attributes('aria-pressed')).toBe('false')
  })

  it('mounts each part on its first show and only hides it afterwards', async () => {
    const w = await mountDock({ activeFlow: { groupId: 'p.ui.0002', name: 'Canvas' } })
    const paletteEl = get(w, 'designer-palette').element

    await selectTool(w, '대화')
    expect(get(w, 'designer-right-dock').attributes('data-tool')).toBe('conversation')
    expect(isShown(get(w, 'designer-right-pane-conversation').element)).toBe(true)
    expect(isShown(get(w, 'designer-right-pane-palette').element)).toBe(false)

    await selectTool(w, 'Git')
    expect(isShown(get(w, 'designer-right-pane-git').element)).toBe(true)
    expect(isShown(get(w, 'designer-right-pane-conversation').element)).toBe(false)
    const convEl = get(w, 'conversation-panel').element

    await selectTool(w, '팔레트')
    expect(get(w, 'designer-palette').element === paletteEl).toBe(true)
    await selectTool(w, '대화')
    expect(get(w, 'conversation-panel').element === convEl).toBe(true)
    // Git stays mounted while hidden, told it is not active.
    expect(get(w, 'designer-git-panel').attributes('data-active')).toBe('false')
  })

  it('hands the Conversation and Git parts the current Project and the active Flow', async () => {
    const w = await mountDock({ tool: 'conversation', activeFlow: { groupId: 'p.ui.0002', name: 'Canvas' } })
    expect(get(w, 'conversation-panel').attributes('data-project')).toBe('p')
    expect(get(w, 'conversation-panel').attributes('data-group')).toBe('p.ui.0002')

    await selectTool(w, 'Git')
    const git = get(w, 'designer-git-panel')
    expect(git.attributes('data-project')).toBe('p')
    expect(git.attributes('data-group')).toBe('p.ui.0002')
    expect(git.attributes('data-active')).toBe('true')

    await w.setProps({ activeFlow: null })
    expect(get(w, 'designer-git-panel').attributes('data-group')).toBe('')
    expect(get(w, 'conversation-panel').attributes('data-group')).toBe('')
  })

  it('keeps the chosen tool and the mounted parts while collapsed', async () => {
    const w = await mountDock({ tool: 'git' })
    await w.setProps({ collapsed: true })
    expect(get(w, 'designer-right-dock').attributes('data-collapsed')).toBe('true')
    expect(isShown(get(w, 'designer-right-header').element)).toBe(false)
    expect(isShown(get(w, 'designer-right-body').element)).toBe(false)
    expect(get(w, 'designer-git-panel').attributes('data-active')).toBe('false')
    await w.setProps({ collapsed: false })
    expect(get(w, 'designer-right-dock').attributes('data-tool')).toBe('git')
    expect(get(w, 'designer-git-panel').attributes('data-active')).toBe('true')
  })
})
