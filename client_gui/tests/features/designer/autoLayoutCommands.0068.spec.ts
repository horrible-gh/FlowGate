/** 0068 T0011: both Arrange entry points expose and emit the same directional commands. */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DesignerMenuBar from '@/features/designer/top/DesignerMenuBar.vue'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import {
  ALL_MENU_TARGETS,
  DESIGNER_ARRANGE_GROUPS,
  DESIGNER_AUTO_LAYOUT_COMMANDS,
  autoLayoutDirectionOf,
  type DesignerWindowState,
} from '@/features/designer/top/designerTop'

registerAppMessages(i18n)
const WINDOW: DesignerWindowState = {
  panels: { left: true, right: true, inspector: true, minimap: true },
  activeTabs: ['project', 'palette'],
}
const EXPECTED = [...DESIGNER_ARRANGE_GROUPS.flat(), 'edit.auto-layout-horizontal', 'edit.auto-layout-vertical']
const LABELS = ['왼쪽 정렬', '가로 가운데 정렬', '오른쪽 정렬', '위쪽 정렬', '세로 가운데 정렬', '아래쪽 정렬', '가로 균등 분배', '세로 균등 분배', '가로 자동정렬', '세로 자동정렬']

describe('directional auto layout entry points', () => {
  it('maps each command to its direction', () => {
    expect(DESIGNER_AUTO_LAYOUT_COMMANDS.map(autoLayoutDirectionOf)).toEqual(['horizontal', 'vertical'])
    expect(autoLayoutDirectionOf('edit.align-left')).toBeNull()
  })

  it.each([0, 2, 3])('Edit > Arrange and Toolbar share order, labels and enablement with %i selected cards', async (selectedCount) => {
    i18n.global.locale.value = 'ko'
    const props = { runPhase: 'idle' as const, density: 'normal' as const, targets: { ...ALL_MENU_TARGETS, selectedCount } }
    const toolbar = mount(DesignerToolbar, { props: { ...props, zoom: 100 }, global: { plugins: [i18n] } })
    const menubar = mount(DesignerMenuBar, { props: { ...props, windowState: WINDOW }, global: { plugins: [i18n] } })
    try {
      await toolbar.get('[data-tool="arrange"]').trigger('click')
      await menubar.get('[data-menu="edit"]').trigger('click')
      expect(menubar.find('[data-command="edit.auto-layout-horizontal"]').exists()).toBe(false)
      await menubar.get('[data-sub="arrange"]').trigger('click')
      const menu = menubar.get('[data-test="designer-menu-subpop"][data-sub="arrange"]')
      const tool = toolbar.get('[data-test="designer-arrange-menu"]')
      const menuItems = menu.findAll('[data-command]')
      const toolItems = tool.findAll('[data-arrange]')
      expect(menuItems.map((el) => el.attributes('data-command'))).toEqual(EXPECTED)
      expect(toolItems.map((el) => el.attributes('data-arrange'))).toEqual(EXPECTED)
      expect(menuItems.map((el) => el.text())).toEqual(LABELS)
      expect(toolItems.map((el) => el.text())).toEqual(LABELS)
      const disabled = [...Array(6).fill(selectedCount < 2), ...Array(2).fill(selectedCount < 3), false, false]
      expect(menuItems.map((el) => el.attributes('disabled') !== undefined)).toEqual(disabled)
      expect(toolItems.map((el) => el.attributes('disabled') !== undefined)).toEqual(disabled)
      expect(menu.findAll('[role="separator"]')).toHaveLength(3)
      expect(tool.findAll('[role="separator"]')).toHaveLength(3)
      expect(menubar.find('[data-command="edit.auto-layout"]').exists()).toBe(false)
      for (const command of DESIGNER_AUTO_LAYOUT_COMMANDS) {
        await menubar.get(`[data-command="${command}"]`).trigger('click')
        await toolbar.get(`[data-arrange="${command}"]`).trigger('click')
        await menubar.get('[data-menu="edit"]').trigger('click')
        await menubar.get('[data-sub="arrange"]').trigger('click')
        await toolbar.get('[data-tool="arrange"]').trigger('click')
      }
      expect(menubar.emitted('command')).toEqual(DESIGNER_AUTO_LAYOUT_COMMANDS.map((c) => [c]))
      expect(toolbar.emitted('command')).toEqual(menubar.emitted('command'))
      await menubar.setProps({ targets: { ...props.targets, flow: false } })
      await toolbar.setProps({ targets: { ...props.targets, flow: false } })
      expect(menubar.get('[data-test="designer-menu-sub"][data-sub="arrange"]').attributes('disabled')).toBeDefined()
      for (const command of DESIGNER_AUTO_LAYOUT_COMMANDS) {
        expect(toolbar.get(`[data-arrange="${command}"]`).attributes('disabled')).toBeDefined()
      }
    } finally {
      toolbar.unmount()
      menubar.unmount()
    }
  })

  it.each(['ko', 'en', 'ja'])('replaces the single label in %s', async (locale) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    const top = messages.designer.top
    expect(top.items).not.toHaveProperty('auto_layout')
    expect(typeof top.items.auto_layout_horizontal).toBe('string')
    expect(typeof top.items.auto_layout_vertical).toBe('string')
    expect(Object.keys(top.layout_notices.invalid_reasons).sort()).toEqual(['direction', 'duplicate', 'invalid_coordinate', 'missing', 'overlap', 'pierce'])
  })
})
