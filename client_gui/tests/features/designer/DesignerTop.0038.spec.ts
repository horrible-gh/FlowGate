import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import DesignerTop from '@/features/designer/shell/DesignerTop.vue'

function createTop(overrides: Record<string, unknown> = {}) {
  return mount(DesignerTop, {
    attachTo: document.body,
    props: {
      projectAvailable: true, flowAvailable: true, hasSelection: false,
      leftCollapsed: false, rightCollapsed: false, leftMode: 'project', rightTool: 'palette',
      ...overrides,
    } as any,
    global: { plugins: [i18n] },
  })
}

describe('Designer top (0038)', () => {
  it('keeps search scope visible after closing assist', async () => {
    const w = createTop()
    await w.get('[data-test="designer-search-input"]').trigger('focus')
    expect(w.get('[data-test="designer-search-assist"]').text()).toContain('검색 범위')
    await w.findAll('.fg-scope-list button').find((button) => button.text() === '노드')!.trigger('click')
    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await nextTick()
    expect(w.get('[data-test="designer-search-scope"]').text()).toContain('노드')
    expect(w.get('[data-test="designer-search-input"]').attributes('placeholder')).toContain('노드 이름')
    expect(w.find('[data-test="designer-search-assist"]').exists()).toBe(false)
    w.unmount()
  })

  it('exposes all six menus and opens adjacent menu on hover', async () => {
    const w = createTop()
    expect(w.findAll('.fg-menu-trigger').map((button) => button.text())).toEqual(['파일', '편집', '실행', '보기', '창', '도움말'])
    await w.get('[data-test="designer-menu-file"]').trigger('click')
    expect(w.text()).toContain('최근 Project')
    await w.get('[data-test="designer-menu-edit"]').element.parentElement!.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }))
    await nextTick()
    expect(w.find('[data-test="designer-menu-edit"]').attributes('aria-expanded')).toBe('true')
    expect(w.find('[data-test="designer-menu-file"]').attributes('aria-expanded')).toBe('false')
    w.unmount()
  })

  it('marks panel toggles and active tabs with different roles', async () => {
    const w = createTop({ leftMode: 'file', rightTool: 'git' })
    await w.get('[data-test="designer-menu-window"]').trigger('click')
    expect(w.findAll('[role="menuitemcheckbox"]').map((button) => button.text())).toEqual(['✓좌측 패널', '✓우측 도구 패널', '✓Inspector', '✓미니맵'])
    expect(w.findAll('[role="menuitemradio"][aria-checked="true"]').map((button) => button.text())).toEqual(['●파일 탐색기', '●Git'])
    w.unmount()
  })

  it('changes only the active run button into stop and restores it', async () => {
    const w = createTop()
    await w.get('[data-test="designer-run-all"]').trigger('click')
    expect(w.get('[data-test="designer-run-all"]').text()).toContain('중지')
    expect(w.get('[data-test="designer-run-next"]').attributes('disabled')).toBeDefined()
    await w.get('[data-test="designer-run-all"]').trigger('click')
    expect(w.get('[data-test="designer-run-all"]').text()).toContain('전체 실행')
    expect(w.emitted('command')?.map(([id]) => id)).toEqual(['run.all', 'run.stop'])
    w.unmount()
  })

  it('shares density state between menu and toolbar', async () => {
    const w = createTop()
    await w.get('[data-test="designer-menu-view"]').trigger('click')
    await w.findAll('.fg-sub-wrap > button').find((button) => button.text().includes('배치 밀도'))!.trigger('click')
    await w.findAll('.fg-sub-pop button').find((button) => button.text().includes('넓게') && !button.text().includes('매우'))!.trigger('click')
    expect(w.find('.fg-density button[aria-label="넓게"]').attributes('aria-pressed')).toBe('true')
    w.unmount()
  })
})
